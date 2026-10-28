"""Clean up: free the disk space posted videos take, keeping a .txt record of each one.

plan() only looks; apply() clears the videos you tick. For each one it first writes a record
(title, account, when and how it was posted, its TikTok link and latest numbers, caption, length,
format, file, score and where ClipMonster found it) to the records folder, then removes:

* the video file
* its thumbnail in the app's cache
* what ClipMonster keeps beside a clip: its .srt and thumbnail, and its row in the folder's
  clips.json and README.md; a video folder with no clips left goes altogether, and so do the
  folders that leaves empty, except a watched folder and the folders right inside one (a niche's
  folder, say), which ClipMonster and TikTok Manager expect to stay
* the copies "Copy to a folder" made of it, with their caption files (the phone folder looks after
  itself: posted clips leave it at its next update)

A video whose record can't be written is left alone. A file another entry still needs (the same
video queued for another account, or a posted copy you didn't tick) stays. The video's entry stays
too, marked cleaned up, so its post history, stats and what ClipMonster learns from it (results.py)
carry on; the Library stops listing it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import clipmonster
from .accounts import Account, Accounts
from .config import Settings, thumbs_dir
from .media import VIDEO_EXTS
from .ready import default_ready_folder, post_text, safe_name
from .store import Store, VideoItem, _key
from .tiktok import compose_caption

DAY = 86400.0
RECORDS_NAME = "Posted videos"
HOW = {"browser": "posted by TikTok Manager", "api": "posted by TikTok Manager", "api_draft": "sent by TikTok Manager",
       "hand": "posted by you (marked on the Ready page)", "seen": "posted by you (found on TikTok by the live view)"}


def documents_dir() -> Path:
    """Windows' Documents folder, wherever it is (OneDrive moves it), else ~/Documents."""
    if os.name == "nt":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
                return Path(buf.value)
        except (OSError, AttributeError):
            pass
    return Path.home() / "Documents"


def default_records_folder() -> Path:
    return documents_dir() / "TikTok Manager" / RECORDS_NAME


def records_folder(s: Settings) -> Path:
    return Path(s.cleanup_folder).expanduser() if s.cleanup_folder else default_records_folder()


def size_text(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


@dataclass
class Target:
    """One posted video the clean-up can clear."""

    item_id: str
    title: str
    account_id: str
    account: str  # the account's label ("removed account" when it's gone)
    posted_at: float
    on_tiktok: bool  # linked to a video on the account, so it's surely up
    origin: str  # "ClipMonster clip", "from a watched folder" or "added by you"
    path: str  # the video file
    files: list[Path] = field(default_factory=list)  # what would be removed
    size: int = 0  # bytes those take
    kept: list[str] = field(default_factory=list)  # files that stay, and why

    @property
    def ticked(self) -> bool:
        """Ticked to start with: only videos seen on TikTok."""
        return self.on_tiktok


@dataclass
class Plan:
    targets: list[Target] = field(default_factory=list)
    folder: Path = field(default_factory=default_records_folder)
    roots: list[Path] = field(default_factory=list)  # watched and ClipMonster folders: never removed
    waiting: int = 0  # posted videos younger than the age picked

    @property
    def size(self) -> int:
        return sum(t.size for t in self.targets)


@dataclass
class Result:
    cleaned: int = 0
    freed: int = 0  # bytes
    files: int = 0
    records: list[Path] = field(default_factory=list)
    folder: Path | None = None  # where the records went
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, why)
    kept: int = 0  # videos whose file stayed because something else needs it

    def summary(self, trashed: bool = False) -> str:
        if not self.cleaned:
            return "Nothing was cleaned up."
        text = f"Cleaned up {self.cleaned} posted video{'s' * (self.cleaned != 1)}"
        if trashed:
            text += f": {size_text(self.freed)} moved to the Recycle Bin (emptying it frees the space)"
        else:
            text += f": {size_text(self.freed)} freed"
        return f"{text}. A record of each is in {self.folder}." if self.folder else f"{text}."


# --- looking --------------------------------------------------------------------------


def eligible(it: VideoItem, cutoff: float) -> bool:
    return it.status == "posted" and not it.cleaned_at and (it.posted_at or 0) <= cutoff


