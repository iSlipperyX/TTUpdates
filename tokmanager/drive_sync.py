"""Bring in the clips ClipMonster uploads to Google Drive (Settings → Google Drive).

ClipMonster 2.8+ uploads every finished clip to a folder in your Drive ("ClipMonster" unless
you renamed it), in the folders it has on the PC that made it:
ClipMonster/Automation/<niche>/<channel>/<video>/01 - ….mp4. This looks through that folder every
few minutes and downloads the clips TikTok Manager hasn't got to a folder on this PC (the Drive
inbox, Videos\\TikTok Manager\\From Google Drive), in the same folders. The inbox is imported like a
watched folder (ready.watch_roots), so from there on a clip from Drive is like any other: scored,
sorted into its niche, queued, posted and cleaned up.

* A clip already in the library (ClipMonster on this PC made it, and a watched folder brought it
  in) isn't downloaded again. Nor is one you removed from the library, one Clean up cleared, or
  one ClipMonster's quality check rejected.
* Each clip downloaded is marked in Drive with this PC's name (a private note only the two apps
  see), so TikTok Manager on another PC leaves it alone and a clip is never posted twice.
* The clip's title, score, hook, niche and part series come from the clips.json ClipMonster
  uploads beside its clips (2.10+). For clips uploaded by an earlier ClipMonster, a clips.json is
  made from what Drive has: the file's description (title, score, the video it came from) and its
  folders (the niche).
* Only clips uploaded in the last `drive_days` days are taken (0: all of them), so connecting to a
  Drive full of old clips doesn't fill this PC with them.

Free of GUI code, so it can be tested and run on a worker thread.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import clipmonster, gdrive, safefile
from .config import Settings, app_dir
from .media import VIDEO_EXTS
from .store import Store, _key

INBOX_NAME = "From Google Drive"
INDEX = "clips.json"
SKIP_DIRS = {clipmonster.REJECTED_DIR.lower(), ".downloads"}
ROOM = 512 * 1024 * 1024  # left free on the disk after a download
PC_PROP, AT_PROP = "tm_pc", "tm_at"  # appProperties: the PC whose TikTok Manager took the clip, and when
MADE_BY = "TikTok Manager, from Google Drive"  # clips.json files made here say so

Log = Callable[[str], None]


# ---------------------------------------------------------------- settings

def default_inbox() -> Path:
    return Path.home() / "Videos" / "TikTok Manager" / INBOX_NAME


def inbox(s: Settings) -> Path:
    return Path(s.drive_inbox).expanduser() if s.drive_inbox.strip() else default_inbox()


def client(s: Settings) -> tuple[str, str, bool]:
    """(client ID, secret, borrowed from ClipMonster): the OAuth client typed here, else ClipMonster's on
    this PC. It must be from the Cloud project ClipMonster uploads with, or the clips can't be seen."""
    cid, secret = s.drive_client_id.strip(), s.drive_client_secret.strip()
    if cid and secret:
        return cid, secret, False
    cm_id, cm_secret = clipmonster.drive_client()
    if cm_id and cm_secret and (not cid or cid == cm_id):
        return cm_id, cm_secret, True
    return cid, secret, False


def folder_name(s: Settings) -> str:
    """The folder in Drive that ClipMonster uploads to."""
    return (s.drive_folder.strip() or clipmonster.drive_folder() or "ClipMonster").strip("/\\")


def problem(s: Settings) -> str:
    """'' when it's set up to bring clips in, else what's missing."""
    cid, secret, _ = client(s)
    if not cid or not secret:
        return ("Google Drive needs the OAuth client ID and secret ClipMonster uses (ClipMonster → Settings → "
                "Upload). Paste them in Settings → Google Drive.")
    if gdrive.load_account(cid) is None:
        return "Sign in to Google Drive in Settings → Google Drive."
    return ""


def ready_to_sync(s: Settings) -> bool:
    return bool(s.drive_on) and not problem(s)


def this_pc() -> str:
    return (os.environ.get("COMPUTERNAME") or platform.node() or "this PC").strip()[:60]


