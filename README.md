# Unheard

A Claude skill that finds artists you have never played, using your own Last.fm history instead of Spotify's recommendations.

Spotify recommends what it thinks you like. After ten years of scrobbling that mostly means more of what you already know. This skill reads your full Last.fm history, takes the artists you actually play, looks at who is similar, and removes everyone you have ever scrobbled. What is left gets checked one more time per artist and per track (`userplaycount = 0`) before it reaches you.

## What it does

- **Discovery.** New artists, never scrobbled by you. With no theme given, it uses your own last six months of listening as the filter. With a named request (an artist, a genre, an era, a mood), it uses that instead.
- **Forgotten.** Artists you played 50+ times and have not touched in 12 months.
- **Loved.** A playlist from artists you already love, for a mood or an evening.
- **Stats.** Top artists by year, how your taste moved, month-by-month history.

Output is a table with the artist, a track, which of your artists it is similar to, and global listener count. A Spotify playlist is built from it.

## What you need

- Claude (Claude Code, or Claude with skills enabled).
- A free Last.fm API key: https://www.last.fm/api/account/create
- Your Last.fm username.
- Optional: the Spotify connector, for playlists. Without it you still get the table.

Set two environment variables:

```
export LASTFM_API_KEY=your_key
export LASTFM_USER=your_username
```

## Install

Copy the `unheard/` folder into `~/.claude/skills/`.

The first run downloads your full history month by month and stores it locally (`monthly.json`). It takes a few minutes for a long history, and later runs refresh only the recent months. Everything the skill derives from your account (taste profile, popularity band, thresholds) is built automatically. Nothing is hard-coded to one person.

## Known limits

- Last.fm sees only scrobbles. If you listen to something in Spotify without scrobbling, the skill does not know. Reply with the artists you already knew and they are excluded from later runs.
- Spotify's playlist generator takes a text prompt, not a track list. It may reorder, swap or drop tracks, and it can add artists you already know. The verified table is the result; the playlist is a convenience copy.
- Tested on one account (231 months, about 17,700 artists). Forgotten and Loved modes were checked in dry runs. Accounts with under 100 known artists get a reduced mode with low confidence.

## Files

- `SKILL.md`: instructions Claude follows.
- `scripts/lastfm.py`: Last.fm client with caching, retries and the discovery logic. Standard library only.
