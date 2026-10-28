"""Crash reports and the "Copy diagnostics" text."""

import sys

import pytest

from tokmanager import diagnostics
from tokmanager.config import Settings


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


def crash(msg="boom"):
    try:
        raise ValueError(msg)
    except ValueError:
        return diagnostics.record_crash(*sys.exc_info(), where="Library")


def test_crash_report_is_written_and_old_ones_pruned(monkeypatch):
    monkeypatch.setattr(diagnostics, "CRASHES_KEPT", 3)
    paths = [crash(f"boom {n}") for n in range(5)]
    text = paths[-1].read_text(encoding="utf-8")
    assert "ValueError: boom 4" in text and "in Library" in text and "TikTok Manager" in text
    assert len(list(diagnostics.crashes_dir().glob("crash-*.txt"))) == 3


def test_report_has_the_essentials_and_no_secrets():
    s = Settings(ai_api_key="sk-ant-secret-123")
    s.save()
    crash("latest problem")
    text = diagnostics.report()
    assert "TikTok Manager" in text and "Accounts: 1" in text and "latest problem" in text
    assert "Live view: 0 account(s) every 5 min" in text and "TikTok API" not in text
    assert "sk-ant-secret-123" not in text
