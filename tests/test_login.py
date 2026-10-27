"""Logging in through a plain browser window (TikTok refuses logins in an automated one)."""

import os
import stat
import time

import pytest

from tokmanager.config import Settings
from tokmanager.tiktok import login
from tokmanager.tiktok.browser import Cancelled

pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses a shell script as the stand-in browser")


def fake_browser(tmp_path, seconds: float):
    """A stand-in 'browser' that records its arguments and 'closes' after `seconds`."""
    exe = tmp_path / "msedge.exe"
    exe.write_text(f"#!/bin/sh\necho \"$@\" > '{tmp_path}/args.txt'\nsleep {seconds}\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    real_sleep = time.sleep
    monkeypatch.setattr(login.time, "sleep", lambda s: real_sleep(min(s, 0.05)))


def test_opens_a_plain_window_on_the_profile_and_waits_for_it_to_close(tmp_path):
    exe = fake_browser(tmp_path, 0.3)
    profile = tmp_path / "profile"
    profile.mkdir()
    lines = []
    assert login.login_window(Settings(), profile, lines.append, browser=exe) is True
    args = (tmp_path / "args.txt").read_text(encoding="utf-8")
    assert f"--user-data-dir={profile}" in args and args.strip().endswith(login.LOGIN_URL)
    assert "remote-debugging" not in args and "enable-automation" not in args  # nothing for TikTok to spot
    assert "close that window" in lines[0]


def test_cancel_closes_the_window(tmp_path):
    exe = fake_browser(tmp_path, 30)
    with pytest.raises(Cancelled):
        login.login_window(Settings(), tmp_path, lambda m: None, cancelled=lambda: True, browser=exe)


def test_window_left_open_too_long_times_out(tmp_path):
    exe = fake_browser(tmp_path, 30)
    with pytest.raises(TimeoutError):
        login.login_window(Settings(), tmp_path, lambda m: None, browser=exe, timeout=0.3)


def test_no_browser_found_means_fall_back(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "find_browser", lambda channel: None)
    assert login.login_window(Settings(), tmp_path, lambda m: None) is False


def test_profile_already_open_is_refused(tmp_path):
    (tmp_path / "SingletonLock").symlink_to("somehost-123")
    with pytest.raises(RuntimeError, match="already open"):
        login.login_window(Settings(), tmp_path, lambda m: None, browser=fake_browser(tmp_path, 0))


def test_finds_edge_and_chrome_in_program_files(tmp_path, monkeypatch):
    for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / var))
    edge = tmp_path / "PROGRAMFILES(X86)" / "Microsoft/Edge/Application/msedge.exe"
    chrome = tmp_path / "LOCALAPPDATA" / "Google/Chrome/Application/chrome.exe"
    for p in (edge, chrome):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"")
    assert login.find_browser("msedge") == edge
    assert login.find_browser("chrome") == chrome
