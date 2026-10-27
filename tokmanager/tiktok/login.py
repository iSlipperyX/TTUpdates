"""Logging in to TikTok in a plain Edge/Chrome window.

TikTok's login page spots a browser that Playwright controls and answers "Too many attempts"
(or "Something went wrong"). So for logging in, the app starts your browser as an ordinary
program on the account's own profile folder: nothing is attached to the page and nothing is
injected into it. The app watches the browser's cookie jar (see cdp.py), and once you're
logged in it closes the window; the login stays in that profile, where the automated
uploads and stat refreshes find it.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from ..config import Settings
from . import cdp
from .browser import SESSION_COOKIES, Cancelled

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


def _logged_in(cookies: list[dict]) -> bool:
    return any(c.get("name") in SESSION_COOKIES and c.get("value") and "tiktok.com" in (c.get("domain") or "")
               for c in cookies)


def login_window(s: Settings, profile_dir: Path, log: Callable[[str], None],
                 cancelled: Callable[[], bool] = lambda: False, timeout: float = 1200,
                 browser: Path | None = None, url: str = LOGIN_URL,
                 finish: Callable[[], bool] = lambda: False) -> bool:
    """Open the login page in a normal browser window on this profile and wait until you're logged in.

    The window closes by itself once TikTok's login cookie appears (read from the browser, not
    the page), when `finish()` turns true (you pressed "I'm logged in"), or when you close it.
    Returns False when no browser was found (the caller can fall back). Raises Cancelled if
    the user cancels, TimeoutError if the window is still open after `timeout` seconds.
    """
    exe = browser or find_browser(s.browser_channel)
    if exe is None:
        return False
    if profile_in_use(profile_dir):
        raise RuntimeError("This account's TikTok browser window is already open. Close it and try again.")
    name = "Edge" if "edge" in exe.name.lower() else "Chrome"
    (profile_dir / cdp.PORT_FILE).unlink(missing_ok=True)  # a stale one from an earlier run
    # A DevTools port, but no automation switches: nothing attaches to TikTok's page, so it sees
    # an ordinary browser. The app only reads the cookie jar through it and closes the window.
    args = [str(exe), f"--user-data-dir={profile_dir}", "--remote-debugging-port=0", "--no-first-run",
            "--no-default-browser-check", "--disable-background-mode", "--new-window", url]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    log(f"Log in to TikTok in the {name} window that just opened (the QR code works too). "
        "TikTok Manager connects by itself once you're in and closes that window.")
    end = time.time() + timeout
    devtools: cdp.CDP | None = None
    closing_since = 0.0

    def connect() -> cdp.CDP | None:
        ep = cdp.endpoint(profile_dir)
        if ep is None:
            return None
        try:
            return cdp.CDP(*ep)
        except (OSError, cdp.CDPError):
            return None

    def close_window() -> None:
        nonlocal devtools, closing_since
        if closing_since:
            return
        closing_since = time.time()
        if devtools is None:
            devtools = connect()
        try:
            if devtools is None:
                raise cdp.CDPError("no DevTools")
            devtools.call("Browser.close")  # a normal close, so the cookies are written out
        except (OSError, cdp.CDPError):
            pass
        if devtools is not None:
            devtools.close()
            devtools = None

    try:
        time.sleep(3)  # give the browser time to take the profile
        while time.time() < end:
            if cancelled():
                proc.terminate()
                raise Cancelled()
            if proc.poll() is not None and not profile_in_use(profile_dir):
                time.sleep(1.5)  # let it finish writing cookies
                return True
            if closing_since:
                if time.time() - closing_since > 20:  # it ignored the close: stop it
                    proc.terminate()
                    closing_since = time.time()
            elif finish():
                log(f"Closing the {name} window…")
                close_window()
            else:
                if devtools is None:
                    devtools = connect()
                if devtools is not None:
                    try:
                        found = _logged_in(devtools.cookies())
                    except (OSError, cdp.CDPError, ValueError):
                        devtools.close()
                        devtools = None
                        found = False
                    if found:
                        log(f"You're logged in. Closing the {name} window…")
                        time.sleep(3)  # TikTok sets a few more cookies right after logging in
                        close_window()
            time.sleep(1)
    finally:
        if devtools is not None:
            devtools.close()
    proc.terminate()
    raise TimeoutError(f"The {name} login window was open for {int(timeout // 60)} minutes; it has been closed.")
