"""Saving and loading the app's JSON files so that one bad write can never wipe them.

Every save keeps the previous good file as `<name>.bak` and, once a day, a dated copy in
`backups/` (the last BACKUP_DAYS days are kept). If a file won't load, it is moved aside as
`<name>.damaged-<time>` (so the next save can't overwrite it) and the newest good copy is used.
What happened is recorded in `notices` for the window to show at start-up.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

BACKUP_DAYS = 7
notices: list[str] = []  # plain-English lines about recovered files, read once by the main window
_lock = threading.Lock()
_good: set[str] = set()  # files this process loaded or wrote whole, so saving needn't re-read them


def _parse(path: Path):
    """The file's JSON, None when it doesn't exist; raises ValueError when it's damaged."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise ValueError("not text") from exc
    if not text.strip():
        raise ValueError("empty")
    return json.loads(text)


def backups_dir(path: Path) -> Path:
    return path.parent / "backups"


def _dated(path: Path) -> list[Path]:
    """This file's daily backups, newest first."""
    folder = backups_dir(path)
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{path.stem}-????-??-??{path.suffix}"), reverse=True)


def load(path: Path, what: str):
    """Load a JSON file, falling back to its backups. None when there is no file (a first start).

    `what` names the file for the user, e.g. "your video library".
    """
    try:
        data = _parse(path)
    except ValueError:
        pass
    else:
        if data is not None:
            _good.add(str(path))
        return data
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aside = path.with_name(f"{path.name}.damaged-{stamp}")
    try:
        os.replace(path, aside)
    except OSError:
        aside = path
    for candidate in [path.with_name(path.name + ".bak"), *_dated(path)]:
        try:
            data = _parse(candidate)
        except ValueError:
            continue
        if data is None:
            continue
        when = time.strftime("%d %b %H:%M", time.localtime(candidate.stat().st_mtime))
        notices.append(f"{what[:1].upper()}{what[1:]} couldn't be read, so the copy saved {when} was loaded. "
                       f"The damaged file was kept as {aside.name}.")
        return data
    notices.append(f"{what[:1].upper()}{what[1:]} couldn't be read and there was no backup, so it starts empty. "
                   f"The damaged file was kept as {aside.name}.")
    return None


def save(path: Path, data, indent: int | None = 2) -> None:
    """Write JSON atomically, keeping the file it replaces as .bak and a daily copy."""
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and _is_good(path):  # only a good file becomes the backup
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass
            _daily(path)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _good.add(str(path))


def _is_good(path: Path) -> bool:
    if str(path) in _good:
        return True
    try:
        return _parse(path) is not None
    except ValueError:
        return False


def _daily(path: Path) -> None:
    folder = backups_dir(path)
    target = folder / f"{path.stem}-{time.strftime('%Y-%m-%d')}{path.suffix}"
    if target.exists():
        return
    try:
        folder.mkdir(exist_ok=True)
        shutil.copy2(path, target)
        for old in _dated(path)[BACKUP_DAYS:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass  # a missing daily copy is no reason to fail the save
