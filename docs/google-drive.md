# Bringing in clips from Google Drive

ClipMonster 2.8 and later can upload every clip it finishes to your Google Drive (ClipMonster →
**Settings → Upload**). TikTok Manager can then download the clips it hasn't got from there, by
itself, every few minutes. They come in like clips from a watched folder: scored, sorted into their
niche, queued, posted and cleaned up.

You need this when TikTok Manager runs on a different PC from ClipMonster, or when ClipMonster's
**Clean up** clears its own copies of clips that are safely in Drive. When both apps are on the same
PC and TikTok Manager watches ClipMonster's Automation folder, you don't need it (it's harmless,
though: clips already in TikTok Manager are never downloaded again).

## Set it up

There's nothing new to make in Google Cloud. TikTok Manager signs in with the **same OAuth client**
ClipMonster uses (see [ClipMonster's guide](https://github.com/iSlipperyX/ClipMostr/blob/main/docs/google-drive-setup.md)
if it isn't uploading yet).

**On the PC ClipMonster runs on:**

1. Open TikTok Manager → **Settings → Google Drive**.
2. Leave the client ID and secret empty: ClipMonster's are used.
3. Click **Sign in to Google Drive…**, pick your account and allow access. Google may say "Google
   hasn't verified this app": click **Continue** (it's your own app).
4. Click **Save and check now**.

**On another PC:**

1. In ClipMonster (on its PC), open **Settings → Upload** and copy the **Google OAuth client ID** and
   **client secret**.
2. In TikTok Manager on the other PC, open **Settings → Google Drive** and paste them.
3. If you renamed ClipMonster's folder in Drive, type the same name under **Folder in your Drive**.
4. Sign in and click **Save and check now**.

It has to be ClipMonster's own client. Google only lets an app see the Drive files made by its own
Google Cloud project (the *drive.file* permission), so a client from another project sees an empty
Drive.

## How it works

- Clips are downloaded to **Videos\TikTok Manager\From Google Drive** (change it under **Download clips
  to**), in the same folders as in Drive: `Automation\<niche>\<channel>\<video>\01 - ….mp4`. A
  download that's cut off carries on where it stopped, and a clip only shows up once it's all there.
- Only clips uploaded in the last 14 days are taken (change it, or pick **Any time**), so connecting
  to a Drive full of old clips doesn't fill the PC.
- Not downloaded: clips already in TikTok Manager (also from ClipMonster's folder on this PC), clips
  you removed from the Library, clips Clean up cleared, and clips ClipMonster's quality check
  rejected.
- Each clip TikTok Manager takes is marked in Drive with the PC's name (a private note only the two
  apps can see). TikTok Manager on another PC leaves those clips alone, so a clip is never posted
  from two PCs.
- A clip ClipMonster exports again is downloaded again while it's still waiting to be posted.
- Each clip's title, score, hook, niche and part series come from the `clips.json` ClipMonster
  uploads beside its clips. Clips uploaded by an older ClipMonster still get their title, score,
  source video and niche (from the file's description in Drive and its folders).
- The Ready page has a **☁ Google Drive** line with the last look, and says what's wrong if
  something stops it. **Check now** looks straight away.
- **Clean up posted videos** clears downloaded clips from the PC once they're posted. The copies in
  Drive stay; ClipMonster looks after those.

## If something goes wrong

| TikTok Manager says | Fix |
|---|---|
| *needs the OAuth client ID and secret ClipMonster uses* | Paste them from ClipMonster → Settings → Upload. |
| *Google doesn't accept the OAuth client ID and secret* | Copy them again. The client must be a **Desktop app** client. |
| *There's no "ClipMonster" folder in your Drive that TikTok Manager can see* | ClipMonster hasn't uploaded anything yet, the folder name differs (Folder in your Drive), or the client is from another Google Cloud project. |
| *The Google Drive API isn't turned on* | Step 2 of ClipMonster's guide. |
| *Google has signed TikTok Manager out* after about a week | The app is still in *Testing* in Google Cloud. Publish it (step 3.5 of ClipMonster's guide) and sign in again. |
| *There isn't enough free space* | Free some up (Clean up posted videos helps), or pick another download folder. |

**Sign out** only forgets the sign-in on this PC. It doesn't cancel it at Google, because that would
sign ClipMonster out too. To remove both, use [myaccount.google.com/connections](https://myaccount.google.com/connections).
