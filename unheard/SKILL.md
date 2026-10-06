---
name: "unheard"
description: "Finds artists a user has never listened to by mining their own Last.fm history, builds Spotify playlists (new-artist discovery, forgotten artists, comfort playlists from artists they already love), and analyzes listening statistics. Make sure to use this skill whenever the user mentions Last.fm, scrobbles, Spotify playlists, music recommendations, something new to listen to, artists they stopped playing, a playlist for a mood, or their listening history, even if they do not name Last.fm or ask for a playlist explicitly."
---

# Unheard

Three playlist modes and one analysis mode:

- **Discovery** (default): artists this user has never listened to.
- **Forgotten**: artists this user once played heavily and then dropped.
- **Loved**: a mood or comfort playlist built only from artists this user already loves.
- **Analysis**: listening statistics and patterns.

Spotify's own algorithm sees only Spotify. This skill sees the user's full Last.fm scrobble history from their registration date onward. That is the advantage, so use it fully.

## Maintenance rules (for anyone editing this file)

1. English only.
2. No data about any specific user in this file: no usernames, keys, artist names, thresholds, weights or taste lists. All of it lives in the per-user profile (see "Profile and persistence").
3. No genre, scene or artist names as examples anywhere in rule text. An example turns into a de facto limit for everything not named.
4. Never write bare "known" or "current" for artists or taste. Use the defined terms below.
5. Replace content that is wrong. Do not append exceptions to it.

## Terms (used exactly this way throughout)

- **User-known artist**: an artist this specific user has heard, shown by at least one scrobble in their own Last.fm history or by their own statement (self-reported lists). It says nothing about how famous the artist is.
- **User-known set**: all user-known artists, normalized (lowercase, leading `the ` removed, diacritics removed, `feat./ft./&` tails dropped).
- **New artist**: normalized name not in the user-known set, and `userplaycount` of 0 for both the artist and the track.
- **Global listeners**: the Last.fm-wide listener count of an artist. It measures general popularity and says nothing about whether this user knows the artist.
- **Own recent-listening cluster**: the tags that dominate what this user actually played in the most recent months, computed from their own scrobbles (see Step 2, path B).
- **Profile**: the per-user file built on first run from the user's own data.
- **Persistent store**: a location that survives across conversations (see "Profile and persistence"). The scratchpad is a working copy only and never the sole copy.

## Limitation: Last.fm sees only scrobbles

Users may listen to and like music in Spotify without scrobbling it. "Zero scrobbles" therefore does not prove "never heard". Step 1b (self-reported lists and per-finalist checks) and Step 5 (likes check) exist to narrow this gap. They cannot close it.

## Access

- Last.fm username and API key: from `LASTFM_API_KEY`/`LASTFM_USER` in the environment, else from `credentials.json` in the persistent store (written by `setup`), else from the user's durable memory. If none has them, this is a first run: follow "First run". Response language and timezone come from the profile. The API key is read-only and gives access to public data. Never repeat the key back to the user or print it in a reply.
- Base URL: `https://ws.audioscrobbler.com/2.0/?method=<m>&api_key=<key>&format=json`, plus `&user=<username>` for `user.*` methods.
- Call it with `curl` or python urllib from Bash. WebFetch returns 404 on this API. Run at most 4 parallel threads. Retry rules are in "Failure handling".
- Repetitive work is bundled in `scripts/lastfm.py` (next to this file): `monthly` builds or refreshes `monthly.json`, `profile` builds `profile.json`, `discover` runs Discovery (Steps 1c to 3 plus the Step 1b checks), `loved` runs Loved mode, `forgotten` computes the Forgotten pool, `verify` runs the Step 1b checks on any list, `setup` checks and saves the username and key. The text of this file is the specification: if a script disagrees with it, fix the script. It reads the credentials from the environment or from `credentials.json` in `--store`, so the key never appears in later command lines, and it already applies the 4-thread limit and the retry rules. Use it instead of rewriting the same loops each session. Run a command with `--help` for its options.
- Spotify connector tools used: `generate_playlist` and `save_to_library`. No other Spotify tool is used. If they are missing, run ToolSearch "spotify". If still missing, tell the user to enable the Spotify connector in this chat and deliver the track table without a playlist.

## Spotify rules

