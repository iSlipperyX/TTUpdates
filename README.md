# TikTok Manager

A Windows desktop app that **scores your videos for TikTok**, lets you **put them in posting order**,
**posts them on a schedule**, and **tracks how they do** on your account.

Run it: `dist\TikTokManager\TikTokManager.exe` (keep the whole `TikTokManager` folder together;
`dist\TikTokManager-windows.zip` is the same thing zipped for moving to another PC).

## Accounts

Run several TikTok accounts from one install. The **account button** at the top left shows which
account the window is on; click it (or press **Ctrl+1…9**) to switch, or to add one.

- Each account has its **own TikTok login** (its own browser profile, or its own API connection),
  its **own queue and schedule**, its own **autopilot switch**, its own **stats and post history**, and
  its own posting defaults: who can watch, hashtags added to every post, and the niche the AI writes
  captions for (**Manage accounts**).
- The **video library is shared**. "Queue for …" adds the selected videos to the account you're on;
  right-click to queue them for another account, move a queued video to another account's queue,
  or queue a copy of an already-posted video for a second account.
- **Autopilot posts for every account that has it on**, one upload at a time (the most overdue video
  goes first). An account that isn't logged in is skipped, and the app tells you once.
- Before each browser upload the app checks which TikTok account the browser is logged in to, and
  stops if it isn't the account the video is queued for.
- Upgrading from 1.0: your queue, schedule, stats and TikTok login become the first account
  automatically. Nothing needs logging in again.

## Pages

**Library**: drop in videos or whole folders. ClipMonster output folders bring their clip titles,
scores and "why it was picked" notes along. Every video gets a 0–100 score with a letter grade,
a breakdown, and concrete tips ("1.2 s of silence at the start: trim it"). Write the caption and
hashtags here, or click **✨ AI caption** to have Claude watch the clip, rate it and write them.

**Queue**: the order the shown account's videos will post in. Drag to reorder, or use **Sort by score** / **Smart order**
(best first, never two clips from the same source video back to back). Set posts per day, the
posting window and the minimum gap; each video shows when it will go out. Pin a video to an exact
time if you want. **Post selected now** posts immediately; **Autopilot** posts each video when its
time comes.

**Ready**: for posting by hand. The shown account's queued videos, in order, each with its caption,
credit line and hashtags ready to copy. **Copy caption**, drag the file in from **Show file** (or
**Open TikTok upload**), post it in TikTok, then click **Mark as posted** (there's an **Undo** for a
few seconds). **Not this one** sends a video back to the Library, **Edit…** opens it on the Queue
page, and **Copy to a folder** copies the videos with a `.txt` caption beside each, for posting from
a phone through a synced folder.

**Account**: log the shown account in, then see followers, likes, every video's views, likes, comments, shares and
engagement, plus the change since the last refresh. "Posted from this app" is the history of
everything the app posted or tried to post. Once a few posts have stats, the page tells you whether
high-scoring videos really do get more views on *your* account.

## Clips that arrive by themselves

**Settings → Ready to upload** watches folders for new clips: add **ClipMonster's Automation folder**
with one click. Every few minutes new clips are brought in, scored and (if you choose) queued:
left in the Library, shared out between your accounts (each goes to the one with the fewest
waiting), or all queued for one account.

- Only finished clips come in: ones ClipMonster has listed in its `clips.json`, or other files nobody
  has written to for a minute. Clips ClipMonster's quality check rejected (its `_rejected` folders)
  are left out, and a video you remove from the Library isn't brought back.
- ClipMonster's **hook** becomes the starting caption, its AI rating and quality-check warnings show
  on the Ready page, and **Creative Commons** clips get a credit line (`🎥 Channel on YouTube (CC BY)`)
  in their caption, which the licence requires. Tick **Write an AI caption** to have Claude write
  one for each new clip instead.
- With autopilot off, a **reminder** pops up when a video's posting time comes round, and a
  **morning summary** says what's ready for each account.
- **ClipMonster learns from your results.** After each stats refresh, the views of ClipMonster clips
  that are two days old or more, compared with your account's usual views, go to ClipMonster's
  `results.json`. ClipMonster then ranks channels whose clips do well higher, and shows its AI the
  hooks that worked. The Account page lists the best and worst source channels and the best hook so
  far. Untick **Tell ClipMonster how its clips do** in Settings → Ready to upload to stop it. Nothing
  leaves your PC.

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
watch" and comment/duet/stitch, and clicks Post. You log in yourself once per account (**Account → Log in**);
the app never sees your password. Leave **Show the browser window** on: TikTok is less likely to block a
visible browser, and you can solve a captcha if one appears.

**Official TikTok API (optional).** For people with their own app at developers.tiktok.com. Paste the
client key and secret in **Settings → TikTok API** and register the redirect URI shown there. Until TikTok
audits your app, it can only post "Only me" videos to a **private** account. That's TikTok's rule, so most
people should use the browser method.

## Autopilot

Autopilot posts while the app is running, for each account where it's switched on (on the Queue page,
or from the tray icon's **Autopilot** menu). Closing the window keeps it running in the system tray.
Turn on **Settings → Start with Windows** so it carries on after a restart. Only one copy of the app
can run at a time, so nothing gets posted twice.

## Good to know

- TikTok changes its website often. If a browser upload fails, the error and a screenshot (in
  `%LOCALAPPDATA%\TikTokManager\logs`) say which step broke. Check TikTok Studio before retrying, so
  the video isn't posted twice.
- Automating TikTok's website is against the spirit of its terms. Keep the pace human (the default
  is 3 posts a day, 2 hours apart).
- Your data: `%APPDATA%\TikTokManager` (library, settings, `accounts.json`; API keys and tokens are
  encrypted with your Windows login, one tokens file per account under `tokens\`) and
  `%LOCALAPPDATA%\TikTokManager` (thumbnails, logs, and each account's browser profile with its TikTok
  login under `browser-profiles\`; an account carried over from 1.0 keeps using `browser-profile`).
- `TikTokManager.exe --selftest` checks ffmpeg and the browser automation and writes `selftest.txt`.

## Developing

```
pip install -e .[build,dev]
python -m tokmanager          # run from source
python -m pytest              # tests
python build.py               # build dist\TikTokManager\TikTokManager.exe
```
