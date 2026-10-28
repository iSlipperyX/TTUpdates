"""Finding out what went wrong.

- `TikTokManager.exe --selftest`: check ffmpeg and the browser automation work, write a report, exit.
- `record_crash()`: an unexpected error's details go to crashes/ (the newest CRASHES_KEPT are kept).
- `report()`: everything worth pasting when asking for help. It holds no API keys or login tokens.
"""

from __future__ import annotations

import platform
import sys
import time
import traceback
from pathlib import Path

from .config import VERSION, Settings, app_dir

CRASHES_KEPT = 20


def crashes_dir() -> Path:
    path = app_dir() / "crashes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_crash(exc_type, exc, tb, where: str = "") -> Path | None:
    """Write one crash report; returns its path (None if it couldn't be written)."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    head = f"TikTok Manager {VERSION} on {platform.platform()}, Python {platform.python_version()}"
    try:
        folder = crashes_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        same_second = sorted(folder.glob(f"crash-{stamp}-*.txt"))
        n = int(same_second[-1].stem.rsplit("-", 1)[1]) + 1 if same_second else 0  # keeps them in time order
        path = folder / f"crash-{stamp}-{n:02d}.txt"
        path.write_text(f"{head}\n{time.strftime('%Y-%m-%d %H:%M:%S')}{' in ' + where if where else ''}\n\n{text}",
                        encoding="utf-8")
        for old in sorted(folder.glob("crash-*.txt"), reverse=True)[CRASHES_KEPT:]:
            old.unlink(missing_ok=True)
        return path
    except OSError:
        return None


def _tail(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(none)"


def report(log_lines: int = 60) -> str:
    """A plain-text summary for a bug report: versions, setup, recent log and the latest crash."""
    from . import ai, updater

    s = Settings.load()
    try:
        from .accounts import Accounts

        accounts = list(Accounts.load(s))
        n_accounts = f"{len(accounts)} ({sum(a.autopilot for a in accounts)} on autopilot)"
        from collections import Counter

        from .tiktok import methods

        ways = Counter(a.post_with for a in accounts)
        posting = ", ".join(f"{n} {way}" for way, n in sorted(ways.items()))
        api = f"app {'set up' if methods.app_ready(s) else 'not set up'}, " + \
              f"{sum(methods.api_connected(a) for a in accounts)} connected, " + \
              f"{sum(a.live for a in accounts if methods.api_connected(a))} watched live every {s.live_interval} s"
    except Exception as exc:  # the report must work even when the app's files are the problem
        n_accounts = f"unknown ({exc})"
        posting = api = "unknown"
    frozen = "installed app" if getattr(sys, "frozen", False) else "running from source"
    lines = [
        f"TikTok Manager {VERSION} ({frozen})",
        f"Windows: {platform.platform()}  Python {platform.python_version()}",
        f"Accounts: {n_accounts}, posting with: {posting}, browser: {s.browser_channel}",
        f"TikTok API: {api}, audited: {'yes' if s.api_audited else 'no'}",
        f"AI key: {'yes' if ai.has_key(s) else 'no'}, watched folders: {len(s.watch_folders)}, "
        f"phone folder: {'on' if s.phone_folder else 'off'}, setup done: {'yes' if s.setup_done else 'no'}, "
        f"auto updates: {'install' if s.auto_install_updates else 'check' if s.auto_check_updates else 'off'}",
        "",
        "--- last self-test ---",
        _tail(app_dir() / "selftest.txt", 20),
        "",
        f"--- last {log_lines} log lines ---",
        _tail(app_dir() / "tokmanager.log", log_lines),
        "",
        "--- last update log ---",
        updater.log_tail(10) or "(none)",
    ]
    crashes = sorted(crashes_dir().glob("crash-*.txt"), reverse=True)
    if crashes:
        lines += ["", f"--- latest crash ({crashes[0].name}, {len(crashes)} in total) ---", _tail(crashes[0], 60)]
    return "\n".join(lines)


def run() -> str:
    lines: list[str] = [time.strftime("Self-test %Y-%m-%d %H:%M:%S")]

    def check(name: str, fn) -> None:
        t0 = time.time()
        try:
            detail = fn()
            lines.append(f"PASS {name} ({time.time() - t0:.1f}s){': ' + detail if detail else ''}")
        except Exception as exc:
            lines.append(f"FAIL {name}: {exc}\n{traceback.format_exc()}")

    def ffmpeg():
        from .media import _run, ffmpeg_exe

        out = _run([ffmpeg_exe(), "-hide_banner", "-version"]).stdout.splitlines()[0]
        return out

    def browser():
        from playwright.sync_api import sync_playwright

        from .tiktok.browser import SANDBOX

        s = Settings.load()
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel=s.browser_channel or "msedge", headless=True,
                                   chromium_sandbox=SANDBOX)
            page = b.new_page()
            page.set_content("<p id=x>ok</p>")
            text = page.locator("#x").inner_text()
            version = b.version
            b.close()
        return f"{s.browser_channel} {version}, page said {text!r}"

    check("ffmpeg", ffmpeg)
    check("browser automation", browser)
    report = "\n".join(lines)
    (app_dir() / "selftest.txt").write_text(report, encoding="utf-8")
    return report