# ---------------------------------------------------------------- what's in Drive

@dataclass
class Remote:
    id: str
    name: str
    folders: list[str]  # the folders it's in below ClipMonster's folder
    parent: str  # its folder's id
    size: int = 0
    md5: str = ""
    created: float = 0.0
    modified: float = 0.0
    description: str = ""
    props: dict = field(default_factory=dict)

    @property
    def taken_by(self) -> str:
        return str(self.props.get(PC_PROP) or "")


def _time(text) -> float:
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _remote(f: dict, folders: list[str]) -> Remote:
    try:
        size = int(f.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    props = f.get("appProperties") if isinstance(f.get("appProperties"), dict) else {}
    return Remote(str(f["id"]), str(f.get("name") or ""), folders, str((f.get("parents") or [""])[0]), size,
                  str(f.get("md5Checksum") or ""), _time(f.get("createdTime")), _time(f.get("modifiedTime")),
                  str(f.get("description") or ""), props)


def walk(files: list[dict], top: str) -> tuple[list[Remote], dict[str, Remote]]:
    """(the videos below the folder `top`, the clips.json in each of its folders by folder id), from
    one listing of everything. Folders ClipMonster keeps rejected clips in are left out."""
    by_id = {str(f["id"]): f for f in files}
    paths: dict[str, list[str] | None] = {top: []}

    def path_of(folder: str, seen: tuple = ()) -> list[str] | None:
        if folder in paths:
            return paths[folder]
        f = by_id.get(folder)
        if f is None or f.get("mimeType") != gdrive.FOLDER_TYPE or folder in seen:
            paths[folder] = None
            return None
        up = path_of(str((f.get("parents") or [""])[0]), seen + (folder,))
        paths[folder] = None if up is None else up + [str(f.get("name") or "")]
        return paths[folder]

    videos: list[Remote] = []
    indexes: dict[str, Remote] = {}
    for f in files:
        if f.get("mimeType") == gdrive.FOLDER_TYPE:
            continue
        parent = str((f.get("parents") or [""])[0])
        folders = path_of(parent)
        if folders is None or any(p.lower() in SKIP_DIRS for p in folders):
            continue
        name = str(f.get("name") or "")
        if name == INDEX:
            indexes[parent] = _remote(f, folders)
        elif Path(name).suffix.lower() in VIDEO_EXTS:
            videos.append(_remote(f, folders))
    return videos, indexes


# ---------------------------------------------------------------- names on this PC

def _safe(part: str) -> str:
    """One folder or file name from Drive as a Windows name (ClipMonster's are already fine)."""
    part = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part).strip().rstrip(". ")
    return part if part and part not in (".", "..") else "_"


def local_path(root: Path, r: Remote) -> Path:
    return root.joinpath(*[_safe(p) for p in r.folders], _safe(r.name))


# ---------------------------------------------------------------- clips.json for clips from an older ClipMonster

_SCORE = re.compile(r"^Score (\d+(?:\.\d+)?)/100\.?\s*(.*)$")
_FROM = re.compile(r"^From:\s*(.*?)\s*(https?://\S+)?\s*$")


def describe(text: str) -> dict:
    """What ClipMonster's upload description says (uploads._describe): title, score and reason, the
    video it came from, and where in it."""
    out: dict = {}
    for n, line in enumerate(l.strip() for l in text.splitlines()):
        if not line:
            continue
        m = _SCORE.match(line)
        if m:
            out["score"], out["reason"] = float(m.group(1)), m.group(2).strip()
        elif line.startswith("From:"):
            m = _FROM.match(line)
            if m:
                out["source_title"], out["source"] = m.group(1).strip(), (m.group(2) or "").strip()
        elif line.startswith("At:"):
            out["watch"] = line[3:].strip()
        elif n == 0:
            out["title"] = line
    return out


