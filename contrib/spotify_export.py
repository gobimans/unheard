#!/usr/bin/env python3
"""Optional: turn an Unheard result into an exact Spotify playlist through the Spotify Web API.

Not part of the skill. The skill creates playlists with Claude's Spotify connector, which generates
playlists from a description and may swap tracks. This script adds exactly the tracks you give it.
It needs your own Spotify app, so it is for people running the skill locally (Claude Code).

Spotify limits apps in development mode to 5 users, and the app owner needs Spotify Premium.
For your own account that is enough.

Setup, once:
  1. developer.spotify.com/dashboard -> Create app. Redirect URI: http://127.0.0.1:8888/callback
     APIs used: Web API. Copy the Client ID.
  2. export SPOTIFY_CLIENT_ID=...
  The first run opens a browser for login (PKCE, no client secret needed) and saves a refresh token
  to spotify_token.json in --store. Later runs reuse it.
  Alternative: export SPOTIFY_ACCESS_TOKEN=... (a token you already have; expires in an hour).

Usage:
  python3 unheard/scripts/lastfm.py discover --store DIR --needed 24 > result.json
  python3 contrib/spotify_export.py --store DIR --name "Unheard 2026-10" --input result.json
  # or pipe:  ... discover ... | python3 contrib/spotify_export.py --store DIR --name "Unheard"
  Add --dry-run to see what would be matched without creating anything.

Standard library only.
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

API = "https://api.spotify.com/v1"
AUTH = "https://accounts.spotify.com"
REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-modify-private playlist-modify-public"
BATCH = 100


def norm(name):
    """Same normalisation as lastfm.py: lowercase, no diacritics, no feat./ft./& tail, no leading 'the '."""
    n = unicodedata.normalize("NFKD", name.casefold())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"\s*(feat\.|ft\.|&).*$", "", n).strip()
    return re.sub(r"^the\s+", "", n)


def read_items(text):
    """Accept the JSON printed by discover/loved ({"tracks": [...]}), the forgotten list, or a plain list."""
    data = json.loads(text)
    rows = data.get("tracks", []) if isinstance(data, dict) else data
    out = []
    for r in rows:
        artist, track = r.get("artist"), r.get("track")
        if artist and track:
            out.append((artist, track))
    return out


# ------------------------------------------------------------------------ auth

def _post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _login(client_id):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Done. You can close this tab.".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8888), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    url = AUTH + "/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    print("Log in to Spotify in the browser. If it did not open, visit:\n" + url, file=sys.stderr)
    webbrowser.open(url)
    for _ in range(300):
        if got:
            break
        time.sleep(1)
    server.server_close()
    if got.get("state") != state or "code" not in got:
        sys.exit("Spotify login failed or timed out: " + got.get("error", "no response"))
    return _post_form(AUTH + "/api/token", {
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
        "client_id": client_id, "code_verifier": verifier})


def get_token(store):
    if os.environ.get("SPOTIFY_ACCESS_TOKEN"):
        return os.environ["SPOTIFY_ACCESS_TOKEN"]
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    if not client_id:
        sys.exit("Set SPOTIFY_CLIENT_ID (your Spotify app) or SPOTIFY_ACCESS_TOKEN. See the top of this file.")
    path = os.path.join(store, "spotify_token.json")
    saved = {}
    try:
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    except FileNotFoundError:
        pass
    if saved.get("refresh_token"):
        try:
            tok = _post_form(AUTH + "/api/token", {"grant_type": "refresh_token",
                                                   "refresh_token": saved["refresh_token"], "client_id": client_id})
        except urllib.error.HTTPError:
            tok = _login(client_id)
    else:
        tok = _login(client_id)
    saved["refresh_token"] = tok.get("refresh_token", saved.get("refresh_token"))
    os.makedirs(store, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(saved, fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok["access_token"]


# ------------------------------------------------------------------------ Web API

class Spotify:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None, params=None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or exc.code >= 500:
                    try:
                        wait = min(float(exc.headers.get("Retry-After") or 0), 60) or 2 ** attempt
                    except ValueError:
                        wait = 2 ** attempt
                    time.sleep(wait)
                    continue
                detail = exc.read().decode("utf-8", "replace")[:300]
                sys.exit(f"Spotify {exc.code} on {path}: {detail}")
        sys.exit(f"Spotify kept failing on {path}")

    def find(self, artist, track):
        """Exact field search first; then a loose search accepted only if an artist name matches after norm()."""
        want = norm(artist)
        for q in (f'artist:"{artist}" track:"{track}"', f"{artist} {track}"):
            res = self.call("GET", "/search", params={"q": q, "type": "track", "limit": 10})
            for item in res.get("tracks", {}).get("items", []):
                if any(norm(a["name"]) == want for a in item.get("artists", [])):
                    return item
        return None

    def create(self, name, description, uris, public=False):
        me = self.call("GET", "/me")
        pl = self.call("POST", f"/users/{urllib.parse.quote(me['id'])}/playlists",
                       {"name": name, "description": description, "public": public})
        for i in range(0, len(uris), BATCH):
            self.call("POST", f"/playlists/{pl['id']}/tracks", {"uris": uris[i:i + BATCH]})
        return pl


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True, help="playlist name")
    p.add_argument("--description", default="Made with Unheard from my Last.fm history")
    p.add_argument("--input", help="JSON file from lastfm.py (default: stdin)")
    p.add_argument("--store", default=".", help="folder for spotify_token.json")
    p.add_argument("--public", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="match tracks, create nothing")
    args = p.parse_args()

    text = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
    items = read_items(text)
    if not items:
        sys.exit("No artist/track pairs in the input.")
    sp = Spotify(get_token(args.store))
    found, missing = [], []
    for artist, track in items:
        hit = sp.find(artist, track)
        (found if hit else missing).append((artist, track, hit["uri"] if hit else None))
    report = {"matched": len(found), "missing": [f"{a} - {t}" for a, t, _ in missing]}
    if not args.dry_run and found:
        pl = sp.create(args.name, args.description, [u for _, _, u in found], args.public)
        report["playlist"] = pl.get("external_urls", {}).get("spotify")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
