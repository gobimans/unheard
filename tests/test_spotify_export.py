"""Unit tests for contrib/spotify_export.py. No network: the Web API layer is mocked."""
import importlib.util
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("spotify_export", os.path.join(ROOT, "contrib", "spotify_export.py"))
sx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sx)


class ReadItems(unittest.TestCase):
    def test_discover_output(self):
        text = json.dumps({"meta": {}, "tracks": [{"artist": "Zofka", "track": "Parfum Vanille", "similar_to": []},
                                                  {"artist": "No Track", "track": None}]})
        self.assertEqual(sx.read_items(text), [("Zofka", "Parfum Vanille")])

    def test_plain_list(self):
        self.assertEqual(sx.read_items('[{"artist": "Air", "track": "Playground Love"}]'), [("Air", "Playground Love")])


class FakeSpotify(sx.Spotify):
    def __init__(self, results):
        super().__init__("token")
        self.results, self.calls = results, []

    def call(self, method, path, body=None, params=None):
        self.calls.append((method, path, body, params))
        if path == "/search":
            return self.results.pop(0) if self.results else {"tracks": {"items": []}}
        if path == "/me":
            return {"id": "me"}
        if path.endswith("/playlists"):
            return {"id": "pl1", "external_urls": {"spotify": "https://open.spotify.com/playlist/pl1"}}
        return {}


def hit(uri, *artists):
    return {"tracks": {"items": [{"uri": uri, "artists": [{"name": a} for a in artists]}]}}


class Find(unittest.TestCase):
    def test_exact_search_first(self):
        sp = FakeSpotify([hit("spotify:track:1", "Zofka")])
        self.assertEqual(sp.find("Zofka", "Parfum Vanille")["uri"], "spotify:track:1")
        self.assertEqual(len(sp.calls), 1)
        self.assertIn('artist:"Zofka"', sp.calls[0][3]["q"])

    def test_loose_search_needs_matching_artist(self):
        sp = FakeSpotify([{"tracks": {"items": []}}, hit("spotify:track:2", "Björk")])
        self.assertEqual(sp.find("Bjork", "Joga")["uri"], "spotify:track:2")

    def test_wrong_artist_rejected(self):
        sp = FakeSpotify([{"tracks": {"items": []}}, hit("spotify:track:3", "Somebody Else")])
        self.assertIsNone(sp.find("Zofka", "Parfum Vanille"))


class Create(unittest.TestCase):
    def test_batches_of_100(self):
        sp = FakeSpotify([])
        uris = [f"spotify:track:{i}" for i in range(150)]
        pl = sp.create("Unheard", "desc", uris)
        adds = [c for c in sp.calls if c[1] == "/playlists/pl1/tracks"]
        self.assertEqual([len(c[2]["uris"]) for c in adds], [100, 50])
        self.assertEqual(pl["id"], "pl1")
        made = [c for c in sp.calls if c[1] == "/users/me/playlists"][0]
        self.assertEqual(made[2], {"name": "Unheard", "description": "desc", "public": False})


if __name__ == "__main__":
    unittest.main()
