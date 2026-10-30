"""Ready to upload: the hands-off way in, for people who post to TikTok themselves.

Clips arrive from watched folders (ClipMonster's Automation folder, say), are scored, and go
to an account's queue, which doubles as its "ready to upload" stack. The Ready page shows each
video with its caption ready to copy; you upload it in TikTok and click "Mark as posted".
Reminders say when a video's slot comes round, and a morning summary says what's waiting.

Everything here is free of GUI code so it can be tested and run on worker threads.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import clipmonster, niches, schedule
from .accounts import Account, Accounts
from .config import Settings
from .store import Store, VideoItem
from .tiktok import compose_caption, normalize_hashtags

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def import_watched(store: Store, folders: list[str], now: float | None = None) -> list[VideoItem]:
    """New, finished clips from the watched folders (missing folders are skipped)."""
    roots = [Path(f).expanduser() for f in folders if f and Path(f).expanduser().is_dir()]
    return store.add_paths(roots, watched=True, now=now) if roots else []


def watch_roots(s: Settings) -> list[str]:
    """The folders new clips come in from: the watched folders, and the Google Drive inbox when
    clips are being brought in from Drive (drive_sync.py)."""
    folders = [f for f in s.watch_folders if f]
    if s.drive_on:
        from .drive_sync import inbox

        box = inbox(s)
        try:
            box.mkdir(parents=True, exist_ok=True)  # there before the first clip, so it never reads as missing
        except OSError:
            pass
        if not watched(folders, box):
            folders.append(str(box))
    return folders


def same_folder(a: str | Path, b: str | Path) -> bool:
    """ClipMonster writes "C:/Users/…/TTVIDEOS", Windows' folder picker "C:\\Users\\…\\TTVIDEOS"."""
    def norm(p) -> str:
        return os.path.normcase(os.path.normpath(os.path.expanduser(str(p)))).replace("\\", "/").rstrip("/")
    return norm(a) == norm(b)


def watched(folders: list[str], path: str | Path) -> bool:
    """`path` is one of the watched folders, or inside one."""
    p = Path(os.path.expanduser(str(path)))
    return any(same_folder(p, f) or any(same_folder(parent, f) for parent in p.parents) for f in folders if f)


def clipmonster_unwatched(s: Settings) -> Path | None:
    """ClipMonster's Automation folder when ClipMonster is set up here but that folder isn't watched.

    ClipMonster can be told to save somewhere else at any time, so this is checked again
    whenever the Ready page is shown, not only on first start.
    """
    if not clipmonster.installed():
        return None
    auto = clipmonster.output_folders()[0]
    if not auto.is_dir():  # Automation hasn't made anything yet
        return None
    return None if watched(s.watch_folders, auto) else auto


@dataclass
class FolderReport:
    """What one watched folder holds, as the Ready page tells it."""

    folder: str
    exists: bool = True
    clips: int = 0  # finished videos found (ClipMonster's rejected ones not counted)
    in_app: int = 0  # of those, in TikTok Manager's library (queued, posted or not)
    rejected: int = 0  # set aside by ClipMonster's quality check (its _rejected folders)
    removed: int = 0  # removed from the Library by you, so they stay out
    waiting: int = 0  # still being written (or not finished long enough ago)


def watch_report(store: Store, folders: list[str], now: float | None = None,
                 settle: float = 60.0) -> list[FolderReport]:
    """Count what's in each watched folder and what became of it. Read-only; safe on a worker thread."""
    from .clipmonster import REJECTED_DIR, read_folder_index
    from .media import VIDEO_EXTS
    from .phone import is_copy
    from .store import _key

    now = time.time() if now is None else now
    with store.lock:
        by_path = {_key(i.path): i for i in store.items.values()}
        ignored = set(store.ignored)
    out = []
    for folder in folders:
        if not folder:
            continue
        root = Path(folder).expanduser()
        r = FolderReport(folder=folder, exists=root.is_dir())
        out.append(r)
        if not r.exists:
            continue
        indexes: dict[Path, dict] = {}
        copies: dict[Path, set[str]] = {}
        try:
            files = sorted(f for f in root.rglob("*") if f.suffix.lower() in VIDEO_EXTS
                           and ".downloads" not in f.parts and not f.name.startswith(".") and not is_copy(f, copies))
        except OSError:
            r.exists = False
            continue
        for f in files:
            if REJECTED_DIR in f.relative_to(root).parts:
                r.rejected += 1
                continue
            if f.parent not in indexes:
                indexes[f.parent] = read_folder_index(f.parent)
            meta = indexes[f.parent].get(f.name)
            if meta and meta.get("rejected"):
                r.rejected += 1
                continue
            key = _key(f)
            if key in by_path:
                r.clips += 1
                r.in_app += 1
            elif key in ignored:
                r.clips += 1
                r.removed += 1
            else:
                try:
                    fresh = not meta and now - f.stat().st_mtime < settle
                except OSError:
                    continue
                if fresh:
                    r.waiting += 1
                else:
                    r.clips += 1  # finished but not brought in yet: the next check takes it
    return out


