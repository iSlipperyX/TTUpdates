"""First-run setup: when it shows, and what finishing or skipping it sets."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokmanager.config import Settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_only_a_new_install_needs_setup(tmp_path):
    assert not Settings.load().setup_done  # no settings file yet
    Settings.path().write_text(json.dumps({"watch_folders": []}), encoding="utf-8")  # saved by 1.3
    assert Settings.load().setup_done
    s = Settings()
    s.save()
    assert not Settings.load().setup_done  # a 1.4 install that hasn't finished setup yet


def clipmonster_here(tmp_path):
    auto = tmp_path / "TTVIDEOS"
    cm = tmp_path / "appdata" / "ClipMonster"
    cm.mkdir(parents=True)
    (cm / "settings.json").write_text(json.dumps({"auto_output_dir": auto.as_posix()}), encoding="utf-8")
    return auto


def test_finishing_sets_up_the_account_clips_and_posting(tmp_path, app, monkeypatch):
    from tokmanager.gui.setup_wizard import SetupWizard
    from tokmanager.gui.state import AppState

    auto = clipmonster_here(tmp_path)
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    (tmp_path / "OneDrive").mkdir()
    st = AppState()
    dlg = SetupWizard(st)
    assert dlg.title.text() == "Your TikTok account" and not dlg.back_btn.isVisibleTo(dlg)
    dlg.name.setText("Podcast clips")
    dlg.next_btn.click()
    assert st.active.name == "Podcast clips" and dlg.title.text() == "Where clips come from"
    assert dlg.watch_cm.isChecked() and str(auto) in dlg.watch_cm.text()
    assert dlg.target.currentData() == st.active.id
    dlg.next_btn.click()
    assert dlg.next_btn.text() == "Finish" and dlg.mode() == "hand" and dlg.use_phone.isChecked()
    dlg.per_day.setValue(4)
    assert dlg.times.text().startswith("reminders at ")
    finished = []
    dlg.setup_finished.connect(finished.append)
    dlg.next_btn.click()
    s = Settings.load()
    assert finished == [True] and s.setup_done
    assert s.watch_folders == [str(auto)] and auto.is_dir() and s.watch_target == st.active.id
    assert s.phone_folder == str(tmp_path / "OneDrive" / "TikTok Ready")
    assert s.clipmonster_offered == str(auto)  # not offered again by itself
    assert st.active.posts_per_day == 4 and not st.active.autopilot


def test_autopilot_leaves_the_phone_folder_alone(tmp_path, app, monkeypatch):
    from tokmanager.gui.setup_wizard import SetupWizard
    from tokmanager.gui.state import AppState

    monkeypatch.setenv("OneDrive", str(tmp_path))
    st = AppState()
    dlg = SetupWizard(st)
    dlg.go(2)
    dlg.mode_buttons["autopilot"].setChecked(True)
    assert not dlg.use_phone.isEnabled() and dlg.times.text().startswith("at ")
    dlg.finish()
    assert st.active.autopilot and st.settings.phone_folder == "" and st.settings.watch_folders == []


def test_without_clipmonster_another_folder_can_be_watched(tmp_path, app):
    from tokmanager.gui.setup_wizard import SetupWizard
    from tokmanager.gui.state import AppState

    st = AppState()
    dlg = SetupWizard(st)
    assert dlg.cm_folder is None and not dlg.use_phone.isVisibleTo(dlg)  # no ClipMonster, no OneDrive
    dlg.other.setText(str(tmp_path / "Clips"))
    dlg.target.setCurrentIndex(dlg.target.findData("library"))
    dlg.finish()
    assert st.settings.watch_folders == [str(tmp_path / "Clips")] and st.settings.watch_target == "library"


def test_closing_it_counts_as_skipping(tmp_path, app):
    from tokmanager.gui.setup_wizard import SetupWizard
    from tokmanager.gui.state import AppState

    st = AppState()
    dlg = SetupWizard(st)
    finished = []
    dlg.setup_finished.connect(finished.append)
    dlg.skip_btn.click()
    assert finished == [False] and Settings.load().setup_done and st.settings.watch_folders == []


def test_the_login_button_asks_the_main_window_and_shows_the_result(tmp_path, app):
    from tokmanager.gui.setup_wizard import SetupWizard
    from tokmanager.gui.state import AppState

    st = AppState()
    dlg = SetupWizard(st)
    asked = []
    dlg.login_requested.connect(lambda: asked.append(True))
    dlg.name.setText("Cats")
    dlg.login_btn.click()
    assert asked == [True] and st.active.name == "Cats"
    st.active.logged_in, st.active.username = True, "cats"
    st.account_changed.emit()
    assert "Logged in as <b>@cats</b>" in dlg.login_label.text()


def test_running_it_again_keeps_where_clips_go(tmp_path, app):
    from tokmanager.gui.setup_wizard import SetupWizard
    from tokmanager.gui.state import AppState

    st = AppState()
    st.settings.watch_folders, st.settings.watch_target = [str(tmp_path / "Clips")], "library"
    dlg = SetupWizard(st)
    assert dlg.target.currentData() == "library"  # chosen before, not the new-install default
    dlg.finish()
    assert st.settings.watch_target == "library"
    b = st.accounts.add("Dogs")
    st.settings.watch_target = b.id
    dlg = SetupWizard(st)  # set up for the first account, while clips go to the other one
    assert dlg.target.currentData() == b.id and dlg.target.currentText() == "Dogs's Ready page"


def test_a_folder_that_cant_be_made_is_said_and_nothing_is_saved(tmp_path, app, monkeypatch):
    from tokmanager.gui import setup_wizard
    from tokmanager.gui.state import AppState

    warned = []
    monkeypatch.setattr(setup_wizard.QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))
    (tmp_path / "notes.txt").write_text("not a folder", encoding="utf-8")
    st = AppState()
    dlg = setup_wizard.SetupWizard(st)
    dlg.go(2)
    dlg.other.setText(f'"{tmp_path / "notes.txt" / "Clips"}"')  # pasted from "Copy as path", quotes and all
    assert dlg.folders() == [str(tmp_path / "notes.txt" / "Clips")]
    dlg.finish()
    assert warned == ["Can't use that folder"] and dlg.pages.currentIndex() == 1
    assert not st.settings.setup_done and st.settings.watch_folders == []


def test_enter_after_typing_the_name_goes_to_the_next_step(tmp_path, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tokmanager.gui.setup_wizard import SetupWizard
    from tokmanager.gui.state import AppState

    st = AppState()
    dlg = SetupWizard(st)
    asked = []
    dlg.login_requested.connect(lambda: asked.append(True))
    dlg.show()
    dlg.name.setFocus()
    dlg.name.setText("Cats")
    QTest.keyClick(dlg.name, Qt.Key_Return)
    assert dlg.pages.currentIndex() == 1 and asked == [] and st.active.name == "Cats"
    dlg.hide()