def plan(store: Store, accounts: Accounts | list[Account], s: Settings, older_than_days: float = 0,
         now: float | None = None) -> Plan:
    """What a clean-up of the posted videos at least `older_than_days` old would clear. Only looks."""
    now = time.time() if now is None else now
    cutoff = now - older_than_days * DAY
    accounts = list(accounts)
    p = Plan(folder=records_folder(s), roots=_roots(s))
    with store.lock:
        items = list(store.items.values())
    posted = [i for i in items if i.status == "posted" and not i.cleaned_at]
    chosen = {i.id for i in posted if eligible(i, cutoff)}
    p.waiting = len(posted) - len(chosen)
    look = _Look(items)
    for it in sorted((i for i in posted if i.id in chosen), key=lambda i: i.posted_at or 0, reverse=True):
        account = next((a for a in accounts if a.id == it.account), None)
        t = Target(item_id=it.id, title=it.headline, account_id=it.account,
                   account=account.label if account else "removed account",
                   posted_at=it.posted_at or 0, on_tiktok=bool(it.tiktok_id), origin=_origin(it), path=it.path)
        t.files, t.kept = _files(it, look, chosen, account, s)
        t.size = sum(_size(f) for f in t.files)
        p.targets.append(t)
    return p


def _origin(it: VideoItem) -> str:
    if it.source_channel or it.source_url or it.niche or it.hook:
        return "ClipMonster clip"
    return "from a watched folder" if it.imported_by == "watch" else "added by you"


def _roots(s: Settings) -> list[Path]:
    roots = [Path(f).expanduser() for f in s.watch_folders if f]
    if clipmonster.installed():
        roots += clipmonster.output_folders()
    return roots


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class _Look:
    """What one look needs again and again: which entries use each file, and folders already read."""

    def __init__(self, items: list[VideoItem]):
        self.users: dict[tuple[str, str], list[VideoItem]] = {}
        for i in items:
            if i.cleaned_at:
                continue
            for attr in ("path", "thumb"):
                if getattr(i, attr):
                    self.users.setdefault((attr, _key(getattr(i, attr))), []).append(i)
        self.indexes: dict[Path, dict | None] = {}  # folder -> its clips.json
        self.listings: dict[Path, list[Path]] = {}  # "Copy to a folder" account folder -> the files in it

    def needed(self, it: VideoItem, chosen: set[str], attr: str) -> VideoItem | None:
        """Another entry, not being cleaned up with this one, that uses the same file."""
        return next((o for o in self.users.get((attr, _key(getattr(it, attr))), [])
                     if o.id != it.id and o.id not in chosen), None)


def _files(it: VideoItem, look: _Look, chosen: set[str], account: Account | None,
           s: Settings) -> tuple[list[Path], list[str]]:
    """The files clearing `it` removes, and the ones that stay with why."""
    files: list[Path] = []
    kept: list[str] = []
    video = Path(it.path)
    if it.path and video.is_file() and video.suffix.lower() in VIDEO_EXTS:
        other = look.needed(it, chosen, "path")
        if other is None:
            files.append(video)
            files += [c for c in _clip_companions(video, look) if c.is_file()]
            files += _exported_copies(it, account, s, video, look)
        else:
            kept.append(f"The video stays: it's also {_what(other)}.")
    thumb = Path(it.thumb) if it.thumb else None
    if thumb and thumb.is_file() and _inside(thumb, thumbs_dir()) and look.needed(it, chosen, "thumb") is None:
        files.append(thumb)
    return files, kept


def _what(other: VideoItem) -> str:
    if other.status == "posted":
        return "a posted video you didn't pick"
    return {"queued": "queued", "uploading": "being posted", "failed": "in a queue"}.get(other.status, "in the Library")


def _inside(path: Path, folder: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(folder))
        return True
    except ValueError:
        return False