def unqueued_watched(store: Store) -> list[VideoItem]:
    """Clips a watched folder brought in that are still only in the Library (not queued or posted)."""
    with store.lock:
        return [i for i in store.items.values()
                if i.imported_by == "watch" and i.status == "new" and not i.account and i.exists()]


def series_order(items: list[VideoItem]) -> list[VideoItem]:
    """`items` in the same order, except that the parts of a series come together, part 1 first
    (where the first of them was), so they're queued to post one after another."""
    out: list[VideoItem] = []
    done: set[str] = set()
    for it in items:
        if it.id in done:
            continue
        group = sorted((i for i in items if i.series == it.series), key=lambda i: i.part or 0) if it.series else [it]
        out += group
        done.update(i.id for i in group)
    return out


def niche_account(accounts: Accounts | list[Account], item: VideoItem) -> Account | None:
    """The account ClipMonster made the clip for: its niche names a TikTok @username (clips.json
    "automation" → "account"), matched to an account's username without caring about case or "@"."""
    want = item.for_account.strip().lstrip("@").strip().lower()
    if not want:
        return None
    return next((a for a in accounts if a.username and a.username.strip().lstrip("@").lower() == want), None)


def assign(store: Store, accounts: Accounts | list[Account], items: list[VideoItem], target: str,
           index: niches.Index | None = None) -> dict[str, int]:
    """Queue freshly imported videos.

    A clip ClipMonster made for a niche goes to the account that niche names, whatever
    Settings.watch_target says, and a clip in a niche that only one account posts (picked by name in
    its niches, Account.post_niches) goes to that one. The rest go as watch_target says: "library"
    leaves them unqueued, "spread" gives each to the account with the fewest videos waiting, an
    account id queues them all for that account. Returns how many each account got.

    `index`: the library's niches, new clips included (worked out here when not given)."""
    accounts = list(accounts)
    got: dict[str, int] = {}
    if not items or not accounts:
        return got
    if index is None:
        with store.lock:
            index = niches.build(list(store.items.values()), accounts)
    fixed = next((a for a in accounts if a.id == target), None)
    waiting = {a.id: sum(1 for i in store.queue(a.id) if i.status == "queued") for a in accounts}
    # The parts of a series all go to the account part 1 went to, also when they come in later.
    ids = {a.id for a in accounts}
    new = {i.id for i in items}
    with store.lock:
        series_to: dict[str, str] = {i.series: i.account for i in store.items.values()
                                     if i.series and i.account in ids and i.id not in new}
    for it in series_order(items):
        owner = niche_account(accounts, it)
        posts_it = index.accounts_for(index.of(it), accounts)
        if owner is None and len(posts_it) == 1:
            owner = posts_it[0]
        if owner is not None:
            aid = owner.id
        elif fixed is not None:
            aid = fixed.id
        elif target == "spread":
            aid = series_to.get(it.series) or min(accounts, key=lambda a: waiting[a.id]).id
        else:  # "library", or an account that has since been removed
            continue
        if it.series:
            series_to.setdefault(it.series, aid)
        store.enqueue([it.id], aid)
        waiting[aid] += 1
        got[aid] = got.get(aid, 0) + 1
    return got


def ready(store: Store, account_id: str) -> list[VideoItem]:
    """The account's videos waiting to be posted, in posting order."""
    return [i for i in store.queue(account_id) if i.status in ("queued", "failed")]


def post_text(item: VideoItem, account: Account, s: Settings) -> str:
    """Exactly what to paste into TikTok's caption box: caption, credit and hashtags."""
    return compose_caption(item, account.apply(s))


def hashtags_text(item: VideoItem, account: Account) -> str:
    return normalize_hashtags(f"{item.hashtags} {account.default_hashtags}")


