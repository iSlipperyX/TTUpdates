"""Phone hand-off: the next day's clips and their captions, kept in a folder that syncs to your phone (OneDrive).

Settings → Ready to upload picks the folder once. From then on the folder holds, for each
account you post to by hand, the videos due in the next 24 hours (and any overdue), in posting
order, each with a .txt beside it holding exactly what to paste into TikTok. A day ahead rather
than "today" so the folder is ready in the morning even when the PC was off overnight. It's
kept up to date by itself: clips you mark posted, take out of the queue or move later leave it;
reordering renames the files rather than copying them again.

Only files the app put there are ever touched (they're listed in MANIFEST). When you delete a
clip from the folder on your phone, presumably after posting it, it isn't copied back: the
Ready page asks whether it was posted.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .accounts import Account, Accounts
from .config import Settings
from .ready import post_text, ready, safe_name
from .store import Store, VideoItem

MANIFEST = ".tiktokmanager-phone.json"
FOLDER_NAME = "TikTok Ready"
HOURS_AHEAD = 24
MAX_PER_ACCOUNT = 12  # a backlog of overdue clips shouldn't fill the phone


def onedrive() -> Path | None:
    """The OneDrive folder on this PC (personal first), if there is one."""
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        path = os.environ.get(var)
        if path and Path(path).is_dir():
            return Path(path)
    return None


def suggested_folder() -> Path | None:
    base = onedrive()
    return base / FOLDER_NAME if base else None


def upcoming(store: Store, account: Account, now: float | None = None) -> list[VideoItem]:
    """The account's clips due in the next HOURS_AHEAD hours (overdue ones too), in posting order."""
    now = time.time() if now is None else now
    end = now + HOURS_AHEAD * 3600
    due = [i for i in ready(store, account.id)
           if i.status == "queued" and i.planned_at and i.planned_at <= end and i.exists()]
    return due[:MAX_PER_ACCOUNT]


@dataclass
class SyncResult:
    folder: Path
    on_phone: list[str] = field(default_factory=list)  # item ids whose video is in the folder now
    copied: int = 0
    removed: int = 0
    gone: list[str] = field(default_factory=list)  # item ids you deleted from the folder yourself
    at: float = 0.0


def _load(folder: Path) -> dict:
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    return {k: v for k, v in items.items() if isinstance(v, dict)} if isinstance(items, dict) else {}


def _save(folder: Path, items: dict) -> None:
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items}, indent=1), encoding="utf-8")
    tmp.replace(folder / MANIFEST)


def _rel(folder: Path, path: Path) -> str:
    return path.relative_to(folder).as_posix()


def sync(store: Store, accounts: Accounts | list[Account], s: Settings, folder: Path | str | None = None,
         now: float | None = None) -> SyncResult:
    """Make the phone folder hold exactly the next day's clips for the accounts you post to by hand.

    Safe to run often: files already in place are left alone, so a run with nothing new only
    looks at the folder. Runs on a worker thread.
    """
    now = time.time() if now is None else now
    folder = Path(folder or s.phone_folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    accounts = [a for a in accounts if not a.autopilot]  # autopilot posts those itself
    multi = len(accounts) > 1
    # item id -> {"video": path in the folder, "text": its caption file, "size": bytes, "gone": True}
    known = _load(folder)
    made = {Path(e[k]).parent for e in known.values() for k in ("video", "text") if e.get(k)}
    result = SyncResult(folder=folder, at=now)

    want: dict[str, tuple[VideoItem, Account, str, str]] = {}  # item id -> (item, account, video, caption file)
    for a in accounts:
        sub = safe_name(a.label, 40) + "/" if multi else ""
        for n, it in enumerate(upcoming(store, a, now), 1):
            when = dt.datetime.fromtimestamp(it.planned_at).strftime("%a %H.%M")  # "Tue 15.00"
            base = f"{sub}{n:02d} {when} - {safe_name(it.headline)}"
            want[it.id] = (it, a, base + Path(it.path).suffix.lower(), base + ".txt")
            made.add(Path(base).parent)

    try:
        # Clips that left the list (posted, unqueued, moved later): take out what the app put there.
        for item_id in [i for i in known if i not in want]:
            entry = known.pop(item_id)
            for key in ("video", "text"):
                if entry.get(key):
                    (folder / entry[key]).unlink(missing_ok=True)
            result.removed += 0 if entry.get("gone") else 1

        # Clips that changed place: out of the way first, so two of them can swap names.
        moving: dict[str, Path] = {}
        for item_id, (_it, _a, video, _text) in want.items():
            entry = known.get(item_id)
            if not entry or entry.get("gone") or not entry.get("video") or entry["video"] == video:
                continue
            old = folder / entry["video"]
            if old.exists():
                tmp = old.with_name(old.name + ".moving")
                old.replace(tmp)
                entry["video"] = _rel(folder, tmp)
                moving[item_id] = tmp

        for item_id, (it, a, video, text) in want.items():
            entry = known.get(item_id) or {}
            if entry.get("gone"):
                result.gone.append(item_id)
                continue
            dest = folder / video
            dest.parent.mkdir(parents=True, exist_ok=True)
            if item_id in moving:
                moving[item_id].replace(dest)
            elif entry.get("video") and not dest.exists():
                # Copied before but not there now: you deleted it (on your phone, say). Don't put it back.
                if entry.get("text"):
                    (folder / entry["text"]).unlink(missing_ok=True)
                known[item_id] = {"gone": True}
                result.gone.append(item_id)
                continue
            if entry.get("text") and entry["text"] != text:
                (folder / entry["text"]).unlink(missing_ok=True)
            src = Path(it.path)
            size = src.stat().st_size
            if not dest.exists() or dest.stat().st_size != size:
                part = dest.with_name(dest.name + ".part")
                try:
                    shutil.copyfile(src, part)
                    part.replace(dest)
                except OSError:
                    part.unlink(missing_ok=True)
                    raise
                result.copied += 1
            caption = post_text(it, a, s) + "\n"
            txt = folder / text
            try:
                same = txt.read_text(encoding="utf-8") == caption
            except (OSError, ValueError):
                same = False
            if not same:
                txt.write_text(caption, encoding="utf-8")
            known[item_id] = {"video": video, "text": text, "size": size}
            result.on_phone.append(item_id)
    finally:
        _save(folder, known)
        _tidy(folder, made)
    return result


def _tidy(folder: Path, subfolders: set[Path]) -> None:
    """Remove the account subfolders the app made that are empty now."""
    for sub in subfolders:
        if sub == Path("."):
            continue
        try:
            if not any((folder / sub).iterdir()):
                (folder / sub).rmdir()
        except OSError:
            pass


def forget_gone(folder: Path, item_ids: list[str]) -> None:
    """You answered about clips deleted from the folder: stop tracking them."""
    known = _load(folder)
    for i in item_ids:
        known.pop(i, None)
    _save(folder, known)
