"""The Ready page in a real (offscreen) window: cards, copying, marking as posted and undo."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QPushButton  # noqa: E402

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


def test_cards_are_named_by_the_hook_and_the_keys_work(state, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tokmanager.gui.ready_page import ReadyPage

    page = ReadyPage(state)
    page.resize(1000, 700)
    page.show()
    page.activateWindow()
    app.processEvents()
    cards = page.cards()
    assert cards[0].title.text() == "1. He quit on air"  # ClipMonster's hook, not the file name "good"
    assert page.current == cards[0].item.id and cards[0].objectName() == "panelCurrent"
    QTest.keyClick(page, Qt.Key_J)
    assert page.current == cards[1].item.id and cards[0].objectName() == "panel"
    QTest.keyClick(page, Qt.Key_K)
    QTest.keyClick(page, Qt.Key_C)
    assert app.clipboard().text().startswith("He quit on air")
    QTest.keyClick(page, Qt.Key_Return)  # mark as posted: the next card takes over
    assert [c.item.title for c in page.cards()] == ["fine"] and page.current == page.cards()[0].item.id
    assert page.summary.text() == "1 video ready to post · 1 posted today ✓"
    QTest.keyClick(page, Qt.Key_Z, Qt.ControlModifier)
    assert [c.item.title for c in page.cards()] == ["good", "fine"] and page.current == page.cards()[0].item.id
    page.hide()


def test_phone_folder_line_and_clips_deleted_on_the_phone(state, app, tmp_path):
    import datetime as dt

    from tokmanager import jobs, phone
    from tokmanager.gui.ready_page import ReadyPage

    noon = dt.datetime.combine(dt.date.today(), dt.time(12)).timestamp()  # 15:00 and 21:00 still to come

    def sync():
        jobs.replan(state.store, state.accounts, state.settings, noon)
        state.phone_result = phone.sync(state.store, state.accounts, state.settings, now=noon)
        state.phone_synced.emit()

    page = ReadyPage(state)
    state.settings.phone_folder = str(tmp_path / "Phone")
    sync()
    good, fine = page.cards()
    assert "📱 Phone folder Phone: 2 clips due in the next 24 hours, with captions" in page.phone_line.text()
    assert "in your phone folder" in good.info.text() and good.gone is None
    assert not page.export_btn.isVisibleTo(page)
    shown = next(p for p in (tmp_path / "Phone").iterdir() if p.suffix == ".mp4" and "He quit" in p.name)
    shown.unlink()  # posted from the phone and deleted there
    sync()
    card = page.cards()[0]
    assert "You deleted this from your phone folder" in card.gone.text()
    page.copy_back(card.item.id)  # it wasn't posted after all
    sync()
    assert not page.gone and shown.exists() and page.cards()[0].gone is None


def test_phone_line_offers_onedrive_when_there_is_one(state, app, tmp_path, monkeypatch):
    from tokmanager.gui.ready_page import ReadyPage

    monkeypatch.setenv("OneDrive", str(tmp_path))
    page = ReadyPage(state)
    asked = []
    page.phone_sync_requested.connect(lambda: asked.append(True))
    assert "keep the next day's clips and captions in OneDrive › TikTok Ready" in page.phone_line.text()
    page._link("phone-on")
    assert state.settings.phone_folder == str(tmp_path / "TikTok Ready") and asked == [True]


def test_queue_page_switches_between_by_hand_and_autopilot(state, app):
    from tokmanager.gui.queue_page import QueuePage

    page = QueuePage(state)
    a = state.active
    assert page.mode.value() == "hand" and "from the <a href='ready'" in page.next_label.text()
    assert page.slots.text().startswith("Daily reminder times:")
    page.mode.buttons["autopilot"].click()
    assert a.autopilot and page.slots.text().startswith("Daily posting times:")
    assert "isn't logged in" in page.next_label.text()
    page.mode.buttons["hand"].click()
    assert not a.autopilot


def test_manage_accounts_sets_the_posting_mode(state, app):
    from tokmanager.gui.accounts_dialog import AccountsDialog

    dlg = AccountsDialog(state)
    a = state.active
    dlg.mode.setCurrentIndex(dlg.mode.findData("autopilot"))
    assert a.autopilot and "posts a day" in dlg.schedule_note.text()
    dlg.mode.setCurrentIndex(dlg.mode.findData("hand"))
    assert not a.autopilot and "reminders a day" in dlg.schedule_note.text()


def test_settings_dialog_sets_the_phone_folder_and_can_ask_for_setup(state, app, tmp_path, monkeypatch):
    from tokmanager.gui.settings_dialog import SettingsDialog

    monkeypatch.setenv("OneDrive", str(tmp_path))
    dlg = SettingsDialog(state.settings, tab="Ready to upload")
    use = next(b for b in dlg.tabs.widget(dlg.tabs.currentIndex()).findChildren(QPushButton) if b.text() == "Use OneDrive")
    use.click()
    dlg.apply()
    assert state.settings.phone_folder == str(tmp_path / "TikTok Ready")
    again = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Run first-run setup again")
    again.click()
    assert dlg.setup_requested and dlg.result() == QDialog.Accepted


def test_the_keys_act_on_the_card_you_click_or_tab_into(state, app):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtTest import QTest

    from tokmanager.gui.ready_page import ReadyPage

    page = ReadyPage(state)
    page.resize(1000, 700)
    page.show()
    app.processEvents()
    good, fine = page.cards()
    QTest.mouseClick(fine.caption, Qt.LeftButton)  # in its caption, to select some of it
    assert page.current == fine.item.id and fine.objectName() == "panelCurrent"
    QApplication.sendEvent(good.copy_btn, QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason))
    assert page.current == good.item.id
    QApplication.sendEvent(fine.copy_btn, QFocusEvent(QEvent.FocusIn, Qt.OtherFocusReason))
    assert page.current == good.item.id  # focus moved by the app (cards rebuilt, say), not by you
    page.hide()


def test_holding_enter_marks_one_video_and_undo_ends_with_its_button(state, app):
    from PySide6.QtGui import QShortcut
    from PySide6.QtTest import QTest

    from tokmanager.gui.ready_page import ReadyPage

    page = ReadyPage(state)
    repeats = {sc.key().toString(): sc.autoRepeat() for sc in page.findChildren(QShortcut)}
    assert not repeats["Return"] and not repeats["Ctrl+Z"] and repeats["Down"] and repeats["J"]
    first = page.cards()[0].item
    page._undo_timer.setInterval(30)  # instead of 20 seconds
    page.mark_posted(first.id)
    QTest.qWait(100)
    assert not page.undo_btn.isVisibleTo(page)
    page._key("undo")  # Ctrl+Z long after
    assert first.status == "posted"


def test_a_hook_with_an_ampersand_shows_as_written(state, app):
    from PySide6.QtCore import Qt

    from tokmanager.gui.ready_page import ReadyPage

    good = next(i for i in state.store.items.values() if i.title == "good")
    good.hook = "Tom & Jerry"
    page = ReadyPage(state)
    card = page.cards()[0]
    assert card.title.textFormat() == Qt.RichText and card.title.text() == "1. Tom &amp; Jerry"
    page.mark_posted(good.id)
    assert page.undo_btn.text() == "Undo “Tom && Jerry”"  # && shows as one &


def test_another_account_starts_at_its_first_card(state, app, tmp_path):
    from tokmanager.gui.ready_page import ReadyPage

    b = state.accounts.add("Dogs")
    items = state.store.add_paths([clipmonster_folder(tmp_path, name="Dogs/Show [def]")])
    state.store.enqueue([i.id for i in sorted(items, key=lambda i: i.title, reverse=True)], b.id)
    page = ReadyPage(state)
    page._key("next")
    assert page.current == page.cards()[1].item.id
    state.switch_account(b.id)
    assert page.cards()[0].item.account == b.id and page.current == page.cards()[0].item.id


def test_only_failed_videos_left_says_so(state, app):
    from tokmanager.gui.ready_page import ReadyPage

    for it in state.store.queue(state.active.id):
        it.status, it.last_error = "failed", "TikTok didn't take it"
    page = ReadyPage(state)
    assert page.summary.text() == "2 failed videos to look at" and len(page.cards()) == 2


def test_cards_rebuilt_right_after_a_copy_dont_crash(state, app, monkeypatch):
    import sys

    import shiboken6
    from PySide6.QtTest import QTest

    from tokmanager.gui.ready_page import ReadyPage

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *e: errors.append(e))
    page = ReadyPage(state)
    page.cards()[0].copy_btn.click()  # "Copied ✓" for a moment
    page.cards()[0].item.caption = "Changed"
    state.library_changed.emit()  # the cards are rebuilt meanwhile
    shiboken6.delete(page)  # and the page closed, before it put the scroll position back
    QTest.qWait(1700)
    assert errors == []


def test_by_hand_times_are_reminders_only_while_reminders_are_on(state, app):
    from tokmanager.gui.accounts_dialog import AccountsDialog
    from tokmanager.gui.queue_page import QueuePage

    state.settings.remind_to_post = False
    page = QueuePage(state)
    assert page.slots.text().startswith("Daily posting times:") and "reminder" not in page.next_label.text()
    assert "posts a day" in AccountsDialog(state).schedule_note.text()
