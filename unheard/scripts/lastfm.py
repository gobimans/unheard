#!/usr/bin/env python3
"""Last.fm helpers for the unheard skill.

Deterministic, repetitive work lives here so it is not rewritten every session.
No user data is stored in this file. Credentials come from the environment:

    LASTFM_API_KEY   read-only Last.fm API key
    LASTFM_USER      Last.fm username

Every command takes --store DIR (default: current directory), the working copy of the
persistent store. Output is JSON on stdout, diagnostics on stderr.

    monthly    build or refresh monthly.json (month -> {artist: plays}) and monthly.meta.json
    profile    build or refresh profile.json and tags_cache.json from monthly.json
    discover   Discovery mode: new artists. Path B by default; path A with --tags, --artist or --era
    loved      Loved mode: playlist candidates from artists the user already loves
    forgotten  Forgotten mode: heavy past plays, none in the silence window
    verify     read a JSON list of {"artist","track"} on stdin and report userplaycount for both

Exit codes: 0 ok, 1 usage or credential problem, 2 some requests failed after retries.
This script never touches Spotify.
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

BASE = "https://ws.audioscrobbler.com/2.0/"
THREADS = 4
RETRY_WAITS = (2, 5, 15)           # seconds; three retries
TRANSIENT = {8, 11, 16, 29}        # operation failed, offline, temporary, rate limit
FATAL = {4, 6, 10, 17, 26}         # auth and parameter errors: never retry

# Generic algorithm defaults. They are copied into profile.json, which is where they are edited
# (put overrides under "overrides" in profile.json). Derived values come from the user's own data.
DEFAULTS = {
    "cluster_window_months": 6, "min_seeds": 8, "second_level_cap": 100, "second_level_decay": 0.5,
    "info_cap": 200, "max_per_seed": 3, "forgotten_min_plays": 50, "forgotten_silence_months": 12,
    "new_window_days": 90, "hit_threshold": 3, "hit_bonus": 1.5, "min_data_threshold": 100,
    "seed_mass": 0.5, "seed_cap": 200, "cluster_mass": 0.6, "cluster_max_tags": 12,
    "band_sample": 300, "band_lo_pct": 0.10, "band_hi_pct": 0.90, "loved_pct": 0.75,
    "reserve": 12, "taste_map_artists_per_year": 25,
}


class LastfmError(Exception):
    def __init__(self, code, message):
        super().__init__(f"Last.fm error {code}: {message}")
        self.code = code


# --------------------------------------------------------------------------- plumbing

def creds(store="."):
    """Environment first, then credentials.json in the store (written by the setup command)."""
    key, user = os.environ.get("LASTFM_API_KEY"), os.environ.get("LASTFM_USER")
    if not key or not user:
        saved = load_json(os.path.join(store, "credentials.json"), {})
        key, user = key or saved.get("api_key"), user or saved.get("user")
    if not key or not user:
        sys.exit("No Last.fm credentials. Run the setup command first, or set LASTFM_API_KEY and LASTFM_USER.")
    return key, user


def api(method, key, **params):
    """One Last.fm call with retries on transient errors only."""
    query = urllib.parse.urlencode({"method": method, "api_key": key, "format": "json", **params})
    last = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            with urllib.request.urlopen(f"{BASE}?{query}", timeout=30) as resp:
                data = json.load(resp)
            if "error" in data:
                err = LastfmError(data["error"], data.get("message", ""))
                if err.code in FATAL:
                    raise err
                last = err
            else:
                return data
        except LastfmError:
            raise
        except urllib.error.HTTPError as exc:          # Last.fm sends API errors with HTTP 4xx and a JSON body
            try:
                body = json.loads(exc.read().decode("utf-8"))
                err = LastfmError(body["error"], body.get("message", ""))
            except Exception:
                err = None
            if err is not None and err.code in FATAL:
                raise err
            last = err or exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        if attempt < len(RETRY_WAITS):
            time.sleep(RETRY_WAITS[attempt])
    raise last


class Ctx:
    """Credentials, store path and a failure counter shared by all calls of one run."""

    def __init__(self, store):
        self.key, self.user = creds(store)
        self.store = store
        os.makedirs(store, exist_ok=True)
        self.failed = 0

    def call(self, method, **params):
        try:
            return api(method, self.key, **params)
        except Exception as exc:                       # counted and reported, the run continues
            self.failed += 1
            print(f"warn: {method} {params.get('artist') or params.get('tag') or ''}: {exc}", file=sys.stderr)
            return None

    def path(self, name):
        return os.path.join(self.store, name)


def pmap(fn, items):
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        return list(pool.map(fn, items))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def norm(name):
    """Normalized artist name: lowercase, no diacritics, no feat./ft./& tail, no leading 'the '."""
    n = unicodedata.normalize("NFKD", name.casefold())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"\s*(feat\.|ft\.|&).*$", "", n).strip()
    return re.sub(r"^the\s+", "", n)


def month_label(ts):
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def label_idx(label):
    return int(label[:4]) * 12 + int(label[5:7]) - 1


def now_idx():
    d = dt.datetime.fromtimestamp(time.time(), dt.timezone.utc)
    return d.year * 12 + d.month - 1


def next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_ranges(start_ts, end_ts):
    """Yield (label, from_ts, to_ts) for every UTC calendar month from start_ts to end_ts."""
    d = dt.datetime.fromtimestamp(start_ts, dt.timezone.utc)
    y, m = d.year, d.month
    end_label = month_label(end_ts)
    while f"{y:04d}-{m:02d}" <= end_label:
        ny, nm = next_month(y, m)
        f = int(dt.datetime(y, m, 1, tzinfo=dt.timezone.utc).timestamp())
        t = int(dt.datetime(ny, nm, 1, tzinfo=dt.timezone.utc).timestamp()) - 1
        yield f"{y:04d}-{m:02d}", f, t
        y, m = ny, nm


def listify(x):
    """Last.fm returns a bare object where a one-element list is expected."""
    return [x] if isinstance(x, dict) else (x or [])


def percentile_pair(values, lo_pct, hi_pct):
    v = sorted(values)
    return v[int(lo_pct * len(v))], v[max(0, int(hi_pct * len(v)) - 1)]


# --------------------------------------------------------------------------- monthly

def parse_chart(data):
    artists = listify(data.get("weeklyartistchart", {}).get("artist", []))
    return {a["name"]: int(a["playcount"]) for a in artists}


def cmd_monthly(args):
    ctx = Ctx(args.store)
    path, meta_path = ctx.path("monthly.json"), ctx.path("monthly.meta.json")
    part_path = path + ".partial"
    months, meta, partial = load_json(path, {}), load_json(meta_path, {}), load_json(part_path, {})

    registered = int(api("user.getinfo", ctx.key, user=ctx.user)["user"]["registered"]["unixtime"])
    now = int(time.time())
    last_done = meta.get("last_refresh", "")
    todo = [(label, f, t) for label, f, t in month_ranges(registered, now)
            if not (label in months or label in partial) or (last_done and label >= last_done)]
    print(f"{len(todo)} month request(s) to run", file=sys.stderr)

    def work(item):
        label, f, t = item
        try:
            chart = parse_chart(api("user.getweeklyartistchart", ctx.key, user=ctx.user, **{"from": f, "to": t}))
            return label, chart, None
        except Exception as exc:                       # reported, not raised, so other months continue
            return label, None, str(exc)

    failed = {}
    for label, chart, err in pmap(work, todo):
        if err:
            failed[label] = err
        else:
            partial[label] = chart                     # an empty chart is a valid, genuinely empty month
    if failed:
        save_json(part_path, partial)
        print(json.dumps({"ok": False, "failed_months": failed, "cache_intact": os.path.exists(path)},
                         ensure_ascii=False))
        return 2
    months.update(partial)
    save_json(path, dict(sorted(months.items())))
    save_json(meta_path, {"registered": registered, "last_refresh": month_label(now), "user": ctx.user})
    if os.path.exists(part_path):
        os.remove(part_path)
    print(json.dumps({"ok": True, "months": len(months), "artists": len({a for c in months.values() for a in c}),
                      "fetched": len(todo), "last_refresh": month_label(now)}))
    return 0


# --------------------------------------------------------------------------- tags

class Tags:
    """artist -> top 8 tags with counts, cached in tags_cache.json."""

    def __init__(self, ctx):
        self.ctx, self.path = ctx, ctx.path("tags_cache.json")
        self.d = load_json(self.path, {})

    def fetch(self, names):
        need = [n for n in dict.fromkeys(names) if n not in self.d]

        def one(n):
            data = self.ctx.call("artist.gettoptags", artist=n, autocorrect=1)
            if data is None:
                return n, None
            return n, [(t["name"].lower(), int(t["count"])) for t in listify(data.get("toptags", {}).get("tag", []))[:8]]

        for n, tags in pmap(one, need):
            if tags is not None:
                self.d[n] = tags
        if need:
            save_json(self.path, self.d)

    def names(self, a):
        return {t for t, _ in self.d.get(a) or []}

    def dominant(self, a):
        t = self.d.get(a) or []
        return t[0][0] if t else None

    def weights(self, a):
        t = self.d.get(a) or []
        s = sum(c for _, c in t) or 1
        return {n: c / s for n, c in t}


# --------------------------------------------------------------------------- profile

def play_tables(months, window_months):
    ni = now_idx()
    allt, last12, window = defaultdict(float), defaultdict(float), defaultdict(float)
    for label, chart in months.items():
        i = label_idx(label)
        for a, n in chart.items():
            allt[a] += n
            if i > ni - 12:
                last12[a] += n
            if i > ni - window_months:
                window[a] += n
    return allt, last12, window


def cosine(u, v):
    keys = set(u) | set(v)
    dot = sum(u.get(k, 0) * v.get(k, 0) for k in keys)
    nu, nv = math.sqrt(sum(x * x for x in u.values())), math.sqrt(sum(x * x for x in v.values()))
    return dot / (nu * nv) if nu and nv else 0.0


def params_from(profile):
    p = dict(DEFAULTS)
    p.update(profile.get("overrides", {}))
    return p


def cluster_from(tags, window, alltime_tag_unused, p):
    """Smallest set of top tags covering cluster_mass of the play-weighted tag mass in the window."""
    top = sorted(window, key=window.get, reverse=True)[:150]
    mass = defaultdict(float)
    for a in top:
        for t, w in tags.weights(a).items():
            mass[t] += w * window[a]
    total, out, cum = sum(mass.values()), [], 0.0
    for t in sorted(mass, key=mass.get, reverse=True):
        out.append(t)
        cum += mass[t]
        if cum >= p["cluster_mass"] * total or len(out) >= p["cluster_max_tags"]:
            break
    return out


def build_profile(ctx):
    months = load_json(ctx.path("monthly.json"), None)
    if months is None:
        sys.exit("monthly.json not found; run the monthly command first.")
    old = load_json(ctx.path("profile.json"), {})
    p = params_from(old)
    allt, last12, window = play_tables(months, p["cluster_window_months"])
    profile = {k: old[k] for k in ("response_language", "playlist_tag", "features", "overrides", "timezone",
                                    "storage", "timeline_artifact") if k in old}
    profile.setdefault("playlist_tag", "LFM")
    profile.setdefault("features", {"time_patterns": {"enabled": False}})
    profile.update({"user": ctx.user, "built_at": dt.datetime.fromtimestamp(time.time(), dt.timezone.utc).isoformat(),
                    "known_count": len(allt), "params": p})
    if len(allt) < p["min_data_threshold"]:
        profile["low_data"] = True
        save_json(ctx.path("profile.json"), profile)
        return profile
    profile["low_data"] = False

    arts = list(allt)
    drift = 1 - cosine(allt, last12)
    rw = 0.5 + 0.5 * drift
    tot_all, tot_rec = sum(allt.values()), sum(last12.values()) or 1
    blend = {a: (1 - rw) * allt[a] / tot_all + rw * last12.get(a, 0) / tot_rec for a in arts}
    ranked = sorted(blend, key=blend.get, reverse=True)
    seeds, cum, tot = [], 0.0, sum(blend.values())
    for a in ranked:
        seeds.append(a)
        cum += blend[a]
        if cum >= p["seed_mass"] * tot or len(seeds) >= p["seed_cap"]:
            break

    tags = Tags(ctx)
    per_year = defaultdict(lambda: defaultdict(float))
    for label, chart in months.items():
        for a, n in chart.items():
            per_year[label[:4]][a] += n
    year_top = {y: sorted(c, key=c.get, reverse=True)[:p["taste_map_artists_per_year"]] for y, c in per_year.items()}
    top_window = sorted(window, key=window.get, reverse=True)[:150]
    tags.fetch(list(seeds) + top_window + [a for v in year_top.values() for a in v])

    alltime_tag = defaultdict(float)
    for a in seeds:
        for t, w in tags.weights(a).items():
            alltime_tag[t] += w * allt[a]
    taste_map = {}
    for y, names in sorted(year_top.items()):
        m = defaultdict(float)
        for a in names:
            for t, w in tags.weights(a).items():
                m[t] += w * per_year[y][a]
        taste_map[y] = sorted(m, key=m.get, reverse=True)[:8]

    eligible = sorted((a for a in allt if allt[a] >= 3), key=allt.get, reverse=True)
    step = max(1, len(eligible) // p["band_sample"])
    sample = eligible[::step][:p["band_sample"]]

    def listeners(a):
        d = ctx.call("artist.getinfo", artist=a, autocorrect=1)
        try:
            return int(d["artist"]["stats"]["listeners"])
        except Exception:
            return None

    vals = [v for v in pmap(listeners, sample) if v]
    plays = sorted(allt[a] for a in eligible)
    profile.update({
        "drift": round(drift, 4), "recent_weight": round(rw, 4), "seeds": seeds,
        "cluster_tags": cluster_from(tags, window, None, p),
        "alltime_tags": dict(sorted(alltime_tag.items(), key=lambda kv: -kv[1])[:300]),
        "taste_map": taste_map,
        "band": list(percentile_pair(vals, p["band_lo_pct"], p["band_hi_pct"])) if vals else None,
        "loved_threshold": plays[int(p["loved_pct"] * len(plays))] if plays else None,
        "support_min": 2,
    })
    save_json(ctx.path("profile.json"), profile)
    return profile


def ensure_profile(ctx):
    prof = load_json(ctx.path("profile.json"), None)
    return prof if prof else build_profile(ctx)


def cmd_profile(args):
    ctx = Ctx(args.store)
    prof = build_profile(ctx)
    brief = {k: prof.get(k) for k in ("known_count", "low_data", "drift", "recent_weight", "cluster_tags",
                                      "band", "loved_threshold")}
    brief["seeds"] = len(prof.get("seeds", []))
    brief["failed_calls"] = ctx.failed                # a few failed tag lookups are tolerated, and reported
    print(json.dumps(brief, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- shared helpers

def load_state(ctx):
    months = load_json(ctx.path("monthly.json"), None)
    if months is None:
        sys.exit("monthly.json not found; run the monthly command first.")
    prof = ensure_profile(ctx)
    p = params_from(prof)
    allt, last12, window = play_tables(months, p["cluster_window_months"])
    return months, prof, p, allt, last12, window


def self_reported(ctx):
    """Optional lists the skill writes next to the cache: liked_outside.json, rejected.json, playlist_log.json."""
    out = {}
    for name in ("liked_outside", "rejected", "playlist_log"):
        out[name] = {norm(a) for a in load_json(ctx.path(name + ".json"), [])}
    return out


def pick_track(ctx, artist):
    d = ctx.call("artist.gettoptracks", artist=artist, limit=8, autocorrect=1)
    tracks = listify(d.get("toptracks", {}).get("track", [])) if d else []
    for t in tracks:
        if not re.search(r"remix|live|intro|edit|version", t["name"], re.I):
            return t["name"]
    return tracks[0]["name"] if tracks else None


def user_tracks(ctx):
    d = ctx.call("user.gettoptracks", user=ctx.user, period="overall", limit=1000)
    mine = defaultdict(list)
    for t in listify(d.get("toptracks", {}).get("track", [])) if d else []:
        mine[norm(t["artist"]["name"])].append((t["name"], int(t["playcount"])))
    return mine


def listeners_of(ctx, names):
    def one(n):
        d = ctx.call("artist.getinfo", artist=n, autocorrect=1)
        try:
            return n, int(d["artist"]["stats"]["listeners"])
        except Exception:
            return n, None
    return {n: v for n, v in pmap(one, names) if v}


# --------------------------------------------------------------------------- discover

def seeds_for_tags(ctx, tag_list, known_weight, p):
    """Path A (tag words): user-known artists that carry the tags, widened by related tags if too few."""
    norm_to_name = {norm(a): a for a in known_weight}

    def tag_artists(tag):
        d = ctx.call("tag.gettopartists", tag=tag, limit=1000)
        return {norm(a["name"]) for a in listify(d.get("topartists", {}).get("artist", []))} if d else set()

    hit = set()
    for t in tag_list:
        hit |= tag_artists(t)
    seeds = [norm_to_name[n] for n in hit if n in norm_to_name]
    if len(seeds) < p["min_seeds"]:
        for t in tag_list:
            d = ctx.call("tag.getsimilar", tag=t)
            for r in listify(d.get("similartags", {}).get("tag", []))[:5] if d else []:
                seeds += [norm_to_name[n] for n in tag_artists(r["name"]) if n in norm_to_name]
    return list(dict.fromkeys(seeds))


def cmd_discover(args):
    ctx = Ctx(args.store)
    months, prof, p, allt, last12, window = load_state(ctx)
    low = prof.get("low_data", False)
    tags = Tags(ctx)
    known = {norm(a) for a in allt} | set().union(*self_reported(ctx).values())
    rej = self_reported(ctx)["rejected"]
    needed = args.needed
    meta = {"mode": "discovery", "low_confidence": low}

    if low:                                           # minimum-data gate: whole list, no weights, no tiers
        weights = {a: 1.0 for a in allt}
        seeds, path, fit, band = list(allt), "low-data", None, None
        meta["path"] = "low-data"
    elif args.artist:
        meta["path"] = "A-artist"
        near = ctx.call("artist.getsimilar", artist=args.artist, limit=100, autocorrect=1)
        sims = [(s["name"], float(s["match"])) for s in listify(near.get("similarartists", {}).get("artist", []))] if near else []
        seeds = [args.artist] + [n for n, _ in sims if norm(n) in {norm(a) for a in allt}][:10]
        weights = {a: allt.get(a, 1.0) for a in seeds}
        fit, band = None, tuple(prof["band"]) if prof.get("band") else None
    elif args.tags:
        words = [t.strip().lower() for t in args.tags.split(",") if t.strip()]
        meta["path"], meta["tags"] = "A-tags", words
        weights = {a: allt[a] for a in allt}
        seeds = seeds_for_tags(ctx, words, weights, p)
        weights = {a: allt[a] for a in seeds}
        fit, band = (lambda a: bool(tags.names(a) & set(words))), tuple(prof["band"]) if prof.get("band") else None
    elif args.era:
        y0, y1 = (int(x) for x in args.era.split("-"))
        meta["path"], meta["era"] = "A-era", args.era
        era = defaultdict(float)
        for label, chart in months.items():
            if y0 <= int(label[:4]) <= y1:
                for a, n in chart.items():
                    era[a] += n
        seeds = sorted(era, key=era.get, reverse=True)[:40]
        weights = {a: era[a] for a in seeds}
        fit, band = None, tuple(prof["band"]) if prof.get("band") else None
    else:
        meta["path"], meta["cluster_tags"] = "B", prof["cluster_tags"]
        cluster = set(prof["cluster_tags"])
        alltime = prof["alltime_tags"]
        rw = prof["recent_weight"]
        tot_all, tot_rec = sum(allt.values()), sum(last12.values()) or 1
        blend = {a: (1 - rw) * allt[a] / tot_all + rw * last12.get(a, 0) / tot_rec for a in prof["seeds"]}
        tags.fetch(prof["seeds"])
        seeds = [a for a in prof["seeds"] if tags.names(a) & cluster and tags.dominant(a) in alltime]
        weights = {a: blend[a] for a in seeds}
        fit = lambda a: bool(tags.names(a) & cluster) and tags.dominant(a) in alltime
        band = tuple(prof["band"]) if prof.get("band") else None
    meta["seeds"] = len(seeds)

    def sim(a):
        d = ctx.call("artist.getsimilar", artist=a, limit=100, autocorrect=1)
        return a, [(s["name"], float(s["match"])) for s in listify(d.get("similarartists", {}).get("artist", []))] if d else []

    score, support, bysrc, level1 = defaultdict(float), defaultdict(set), defaultdict(lambda: defaultdict(float)), defaultdict(float)
    for a, sl in pmap(sim, seeds):
        for n, m in sl:
            level1[n] += weights[a] * m
            if norm(n) in known or norm(n) in rej:
                continue
            score[n] += weights[a] * m
            support[n].add(a)
            bysrc[n][a] += weights[a] * m
    smin = 1 if low else 2
    while not low and smin < 4 and sum(1 for n in score if len(support[n]) >= smin) > 10 * needed:
        smin += 1
    pool = [n for n in score if len(support[n]) >= smin]
    second = False
    if len(pool) < needed:                            # first-level similarity exhausted by the user-known set
        second = True
        parents = sorted((n for n in level1 if norm(n) in known), key=level1.get, reverse=True)[:p["second_level_cap"]]
        for parent, sl in pmap(sim, parents):
            for n, m in sl:
                if norm(n) in known or norm(n) in rej:
                    continue
                score[n] += level1[parent] * m * p["second_level_decay"]
                support[n].add(parent)
                bysrc[n][parent] += level1[parent] * m * p["second_level_decay"]
        pool = [n for n in score if len(support[n]) >= smin]
    meta.update({"support_min": smin, "second_level": second, "pool": len(pool)})
    pool = sorted(pool, key=lambda n: score[n] * math.log(len(support[n]) + 1), reverse=True)[:p["info_cap"]]

    lis = listeners_of(ctx, pool)
    inband = [n for n in pool if n in lis and (band is None or band[0] <= lis[n] <= band[1])]
    meta["band"], meta["in_band"] = list(band) if band else None, len(inband)
    tags.fetch(inband)                                # tags also drive the diversity spread below
    if fit is not None:
        inband = [n for n in inband if fit(n)]
    meta["after_fit"] = len(inband)

    chosen, skipped, per_seed, per_tag = [], [], defaultdict(int), defaultdict(int)
    for n in inband:
        main = max(bysrc[n], key=bysrc[n].get) if bysrc.get(n) else None
        dom = tags.dominant(n) if n in tags.d else None
        if (main and per_seed[main] >= p["max_per_seed"]) or (dom and per_tag[dom] >= max(3, needed // 4)):
            skipped.append(n)                         # diversity caps: at most N per seed and per dominant tag
            continue
        chosen.append(n)
        per_seed[main] += 1
        per_tag[dom] += 1
        if len(chosen) >= needed + p["reserve"]:
            break
    meta["diversity_relaxed"] = False
    if len(chosen) < needed + p["reserve"] and skipped:   # caps left the list short: fill in rank order
        chosen += skipped[:needed + p["reserve"] - len(chosen)]
        meta["diversity_relaxed"] = True

    picked = dict(zip(chosen, pmap(lambda n: pick_track(ctx, n), chosen)))
    ver = pmap(lambda n: check_one({"artist": n, "track": picked[n]}, ctx.key, ctx.user),
               [n for n in chosen if picked[n]])
    good = [v for v in ver if v["new"] is True][:needed]
    rows = []
    for v in good:
        n = v["artist"]
        src = sorted(bysrc[n], key=bysrc[n].get, reverse=True)[:2] if bysrc.get(n) else []
        rows.append({"artist": v.get("canonical_artist", n), "track": v.get("canonical_track", v["track"]),
                     "similar_to": src, "global_listeners": lis.get(n)})
    meta.update({"verified_new": len(rows), "dropped_by_verification": len(ver) - len([v for v in ver if v["new"] is True]),
                 "failed_calls": ctx.failed})
    print(json.dumps({"meta": meta, "tracks": rows}, ensure_ascii=False, indent=1))
    return 2 if ctx.failed and len(rows) < needed else 0


# --------------------------------------------------------------------------- loved

def cmd_loved(args):
    ctx = Ctx(args.store)
    months, prof, p, allt, last12, window = load_state(ctx)
    tags = Tags(ctx)
    rej = self_reported(ctx)["rejected"]
    liked = [a for a in load_json(ctx.path("liked_outside.json"), [])]
    thr = prof.get("loved_threshold") or 0
    pool = [a for a in allt if allt[a] >= thr and norm(a) not in rej]
    pool += [a for a in liked if a not in pool]
    rw = prof.get("recent_weight", 0.5)
    tot_all, tot_rec = sum(allt.values()), sum(last12.values()) or 1
    blend = {a: (1 - rw) * allt.get(a, 0) / tot_all + rw * last12.get(a, 0) / tot_rec for a in pool}
    pool.sort(key=blend.get, reverse=True)
    tags.fetch(pool[:600])
    meta = {"mode": "loved", "pool": len(pool), "loved_threshold": thr}
    sel = []
    if args.tags:
        words = {t.strip().lower() for t in args.tags.split(",") if t.strip()}
        meta["tags"] = sorted(words)
        sel = [a for a in pool if tags.names(a) & words]
        if len(sel) < p["min_seeds"]:
            extra = set()
            for w in words:
                d = ctx.call("tag.getsimilar", tag=w)
                extra |= {r["name"].lower() for r in listify(d.get("similartags", {}).get("tag", []))[:6]} if d else set()
            meta["related_tags"] = sorted(extra)
            sel = list(dict.fromkeys(sel + [a for a in pool if tags.names(a) & extra]))
        if not sel:                                   # nothing matched: fall back to the user's own cluster
            meta["fallback"] = "own recent-listening cluster"
    if not sel:
        cluster = set(prof.get("cluster_tags", []))
        meta["cluster_tags"] = sorted(cluster)
        sel = [a for a in pool if not cluster or tags.names(a) & cluster] or pool
    sel = sel[:args.needed]
    mine = user_tracks(ctx)
    rows = []
    for a in sel:
        tr = mine.get(norm(a))
        track = tr[0][0] if tr else pick_track(ctx, a)
        rows.append({"artist": a, "track": track, "track_plays": tr[0][1] if tr else None,
                     "artist_plays": int(allt.get(a, 0))})
    meta["failed_calls"] = ctx.failed
    print(json.dumps({"meta": meta, "tracks": rows}, ensure_ascii=False, indent=1))
    return 0


# --------------------------------------------------------------------------- verify

def check_one(item, key, user):
    out = {"artist": item["artist"], "track": item["track"], "new": None}
    try:
        a = api("artist.getinfo", key, artist=item["artist"], username=user, autocorrect=1)["artist"]
        out["canonical_artist"] = a["name"]
        out["artist_playcount"] = int(a.get("stats", {}).get("userplaycount", 0))
        try:
            t = api("track.getinfo", key, artist=a["name"], track=item["track"],
                    username=user, autocorrect=1)["track"]
            out["canonical_track"] = t["name"]
            out["track_playcount"] = int(t.get("userplaycount", 0))
        except LastfmError as exc:
            if exc.code != 6:                         # 6 = track not found: no scrobbles under that name
                raise
            out["track_playcount"] = 0
            out["note"] = "track not found on Last.fm"
        out["new"] = out["artist_playcount"] == 0 and out["track_playcount"] == 0
    except Exception as exc:
        out["error"] = str(exc)                       # new stays null: the skill drops this finalist
    return out


def cmd_verify(args):
    key, user = creds(args.store)
    items = json.load(sys.stdin)
    results = pmap(lambda i: check_one(i, key, user), items)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 2 if any(r["new"] is None for r in results) else 0


# --------------------------------------------------------------------------- forgotten

def forgotten_pool(months, min_plays, silence_months, today):
    """Pure function: artists with >= min_plays before the window and none inside it."""
    first_window = (today.year * 12 + today.month - 1) - (silence_months - 1)   # last N months incl. current
    totals, last_seen, per_year, names = {}, {}, {}, {}
    in_window = set()
    for label, chart in months.items():
        idx = label_idx(label)
        for name, plays in chart.items():
            k = name.casefold()
            names.setdefault(k, name)
            if idx >= first_window:
                in_window.add(k)
                continue
            totals[k] = totals.get(k, 0) + plays
            last_seen[k] = max(last_seen.get(k, ""), label)
            per_year.setdefault(k, {})
            per_year[k][label[:4]] = per_year[k].get(label[:4], 0) + plays
    pool = [{"artist": names[k], "total_plays": n, "last_played": last_seen[k],
             "peak_year": max(per_year[k], key=per_year[k].get)}
            for k, n in totals.items() if n >= min_plays and k not in in_window]
    return sorted(pool, key=lambda r: -r["total_plays"])


def cmd_forgotten(args):
    months = load_json(os.path.join(args.store, "monthly.json"), None)
    if months is None:
        sys.exit("monthly.json not found; run the monthly command first.")
    today = dt.datetime.fromtimestamp(time.time(), dt.timezone.utc).date()
    print(json.dumps(forgotten_pool(months, args.min_plays, args.silence_months, today),
                     ensure_ascii=False, indent=1))
    return 0



# --------------------------------------------------------------------------- setup

def cmd_setup(args):
    """Check a username and API key against Last.fm and save them to the store.

    The key is read from stdin so it never appears in a command line or a shell history."""
    key = sys.stdin.readline().strip()
    user = args.user.strip()
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        print(json.dumps({"ok": False, "problem": "key_format",
                          "hint": "An API key is 32 characters, letters a-f and digits only. The shared secret is a different value."}))
        return 1
    try:
        info = api("user.getinfo", key, user=user)["user"]
    except LastfmError as exc:
        problem = {10: "bad_key", 26: "bad_key", 6: "no_user", 17: "private_user"}.get(exc.code, "lastfm_error")
        print(json.dumps({"ok": False, "problem": problem, "detail": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "problem": "unreachable", "detail": str(exc)}))
        return 1
    reg = dt.datetime.fromtimestamp(int(info["registered"]["unixtime"]), dt.timezone.utc).date()
    today = dt.date.today()
    months = (today.year - reg.year) * 12 + today.month - reg.month + 1
    os.makedirs(args.store, exist_ok=True)
    path = os.path.join(args.store, "credentials.json")
    with open(path, "w") as fh:
        json.dump({"user": info.get("name", user), "api_key": key}, fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(json.dumps({"ok": True, "user": info.get("name", user), "registered": reg.isoformat(),
                      "months": months, "scrobbles": int(info.get("playcount", 0)),
                      "artists": int(info.get("artist_count", 0) or 0)}))
    return 0

# --------------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("setup", help="check and save the Last.fm username and API key (key on stdin)")
    sp.add_argument("--store", default=".", help="working copy of the persistent store")
    sp.add_argument("--user", required=True, help="Last.fm username")
    sp.set_defaults(fn=cmd_setup)
    for name, fn in (("monthly", cmd_monthly), ("profile", cmd_profile), ("discover", cmd_discover),
                     ("loved", cmd_loved), ("verify", cmd_verify), ("forgotten", cmd_forgotten)):
        sp = sub.add_parser(name)
        sp.add_argument("--store", default=".", help="working copy of the persistent store")
        if name == "forgotten":
            sp.add_argument("--min-plays", type=int, default=DEFAULTS["forgotten_min_plays"])
            sp.add_argument("--silence-months", type=int, default=DEFAULTS["forgotten_silence_months"])
        if name in ("discover", "loved"):
            sp.add_argument("--needed", type=int, default=24, help="artists wanted in the playlist")
            sp.add_argument("--tags", help="comma-separated Last.fm tags (path A tag request, or mood tags for loved)")
        if name == "discover":
            sp.add_argument("--artist", help="path A: a named artist")
            sp.add_argument("--era", help="path A: a period of the user's history, e.g. 2009-2012")
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
