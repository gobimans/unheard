"""Unit tests for unheard/scripts/lastfm.py. Standard library only, no network.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import datetime as dt
import importlib.util
import io
import json
import os
import unittest
import urllib.error
from email.message import Message
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("lastfm", os.path.join(ROOT, "unheard", "scripts", "lastfm.py"))
lastfm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lastfm)


def ts(y, m, d=1, h=0):
    return int(dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp())


class Norm(unittest.TestCase):
    def test_case_and_diacritics(self):
        self.assertEqual(lastfm.norm("Björk"), "bjork")
        self.assertEqual(lastfm.norm("Sigur Rós"), "sigur ros")

    def test_collaboration_tail(self):
        self.assertEqual(lastfm.norm("Nujabes feat. Shing02"), "nujabes")
        self.assertEqual(lastfm.norm("Kanye West ft. Jay-Z"), "kanye west")
        self.assertEqual(lastfm.norm("Simon & Garfunkel"), "simon")

    def test_leading_article(self):
        self.assertEqual(lastfm.norm("The xx"), "xx")
        self.assertEqual(lastfm.norm("Theatre of Tragedy"), "theatre of tragedy")

    def test_idempotent(self):
        for name in ("Björk", "The Libertines", "Cœur de Pirate"):
            self.assertEqual(lastfm.norm(lastfm.norm(name)), lastfm.norm(name))


class Dates(unittest.TestCase):
    def test_label_idx(self):
        self.assertEqual(lastfm.label_idx("2007-01"), 2007 * 12)
        self.assertEqual(lastfm.label_idx("2007-12") - lastfm.label_idx("2007-01"), 11)
        self.assertEqual(lastfm.label_idx("2008-01") - lastfm.label_idx("2007-12"), 1)

    def test_next_month(self):
        self.assertEqual(lastfm.next_month(2020, 5), (2020, 6))
        self.assertEqual(lastfm.next_month(2020, 12), (2021, 1))

    def test_month_label(self):
        self.assertEqual(lastfm.month_label(ts(2019, 3, 31, 23)), "2019-03")

    def test_month_ranges_across_year(self):
        r = list(lastfm.month_ranges(ts(2019, 11, 15), ts(2020, 2, 3)))
        self.assertEqual([x[0] for x in r], ["2019-11", "2019-12", "2020-01", "2020-02"])
        self.assertEqual(r[0][1], ts(2019, 11, 1))          # whole calendar month, not the registration day
        self.assertEqual(r[1][2], ts(2020, 1, 1) - 1)       # ends one second before the next month

    def test_month_ranges_contiguous(self):
        r = list(lastfm.month_ranges(ts(2007, 8, 31), ts(2009, 1, 1)))
        for (_, _, to), (_, start, _) in zip(r, r[1:]):
            self.assertEqual(start, to + 1)

    def test_month_ranges_single_month(self):
        r = list(lastfm.month_ranges(ts(2021, 6, 2), ts(2021, 6, 20)))
        self.assertEqual([x[0] for x in r], ["2021-06"])


class Parsing(unittest.TestCase):
    def test_chart_list(self):
        data = {"weeklyartistchart": {"artist": [{"name": "Air", "playcount": "12"}, {"name": "Bonobo", "playcount": "3"}]}}
        self.assertEqual(lastfm.parse_chart(data), {"Air": 12, "Bonobo": 3})

    def test_chart_single_object(self):
        data = {"weeklyartistchart": {"artist": {"name": "Air", "playcount": "7"}}}
        self.assertEqual(lastfm.parse_chart(data), {"Air": 7})

    def test_chart_empty(self):
        self.assertEqual(lastfm.parse_chart({"weeklyartistchart": {"artist": []}}), {})
        self.assertEqual(lastfm.parse_chart({}), {})

    def test_listify(self):
        self.assertEqual(lastfm.listify({"a": 1}), [{"a": 1}])
        self.assertEqual(lastfm.listify([1, 2]), [1, 2])
        self.assertEqual(lastfm.listify(None), [])


class Metrics(unittest.TestCase):
    def test_cosine(self):
        self.assertAlmostEqual(lastfm.cosine({"a": 1, "b": 2}, {"a": 2, "b": 4}), 1.0)
        self.assertAlmostEqual(lastfm.cosine({"a": 1}, {"b": 1}), 0.0)
        self.assertEqual(lastfm.cosine({}, {"a": 1}), 0.0)

    def test_percentile_pair(self):
        lo, hi = lastfm.percentile_pair(list(range(1, 101)), 0.10, 0.90)
        self.assertEqual((lo, hi), (11, 90))
        self.assertEqual(lastfm.percentile_pair([5], 0.1, 0.9), (5, 5))


class Forgotten(unittest.TestCase):
    TODAY = dt.date(2026, 10, 6)

    def months(self):
        return {
            "2015-03": {"Old Favourite": 40, "Still Playing": 30, "Light": 10},
            "2016-07": {"Old Favourite": 25, "Still Playing": 30},
            "2024-12": {"Old Favourite": 1},
            "2026-02": {"Still Playing": 5},
        }

    def test_pool(self):
        pool = lastfm.forgotten_pool(self.months(), 50, 12, self.TODAY)
        self.assertEqual([r["artist"] for r in pool], ["Old Favourite"])
        r = pool[0]
        self.assertEqual(r["total_plays"], 66)
        self.assertEqual(r["last_played"], "2024-12")
        self.assertEqual(r["peak_year"], "2015")

    def test_threshold_and_window(self):
        self.assertEqual(lastfm.forgotten_pool(self.months(), 67, 12, self.TODAY), [])
        # a 24-month silence window also excludes the 2024-12 play
        self.assertEqual(lastfm.forgotten_pool(self.months(), 50, 24, self.TODAY), [])

    def test_case_insensitive_merge(self):
        m = {"2015-01": {"AIR": 30}, "2015-02": {"Air": 30}}
        pool = lastfm.forgotten_pool(m, 50, 12, self.TODAY)
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["total_plays"], 60)


# ---------------------------------------------------------------------------- network layer

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok(obj):
    return _Resp(json.dumps(obj).encode())


def http_error(code, body=b"", headers=None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://ws.audioscrobbler.com/2.0/", code, "err", msg, io.BytesIO(body))


class Api(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self._p = mock.patch.object(lastfm, "_sleep", self.sleeps.append)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def run_api(self, *responses):
        seq = list(responses)

        def fake(*a, **k):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch.object(lastfm.urllib.request, "urlopen", side_effect=fake) as m:
            try:
                return lastfm.api("user.getinfo", "k" * 32, user="x"), m.call_count
            except Exception as exc:
                return exc, m.call_count

    def test_success(self):
        res, n = self.run_api(ok({"user": {"name": "x"}}))
        self.assertEqual(res, {"user": {"name": "x"}})
        self.assertEqual((n, self.sleeps), (1, []))

    def test_transient_code_retried_with_jitter(self):
        res, n = self.run_api(ok({"error": 29, "message": "rate"}), ok({"user": {}}))
        self.assertEqual(res, {"user": {}})
        self.assertEqual(n, 2)
        self.assertTrue(2 * 0.8 <= self.sleeps[0] <= 2 * 1.25)

    def test_fatal_code_raised_at_once(self):
        res, n = self.run_api(ok({"error": 10, "message": "bad key"}))
        self.assertIsInstance(res, lastfm.LastfmError)
        self.assertEqual((res.code, n, self.sleeps), (10, 1, []))

    def test_fatal_code_in_http_403_body(self):
        res, n = self.run_api(http_error(403, b'{"error": 10, "message": "Invalid API key"}'))
        self.assertEqual((res.code, n), (10, 1))

    def test_other_non_transient_code_raised(self):
        res, n = self.run_api(ok({"error": 3, "message": "invalid method"}))
        self.assertEqual((res.code, n, self.sleeps), (3, 1, []))

    def test_plain_4xx_raised_at_once(self):
        res, n = self.run_api(http_error(403, b"<html>forbidden</html>"))
        self.assertIsInstance(res, urllib.error.HTTPError)
        self.assertEqual((n, self.sleeps), (1, []))

    def test_429_honours_retry_after(self):
        res, n = self.run_api(http_error(429, b"", {"Retry-After": "7"}), ok({"user": {}}))
        self.assertEqual((res, n, self.sleeps), ({"user": {}}, 2, [7.0]))

    def test_429_retry_after_capped(self):
        self.run_api(http_error(429, b"", {"Retry-After": "3600"}), ok({}))
        self.assertEqual(self.sleeps, [lastfm.RETRY_AFTER_CAP])

    def test_429_without_header_uses_backoff(self):
        self.run_api(http_error(429), ok({}))
        self.assertTrue(2 * 0.8 <= self.sleeps[0] <= 2 * 1.25)

    def test_5xx_html_retried(self):
        res, n = self.run_api(http_error(503, b"<html>cloudflare</html>"), http_error(502), ok({"ok": 1}))
        self.assertEqual((res, n), ({"ok": 1}, 3))

    def test_gives_up_after_all_retries(self):
        res, n = self.run_api(*[ok({"error": 29, "message": "rate"}) for _ in range(4)])
        self.assertIsInstance(res, lastfm.LastfmError)
        self.assertEqual((n, len(self.sleeps)), (4, 3))

    def test_network_error_retried(self):
        res, n = self.run_api(urllib.error.URLError("down"), ok({"fine": True}))
        self.assertEqual((res, n), ({"fine": True}, 2))


class CheckOne(unittest.TestCase):
    def test_new_when_both_zero_and_track_missing(self):
        def fake(method, key, **p):
            if method == "artist.getinfo":
                return {"artist": {"name": "Zofka", "stats": {"userplaycount": "0"}}}
            raise lastfm.LastfmError(6, "Track not found")
        with mock.patch.object(lastfm, "api", side_effect=fake):
            r = lastfm.check_one({"artist": "zofka", "track": "Parfum Vanille"}, "k", "u")
        self.assertIs(r["new"], True)
        self.assertEqual(r["canonical_artist"], "Zofka")

    def test_known_artist_not_new(self):
        def fake(method, key, **p):
            if method == "artist.getinfo":
                return {"artist": {"name": "Air", "stats": {"userplaycount": "972"}}}
            return {"track": {"name": "La Femme d'Argent", "userplaycount": "0"}}
        with mock.patch.object(lastfm, "api", side_effect=fake):
            self.assertIs(lastfm.check_one({"artist": "Air", "track": "x"}, "k", "u")["new"], False)

    def test_failure_leaves_new_unknown(self):
        with mock.patch.object(lastfm, "api", side_effect=TimeoutError("slow")):
            r = lastfm.check_one({"artist": "A", "track": "B"}, "k", "u")
        self.assertIsNone(r["new"])
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