- `generate_playlist` is generative. It takes a natural-language prompt plus a `language` parameter and cannot accept an explicit track list. It may reorder, swap or drop tracks, and it may shorten the title. Tell the user this. Never claim the playlist contains exactly the requested tracks, and never state that a given artist is or is not in it. No tool returns a playlist's contents, so the result cannot be verified from here. The generator personalizes from the user's own Spotify listening, so it can add artists the user already knows, including artists from their Last.fm history. The verified table, not the generated playlist, is the deliverable of Discovery mode.
- A generated playlist is not in the user's library. Right after creation, call `save_to_library` with the returned `spotify:playlist:` URI. Otherwise the user will not see it.
- Spotify `search` is never used: not for single artists, not for bulk checks, not for track resolution, not for liked-song lookups. It cannot return track lists, and bulk queries create stray generative playlists that cannot be deleted from here. `remove_from_library`, `get_currently_playing` and any playback control are also out of scope.
- "Has this user already heard this artist" is answered only by Last.fm `userplaycount` and by the user's own self-reported lists. Spotify likes, followed artists and history cannot be read in bulk. They enter this skill's data only when the user names them (Step 5).
- Failure behavior is defined in "Failure handling". There is no search-based workaround.

## Methods in use

| Task | Method | Params |
|---|---|---|
| Profile and registration date | user.getinfo | none; read `registered.unixtime` and `playcount` |
| Top artists/albums/tracks | user.gettopartists / gettopalbums / gettoptracks | period: overall, 7day, 1month, 3month, 6month, 12month; limit up to 1000 |
| Recent plays | user.getrecenttracks | limit up to 200, page, from/to (unix) |
| Arbitrary period | user.getweeklyartistchart | from/to (unix), any range works |
| Similar | artist.getsimilar / track.getsimilar | artist, track; limit up to 100 |
| Tags | artist.gettoptags, tag.gettopartists, tag.getsimilar, tag.getinfo | artist / tag |
| Global popularity | artist.getinfo | artist (`stats.listeners`) |
| Per-user check | artist.getinfo / track.getinfo | add `username=<user>`, `autocorrect=1`; read `userplaycount` |
| Candidate tracks | artist.gettoptracks | artist |

## First run

A first run is any run with no saved credentials. Assume the user has no technical background: no terminal words, no file names, no jargon in what they read. Write it in the language of their message.

**1. Ask for the two values, with the full instructions, in one message.** This is the one question of the turn. Use this content, in the user's language:

