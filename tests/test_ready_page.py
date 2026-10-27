"""The Ready page in a real (offscreen) window: cards, copying, marking as posted and undo."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokmanager import ready  # noqa: E402
from test_ready import clipmonster_folder  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def state(tmp_path, app):
    from tokmanager.gui.state import AppState

    st = AppState()
    st.active.username = "cats"
    items = st.store.add_paths([clipmonster_folder(tmp_path)])
    st.store.enqueue([i.id for i in sorted(items, key=lambda i: i.title, reverse=True)], st.active.id)
    return st


def test_cards_copy_and_mark_posted_with_undo(state, app):
    from tokmanager.gui.ready_page import ReadyPage

    page = ReadyPage(state)
    cards = page.cards()
    assert [c.item.title for c in cards] == ["good", "fine"]
    assert page.summary.text() == "2 videos ready to post"
    cards[0].copy_btn.click()
    assert app.clipboard().text() == "He quit on air\n🎥 Pod People on YouTube (CC BY) #fyp"
    page.mark_posted(cards[0].item.id)
    assert [c.item.title for c in page.cards()] == ["fine"] and page.undo_btn.isVisibleTo(page)
    assert [i.title for i in state.store.posted(state.active.id)] == ["good"]
    page.undo()
    assert [c.item.title for c in page.cards()] == ["good", "fine"] and not page.undo_btn.isVisibleTo(page)


def test_not_this_one_returns_it_to_the_library(state):
    from tokmanager.gui.ready_page import ReadyPage

    page = ReadyPage(state)
    fine = page.cards()[1].item
    page.unqueue(fine.id)
    assert fine.status == "new" and [c.item.title for c in page.cards()] == ["good"]


def test_empty_page_points_to_the_watch_settings(tmp_path, app):
    from tokmanager.gui.ready_page import ReadyPage
    from tokmanager.gui.state import AppState

    page = ReadyPage(AppState())
    assert page.cards() == [] and "watch a folder" in page.empty.text()
    assert page.summary.text() == "Nothing waiting" and not page.export_btn.isEnabled()


def test_settings_dialog_saves_the_ready_tab(state, app, tmp_path):
    from tokmanager.gui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(state.settings, accounts=[(a.id, a.label) for a in state.accounts], tab="Ready to upload")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Ready to upload"
    dlg._add_watch(str(tmp_path / "Automation"))
    dlg._add_watch(str(tmp_path / "Automation"))  # once only
    dlg.watch_target.setCurrentIndex(dlg.watch_target.findData(state.active.id))
    dlg.apply()
    assert state.settings.watch_folders == [str(tmp_path / "Automation")]
    assert state.settings.watch_target == state.active.id
    assert ready.import_watched(state.store, state.settings.watch_folders) == []  # already in the library


def test_page_says_when_new_clips_go_to_the_library_and_can_queue_them(tmp_path, app):
    from tokmanager.gui.ready_page import ReadyPage
    from tokmanager.gui.state import AppState

    st = AppState()
    root = str(clipmonster_folder(tmp_path).parent)
    st.settings.watch_folders, st.settings.watch_target = [root], "library"
    ready.import_watched(st.store, [root])
    st.watch_reports, st.watch_checked_at = ready.watch_report(st.store, [root]), 1.0
    page = ReadyPage(st)
    assert "go to the <b>Library</b>" in page.hint.text()
    assert "2 clips brought in, 1 set aside by ClipMonster's quality check" in page.counts.text()
    assert page.take_btn.isVisibleTo(page) and page.take_btn.text() == "Queue 2 new clips here"
    assert "press “Queue 2 new clips here”" in page.empty.text()
    page.take_new_clips()
    assert [c.item.title for c in page.cards()] == ["good", "fine"] and not page.take_btn.isVisibleTo(page)
    page._link("target-here")
    assert st.settings.watch_target == st.active.id and "come in here every few minutes" in page.hint.text()


def test_page_points_out_clipmonsters_folder_when_it_isnt_watched(tmp_path, app):
    import json

    from tokmanager.gui.ready_page import ReadyPage
    from tokmanager.gui.state import AppState

    auto = tmp_path / "TTVIDEOS"
    auto.mkdir()
    cm = tmp_path / "appdata" / "ClipMonster"
    cm.mkdir(parents=True)
    (cm / "settings.json").write_text(json.dumps({"auto_output_dir": auto.as_posix()}), encoding="utf-8")
    page = ReadyPage(AppState())
    asked = []
    page.watch_clipmonster_requested.connect(lambda: asked.append(True))
    assert "isn't watching" in page.hint.text() and "TTVIDEOS" in page.hint.text()
    page._link("watch-cm")
    assert asked == [True]


def test_clipmonster_offer_lists_where_clips_can_go(tmp_path, app):
    from tokmanager.gui.ready_page import ClipMonsterOffer

    dlg = ClipMonsterOffer(None, tmp_path, [("a1", "@cats"), ("a2", "@dogs")], "a2")
    assert dlg.target() == "a2"
    assert [dlg.where.itemData(i) for i in range(dlg.where.count())] == ["a1", "a2", "spread", "library"]