def _clip_record(video: Path, look: _Look) -> dict | None:
    """The clip's entry in ClipMonster's clips.json beside it, if ClipMonster made it."""
    if video.parent not in look.indexes:
        try:
            look.indexes[video.parent] = json.loads((video.parent / "clips.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            look.indexes[video.parent] = None
    data = look.indexes[video.parent]
    clips = data.get("clips") if isinstance(data, dict) else None
    for rec in clips if isinstance(clips, list) else []:
        if isinstance(rec, dict) and isinstance(rec.get("file"), str) and \
                _key(video.parent / rec["file"]) == _key(video):
            return rec
    return None


def _clip_companions(video: Path, look: _Look) -> list[Path]:
    """ClipMonster's subtitles and thumbnail for the clip (the same files its own Clean up takes)."""
    rec = _clip_record(video, look)
    if rec is None:
        return []
    out = [video.with_suffix(".srt")]
    if isinstance(rec.get("rank"), int):
        out.append(video.parent / "thumbnails" / f"{rec['rank']:02d}.jpg")
    return out


def _exported_copies(it: VideoItem, account: Account | None, s: Settings, video: Path, look: _Look) -> list[Path]:
    """Copies "Copy to a folder" (ready.export_ready) made: <folder>/<account>/<date>/NN - <name>.mp4 and
    its .txt, found by name and size."""
    if account is None:
        return []
    base = Path(s.ready_folder or default_ready_folder()).expanduser() / safe_name(account.label, 40)
    if base not in look.listings:
        try:
            look.listings[base] = [f for day in base.iterdir() if day.is_dir() for f in day.iterdir()]
        except OSError:
            look.listings[base] = []
    size = _size(video)
    pattern = re.compile(r"\d{2} - " + re.escape(safe_name(it.headline)) + re.escape(video.suffix.lower()))
    out = []
    for f in look.listings[base]:
        if pattern.fullmatch(f.name) and _size(f) == size:
            out.append(f)
            if f.with_suffix(".txt").is_file():
                out.append(f.with_suffix(".txt"))
    return out


# --- the record -----------------------------------------------------------------------


def _when(t: float | None) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%a %d %b %Y, %H:%M") if t else "not known"


def _length(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _num(n: int) -> str:
    return f"{n:,}"


def record_text(store: Store, it: VideoItem, account: Account | None, s: Settings, now: float,
                kept: list[str] | None = None) -> str:
    """What the .txt for a cleaned-up video says."""
    v = store.video_for(it)
    lines = ["TikTok Manager: posted video record", "=" * 35, ""]

    def row(name: str, value) -> None:
        if value not in (None, ""):
            lines.append(f"{name + ':':<14}{value}")

    row("Title", it.headline)
    if it.title and it.title != it.headline:
        row("Clip name", it.title)
    if account is not None:
        handle = f"@{account.username}" if account.username else ""
        named = account.name and handle and account.name.lstrip("@").lower() != account.username.lower()
        row("Account", f"{account.name} ({handle})" if named else handle or account.label)
    else:
        row("Account", "an account since removed from TikTok Manager")
    row("Posted", _when(v.create_time if v and v.create_time else it.posted_at)
        + (f" ({HOW[it.posted_via]})" if it.posted_via in HOW else ""))
    row("TikTok link", it.tiktok_url or (v.url if v else "") or "not found on TikTok yet")
    row("TikTok id", it.tiktok_id)
    row("Part", it.part_label)

    caption = v.description if v and v.description else (post_text(it, account, s) if account is not None
                                                            else compose_caption(it, s))
    lines += ["", "Caption" + (" on TikTok:" if v and v.description else " as posted:")]
    lines += [f"  {line}" for line in caption.splitlines() or [""]]

    if v is not None:
        read = v.history[-1][0] if v.history else 0
        lines += ["", "Numbers" + (f" (last read {_when(read)}):" if read else ":"),
                  f"  {_num(v.views)} views · {_num(v.likes)} likes · {_num(v.comments)} comments · "
                  f"{_num(v.shares)} shares" + (f" · {_num(v.saves)} saves" if v.saves else "")]
        if v.views:
            lines.append(f"  Engagement {v.engagement:.1f}%")

    info = it.info or {}
    lines += ["", "Video:"]
    sub = lambda name, value: lines.append(f"  {name + ':':<14}{value}") if value not in (None, "") else None
    sub("Length", _length(it.duration) if it.duration else "")
    if info.get("width") and info.get("height"):
        fps = f", {info['fps']:.0f} fps" if info.get("fps") else ""
        sub("Format", f"{info['width']}×{info['height']}{fps}")
    sub("File size", size_text(info["size_bytes"]) if info.get("size_bytes") else "")
    sub("File", it.path)
    sub("Score", f"{it.total:.0f} / 100" if it.total is not None else "")
    if it.ai.get("score") is not None:
        sub("AI rating", f"{it.ai['score']} / 100")
    sub("Hashtags", it.hashtags)

    if it.source_channel or it.source_url or it.niche or it.hook:
        lines += ["", "From ClipMonster:"]
        sub("Channel", it.source_channel)
        sub("Source", it.source_url)
        sub("Niche", it.niche)
        sub("Hook", it.hook)
        sub("Picked for", it.source_reason)
        sub("Clip score", f"{it.source_score:.0f} / 100" if it.source_score is not None else "")
        sub("Stands alone", f"{it.source_rating} / 10" if it.source_rating is not None else "")
        sub("Allowed by", it.source_permission)
        sub("Credit", it.credit)

    lines += ["", f"Added to TikTok Manager {_when(it.added_at)}."]
    if kept:
        lines.append(f"Cleaned up {_when(now)}. " + " ".join(kept))
    else:
        lines.append(f"Cleaned up {_when(now)}: the video and its copies were deleted to free space.")
    return "\n".join(lines + [""])


def _record_path(folder: Path, it: VideoItem, account_label: str) -> Path:
    if it.record:  # a clean-up that couldn't remove every file wrote it already
        return Path(it.record)
    when = dt.datetime.fromtimestamp(it.posted_at or it.added_at)
    sub = folder / safe_name(account_label, 40)
    base = f"{when:%Y-%m-%d %H.%M} - {safe_name(it.headline)}"
    path, n = sub / f"{base}.txt", 2
    while path.exists():
        path, n = sub / f"{base} ({n}).txt", n + 1
    return path


# --- clearing -------------------------------------------------------------------------


def _unlink(path: Path) -> None:
    path.unlink()


def apply(store: Store, accounts: Accounts | list[Account], s: Settings, p: Plan, ids: list[str] | set[str],
          remove: Callable[[Path], None] | None = None, now: float | None = None) -> Result:
    """Clear the plan's videos whose ids are in `ids`. `remove` deletes one file (or moves it to the
    Recycle Bin) and raises OSError when it can't. Marks each cleared entry; the caller saves."""
    remove = remove or _unlink
    now = time.time() if now is None else now
    accounts = list(accounts)
    r = Result()
    with store.lock:
        look = _Look(list(store.items.values()))
    by_id = {t.item_id for t in p.targets}
    chosen = {i for i in ids if i in by_id}
    touched: dict[Path, set[str]] = {}
    for item_id in [t.item_id for t in p.targets if t.item_id in chosen]:
        it = store.get(item_id)
        if it is None or it.status != "posted" or it.cleaned_at:
            continue
        account = next((a for a in accounts if a.id == it.account), None)
        label = account.label if account else "Removed account"
        files, kept = _files(it, look, chosen, account, s)
        # The record first: no record, nothing deleted.
        try:
            record = _record_path(p.folder, it, label)
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(record_text(store, it, account, s, now, kept), encoding="utf-8")
        except OSError as exc:
            r.failed.append((it.headline, f"couldn't write its record: {exc}"))
            continue
        r.records.append(record)
        r.folder = p.folder
        r.kept += bool(kept)
        video = Path(it.path)
        failed: list[Path] = []
        for f in files:
            size = _size(f)
            try:
                if f.exists():
                    remove(f)
            except OSError as exc:
                r.failed.append((str(f), str(exc) or exc.__class__.__name__))
                failed.append(f)
                continue
            r.freed += size
            r.files += 1
        if video in files and video not in failed:
            touched.setdefault(video.parent, set()).add(video.name)
        for d in {f.parent for f in files}:
            _rmdir_empty_copies(d, s, account)
        with store.lock:
            it.record = str(record)
            if it.thumb and not Path(it.thumb).exists():
                it.thumb = ""
            if failed:  # it stays on the list, so the next clean-up tries those files again
                continue
            it.cleaned_at = now
        r.cleaned += 1
    for folder, names in touched.items():
        try:
            _tidy(folder, names, p.roots, remove)
        except OSError as exc:
            r.failed.append((str(folder), str(exc) or exc.__class__.__name__))
    return r


def _rmdir_empty_copies(day: Path, s: Settings, account: Account | None) -> None:
    """A "Copy to a folder" day folder (and the account's folder above it) left empty goes."""
    if account is None:
        return
    base = Path(s.ready_folder or default_ready_folder()).expanduser() / safe_name(account.label, 40)
    if _key(day.parent) != _key(base):
        return
    if _rmdir(day):
        _rmdir(base)


def _tidy(folder: Path, removed: set[str], roots: list[Path], remove: Callable[[Path], None]) -> None:
    """A ClipMonster video folder after clips left it: their rows go from its clips.json and README.md, or,
    with no clips left, its own files go and so do the folders that leaves empty (not a root, nor a
    folder right inside one, like a niche's folder)."""
    index = folder / "clips.json"
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
        return
    gone = {_key(folder / n) for n in removed}
    keep = [rec for rec in data["clips"] if not (isinstance(rec, dict) and isinstance(rec.get("file"), str)
                                                 and _key(folder / rec["file"]) in gone)]
    left = [rec for rec in keep if isinstance(rec, dict) and isinstance(rec.get("file"), str)
            and (folder / rec["file"]).is_file()]
    if left:
        if len(keep) != len(data["clips"]):
            files = {rec["file"] for rec in data["clips"] if rec not in keep}
            data["clips"] = keep
            data["cleaned_up"] = time.time()
            if isinstance(data.get("qc"), dict):  # the counts ClipMonster's Library shows follow
                checks = [rec.get("qc") if isinstance(rec.get("qc"), dict) else {} for rec in keep
                          if isinstance(rec, dict)]
                data["qc"] = {"passed": sum(c.get("ok") is not False for c in checks),
                              "rejected": sum(c.get("ok") is False for c in checks),
                              "warned": sum(c.get("ok") is not False and bool(c.get("warnings")) for c in checks)}
            _rewrite(index, json.dumps(data, indent=2, ensure_ascii=False))
            readme = folder / "README.md"
            try:
                _rewrite(readme, _readme_without(readme.read_text(encoding="utf-8"), files))
            except OSError:
                pass
        return
    # Nothing left to post from this video.
    for f in [index, folder / "README.md", *sorted((folder / "thumbnails").glob("[0-9][0-9].jpg"))]:
        if f.is_file():
            remove(f)
    for d in (folder / "thumbnails", folder / clipmonster.REJECTED_DIR):
        _rmdir(d)
    keep_dirs = {_key(r) for r in roots} | {_key(c) for r in roots if r.is_dir() for c in _subdirs(r)}
    d = folder
    while _key(d) not in keep_dirs and any(_inside(d, r) for r in roots) and _rmdir(d):
        d = d.parent


def _subdirs(root: Path) -> list[Path]:
    try:
        return [c for c in root.iterdir() if c.is_dir()]
    except OSError:
        return []


def _rewrite(path: Path, text: str) -> None:
    """Replace a file's text, keeping its date (ClipMonster's Library lists videos by when they were made)."""
    st = path.stat()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    os.utime(path, (st.st_atime, st.st_mtime))


def _readme_without(text: str, files: set[str]) -> str:
    """A ClipMonster README.md without the table rows of these clip files."""
    out, dropping = [], False
    for line in text.splitlines():
        if re.match(r"\| \d+ \|", line):  # a clip's first row; the rows under it start with "| |"
            dropping = any(f"](<{f}>)" in line for f in files)
        elif not line.startswith("| |"):
            dropping = False
        if not dropping:
            out.append(line)
    return "\n".join(out) + "\n"


def _rmdir(d: Path) -> bool:
    try:
        d.rmdir()
        return True
    except OSError:
        return False
