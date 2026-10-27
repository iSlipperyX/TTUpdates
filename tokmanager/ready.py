"""Ready to upload: the hands-off way in, for people who post to TikTok themselves.

Clips arrive from watched folders (ClipMonster's Automation folder, say), are scored, and go
to an account's queue, which doubles as its "ready to upload" stack. The Ready page shows each
video with its caption ready to copy; you upload it in TikTok and click "Mark as posted".
Reminders say when a video's slot comes round, and a morning summary says what's waiting.

Everything here is free of GUI code so it can be tested and run on worker threads.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import time
from pathlib import Path

from . import schedule
from .accounts import Account, Accounts
from .config import Settings
from .store import Store, VideoItem
from .tiktok import compose_caption, normalize_hashtags

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def import_watched(store: Store, folders: list[str], now: float | None = None) -> list[VideoItem]:
    """New, finished clips from the watched folders (missing folders are skipped)."""
    roots = [Path(f).expanduser() for f in folders if f and Path(f).expanduser().is_dir()]
    return store.add_paths(roots, watched=True, now=now) if roots else []


def assign(store: Store, accounts: Accounts | list[Account], items: list[VideoItem], target: str) -> dict[str, int]:
    """Queue freshly imported videos as Settings.watch_target says: "library" leaves them unqueued,
    "spread" gives each to the account with the fewest videos waiting, an account id queues them all
    for that account. Returns how many each account got."""
    accounts = list(accounts)
    got: dict[str, int] = {}
    if target == "library" or not items or not accounts:
        return got
    fixed = next((a for a in accounts if a.id == target), None)
    if target != "spread" and fixed is None:
        return got
    waiting = {a.id: sum(1 for i in store.queue(a.id) if i.status == "queued") for a in accounts}
    for it in items:
        aid = fixed.id if fixed else min(accounts, key=lambda a: waiting[a.id]).id
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


def mark_posted(store: Store, item_id: str, now: float | None = None) -> VideoItem | None:
    """You posted it yourself: it leaves the queue and joins the account's post history, where the
    next stats refresh links it to the TikTok video (by caption and time)."""
    now = time.time() if now is None else now
    with store.lock:
        it = store.get(item_id)
        if it is None or it.status in ("posted", "uploading") or not it.account:
            return None
        it.status, it.posted_at, it.last_error = "posted", now, ""
        it.queue_pos = it.pinned_at = it.planned_at = None
        store._renumber()
    return it


def unmark_posted(store: Store, item_id: str, account_id: str) -> bool:
    """Undo mark_posted: back to the top of the account's queue."""
    with store.lock:
        it = store.get(item_id)
        if it is None or it.status != "posted" or it.tiktok_id:
            return False
        it.status, it.posted_at, it.account, it.queue_pos = "queued", None, account_id, -1
        store._renumber()
    return True


def default_ready_folder() -> Path:
    return Path.home() / "Videos" / "TikTok Ready"


def _safe(text: str, limit: int = 60) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip(" .")
    return text[:limit].rstrip(" .") or "video"


def export_ready(store: Store, account: Account, s: Settings, root: Path | None = None,
                 now: float | None = None) -> tuple[Path, int]:
    """Copy the account's ready videos, in posting order, to <root>/<account>/<date>/ with a .txt
    caption beside each: handy for posting from a phone through a synced folder."""
    now = time.time() if now is None else now
    folder = Path(root or s.ready_folder or default_ready_folder()).expanduser()
    folder = folder / _safe(account.label, 40) / dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for pos, it in enumerate((i for i in ready(store, account.id) if i.exists()), 1):
        src = Path(it.path)
        dest = folder / f"{pos:02d} - {_safe(it.name)}{src.suffix.lower()}"
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
    """'4 ready for @cats (next 12:00) · 2 for @dogs', or "" when nothing is waiting."""
    now = time.time() if now is None else now
    parts = []
    for a in accounts:
        q = [i for i in ready(store, a.id) if i.status == "queued"]
        if not q:
            continue
        nxt = min((i.planned_at for i in q if i.planned_at), default=None)
        parts.append(f"{len(q)} for {a.handle}" + (f" (next {schedule.fmt_when(nxt, now)})" if nxt else ""))
    return " · ".join(parts)
