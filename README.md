# Unheard

A Claude skill that finds artists you have never played, using your own Last.fm history instead of Spotify's recommendations.

Spotify recommends what it thinks you like. After years of scrobbling that mostly means more of what you already know. Unheard reads your whole Last.fm history, takes the artists you actually play, looks at who is similar, and throws out everyone you have ever scrobbled. Each remaining artist and track is checked once more for zero plays before it reaches you.

## What you can ask

- "Find me something new to listen to." New artists you have never scrobbled, close to what you play these days.
- "Something new like Massive Attack" or "new 90s hip-hop for me". The same, around a theme you name.
- "Artists I used to play all the time and dropped." Artists with 50+ plays and nothing in the last 12 months.
- "A cozy playlist, only stuff I already love." A mood playlist from your own favourites.
- "My top artists by year." Statistics from your history.

You get a table: artist, track, which of your artists it is similar to, and how many listeners it has on Last.fm. If Spotify is connected, Claude also creates the playlist in your Spotify library.

## Setup

You need a Last.fm account with some listening history. Spotify is optional.

### Step 1. Turn on code execution in Claude

The skill runs a small script, so Claude needs permission to run code.

1. Open [claude.ai](https://claude.ai) and go to **Settings → Capabilities**.
2. Turn on **Code execution and file creation**.

On a Team or Enterprise plan, an admin does this in **Organization settings**.

### Step 2. Add the skill to Claude

1. Download [unheard.zip](https://github.com/gobimans/unheard/raw/main/unheard.zip). Do not unzip it.
2. In Claude, open **Customize → Skills** ([claude.ai/customize/skills](https://claude.ai/customize/skills)).
3. Press **+**, then **Create skill**, then **Upload a skill**.
4. Choose `unheard.zip`.

### Step 3. Connect Spotify (optional)

Without Spotify you still get the list of artists and tracks. With it, Claude also makes the playlist for you.

1. In Claude's settings, open **Connectors**.
2. Find **Spotify**, press **Connect** and log in to your Spotify account.

Spotify's playlist generator needs a Spotify Premium account.

### Step 4. Get your Last.fm API key

This is a free key that lets the script read your listening history. It cannot post anything or change your account.

1. Log in at [last.fm](https://www.last.fm).
2. Open [last.fm/api/account/create](https://www.last.fm/api/account/create).
3. Fill in the form:
   - **Contact email:** your email.
   - **Application name:** anything, for example `Unheard`.
   - **Application description:** anything, for example `personal playlists`.
   - **Callback URL** and **Application homepage:** leave empty.
4. Press **Submit**.
5. The next page shows two values: **API key** and **Shared secret**. You need only the **API key**. It is 32 letters and digits.

Lost it? Your keys are listed at [last.fm/api/accounts](https://www.last.fm/api/accounts).

Your **username** is the name at the end of your profile address: last.fm/user/**yourname**.

### Step 5. Ask for music

Start a new chat and write something like "find me something new to listen to".

The first time, Claude asks for your username and API key. Paste them in. Claude checks them, then downloads your history. A long history takes a few minutes, and only the first time. After that it remembers you.

## If something goes wrong

- **"This is not an API key."** You probably copied the Shared secret. Copy the line labelled API key.
- **"User not found."** Check the spelling of your username in your profile address.
- **"Your history is hidden."** On last.fm open Settings → Privacy and turn off the option that hides your recent listening.
- **"Last.fm cannot be reached."** The place where Claude runs code has no internet access to Last.fm. This depends on your Claude plan and settings. Use Claude Code (below) instead.
- **Claude doesn't use the skill.** Mention Last.fm or your scrobbles in the message, or check that the skill is turned on in Customize → Skills.

## Claude Code

If you use Claude Code in a terminal:

```
git clone https://github.com/gobimans/unheard
cp -r unheard/unheard ~/.claude/skills/
```

Then ask Claude for music as in Step 5. You can also put the credentials in your environment instead of pasting them in chat:

```
export LASTFM_API_KEY=your_key
export LASTFM_USER=your_username
```

## Exact Spotify playlists (optional, advanced)

The skill makes playlists with Claude's Spotify connector, which builds them from a description and may swap tracks. If you run the skill locally in Claude Code and have your own Spotify app, `contrib/spotify_export.py` adds exactly the tracks Unheard found:

```
python3 unheard/scripts/lastfm.py discover --store DIR --needed 24 | python3 contrib/spotify_export.py --store DIR --name "Unheard"
```

It needs a Client ID from [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) with the redirect URI `http://127.0.0.1:8888/callback`. Spotify limits such apps to 5 users and requires Premium for the owner, so this is for your own account, not for sharing. Setup details are at the top of the script.

## Tests

```
python3 -m unittest discover -s tests -v
```

## Good to know

- **Last.fm only sees scrobbles.** If you listened to an artist in Spotify without scrobbling, the skill thinks you never heard them. Tell Claude which artists you already knew and they are excluded next time.
- **Spotify builds the playlist its own way.** Its generator takes a description, not an exact track list. It can reorder or swap tracks and sometimes adds artists you know. The table Claude gives you is the checked result. The playlist is a convenient copy.
- **Where your data goes.** Your history is saved as files in a folder named `unheard` (in your connected cloud drive or on your computer, if Claude has access to one), so it is not downloaded again each time. Nothing is sent anywhere except Last.fm and, for playlists, Spotify.
- **Tested** on one account with 19 years of history (about 17,700 artists). Accounts with fewer than 100 artists get a simpler mode with a warning.

## Files

- `unheard/SKILL.md`: the instructions Claude follows.
- `unheard/scripts/lastfm.py`: the Last.fm script (no extra packages needed).
- `unheard/evals/evals.json`: test prompts used to check the skill.
- `unheard.zip`: the same folder, packed for uploading to Claude.
- `contrib/spotify_export.py`: optional exact-playlist export (see above).
- `tests/`: unit tests, standard library only.
