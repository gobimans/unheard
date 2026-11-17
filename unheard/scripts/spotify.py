#!/usr/bin/env python3
"""Spotify Web API helpers for the unheard skill.

Writes exact track lists into Spotify playlists, which the Spotify connector's
generate_playlist cannot do. Uses the user's own Development Mode app with
Authorization Code + PKCE (no client secret).

State lives in spotify.json in --store: client_id, refresh_token, access_token,
expires_in, obtained, plus the pending PKCE verifier during auth. Spotify rotates
the refresh token on every refresh, so spotify.json changes after any API call:
copy it back to the persistent store at the end of every run.

    auth-url   --client-id ID           print the authorization link (saves the PKCE verifier)
    auth-code  --url CALLBACK_URL       exchange the redirected URL for tokens
    fill       [--playlist ID | --name NAME] [--public]
               stdin: JSON list of {"artist","track","fallback":[{"artist","track"},...]}
               finds each track, writes the list in order (replaces playlist contents),
               reads the playlist back and prints JSON {playlist, url, written, missing, readback}
    read       --playlist ID            print the playlist's tracks

Endpoints follow the February 2026 Web API: /playlists/{id}/items (not /tracks),
/me/playlists for creation. Search returns at most 10 results per call.
Exit codes: 0 ok, 1 usage/auth problem, 2 API failure.
"""
import argparse, base64, hashlib, json, os, re, secrets, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request

REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-modify-private playlist-modify-public playlist-read-private"
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"


class Store:
    def __init__(self, d):
        self.path = os.path.join(d, "spotify.json")
        self.s = {}
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.s = json.load(f)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


def post_token(data):
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        sys.exit(json.dumps({"ok": False, "error": "token", "status": e.code, "detail": body[:300],
                             "hint": "re-authorize: run auth-url, approve, then auth-code"}))


def token(st):
    s = st.s
    if "refresh_token" not in s:
        sys.exit(json.dumps({"ok": False, "error": "not_authorized", "hint": "run auth-url then auth-code"}))
    if time.time() < s.get("obtained", 0) + s.get("expires_in", 0) - 60:
        return s["access_token"]
    n = post_token({"grant_type": "refresh_token", "refresh_token": s["refresh_token"], "client_id": s["client_id"]})
    s.update(n)
    s["obtained"] = int(time.time())
    st.save()                                   # rotated refresh token must persist immediately
    return s["access_token"]


def api(st, method, path, body=None, q=None):
    url = API + path + ("?" + urllib.parse.urlencode(q) if q else "")
    for i in range(5):
        req = urllib.request.Request(url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + token(st), "Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=30)
            raw = r.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", "2")) + 1)
                continue
            if e.code >= 500 and i < 4:
                time.sleep(2 * (i + 1))
                continue
            raise RuntimeError(f"{method} {path}: {e.code} {e.read()[:200]!r}")
        except urllib.error.URLError:
            if i < 4:
                time.sleep(2 * (i + 1))
                continue
            raise


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^the ", "", s)
    return re.sub(r"[^\w ]", "", s).strip()


BAD = re.compile(r"\b(remix|rmx|live|instrumental|karaoke|sped up|slowed)\b", re.I)


def find(st, artist, track):
    """Exact artist match required. Prefer exact title, non-remix; else the artist's first clean track."""
    na, nt = norm(artist), norm(track)
    best = None
    for q in (f"track:{track} artist:{artist}", f"{artist} {track}"):
        items = api(st, "GET", "/search", q={"q": q, "type": "track", "limit": 10})["tracks"]["items"]
        for it in items:
            if not any(norm(a["name"]) == na for a in it["artists"]):
                continue
            ni = norm(it["name"])
            if nt and (nt == ni or ni.startswith(nt) or nt in ni):
                score = (nt == ni) * 2 + (not BAD.search(it["name"]) or bool(BAD.search(track)))
                if best is None or score > best[0]:
                    best = (score, it)
        if best and best[0] >= 1:
            break
    if best:
        return best[1], "exact"
    items = api(st, "GET", "/search", q={"q": f"artist:{artist}", "type": "track", "limit": 10})["tracks"]["items"]
    for it in items:
        if any(norm(a["name"]) == na for a in it["artists"]) and not BAD.search(it["name"]):
            return it, "other_track"
    return None, "missing"


def readback(st, pid):
    out, off = [], 0
    while True:
        r = api(st, "GET", f"/playlists/{pid}/items", q={"limit": 50, "offset": off})
        for x in r["items"]:
            t = x.get("item") or x.get("track") or {}
            out.append({"artist": ", ".join(a["name"] for a in t.get("artists", [])), "track": t.get("name"), "uri": t.get("uri")})
        off += 50
        if not r.get("next"):
            return out


def cmd_auth_url(a):
    st = Store(a.store)
    v = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_hex(8)
    st.s.update({"client_id": a.client_id, "verifier": v, "state": state})
    st.save()
    print(json.dumps({"ok": True, "url": "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(dict(
        client_id=a.client_id, response_type="code", redirect_uri=REDIRECT, code_challenge_method="S256",
        code_challenge=c, state=state, scope=SCOPES))}))