def mark_posted(store: Store, item_id: str, now: float | None = None, via: str = "hand") -> VideoItem | None:
    """You posted it yourself: it leaves the queue and joins the account's post history, where the
    next stats refresh links it to the TikTok video (by caption and time). The live view calls this
    with via="seen" when it finds the video on TikTok."""
    now = time.time() if now is None else now
    with store.lock:
        it = store.get(item_id)
        if it is None or it.status in ("posted", "uploading") or not it.account:
            return None
        it.status, it.posted_at, it.last_error, it.posted_via = "posted", now, "", via
        it.queue_pos = it.pinned_at = it.planned_at = None
        store._renumber()
    return it


def unmark_posted(store: Store, item_id: str, account_id: str) -> bool:
    """Undo mark_posted: back to the top of the account's queue."""
    with store.lock:
        it = store.get(item_id)
        if it is None or it.status != "posted" or it.tiktok_id:
            return False
        it.status, it.posted_at, it.account, it.queue_pos, it.posted_via = "queued", None, account_id, -1, ""
        store._renumber()
    return True


def default_ready_folder() -> Path:
    return Path.home() / "Videos" / "TikTok Ready"


def safe_name(text: str, limit: int = 60) -> str:
    """`text` as part of a Windows file name: no characters Windows refuses, on one line, not too long."""
    text = " ".join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text).split()).strip(" .")
    return text[:limit].rstrip(" .") or "video"


def export_ready(store: Store, account: Account, s: Settings, root: Path | None = None,
                 now: float | None = None, items: list[VideoItem] | None = None) -> tuple[Path, int]:
    """Copy the account's ready videos (or just `items` of them, e.g. one niche's), in posting order, to
    <root>/<account>/<date>/ with a .txt caption beside each: handy for posting from a phone through a
    synced folder."""
    now = time.time() if now is None else now
    folder = Path(root or s.ready_folder or default_ready_folder()).expanduser()
    folder = folder / safe_name(account.label, 40) / dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for pos, it in enumerate((i for i in (ready(store, account.id) if items is None else items) if i.exists()), 1):
        src = Path(it.path)
        dest = folder / f"{pos:02d} - {safe_name(it.headline)}{src.suffix.lower()}"
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        when = f"Post at: {schedule.fmt_when(it.planned_at, now)}\n\n" if it.planned_at else ""
        dest.with_suffix(".txt").write_text(when + post_text(it, account, s) + "\n", encoding="utf-8")
        n += 1
    return folder, n


def reminders(store: Store, accounts: Accounts | list[Account], s: Settings,
              now: float | None = None) -> list[tuple[Account, VideoItem]]:
    """Videos whose posting time has come on accounts you post to yourself (autopilot off): the
    first due one per account, each slot reminded once."""
    from .jobs import replan

    now = time.time() if now is None else now
    replan(store, accounts, s, now)
    out = []
    for a in accounts:
        if a.autopilot:
            continue
        item = schedule.due(store.queue(a.id), now)
        if item and not (item.reminded_at and item.planned_at and item.reminded_at >= item.planned_at):
            with store.lock:
                item.reminded_at = now
            out.append((a, item))
    return out


def summary_due(s: Settings, accounts: Accounts | list[Account], now: float | None = None) -> str:
    """Today's date when the morning summary should go out now, else "". It goes once a day, from
    the earliest first posting time of any account."""
    now = time.time() if now is None else now
    when = dt.datetime.fromtimestamp(now)
    day = when.strftime("%Y-%m-%d")
    if not s.morning_summary or s.last_summary_day == day:
        return ""
    first = min((schedule._hm(a.first_post_time, (9, 0)) for a in accounts), default=(9, 0))
    return day if (when.hour, when.minute) >= first else ""


def summary(store: Store, accounts: Accounts | list[Account], now: float | None = None) -> str:
    """'4 ready for @cats (next 12:00) · 2 for @dogs', or "" when nothing is waiting. Videos that have
    no posting time because they're not in a niche the account posts (jobs.replan) are counted apart."""
    now = time.time() if now is None else now
    parts = []
    for a in accounts:
        q = [i for i in ready(store, a.id) if i.status == "queued"]
        timed = [i for i in q if i.planned_at]
        waiting = len(q) - len(timed)
        if not q:
            continue
        nxt = min((i.planned_at for i in timed), default=None)
        text = f"{len(timed)} for {a.handle}" + (f" (next {schedule.fmt_when(nxt, now)})" if nxt else "")
        parts.append(text + (f", {waiting} on hold (not in its niches)" if waiting else ""))
    return " · ".join(parts)
