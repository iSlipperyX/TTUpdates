# TikTok Manager

A Windows desktop app that **scores your videos for TikTok**, lets you **put them in posting order**,
**posts them on a schedule**, and **tracks how they do** on your account.

Install it with **TikTokManager-Setup.exe** from the [latest release](https://github.com/iSlipperyX/TTUpdates/releases/latest)
(per user, no admin needed). `TikTokManager-windows.zip` from the same release is the portable version:
unzip it anywhere and keep the `TikTokManager` folder together. Either way it updates itself.

On first start a short **setup** walks through the three things that matter: your TikTok account
(and logging in), where your clips come from (ClipMonster's folder is found by itself), and how you
want to post. Skip it if you like; **Settings → Ready to upload → Run first-run setup again** brings it back.

## Accounts

Run several TikTok accounts from one install. The **account button** at the top left shows which
account the window is on; click it (or press **Ctrl+1…9**) to switch, or to add one.

- Each account has its **own TikTok login** (its own browser profile), its **own queue and schedule**, its own **posting mode** (by hand or autopilot), its own **stats and post history**, and
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
- **Profile…** sets each account's **profile picture, name and bio**, and **Save and apply to TikTok**
  fills in TikTok's Edit profile box for you, then checks TikTok took it. TikTok lets a name change only
  once every 7 days; until then the picture and bio are still saved. **✨ Suggest bios** writes three
  bios from the account's niche (needs a Claude or Gemini key).

## Pages

**Library**: drop in videos or whole folders. ClipMonster output folders bring their clip titles,
scores and "why it was picked" notes along. Every video gets a 0–100 score with a letter grade,
a breakdown, and concrete tips ("1.2 s of silence at the start: trim it"). Write the caption and
hashtags here, or click **✨ AI caption** to have the AI watch the clip, rate it and write them.
**✨ Hashtags** writes just the hashtags (a quick text-only call): tags for the people, game or topic
in the clip, fitted to the account's niche, leaning on the hashtags that got your posts the most views.
No spam tags, and none the account already adds to every post.

**The AI** is optional. Under *Settings → AI* choose **Claude** (an Anthropic API key, from
console.anthropic.com) or **Gemini** (a Gemini API key, free from aistudio.google.com/apikey; Google may
use what free keys send to improve its products), add its key and click **Test keys** (no tokens used).
ClipMonster's keys are used when TikTok Manager has none of its own. With both keys and **If it fails,
try the other AI** on, a call one AI can't answer (a used-up quota, busy servers) goes to the other.

**Queue**: the order the shown account's videos will post in. Drag to reorder, or use **Sort by score** / **Smart order**
(best first, never two clips from the same source video back to back). Set posts per day, the
posting window and the minimum gap; each video shows when it will go out. Pin a video to an exact
time if you want. **Posting: ✋ By hand / 🤖 Autopilot** picks how the account posts. By hand, the
times are reminders and you post from the Ready page (or your phone). On autopilot the app posts each
video itself when its time comes. **Post selected now** posts immediately either way. The app opens on
the Ready page for an account you post to by hand, and on the Queue for one on autopilot.

**Ready**: for posting by hand. The shown account's queued videos, in order, each named by
ClipMonster's hook (not its file name) with its caption, credit line and hashtags ready to copy.
**Copy caption**, drag the file in from **Show file** (or **Open TikTok upload**), post it in TikTok,
then click **Mark as posted** (there's an **Undo** for a few seconds). The top line counts what's
waiting and what you've posted today. **✨ Hashtags** has the AI write a video's hashtags
(**✨ Hashtags for N** does every waiting video without any). **Not this one** sends a video back to
the Library, and **Edit…** opens it on the Queue page. Without a phone folder, **Copy to a folder**
copies the videos with a `.txt` caption beside each.

Everything on the Ready page works from the keyboard. The keys act on the outlined card: the one you
last clicked or tabbed into, or moved to with ↓ / ↑.

| Key | Does |
|---|---|
| ↓ / J, ↑ / K | next / previous video |
| C, H | copy the caption / just the hashtags |
| F, P | show the file (to drag into TikTok) / play it |
| T, E | ✨ hashtags / edit on the Queue page |
| Enter | mark as posted (the next video takes over) |
| Ctrl+Z | undo the last "mark as posted" (while its **Undo** button shows) |

**Live**: what's happening on the shown account right now. Every 5 minutes (or every 2 to 30 min,
**Settings → Live**) the app reads the account's TikTok profile in its own browser login, with the
window hidden, after any post that's due. Today's new views, likes, comments, shares and followers, your
latest videos with their views in the last hour and a small trend line, and an activity feed:
"+250 views on …", new followers, milestones (1K, 10K…), a video taking off, and **a video you
posted from your phone**, which is matched to its clip in the queue and marked as posted by itself.
Notifications when a queued clip shows up as posted, for milestones and for videos taking off are on
by default (**Settings → Live**); several at once come as one. **Watch live** turns it off per account.
It starts by itself once the account is logged in. TikTok's profile page lists **public** videos only,
so "Only me" posts don't show up. If TikTok keeps asking for a captcha, turn on **Settings → Live →
Show the browser while reading** and solve it once.

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
  in their caption, which the licence requires. Tick **Write an AI caption** to have the AI write
  one for each new clip instead.
- **Niches.** A clip from a ClipMonster niche whose account name matches one of your accounts'
  usernames (with or without the @) is queued for that account, whatever the sharing rule says, and
  so is a clip in a niche that only one account has ticked under **Niches it posts** (see below).
- **Part series.** ClipMonster's "Part 1 / 2 / 3" clips stay in order, and back to back, in the queue
  and on the Ready page, which shows "Part 2 of 3" on each.
- ClipMonster's **Clean up** may delete clips you've already posted. Their stats and history are
  kept, they aren't brought in again, and the Library just says the file was deleted after posting.
- For accounts you post to by hand, a **reminder** pops up when a video's posting time comes round, and a
  **morning summary** says what's ready for each account.
- **ClipMonster learns from your results.** After each stats refresh, the views of ClipMonster clips
  that are two days old or more, compared with your account's usual views, go to ClipMonster's
  `results.json`. ClipMonster then ranks channels whose clips do well higher, and shows its AI the
  hooks that worked. The Account page lists the best and worst source channels and the best hook so
  far. Untick **Tell ClipMonster how its clips do** in Settings → Ready to upload to stop it. Nothing
  leaves your PC.

## Clips from Google Drive

When ClipMonster uploads its clips to Google Drive (ClipMonster 2.8+, its Settings → Upload),
**Settings → Google Drive** brings them in: every 10 minutes the clips TikTok Manager hasn't got are
downloaded to **Videos\TikTok Manager\From Google Drive** and come in like a watched folder's, with
their titles, scores, hooks, niches and part series. It signs in with ClipMonster's own Google
client (read from ClipMonster on the same PC, or pasted on another PC), so there's nothing new to
set up in Google Cloud.

- Clips already here (from ClipMonster's folder on this PC), removed, cleaned up or rejected aren't
  downloaded, and only clips uploaded in the last 14 days are taken (changeable).
- Each clip taken is marked in Drive with the PC's name, so TikTok Manager on a second PC leaves it
  alone and nothing is posted twice.
- The Ready page's **☁ Google Drive** line shows the last look, or what's stopping it.

Step by step: [docs/google-drive.md](docs/google-drive.md).

## Posting from your phone

**Settings → Ready to upload → Phone folder** (or the 📱 line on the Ready page, one click with
OneDrive) keeps the next day's clips for the accounts you post to by hand in a folder that syncs to
your phone, **OneDrive › TikTok Ready** by default. Each video sits in posting order with a `.txt`
holding exactly what to paste into TikTok, e.g. `01 Tue 15.00 - He quit his job live on air.mp4`.
With more than one account, each gets its own subfolder.

- It's kept up to date by itself: posted clips are taken out, reordering renames files rather than
  copying them again, and the next clips go in as their day comes.
- On your phone, open the folder in the OneDrive app, save the clip, paste its caption into TikTok,
  post, then delete the clip there. A clip you delete isn't copied back: its card on the Ready page
  asks whether you posted it (**Mark as posted**) or want it back in the folder.
- Only files the app put there are touched, and each PC keeps its own list, so two PCs can share the
  folder. Accounts on autopilot are left out (they post themselves). The folder can even sit inside a
  watched folder: its copies aren't brought in again as new clips.

## Niches

When your clips cover more than one topic (ClipMonster's niches, like *Gaming Videos* and *Financial
Advisor*), the **niche switcher** next to the account button shows one at a time. Pick **Gaming
Videos** and the Library, Ready, Queue and Account pages show only gaming clips; **All niches** shows
everything again. The switcher only changes what you see. The menu counts each niche's videos, and
the switcher only appears once the library has niches.

What does decide posting is **Niches it posts**, under each account's schedule on the Queue page.
Only queued videos tagged with one of its niches get a posting time; the rest stay in their place in
the queue, marked "No posting time", and are never posted, reminded of or copied to the phone until
they're tagged (or moved to an account that posts their niche). An account starts on **Every niche**,
so only videos with no niche wait; tick niches by name to narrow it, or **Videos with no niche** to
let those in too. The Queue and Ready pages count the videos on hold, and **Select them** on the Queue
picks them out so you can tag them all at once (right-click → **Niche**). A library with no niches at
all schedules everything, as before.

- **Where a video's niche comes from**, first match wins: the niche you picked for it; ClipMonster's
  word (its clips.json, or the list of videos it picked), under the niche's current name; a folder
  it's in named after a niche (ClipMonster's `TTVIDEOS\Gaming Videos\…`); the other clips from its
  folder; its channel being in a niche's channel list in ClipMonster, or the other clips from that
  channel all being one niche; the account it's for being the one a niche posts to; and, for clips
  from before ClipMonster had niches, its video coming from the YouTube charts when only one niche
  takes chart videos. The Library's **Niche** column shows it, with why in its tooltip.
- **Got one wrong?** Select videos in the Library, right-click → **Niche** and pick one, **New
  niche…**, **No niche**, or **Work it out again**. What you pick wins over everything else.
- **On each page:** the Ready page says how many more are waiting in other niches and **Queue N new
  clips here** takes only the shown niche's. On the Queue, numbers are places in the whole queue, and
  moving or sorting a niche's videos only swaps them between the places they already have, so the
  other niches' videos keep their times. The Account page's views, median and both tables count only
  that niche's posts (followers and likes are the whole account's).

## Cleaning up posted videos

**🧹 Clean up…** (on the Library, or **Clean up posted videos…** on the Account page) frees the disk
space your posted videos take. It lists every posted video with the space it frees, you tick the ones
to clear, press **Clean up** and answer the question that follows. Nothing goes before that.

- **A record first.** Each video gets a `.txt` in **Documents › TikTok Manager › Posted videos ›
  <account>** (**Change…** picks another folder), e.g. `2026-10-25 15.00 - He quit live on air.txt`:
  title, account, when and how it was posted, its TikTok link, its latest views, likes, comments and
  shares, the caption, length, format, file, score and, for ClipMonster clips, the channel, source
  link, niche, hook and why it was picked. If the record can't be written, the video is left alone.
- **Then its files go:** the video, its thumbnail, ClipMonster's `.srt` and thumbnail for it (and its
  row in the folder's `clips.json` and `README.md`; a video folder with no clips left goes, but never a
  watched folder or a niche's folder), and the copies **Copy to a folder** made. The phone folder takes
  posted clips out by itself.
- **Safe by default.** Videos not yet seen on TikTok (not linked by a stats refresh) start unticked. A
  file still queued for another account stays. **Posted more than N days ago** keeps recent ones, and
  **Move to the Recycle Bin** lets you change your mind (the space is freed when you empty it).
- **The history stays.** The video's post history and stats stay on the Account page (as *Posted ·
  cleaned up*, right-click → **Open its record**), ClipMonster keeps learning from it, and the Library
  lists it only under **Cleaned up**.

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
| AI rating | The AI's prediction of appeal and hook (optional) |