> To read your listening history I need two things from Last.fm.
>
> 1. **Your username.** It is the name at the end of your profile address: last.fm/user/**name**. You can also see it in the top right corner of last.fm when you are logged in.
> 2. **An API key.** It is free and takes a minute:
>    - Log in at last.fm, then open https://www.last.fm/api/account/create
>    - Contact email: your email. Application name: anything, for example Unheard. Application description: anything, for example "personal playlists".
>    - Leave Callback URL and Application homepage empty.
>    - Press Submit. The next page shows **API key** and **Shared secret**. Copy only the API key (32 letters and digits). The shared secret is not needed.
>    - If you already made one before, it is listed at https://www.last.fm/api/accounts
>
> Paste both here. The key can only read public listening data. It cannot post anything or change your account.

**2. Check and save them.** Run `python scripts/lastfm.py setup --store <working dir> --user <username>` with the key passed on stdin. It answers with one JSON line. On a problem, say what to do in plain words and ask again:

- `key_format`: the pasted value is not an API key. Most often the shared secret was copied instead.
- `bad_key`: Last.fm does not recognise the key. Ask them to copy the API key again from https://www.last.fm/api/accounts
- `no_user`: no such username. Ask them to check the name in their profile address.
- `private_user`: their listening history is hidden. On last.fm: Settings, Privacy, turn off the option that hides recent listening, save, then say "done".
- `unreachable`: Last.fm cannot be reached from this environment. Tell the user that Claude's sandbox here does not have internet access to Last.fm, so the skill cannot run in this place. Do not loop retries.

**3. Say what happens next, in one line**, using the numbers `setup` returned: "Found your account: N scrobbles since <month year>. Downloading the history now, about M months. A long history takes a few minutes, and only the first time."

**4. Build everything silently:** `monthly`, then `profile`. Save `credentials.json` with the rest of the persistent store (see "Profile and persistence") so the user is never asked again. If only durable memory is available, store the username and key there.

**5. Check Spotify.** If `generate_playlist` is not available after ToolSearch "spotify", tell the user in one line: playlists need the Spotify connector, which they can turn on in Claude's connector settings by choosing Spotify and logging in. Then continue without it: the result comes as a table.

**6. Do what they asked.** If the first message already had a request, run it now. If they only set the skill up, end with one line of what they can ask, for example: "something new for tonight", "artists I used to play and dropped", "a cozy playlist from what I already love", "my top artists by year".

## Profile and persistence

The profile is built automatically on first run from the user's own data and is the only place user-specific values live. The first-run build happens silently as part of the user's request. It is not an interview. Build it with `python scripts/lastfm.py profile --store <working dir>` after `monthly`; it also fills `tags_cache.json`. A rebuild keeps the user's own settings (response language, playlist tag, feature flags, `overrides`). Parameters the user wants changed go under `overrides` in `profile.json`.

**Where things are kept** (first available option; say once, in one line, where they went):

1. A folder named `unheard` in the user's connected cloud drive.
2. A folder on the user's linked computer.
3. The user's durable memory, for the profile and the small lists only. Caches are too large for it, so with this option the caches stay in scratch and the user is told they will be rebuilt next conversation.

**Files in the persistent store:**

- `profile.json`: username, registration date, response language, timezone, playlist tag, derived parameters, taste map, own recent-listening cluster (with build date), feature flags, storage location, published timeline artifact URL if any.
- `monthly.json`: month -> {artist: plays}, from the registration month onward. `monthly.meta.json` beside it holds the registration date and the last refresh month.
- `tags_cache.json`: artist -> top tags, so tags are never fetched twice.
- `history.csv`: per-scrobble history, only if the time-pattern feature is enabled (see "Opt-in: time-pattern personalization").

**Where the small lists live:** the user's durable memory (self-reported "heard outside Last.fm" artists, explicitly rejected artists, a playlist log of date, name, mode and artists). If memory is unavailable, they go in `profile.json`. Recognize these lists by meaning, not by exact label wording. Before running `discover` or `loved`, write them into the working directory as JSON arrays of artist names: `liked_outside.json`, `rejected.json`, `playlist_log.json`.

**Parameters.** Each parameter is either derived from the user's data or a generic algorithm default. All are written to `profile.json` on first run, and the profile is the only place they are edited.

| Parameter | Source | Value |
|---|---|---|
| `recent_weight` | derived | 0.5 + 0.5 x drift, where drift = 1 - cosine similarity between the user's all-time and last-12-month artist play vectors |
| `seed_count` | derived | top artists by blended weight until they cover 50% of total blended weight, capped at 200 |
| `cluster_window_months` | default | 6 |
| `cluster_tags` | derived | smallest set of top tags covering 60% of the play-weighted tag mass over the window, at most 12 |
| `taste_map` | derived | per calendar year, top tags by play-weighted share (from `monthly.json` and `tags_cache.json`) |
| `band` | derived | 10th to 90th percentile of global listeners across a sample of up to 300 of the user's artists with at least 3 plays, taken evenly down the play-count ranking (sampling only the most played skews the band toward widely listened-to artists) |
| `min_seeds` | default | 8 |
| `support_min` | derived | start at 2; add 1 while the filtered candidate pool exceeds 10x the artists needed; never above 4 |
| `second_level_cap` / `second_level_decay` | default | 100 expansion parents / 0.5 |
| `info_cap` | default | 200 candidates looked up with artist.getinfo |
| `max_per_seed` | default | 3 |
| `loved_threshold` | derived | 75th percentile of per-artist play counts among artists with at least 3 plays |
| `forgotten_min_plays` / `forgotten_silence_months` | default | 50 / 12 |
| `new_window_days` | default | 90 |
| `hit_threshold` / `hit_bonus` | default | 3 plays / 1.5x seed weight |
| `min_data_threshold` | default | 100 user-known artists |
| `response_language` | derived | language of the user's first message; changed on request |
| `playlist_tag` | default | `LFM`, unless the user's playlist log already shows a tag; changed on request |

Rebuild derived parameters when the user asks. Recompute `cluster_tags` at every run that uses path B.

## Request routing

- Default is Discovery. A request for a playlist that does not say whether the artists should be new or already loved runs as Discovery.
- Loved runs only on explicit signals (comfort, familiar, already love, my favorites, a mood from artists the user already likes).
- Forgotten runs only on explicit signals (forgotten, dropped, stopped playing).
- Analysis runs for statistics requests.
- Begin every reply with one line stating the mode that ran, and for Discovery path B the cluster tags that were used. Do not ask which mode the user meant.

## Step 1: user-known set (always first, Discovery and Loved)

- Read `registered.unixtime` from `user.getinfo`. The history range runs from that month to the current month. The request count is the number of calendar months in that range, one `user.getweeklyartistchart` request per month. On a first build, state that count and expect a short wait.
- Some months are genuinely empty. An empty response is not an error: do not retry it. Retry only on errors.
- Copy `monthly.json` and `monthly.meta.json` from the persistent store into the working directory (if they exist) and run `python scripts/lastfm.py monthly --store <working dir>`, then copy the results back to the persistent store before doing anything else. The script builds the file when it is missing, otherwise refetches only the months from the last refresh through the current month, and resumes after an interruption. If any month fails after retries it exits with code 2 and leaves `monthly.json` untouched, which feeds the cache rules in "Failure handling".
- User-known set = all artists in `monthly.json`, plus the self-reported lists, plus every artist in the playlist log (they are not new any more). Apply the normalization defined in Terms.

## Step 1b: verification of finalists (always, before a playlist is built)

- Name variants: the same act can be spelled differently (spelling variants, apostrophe styles, transliteration). String matching on normalized names is not enough. For every finalist run `artist.getinfo` and `track.getinfo` with `username=<user>&autocorrect=1` and require `userplaycount == 0` for both. Use the canonical name Last.fm returns. `scripts/lastfm.py verify` does this for a whole list: send a JSON list of `{"artist", "track"}` on stdin and read back canonical names and both play counts. `new` is null when a check failed, and those finalists are dropped.
- A track can be scrobbled under a compilation curator or label rather than the performing artist. If the curator or label has plays above `loved_threshold` in this user's history, the performing artist may be familiar to them through that release. Prefer another candidate or flag it.
- A finalist whose check cannot be completed is dropped. It is never assumed new.

## Step 1c: minimum-data gate (Discovery)

If the user-known set has fewer than `min_data_threshold` artists, do not run the normal pipeline silently.

1. Say plainly that the history is too thin for the full discovery logic, and that more scrobbling history or seed artists supplied by the user are needed. Invite the seed artists as the one question of the turn.
2. In the same reply, still run a low-confidence similarity search: seeds are the whole user-known list, with no weighting and no tiering, no derived parameters, no own recent-listening cluster. Rank candidates by number of distinct seeds pointing to them, then by summed match. Run Step 1b on the finalists.
3. Label the output "low confidence" and say why.
4. If the user supplies seed artists, run them as a named request (Step 2, path A, artist kind). The low-confidence label stays until the user-known set reaches the threshold.

Forgotten and Loved modes do not use discovery logic. They run on whatever the user-known set contains and state when the pool is thin.

## Step 2: seeds and filter (Discovery)

Two separate paths, chosen by whether the request names anything.

Run `python scripts/lastfm.py discover --store <working dir> --needed <n>` for the whole Discovery pipeline. No flag is path B. `--tags a,b` is path A for a genre, scene, theme or mood (you choose the tags, following the mood rule below). `--artist <name>` and `--era <y0-y1>` are the other path A kinds. It returns JSON: `meta` (path, cluster or tags, second-level use, low-confidence flag, counts) and the verified `tracks`. State the `meta` facts in the first line of the reply.

**Path A: named request.** The user gave a specific artist, genre, era, scene, theme or mood. Use exactly what was given. No cluster filtering is applied to seeds or candidates.

- Artist: seeds are that artist plus its nearest user-known neighbors (`artist.getsimilar` intersected with the user-known set). Candidates need no tag fit.
- Genre, scene, theme or mood word: treat the word as a Last.fm tag. Verify it with `tag.getinfo`. Seeds are user-known artists in `tag.gettopartists` for that tag. If fewer than `min_seeds` match, add artists from the tag's related tags (`tag.getsimilar`), still limited to user-known artists. Candidates must carry the requested tag(s) among their top 8 tags. Some words, especially moods, are barely used as tags, so the match comes back empty even though the user means something clear. In that case choose up to three Last.fm tags that express the same meaning, verify each with `tag.getinfo` and `tag.gettopartists`, use them as the requested tags, and name them in the first line of the reply. Ask for another word or a seed artist only if no populated tag expresses it.
- Era: seeds are the user's top artists by plays inside that period of `monthly.json`. Candidates must still be new. No tag fit.

**Path B: no theme given.** Do not ask. Filter by the user's own recent-listening cluster.

- Build the cluster from `monthly.json` for the last `cluster_window_months` months: weight each artist's top tags (from `tags_cache.json`) by that artist's plays, keep the tags that make up `cluster_tags`.
- Seeds: the `seed_count` top artists by blended weight, where weight = (1 - `recent_weight`) x share of all-time plays + `recent_weight` x share of last-12-month plays. Keep seeds whose top 8 tags intersect the cluster and whose dominant tag has non-zero share in this user's own all-time tag distribution.
- Candidates must have top 8 tags that intersect the cluster and a dominant tag with non-zero share in this user's own all-time tag distribution.
- State to the user which cluster tags were used (first line of the reply).

## Step 3: candidates and scoring (Discovery)

1. For each seed, `artist.getsimilar` (limit 100). `score[c] += seed_weight x match`. Count `support[c]` = distinct seeds pointing to `c`.
2. Remove every candidate in the user-known set.
3. Require `support >= support_min`.
4. **Second-level expansion.** If fewer eligible candidates remain than the playlist needs, the dense user-known set has absorbed the first level. Expand through the first-level similar artists, highest accumulated score first, user-known ones first, up to `second_level_cap` parents: call `artist.getsimilar` on each. A second-level candidate's score contribution = parent's score share x match x `second_level_decay`, and its support counts distinct parents. Apply steps 2 and 3 again. Never go deeper than two levels. Say in the reply that second-level similarity was used.
5. `artist.getinfo` for up to `info_cap` top candidates: read global listeners and tags. Drop candidates outside `band`. If the user asks for more widely listened-to artists, use the upper half of the band. If they ask for deep cuts, use the lower half.
6. Fit check: path A applies the requested-tag check from Step 2; path B applies the cluster check from Step 2.
7. Final score = score x log(support + 1). Take the top N (a 25-30 track playlist needs 20-26 artists).
8. Diversity: at most `max_per_seed` candidates driven mainly by the same single seed, and spread the picks across the candidates' dominant-tag groups instead of one block.
9. Run Step 1b on the finalists. Keep a reserve of extra candidates to replace drops.

## Step 4: tracks and playlist

- One track per artist (two for the top three): `artist.gettoptracks` number 1 or 2. Skip remixes, live versions and intros.
- Order: open with the three strongest matches, keep neighbouring tracks close in tempo and mood, avoid two similar-sounding artists back to back.
- Playlist name: short and specific, no clichés. Format `<Theme> · <playlist_tag> <yyyy-mm>`, in the response language. The connector may shorten it. The description names 3-4 of the user's own artists it was built from.
- Create it with `generate_playlist` (one prompt, `language` taken from `response_language`, tracks as `Artist — Track`), then `save_to_library` with the returned URI.
- Reply with a table: artist | track | which of the user's artists it is similar to (show the chain for second-level picks) | global listeners. Present the table as the verified result. State that Spotify generated the playlist, that its content is unverified, and that it may contain tracks or artists the user already knows. If the user reports such an artist in the playlist, confirm it plainly without explaining it away, and note that removing it is a manual step in Spotify.

## Step 5: feedback loop

- After creating a playlist, append one line to the playlist log: date, name, mode, artists.
- Likes check: ask the user to name the tracks or artists they liked in Spotify, because Last.fm cannot see those. Store what they name in the self-reported "heard outside Last.fm" list. Those artists are excluded from every later run and replaced in the current playlist on request. This is the one question of the reply (see "One question per turn").
- On the next run, or when asked what landed, call `user.getrecenttracks` with `from=<playlist date>`. An artist is a hit at `hit_threshold` or more plays. Report hits and misses.
- Hits become seeds with `hit_bonus`. Artists the user explicitly rejects are excluded for good (stored in the rejected list).
- **Limitation:** this loop detects re-listens only through Last.fm scrobbles. Re-listening that happens only in Spotify and is not scrobbled is invisible. A "miss" therefore means "no scrobbled plays", not "not liked", and a miss never triggers an automatic rejection.

## Mode: Forgotten

Not discovery. Candidates are user-known by definition, so there is no similarity search, no newness check and no cluster filter (the own recent-listening cluster would exclude exactly these artists).

- Pool: artists with at least `forgotten_min_plays` total plays before the silence window and 0 plays in the last `forgotten_silence_months` months, computed from `monthly.json` with no extra API calls beyond the refresh (`scripts/lastfm.py forgotten --min-plays <n> --silence-months <n>`, with the profile's values). Remove explicitly rejected artists.
- If the user named an era, genre or scene, apply it as in path A (era from `monthly.json`; tag fit by top 8 tags).
- Rank by historical plays and spread across the eras of the user's history (by each artist's peak year).
- Tracks: the artist's tracks this user played most, from `user.gettoptracks` (period `overall`, limit 1000) filtered by artist. Fall back to `artist.gettoptracks` if none match.
- Reply table: artist | track | total plays | last played month. Build the playlist as in Step 4, and log it with mode "forgotten".
- "Forgotten" means no scrobbles. Spotify-only listening is invisible (see Limitation).

## Mode: Loved (comfort or mood from already-loved artists)

Not discovery. No similarity search for new artists and no newness check. `python scripts/lastfm.py loved --store <working dir> --tags a,b --needed <n>` implements this mode (omit `--tags` when no mood was named).

- Pool: artists with plays at or above `loved_threshold`, plus self-reported liked artists. Remove explicitly rejected artists.
- Mood or theme named: treat the word as a Last.fm tag and keep pool artists carrying it among their top 8 tags. If the pool is too small, add the tag's related tags (`tag.getsimilar`), then pool artists similar to the matches (`artist.getsimilar` intersected with the pool). Apply the same tag-choice rule as in Step 2 when the word itself is barely used as a tag. If the pool is still empty, fall back to the own recent-listening cluster and say so.
- No mood named: use the own recent-listening cluster over the pool, ranked by blended weight, so the playlist mixes long-term favorites with recent rotation.
- Tracks: the user's own most-played tracks for those artists (`user.gettoptracks`, `overall` and `12month`), one or two per artist, no duplicates.
- Reply table: artist | track | user's plays. Build the playlist as in Step 4, and log it with mode "loved".

## Opt-in: time-pattern personalization

Off by default (`features.time_patterns.enabled = false` in the profile). It starts only when the user explicitly asks for it. It is never offered in a routine reply. If the user asks what the skill can do, it may be mentioned in one line.

- Enabling: page `user.getrecenttracks` (limit 200) from the registration date. The page count is ceil(`playcount` / 200), taken from `user.getinfo`. State the count before starting. Persist `history.csv` (ts, date_utc, artist, album, track) in the persistent store, skip the `nowplaying` entry, and later fetch only new plays with `from=<max ts>`.
- Use: only when a request names a time-of-day or day-of-week context, or explicitly asks for pattern-based personalization. Compute the tag mass of the user's own plays in that slot (in the user's timezone) and use it as the filter in place of the path B cluster, in Discovery or Loved mode. Say that it was used.
- If the feature is off and a request names a time context, treat the words as a theme (path A, tag-based) and use no history.
- Disabling: stop using the history on request. Delete `history.csv` only if the user asks.

## Analysis

- One-off per-scrobble analyses (hour of day, weekday) need the full history. If the feature above is not enabled, hold the history in scratch only and do not persist it.
- Patterns:
  - Eras: top 5 artists per year, and the year each artist first appeared.
  - Dropped: the Forgotten pool definition above.
  - New: artists whose first scrobble falls within the last `new_window_days` days.
  - Time: plays by hour and weekday, in the user's timezone from the profile.
  - Shift: `3month` against `overall`.
- Do the analysis in pandas, not by paging through the conversation.
- If the profile lists a published timeline artifact, update it by URL after refreshing `monthly.json`.
- Long analysis or visuals go into an HTML artifact. Load the dataviz and artifact-design skills first if available.

## Failure handling

**Last.fm.** Retry transient errors (rate limit 29, temporary error 16, service offline 11, operation failed 8, network timeouts) up to 3 times with growing waits. Do not retry authentication or parameter errors (4, 6, 10, 17, 26): stop and tell the user which credential or input is wrong. After retries are exhausted:

- Refresh of `monthly.json` fails and a cached copy exists: continue with the cache, state its last refresh date once, and keep Step 1b mandatory. If no cache exists, stop. A partial user-known set would label heard artists as new.
- Similarity or info calls fail for some seeds or candidates: continue with the partial data only if enough verified candidates remain to fill the playlist. Say which seeds failed in one line. Otherwise stop and report.
- Step 1b checks fail: drop the affected finalists. If the checks are unavailable for all of them, stop. Never deliver unverified claims of newness.
- Track lookup fails for an artist: drop the artist and use a reserve candidate.
- A write to the persistent store fails: continue the task, and say the cache will not persist.
- Any stop: say what failed, what had completed, and whether the cache is intact. No silent fallbacks.

**Spotify.**

- `generate_playlist` errors or returns no playlist URI: tell the user plainly that playlist creation failed and quote the error. Deliver the track table anyway so nothing is lost. Do not retry automatically, because the playlist may exist server-side and a retry would create a duplicate. Do not use `search` or any other tool as a workaround. The user can say "retry".
- `save_to_library` fails: retry once (it is safe to repeat). If it still fails, say the playlist exists in Spotify but is not saved to the library, and give the URI and the track table.
- Connector unavailable: say so and deliver the track table.

## One question per turn

- A reply contains at most one question. A simple playlist request is answered with the playlist: no onboarding, no settings questions, no mode questions.
- Unspecified details are defaulted silently and stated in one line (mode, cluster, language).
- If several questions are possible, the order is: (1) missing information the task cannot run without (credentials, an unusable tag word, seed artists for the minimum-data gate), (2) the Step 5 likes check after a delivered Discovery playlist, (3) nothing else.
- A reply carries at most one of: a likes check, an opt-in offer, a taste-shift report. The others wait for a later turn, or for the user to ask. An offer the user declined is not repeated.

## Output

- Reply in `response_language` from the profile. It is a per-user setting. The user can change it at any time, and the change is saved to the profile.
- Tables for track lists. Follow the user's own preferences on tone and length.

## Changelog

- v0.1: access and methods.
- v0.2: discovery pipeline (user-known set, seeds, similarity scoring, support threshold, popularity band), Spotify playlists, feedback loop.
- v0.3: Last.fm does not see Spotify likes. Added per-finalist checks, self-reported lists, the real Spotify connector limits (generative playlists, mandatory save to library).
- v0.8 (2026-10-06): `lastfm.py` honours Retry-After on HTTP 429, adds jitter to backoff, retries HTTP 5xx, and stops at once on other 4xx and non-transient Last.fm codes. Unit tests in `tests/`.
- v0.7 (2026-10-06): renamed to Unheard. First-run onboarding for non-technical users: plain-language guide to getting a Last.fm API key, `setup` command that validates and saves credentials, specific fixes for each setup error.
- v0.6 (2026-10-06): after the first live playlist, Spotify added a known artist (not in the requested list). Rules now say the verified table is the deliverable, claims about playlist contents are forbidden, and the reply must warn that known artists may appear.
- v0.5 (2026-10-06): trigger-oriented description, bundled `scripts/lastfm.py` (cache build and refresh, profile build, Discovery, Loved, Forgotten, finalist verification), `evals/evals.json` with three test prompts. Fixes from the first test run: mood words that are barely used as Last.fm tags are mapped to up to three populated tags, and the popularity band is sampled across all of the user's artists with at least 3 plays instead of only the most played.
- v0.4 (2026-10-06): full rewrite. User-agnostic: history range derived from the registration date, all personal values moved to an auto-built profile, caches persisted outside scratch. Filter split into path A (named request) and path B (own recent-listening cluster). Spotify limited to `generate_playlist` and `save_to_library`. Added Forgotten and Loved modes, second-level similarity, minimum-data gate, failure handling, one-question rule, per-user response language, opt-in time-pattern personalization, and the scrobble-only limitation on the feedback loop.