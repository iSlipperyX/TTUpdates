"""The Clean up window in a real (offscreen) window: the list, ticking, the question and what happens after."""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tokmanager import cleanup, ready  # noqa: E402
from tokmanager.store import TikTokVideo  # noqa: E402
from test_cleanup import DAY, clip_folder  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(cleanup, "documents_dir", lambda: tmp_path / "Documents")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def state(tmp_path, app):
    from tokmanager.gui.state import AppState

    st = AppState()
    st.active.username = "cats"
    root = tmp_path / "TTVIDEOS"
    clip_folder(root)
    st.settings.watch_folders = [str(root)]
    items = sorted(ready.import_watched(st.store, [str(root)], now=time.time() + 120), key=lambda i: i.path)
    when = time.time() - 3 * DAY
    for it in items:
        st.store.enqueue([it.id], st.active.id)
        ready.mark_posted(st.store, it.id, now=when)
    v = TikTokVideo(id="71", description="Hook 1", create_time=when, url="https://www.tiktok.com/@cats/video/71")
    st.store.update_tiktok_videos(st.active.id, [v])
    assert items[0].tiktok_id and not items[1].tiktok_id
    return st


def wait_for(dlg, app):
    end = time.time() + 10
    while dlg._job is not None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert dlg._job is None


def test_lists_ticks_asks_and_cleans(state, app, monkeypatch):
    from tokmanager.gui.cleanup_dialog import CleanupDialog
    from tokmanager.gui.library import LibraryPage

    good, fine = sorted(state.store.posted(), key=lambda i: i.path)
    dlg = CleanupDialog(state)
    assert dlg.tree.topLevelItemCount() == 2
    assert dlg.ticked() == [good.id]  # the one not seen on TikTok starts unticked
    assert "isn't linked to a video on TikTok yet" in dlg.notes.text()
    assert dlg.go.text().startswith("Clean up 1 video (")

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    dlg.go.click()
    assert "for good" in asked[0] and Path(good.path).exists()  # said No: nothing happened

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    dlg.go.click()
    wait_for(dlg, app)
    assert not Path(good.path).exists() and Path(fine.path).exists() and good.cleaned_at
    assert shown and shown[0].startswith("Cleaned up 1 posted video")
    assert dlg.tree.topLevelItemCount() == 1  # what's left
    assert Path(good.record).is_file()

    page = LibraryPage(state)
    shown_ids = {page.table.item(r, 0).data(Qt.UserRole + 1) for r in range(page.table.rowCount())}
    assert good.id not in shown_ids and fine.id in shown_ids
    page.filter.setCurrentIndex(page.filter.findData("cleaned"))
    assert page.table.rowCount() == 1 and page.table.item(0, 5).text() == "Cleaned up"


def test_ticking_by_hand_survives_a_new_look_and_closing_waits(state, app):
    from tokmanager.gui.cleanup_dialog import CleanupDialog

    dlg = CleanupDialog(state)
    dlg._tick_all(True)
    dlg.days.setValue(1)
    dlg.look()
    assert len(dlg.ticked()) == 2
    dlg.days.setValue(30)
    dlg.look()
    assert dlg.tree.topLevelItemCount() == 0 and "wait until they're older" in dlg.empty.text()
    dlg._job = object()  # a clean-up running
    dlg.reject()
    assert dlg.isVisible() is False and dlg.result() == 0
    dlg._job = None