Weights are adjustable in **Settings → Scoring**.

## Posting

The app opens TikTok Studio in your own Microsoft Edge (or Chrome) using a separate browser profile,
uploads the file, types the caption and hashtags, sets "who can watch" and comment/duet/stitch, and
clicks Post. You log in yourself once per account (**Account → Log in** opens
a normal Edge window; it closes by itself once you're logged in); the app never sees your password.
Leave **Show the browser window** on: TikTok is less likely to block a visible browser, and you can
solve a captcha if one appears.

## Autopilot

Autopilot posts while the app is running, for each account set to **🤖 Autopilot** (on the Queue page,
in **Manage accounts → Posting**, or from the tray icon's **Autopilot** menu). Closing the window keeps it running in the system tray.
Turn on **Settings → Start with Windows** so it carries on after a restart. Only one copy of the app
can run at a time, so nothing gets posted twice.

## Good to know

- TikTok changes its website often. If a browser upload fails, the error and a screenshot (in
  `%LOCALAPPDATA%\TikTokManager\logs`) say which step broke. Check TikTok Studio before retrying, so
  the video isn't posted twice.
- **Test posting** on the Account page walks TikTok Studio's upload page with a 3-second black test
  clip, up to just before Post, then throws the clip away. Nothing is posted. With autopilot on, a
  shorter check (logged in, upload page opens) runs about once a day, never within 30 minutes of a
  post, and warns you if TikTok has changed something.
- Automating TikTok's website is against the spirit of its terms. Keep the pace human (the default
  is 3 posts a day, 2 hours apart).
- Your data: `%APPDATA%\TikTokManager` (library, settings, `accounts.json`; the AI key is
  encrypted with your Windows login; the Live page's activity under `live\`) and
  `%LOCALAPPDATA%\TikTokManager` (thumbnails, logs, and each account's browser profile with its TikTok
  login under `browser-profiles\`; an account carried over from 1.0 keeps using `browser-profile`).
- Every save keeps the previous file as `.bak`, plus a daily copy in `backups\` (the last 7 days). If
  the library, settings or account list is ever damaged, the app loads the newest good copy, keeps the
  damaged file beside it, and tells you.
- If the app closes in the middle of an upload, that video is marked failed rather than queued again,
  because it may already be on TikTok. The next stats refresh marks it posted if it finds it there.
- Unexpected errors are shown, and their details saved in `crashes\`. **Settings → Updates → Copy
  diagnostics** copies the version, setup, recent log and latest error (no keys or logins) for a bug report.
- `TikTokManager.exe --selftest` checks ffmpeg and the browser automation and writes `selftest.txt`.

## Developing

```
pip install -e .[build,dev]
python -m tokmanager          # run from source
python -m pytest              # tests
python build.py               # build dist\TikTokManager\ (+ the installer if Inno Setup 6 is installed)
```

Releasing: write `release-notes/vX.Y.Z.md`, commit it, then `python release.py X.Y.Z`. That bumps the
version, tags and pushes; GitHub Actions (`.github/workflows/release.yml`) builds the zip and installer
on Windows and publishes the release. `python release.py X.Y.Z --local` builds and uploads from this PC
instead (needs a GitHub token with Contents: Read and write).
