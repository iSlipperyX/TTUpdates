# TikTok Manager

A Windows desktop app that **scores your videos for TikTok**, lets you **put them in posting order**,
**posts them on a schedule**, and **tracks how they do** on your account.

Run it: `dist\TikTokManager\TikTokManager.exe` (keep the whole `TikTokManager` folder together;
`dist\TikTokManager-windows.zip` is the same thing zipped for moving to another PC).

## Pages

**Library**: drop in videos or whole folders. ClipMonster output folders bring their clip titles,
scores and "why it was picked" notes along. Every video gets a 0–100 score with a letter grade,
a breakdown, and concrete tips ("1.2 s of silence at the start: trim it"). Write the caption and
hashtags here, or click **✨ AI caption** to have Claude watch the clip, rate it and write them.

**Queue**: the order videos will post in. Drag to reorder, or use **Sort by score** / **Smart order**
(best first, never two clips from the same source video back to back). Set posts per day, the
posting window and the minimum gap; each video shows when it will go out. Pin a video to an exact
time if you want. **Post selected now** posts immediately; **Autopilot** posts each video when its
time comes.

**Account**: log in, then see followers, likes, every video's views, likes, comments, shares and
engagement, plus the change since the last refresh. "Posted from this app" is the history of
everything the app posted or tried to post. Once a few posts have stats, the page tells you whether
high-scoring videos really do get more views on *your* account.

## How the score works

| Part | What it measures |
|---|---|
| Hook (first 3 s) | Sound from the first frame, energy and movement in the opening vs the rest |
| Format | 9:16 vertical, resolution, frame rate |
| Length | Closeness to your sweet spot (default 21–45 s; set in Settings) |
| Pacing | Cuts per minute, on-screen motion, dead air |
| Audio | Loudness vs about −14 LUFS, clipping; no audio caps the total at 45 |
| Picture | Brightness, contrast, colour, sharpness, compression |
| Clip finder | ClipMonster's own score, if the clip came from it |
| AI rating | Claude's prediction of appeal and hook (optional) |

Weights are adjustable in **Settings → Scoring**.

## Posting: two methods

**Browser (default, recommended).** The app opens TikTok Studio in your own Microsoft Edge (or Chrome)
using a separate browser profile, uploads the file, types the caption and hashtags, sets "who can
watch" and comment/duet/stitch, and clicks Post. You log in yourself once (**Account → Log in**); the
app never sees your password. Leave **Show the browser window** on: TikTok is less likely to block a
visible browser, and you can solve a captcha if one appears.

**Official TikTok API (optional).** For people with their own app at developers.tiktok.com. Paste the
client key and secret in **Settings → TikTok API** and register the redirect URI shown there. Until TikTok
audits your app, it can only post "Only me" videos to a **private** account. That's TikTok's rule, so most
people should use the browser method.

## Autopilot

Autopilot posts while the app is running. Closing the window keeps it running in the system tray.
Turn on **Settings → Start with Windows** so it carries on after a restart. Only one copy of the app
can run at a time, so nothing gets posted twice.

## Good to know

- TikTok changes its website often. If a browser upload fails, the error and a screenshot (in
  `%LOCALAPPDATA%\TikTokManager\logs`) say which step broke. Check TikTok Studio before retrying, so
  the video isn't posted twice.
- Automating TikTok's website is against the spirit of its terms. Keep the pace human (the default
  is 3 posts a day, 2 hours apart).
- Your data: `%APPDATA%\TikTokManager` (library, settings; API keys and tokens are encrypted with your
  Windows login) and `%LOCALAPPDATA%\TikTokManager` (thumbnails, the browser profile with your TikTok
  login, logs).
- `TikTokManager.exe --selftest` checks ffmpeg and the browser automation and writes `selftest.txt`.

## Developing

```
pip install -e .[build,dev]
python -m tokmanager          # run from source
python -m pytest              # tests
python build.py               # build dist\TikTokManager\TikTokManager.exe
```
