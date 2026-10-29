"""Google Drive in the window: the Settings tab, the Ready page's line, and clips arriving from Drive."""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fake_google import FakeGoogle  # noqa: E402
from test_drive import browser_that_allows, clipmonster_drive  # noqa: E402
from test_main_window import app, wait_for, window  # noqa: E402,F401,F811 (pytest fixtures)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-MAIN")
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    from tokmanager import gdrive

    gdrive._tokens.clear()


@pytest.fixture
def google(monkeypatch):
    server = FakeGoogle()
    monkeypatch.setenv("TIKTOKMANAGER_GOOGLE_BASE", server.url)
    yield server
    server.stop()


def test_settings_tab_saves_the_drive_settings(app, tmp_path):
    from tokmanager.config import Settings
    from tokmanager.gui.settings_dialog import SettingsDialog

    s = Settings()
    dlg = SettingsDialog(s, tab="Google Drive")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Google Drive"
    assert dlg.drive_status.text() == "Needs the client ID and secret first"
    dlg.drive_on.setChecked(True)
    dlg.drive_id.setText(" 1-x.apps.googleusercontent.com ")
    dlg.drive_secret.setText("GOCSPX-s")
    assert dlg.drive_status.text() == "Not signed in"
    dlg.drive_folder.setText("/My Clips/")
    dlg.drive_inbox.setText(str(tmp_path / "in"))
    dlg.drive_days.setValue(0)
    assert dlg.drive_days.text() == "Any time"
    dlg.apply()
    assert (s.drive_on, s.drive_client_id, s.drive_client_secret, s.drive_folder, s.drive_inbox, s.drive_days) == \
        (True, "1-x.apps.googleusercontent.com", "GOCSPX-s", "My Clips", str(tmp_path / "in"), 0)
    s.save()
    saved = json.loads(Settings.path().read_text(encoding="utf-8"))
    assert saved["drive_client_id"] == "1-x.apps.googleusercontent.com"
    assert Settings.load().drive_client_secret == "GOCSPX-s"


def test_settings_tab_uses_clipmonsters_client_when_it_is_on_this_pc(app, tmp_path, google, monkeypatch):
    from tokmanager import gdrive
    from tokmanager.config import Settings
    from tokmanager.gui.settings_dialog import SettingsDialog

    folder = tmp_path / "appdata" / "ClipMonster"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(json.dumps({"gdrive_client_id": google.client_id,
                                                      "gdrive_client_secret": google.client_secret,
                                                      "gdrive_folder": "Clips"}), encoding="utf-8")
    monkeypatch.setattr(gdrive.webbrowser, "open", browser_that_allows())
    dlg = SettingsDialog(Settings(), tab="Google Drive")
    assert "ClipMonster's" in dlg.drive_id.placeholderText() and dlg.drive_folder.placeholderText() == "Clips"
    assert dlg.drive_status.text() == "Not signed in" and not dlg.drive_out.isEnabled()
    dlg._drive_sign_in()
    assert wait_for(lambda: dlg.drive_status.text().startswith("✓"))
    assert dlg.drive_status.text() == "✓ Signed in as justyn@example.com"
    assert dlg.drive_on.isChecked()  # signing in means you want the clips
    dlg._drive_sign_out()
    assert dlg.drive_status.text() == "Not signed in"


def test_clips_from_drive_arrive_on_the_ready_page(window, tmp_path, google):
    from tokmanager import gdrive

    gdrive.sign_in(google.client_id, google.client_secret, open_browser=browser_that_allows())
    clipmonster_drive(google, n=2)
    w = window()
    lines = []
    w.state.message.connect(lines.append)
    s = w.state.settings
    s.drive_on, s.drive_client_id, s.drive_client_secret = True, google.client_id, google.client_secret
    s.drive_inbox = str(tmp_path / "inbox")
    s.watch_target = w.state.active.id
    w.nav_buttons[1].click()
    w.drive_tick(manual=True)
    assert wait_for(lambda: len(w.state.store.queue(w.state.active.id)) == 2, 10000)
    assert any(line.startswith("☁ Google Drive: 2 clips in Drive: 2 new clips downloaded") for line in lines)
    names = sorted(Path(i.path).name for i in w.state.store.queue(w.state.active.id))
    assert names == ["01 - clip.mp4", "02 - clip.mp4"]
    assert w.state.drive_result.downloaded and not w.state.drive_result.problem
    w.ready.refresh()
    assert "☁ Google Drive: 2 clips in Drive" in w.ready.drive_line.text() and w.ready.drive_line.isVisibleTo(w)
    assert "Google Drive" in w.ready.hint.text()  # where new clips come from


def test_a_drive_problem_shows_on_the_ready_page_once_in_the_log(window, tmp_path):
    w = window()
    s = w.state.settings
    s.drive_on, s.drive_inbox = True, str(tmp_path / "inbox")
    lines = []
    w.state.message.connect(lines.append)
    w.drive_tick()
    w.drive_tick()
    assert len([line for line in lines if "Google Drive" in line]) == 1
    w.nav_buttons[1].click()
    w.ready.refresh()
    assert "ClipMonster → Settings → Upload" in w.ready.drive_line.text()
    assert "drive-settings" in w.ready.drive_line.text()


def test_the_drive_line_is_hidden_while_drive_is_off(window):
    w = window()
    w.nav_buttons[1].click()
    w.ready.refresh()
    assert not w.ready.drive_line.isVisibleTo(w)
