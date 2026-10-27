"""Logging in to TikTok in a plain Edge/Chrome window.

TikTok's login page spots a browser that Playwright controls and answers "Too many attempts"
(or "Something went wrong"). So for logging in, the app starts your browser as an ordinary
program on the account's own profile folder: nothing is attached to it and nothing is
injected into the page. You log in and close the window; the login stays in that profile,
where the automated uploads and stat refreshes find it.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from ..config import Settings
from .browser import Cancelled

LOGIN_URL = "https://www.tiktok.com/login"

_CANDIDATES = {
    "msedge": ("Microsoft", "Edge", "Application", "msedge.exe"),
    "chrome": ("Google", "Chrome", "Application", "chrome.exe"),
}


def find_browser(channel: str) -> Path | None:
    """The installed msedge.exe / chrome.exe, or None (then the app falls back to the Playwright window)."""
    roots = [os.environ.get(v) for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    rel = _CANDIDATES.get(channel or "msedge", _CANDIDATES["msedge"])
    for root in filter(None, roots):
        path = Path(root).joinpath(*rel)
        if path.is_file():
            return path
    if os.name == "nt":
        try:
            import winreg

            exe = "msedge.exe" if channel != "chrome" else "chrome.exe"
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        path = Path(winreg.QueryValue(k, None))
                        if path.is_file():
                            return path
                except OSError:
                    continue
        except ImportError:
            pass
    return None


def profile_in_use(profile_dir: Path) -> bool:
    """Chromium keeps <profile>/lockfile open (Windows) or a SingletonLock link (elsewhere) while it runs."""
    lock = profile_dir / "lockfile"
    if lock.exists():
        try:
            lock.unlink()  # only succeeds once the browser has let go of it
        except OSError:
            return True
    return (profile_dir / "SingletonLock").is_symlink()


def login_window(s: Settings, profile_dir: Path, log: Callable[[str], None],
                 cancelled: Callable[[], bool] = lambda: False, timeout: float = 1200,
                 browser: Path | None = None, url: str = LOGIN_URL) -> bool:
    """Open the login page in a normal browser window on this profile and wait until it's closed.

    Returns False when no browser was found (the caller can fall back). Raises Cancelled if
    the user cancels, TimeoutError if the window is still open after `timeout` seconds.
    """
    exe = browser or find_browser(s.browser_channel)
    if exe is None:
        return False
    if profile_in_use(profile_dir):
        raise RuntimeError("This account's TikTok browser window is already open. Close it and try again.")
    name = "Edge" if "edge" in exe.name.lower() else "Chrome"
    args = [str(exe), f"--user-data-dir={profile_dir}", "--no-first-run", "--no-default-browser-check",
            "--disable-background-mode", "--new-window", url]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    log(f"Log in to TikTok in the {name} window that just opened, then close that window. "
        "(It's a normal browser window, so TikTok's login works as usual. The QR code works too.)")
    end = time.time() + timeout
    time.sleep(3)  # give the browser time to take the profile
    while time.time() < end:
        if cancelled():
            proc.terminate()
            raise Cancelled()
        if proc.poll() is not None and not profile_in_use(profile_dir):
            time.sleep(1.5)  # let it finish writing cookies
            return True
        time.sleep(1)
    proc.terminate()
    raise TimeoutError(f"The {name} login window was open for {int(timeout // 60)} minutes; it has been closed.")