def _niche_of(folders: list[str]) -> tuple[str, str, str]:
    """(niche, niche id, TikTok account) for a clip in Automation/<niche>/…, using ClipMonster's niches
    on this PC (if it's here) for the id and the account."""
    if len(folders) < 2 or folders[0].lower() != "automation":
        return "", "", ""
    name = folders[1]
    try:
        from .niches import clipmonster_info, fold

        for n in clipmonster_info().niches:
            if fold(n.name) == fold(name) or fold(_safe(n.name)) == fold(name):
                nid = "" if n.key.startswith("name:") else n.key
                return n.name, nid, n.account
    except Exception:
        pass
    return name, "", ""


def made_index(videos: list[Remote], folder_id: str, earlier: dict | None = None) -> dict:
    """A clips.json, as ClipMonster writes them, for the clips in one Drive folder, from their
    descriptions. Clips listed in an `earlier` one made here stay listed."""
    clips = {str(c.get("file")): c for c in (earlier or {}).get("clips") or [] if isinstance(c, dict)}
    first: dict = {}
    for r in sorted(videos, key=lambda r: r.name):
        d = describe(r.description)
        first = first or d
        clip = {"file": _safe(r.name), "title": d.get("title") or Path(r.name).stem}
        if "score" in d:
            clip["score"], clip["reason"] = d["score"], d.get("reason", "")
        if d.get("watch"):
            clip["watch"] = d["watch"]
        clips[clip["file"]] = clip
    niche, niche_id, account = _niche_of(videos[0].folders) if videos else ("", "", "")
    source = first.get("source", "")
    return {"made_by": MADE_BY,
            "video": {"id": f"gdrive:{folder_id}", "title": first.get("source_title", ""), "url": source,
                      "channel": videos[0].folders[-2] if videos and len(videos[0].folders) >= 2 else ""},
            "source": source,
            "automation": {"niche": niche, "niche_id": niche_id, "account": account},
            "clips": sorted(clips.values(), key=lambda c: c["file"])}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_local(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- what was done before

def state_path() -> Path:
    return app_dir() / "google-drive-sync.json"


def load_state() -> dict:
    data = safefile.load(state_path(), "the list of clips from Google Drive")
    data = data if isinstance(data, dict) else {}
    for key in ("files", "indexes", "last"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def save_state(data: dict) -> None:
    safefile.save(state_path(), data, indent=1)


@dataclass
class Result:
    """What one look at Drive found and did."""

    at: float = 0.0
    in_drive: int = 0  # clips in ClipMonster's Drive folder (not counting rejected ones)
    downloaded: list[str] = field(default_factory=list)  # names, this time
    updated: list[str] = field(default_factory=list)  # downloaded again: ClipMonster exported them again
    updated_paths: list[str] = field(default_factory=list)  # where those are, to score them again
    here: int = 0  # already on this PC (downloaded before, or ClipMonster made them here)
    taken: dict[str, int] = field(default_factory=dict)  # PC name -> clips another PC's TikTok Manager took
    removed: int = 0  # you removed them from the library, so they stay out
    older: int = 0  # uploaded before the "last N days" window, never taken
    failed: list[str] = field(default_factory=list)  # "name: why"
    problem: str = ""  # what stops it altogether (signed out, no folder, disk full)
    bytes: int = 0
    folder: str = ""

    def text(self) -> str:
        if self.problem:
            return self.problem
        bits = []
        if self.downloaded:
            bits.append(f"{len(self.downloaded)} new clip{'s' * (len(self.downloaded) != 1)} downloaded")
        if self.updated:
            bits.append(f"{len(self.updated)} updated")
        bits.append(f"{self.here} already here")
        for pc, n in sorted(self.taken.items()):
            bits.append(f"{n} taken by {pc}")
        if self.older:
            bits.append(f"{self.older} older clip{'s' * (self.older != 1)} left in Drive")
        if self.removed:
            bits.append(f"{self.removed} you removed")
        if self.failed:
            bits.append(f"{len(self.failed)} couldn't download (last: {self.failed[-1]})")
        return f"{self.in_drive} clip{'s' * (self.in_drive != 1)} in Drive: " + ", ".join(bits) + "."

    def to_json(self) -> dict:
        return {"at": self.at, "text": self.text(), "problem": self.problem, "downloaded": len(self.downloaded),
                "in_drive": self.in_drive, "failed": self.failed[-3:]}


def last(state: dict | None = None) -> dict:
    """The last look's {"at", "text", "problem", …}, or {} before the first."""
    return (state or load_state()).get("last") or {}


# ---------------------------------------------------------------- the look

def _library(store: Store) -> tuple[dict[str, object], dict[tuple[str, str], list], set[str]]:
    """(entry by path key, entries by (video folder, file name), removed paths)."""
    with store.lock:
        by_path = {_key(i.path): i for i in store.items.values() if i.path}
        by_clip: dict[tuple[str, str], list] = {}
        for i in store.items.values():
            if i.path:
                p = Path(i.path)
                by_clip.setdefault((p.parent.name.lower(), p.name.lower()), []).append(i)
        return by_path, by_clip, set(store.ignored)


def _same_clip(r: Remote, by_clip: dict[tuple[str, str], list]) -> bool:
    """The library has this clip from somewhere else (ClipMonster's own folder on this PC)."""
    parent = _safe(r.folders[-1]).lower() if r.folders else ""
    for it in by_clip.get((parent, _safe(r.name).lower()), []):
        try:
            if not r.size or Path(it.path).stat().st_size == r.size:
                return True
        except OSError:
            return True  # cleaned up or moved since: it was here, so it isn't new
    return False


def _room(folder: Path, size: int) -> bool:
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free >= size + ROOM
    except OSError:
        return True


def sync(store: Store, s: Settings, log: Log = lambda _m: None, cancelled: Callable[[], bool] = lambda: False,
         progress: Callable[[str], None] = lambda _m: None, now: float | None = None,
         drive: gdrive.Drive | None = None) -> Result:
    """Download the clips in Drive that TikTok Manager hasn't got (see the top of this file). The
    caller imports the inbox afterwards (ready.import_watched)."""
    now = time.time() if now is None else now
    res = Result(at=now)
    state = load_state()
    try:
        _sync(store, s, log, cancelled, progress, now, drive, res, state)
    except gdrive.DriveError as exc:
        if exc.kind != "cancelled":
            if exc.kind in ("sign_in", "setup", "disk") or not res.in_drive:
                res.problem = str(exc)
            else:
                res.failed.append(str(exc))
    finally:
        state["last"] = res.to_json()
        try:
            save_state(state)
        except OSError:
            pass
    return res


def _sync(store, s, log, cancelled, progress, now, drive, res: Result, state: dict) -> None:
    why = problem(s)
    if why:
        res.problem = why
        return
    cid, secret, _ = client(s)
    drive = drive or gdrive.Drive(cid, secret)
    name = folder_name(s)
    res.folder = name
    progress("Looking in Google Drive")
    top = drive.find_top(name)
    if not top:
        res.problem = (f"There's no “{name}” folder in your Drive that TikTok Manager can see. Either "
                       "ClipMonster hasn't uploaded a clip yet, or the OAuth client here is from a different "
                       "Google Cloud project than ClipMonster's (Google only shows an app the files made with "
                       "its own project's clients).")
        return
    videos, indexes = walk(drive.everything(), top)
    videos.sort(key=lambda r: (-r.created, r.folders, r.name))
    res.in_drive = len(videos)
    root = inbox(s)
    files: dict = state["files"]
    by_path, by_clip, ignored = _library(store)
    pc = this_pc()
    cutoff = now - s.drive_days * 86400 if s.drive_days > 0 else 0.0
    by_folder: dict[str, list[Remote]] = {}
    for r in videos:
        by_folder.setdefault(r.parent, []).append(r)
    ready_folders: set[str] = set()

    def index_for(r: Remote) -> None:
        """Put the folder's clips.json on this PC before its first clip arrives, so the clip is
        imported with its title and score (never without them)."""
        if r.parent in ready_folders:
            return
        ready_folders.add(r.parent)
        dest = local_path(root, r).with_name(INDEX)
        known = state["indexes"].get(r.parent) or {}
        theirs = indexes.get(r.parent)
        if theirs is not None:  # ClipMonster's own (2.10+)
            if known.get("md5") == theirs.md5 and theirs.md5 and dest.is_file():
                return
            _write(dest, drive.read(theirs.id))
            state["indexes"][r.parent] = {"md5": theirs.md5, "path": str(dest)}
            return
        local = _read_local(dest)
        if local and local.get("made_by") != MADE_BY:
            return  # one that came from ClipMonster earlier: keep it
        data = made_index(by_folder.get(r.parent, [r]), r.parent, local)
        _write(dest, json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8"))
        state["indexes"][r.parent] = {"md5": "", "path": str(dest)}

    for r in videos:
        if cancelled():
            raise gdrive.DriveError("Stopped.", "cancelled")
        dest = local_path(root, r)
        key = _key(dest)
        rec = files.get(r.id)
        item = by_path.get(key)
        if rec and rec.get("state") == "downloaded":
            changed = r.md5 and rec.get("md5") and r.md5 != rec.get("md5")
            fresh = item is None or getattr(item, "status", "") in ("new", "queued", "failed")
            if not (changed and fresh and dest.is_file()):
                res.here += 1
                continue
            # ClipMonster exported it again and it isn't posted yet: fetch the new one below
        elif rec and rec.get("state") == "here":
            res.here += 1
            continue
        elif item is not None or dest.is_file():  # downloaded before this list was kept
            files[r.id] = {"state": "downloaded", "path": str(dest), "md5": r.md5, "size": r.size, "at": now}
            res.here += 1
            continue
        if key in ignored:
            res.removed += 1
            continue
        old = not rec and cutoff and r.created < cutoff
        if old:  # outside the "last N days": left in Drive (and not marked, so no flood of notes on a first look)
            if _same_clip(r, by_clip):
                res.here += 1
            else:
                res.older += 1
            continue
        if not rec and _same_clip(r, by_clip):
            files[r.id] = {"state": "here", "path": "", "md5": r.md5, "size": r.size, "at": now}
            res.here += 1
            if r.taken_by != pc:
                try:  # so TikTok Manager on another PC doesn't take it too
                    drive.set_properties(r.id, {PC_PROP: pc, AT_PROP: str(int(now))})
                except gdrive.DriveError as exc:
                    if exc.kind in ("sign_in", "setup"):
                        raise
            continue
        if r.taken_by and r.taken_by != pc:
            res.taken[r.taken_by] = res.taken.get(r.taken_by, 0) + 1
            continue
        if not _room(dest.parent, r.size):
            res.problem = (f"There isn't enough free space for {r.name} on the drive with {root}. Free some up "
                           "(Clean up posted videos helps), or pick another folder in Settings → Google Drive.")
            return
        again = bool(rec)
        try:
            index_for(r)
            if r.taken_by != pc:
                drive.set_properties(r.id, {PC_PROP: pc, AT_PROP: str(int(now))})
            label = f"{'Updating' if again else 'Downloading'} {r.name}"
            progress(label)
            drive.download(r.id, dest, r.size, lambda f, label=label: progress(f"{label} ({f:.0%})"), cancelled,
                           md5=r.md5)
            when = r.modified or r.created or now
            try:
                os.utime(dest, (when, when))  # it's finished: no need to wait for it to "settle"
            except OSError:
                pass
        except gdrive.DriveError as exc:
            if exc.kind in ("sign_in", "setup", "disk", "cancelled"):
                raise
            if exc.kind == "gone":  # deleted in Drive while looking
                continue
            res.failed.append(f"{r.name}: {exc}")
            log(f"⚠ Couldn't download {r.name} from Google Drive: {exc}")
            continue
        except OSError as exc:
            res.failed.append(f"{r.name}: {exc.strerror or exc}")
            log(f"⚠ Couldn't save {r.name} from Google Drive to {dest.parent}: {exc.strerror or exc}")
            continue
        files[r.id] = {"state": "downloaded", "path": str(dest), "md5": r.md5, "size": r.size, "at": now}
        res.bytes += r.size
        (res.updated if again else res.downloaded).append(r.name)
        if again:
            res.updated_paths.append(str(dest))
        save_state(state)  # so a crash halfway never downloads it twice