def cmd_auth_code(a):
    st = Store(a.store)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(a.url.strip()).query)
    if "error" in qs:
        sys.exit(json.dumps({"ok": False, "error": qs["error"][0]}))
    if qs.get("state", [None])[0] != st.s.get("state"):
        sys.exit(json.dumps({"ok": False, "error": "state_mismatch", "hint": "use the link from the latest auth-url"}))
    n = post_token({"grant_type": "authorization_code", "code": qs["code"][0], "redirect_uri": REDIRECT,
                    "client_id": st.s["client_id"], "code_verifier": st.s["verifier"]})
    for k in ("verifier", "state"):
        st.s.pop(k, None)
    st.s.update(n)
    st.s["obtained"] = st.s["authorized"] = int(time.time())
    st.save()
    me = api(st, "GET", "/me")
    print(json.dumps({"ok": True, "user": me.get("display_name") or me.get("id"), "scope": n.get("scope"),
                      "reauthorize_before": time.strftime("%Y-%m-%d", time.gmtime(st.s["authorized"] + 182 * 86400))}))


def cmd_fill(a):
    st = Store(a.store)
    rows = json.load(sys.stdin)
    pid = a.playlist
    if not pid:
        if not a.name:
            sys.exit(json.dumps({"ok": False, "error": "need --playlist or --name"}))
        pid = api(st, "POST", "/me/playlists", body={"name": a.name, "public": bool(a.public),
                                                    "description": a.description or ""})["id"]
    written, missing, seen = [], [], set()
    for r in rows:
        for cand in [r] + list(r.get("fallback", [])):
            it, how = find(st, cand["artist"], cand.get("track", ""))
            if it and it["uri"] not in seen:
                seen.add(it["uri"])
                written.append({"requested": f'{r["artist"]} — {r.get("track", "")}', "artist": it["artists"][0]["name"],
                                "track": it["name"], "uri": it["uri"], "match": how if cand is r else "fallback:" + how})
                break
        else:
            missing.append(f'{r["artist"]} — {r.get("track", "")}')
    uris = [w["uri"] for w in written]
    api(st, "PUT", f"/playlists/{pid}/items", body={"uris": uris[:100]})
    for i in range(100, len(uris), 100):
        api(st, "POST", f"/playlists/{pid}/items", body={"uris": uris[i:i + 100]})
    back = readback(st, pid)
    ok = [b["uri"] for b in back] == uris
    print(json.dumps({"ok": ok, "playlist": pid, "url": f"https://open.spotify.com/playlist/{pid}",
                      "written": written, "missing": missing, "readback_matches": ok, "readback_count": len(back)},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 2


def cmd_read(a):
    print(json.dumps(readback(Store(a.store), a.playlist), ensure_ascii=False, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("auth-url", "auth-code", "fill", "read"):
        s = sub.add_parser(name)
        s.add_argument("--store", default=".")
    sub.choices["auth-url"].add_argument("--client-id", required=True)
    sub.choices["auth-code"].add_argument("--url", required=True)
    f = sub.choices["fill"]
    f.add_argument("--playlist"); f.add_argument("--name"); f.add_argument("--description")
    f.add_argument("--public", action="store_true")
    sub.choices["read"].add_argument("--playlist", required=True)
    a = p.parse_args()
    try:
        return {"auth-url": cmd_auth_url, "auth-code": cmd_auth_code, "fill": cmd_fill, "read": cmd_read}[a.cmd](a) or 0
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
