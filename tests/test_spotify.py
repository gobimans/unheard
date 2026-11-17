"""Unit tests for unheard/scripts/spotify.py. Standard library only, no network.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("spotify", os.path.join(HERE, "..", "unheard", "scripts", "spotify.py"))
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


def track(uri, name, *artists):
    return {"uri": uri, "name": name, "artists": [{"name": a} for a in artists]}


class FakeAPI:
    """Stands in for spotify.api: search answers by query, playlist writes are recorded."""

    def __init__(self, search):
        self.search, self.calls, self.items = search, [], []

    def __call__(self, st, method, path, body=None, q=None):
        self.calls.append((method, path, body))
        if path == "/search":
            return {"tracks": {"items": self.search.get(q["q"], [])}}
        if path == "/me/playlists":
            return {"id": "pl1"}
        if path.endswith("/items") and method == "PUT":
            self.items = list(body["uris"])
            return {}
        if path.endswith("/items") and method == "POST":
            self.items += body["uris"]
            return {}
        if path.endswith("/items") and method == "GET":
            return {"items": [{"item": {"uri": u, "name": "x", "artists": [{"name": "a"}]}} for u in self.items], "next": None}
        raise AssertionError(path)


class TestFind(unittest.TestCase):
    def test_exact_title_beats_remix(self):
        api = FakeAPI({"track:Drums artist:Outlines": [
            track("u:remix", "Drums - Someone Remix", "Outlines"),
            track("u:orig", "Drums", "Outlines")]})
        with mock.patch.object(sp, "api", api):
            it, how = sp.find(None, "Outlines", "Drums")
        self.assertEqual((it["uri"], how), ("u:orig", "exact"))

    def test_other_artist_with_same_title_is_rejected(self):
        api = FakeAPI({"track:Song artist:Björk": [track("u:x", "Song", "Somebody Else")],
                       "Björk Song": [], "artist:Björk": [track("u:b", "Another", "Bjork")]})
        with mock.patch.object(sp, "api", api):
            it, how = sp.find(None, "Björk", "Song")
        self.assertEqual((it["uri"], how), ("u:b", "other_track"))

    def test_missing(self):
        with mock.patch.object(sp, "api", FakeAPI({})):
            self.assertEqual(sp.find(None, "Nobody", "Nothing"), (None, "missing"))


class TestFill(unittest.TestCase):
    def run_fill(self, rows, search):
        api = FakeAPI(search)
        args = types.SimpleNamespace(store=tempfile.mkdtemp(), playlist=None, name="Test", description="", public=False)
        out = io.StringIO()
        with mock.patch.object(sp, "api", api), mock.patch.object(sys, "stdin", io.StringIO(json.dumps(rows))), \
                mock.patch("sys.stdout", out):
            code = sp.cmd_fill(args)
        return code, json.loads(out.getvalue()), api

    def test_fallback_used_and_order_kept(self):
        rows = [{"artist": "A", "track": "One"},
                {"artist": "Gone", "track": "Two", "fallback": [{"artist": "C", "track": "Three"}]}]
        search = {"track:One artist:A": [track("u:1", "One", "A")],
                  "track:Three artist:C": [track("u:3", "Three", "C")]}
        code, res, api = self.run_fill(rows, search)
        self.assertEqual(code, 0)
        self.assertTrue(res["readback_matches"])
        self.assertEqual([w["uri"] for w in res["written"]], ["u:1", "u:3"])
        self.assertEqual(res["written"][1]["match"], "fallback:exact")
        self.assertEqual(res["missing"], [])
        self.assertIn(("POST", "/me/playlists"), [(m, p) for m, p, _ in api.calls])
        self.assertNotIn("/tracks", " ".join(p for _, p, _ in api.calls))

    def test_missing_reported(self):
        code, res, _ = self.run_fill([{"artist": "Gone", "track": "Two"}], {})
        self.assertEqual(res["missing"], ["Gone — Two"])


class TestAuth(unittest.TestCase):
    def test_state_mismatch_stops(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "spotify.json"), "w") as f:
            json.dump({"client_id": "id", "verifier": "v", "state": "good"}, f)
        args = types.SimpleNamespace(store=d, url="http://127.0.0.1:8888/callback?code=c&state=bad")
        with self.assertRaises(SystemExit) as e:
            sp.cmd_auth_code(args)
        self.assertIn("state_mismatch", str(e.exception))

    def test_auth_url_saves_verifier(self):
        d = tempfile.mkdtemp()
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            sp.cmd_auth_url(types.SimpleNamespace(store=d, client_id="abc"))
        url = json.loads(out.getvalue())["url"]
        with open(os.path.join(d, "spotify.json")) as f:
            saved = json.load(f)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn(saved["state"], url)
        self.assertEqual(saved["client_id"], "abc")


if __name__ == "__main__":
    unittest.main()
