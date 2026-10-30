"""The main window: which page it opens on, first-run setup, and keeping the phone folder up to date."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from test_ready import clipmonster_folder  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)


def wait_for(condition, ms=5000):
    for _ in range(ms // 20):
        if condition():
            return True
        QTest.qWait(20)
    return condition()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    from tokmanager.gui.main_window import MainWindow
    from tokmanager.gui.state import AppState

    made = []

    def make(setup_done=True, autopilot=False, clips=None):
        st = AppState()
        st.settings.setup_done = setup_done
        st.settings.auto_check_updates = False  # no network in tests
        st.active.autopilot = autopilot
        if clips is not None:
            items = st.store.add_paths([clips])
            st.store.enqueue([i.id for i in items], st.active.id)
        w = MainWindow(st)
        made.append(w)
        return w

    yield make
    for w in made:
        for timer in (w.autopilot_timer, w.watch_timer, w.update_timer, w.clock, w.phone_timer, w.phone_soon,
                      w.drive_timer, w.live_monitor.timer, w.live.clock):
            timer.stop()
        if w._setup is not None:
            w._setup.close()
        w.hide()
        w.state.cancel_all()
        w.state.analysis_pool.waitForDone(5000)
        w.state.tiktok_pool.waitForDone(5000)
        w.state.drive_pool.waitForDone(5000)
        w.deleteLater()
    QApplication.processEvents()


def test_a_fresh_install_opens_setup_over_the_library(window):
    w = window(setup_done=False)
    QTest.qWait(900)
    assert w._setup is not None and w._setup.isVisible()
    assert w.stack.currentWidget() is w.library  # nothing yet: somewhere to drop the first clips
    w._setup.mode_buttons["hand"].setChecked(True)
    w._setup.other.setText(str(w.state.store.path.parent / "Clips"))
    w._setup.finish()
    assert w.state.settings.setup_done and w.stack.currentWidget() is w.ready  # clips will arrive there


def test_it_opens_on_the_page_for_how_the_account_posts(window, tmp_path):
    w = window(clips=clipmonster_folder(tmp_path))
    assert w.stack.currentWidget() is w.ready and w._setup is None
    w.state.set_autopilot(w.state.active.id, True)
    w.show_start_page()
    assert w.stack.currentWidget() is w.queue
    w.queue.next_label.linkActivated.emit("ready")
    w.state.set_autopilot(w.state.active.id, False)
    w.queue.next_label.linkActivated.emit("ready")
    assert w.stack.currentWidget() is w.ready


def test_the_phone_folder_follows_what_is_posted(window, tmp_path):
    from tokmanager import ready

    w = window(clips=clipmonster_folder(tmp_path))
    st = w.state
    st.settings.phone_folder = str(tmp_path / "Phone")
    w.phone_sync()
    assert wait_for(lambda: st.phone_result is not None)
    first = st.phone_result.on_phone[:1]
    assert first and "📱 Phone folder Phone:" in w.ready.phone_line.text()
    ready.mark_posted(st.store, first[0])
    st.library_changed.emit()  # as the Ready page does: the folder catches up a few seconds later
    assert w.phone_soon.isActive()
    w.phone_soon.stop()
    w.phone_sync()
    assert wait_for(lambda: first[0] not in st.phone_result.on_phone)


def test_closing_setup_while_logging_in_lets_the_login_finish(window, monkeypatch):
    from PySide6.QtCore import QEvent

    from tokmanager.gui.login_dialog import LoginDialog

    w = window(setup_done=False)
    assert wait_for(lambda: w._setup is not None)
    runs = []
    monkeypatch.setattr(w.state, "run", lambda job, **kw: runs.append(kw))  # the browser never opens
    w._setup.login_btn.click()
    (login,) = w.findChildren(LoginDialog)
    assert login.parent() is w and login.isVisible()
    w._setup.skip_btn.click()  # setup closes while the login window is still open
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert w._setup is None and login.isVisible()
    runs[0]["on_success"](None)  # then the login finishes
    assert not login.isVisible() and w.state.settings.setup_done


def test_the_live_page_sits_between_queue_and_account(window):
    w = window()
    names = [b.text() for b in w.nav_buttons]
    assert names == ["Library", "Ready", "Queue", "Live", "Account"]
    w.show_page(w.account)
    assert w.stack.currentWidget() is w.account
    w.account.live_requested.emit()
    assert w.stack.currentWidget() is w.live


def test_the_live_page_sends_you_to_log_in_or_to_its_settings(window, monkeypatch):
    w = window()
    logins, opened = [], []
    monkeypatch.setattr(w, "login", lambda: logins.append(True))
    monkeypatch.setattr(w, "open_settings", lambda tab="": opened.append(tab))
    w.live.login_requested.emit()
    w.live.settings_requested.emit()
    assert logins == [True] and opened == ["Live"]


def test_the_account_page_compares_with_a_day_before(window):
    w = window()
    st = w.state
    now = time.time()
    p = st.store.profile(st.active.id)
    p.history = [[now - 30 * 3600, 100, 1000, 3], [now - 3600, 140, 1100, 4], [now, 150, 1200, 4]]
    p.followers, p.likes, p.fetched_at = 150, 1200, now
    w.account.refresh()
    assert w.account.c_followers.sub.text() == "+50 in 24 h"
    p.history = p.history[1:]
    w.account.refresh()
    assert w.account.c_followers.sub.text() == "+10 since 1 h ago"


def test_the_queue_schedule_shows_what_really_fits_and_can_warm_up_a_new_account(window, tmp_path):
    w = window(clips=clipmonster_folder(tmp_path))
    q, a = w.queue, w.state.active
    q.per_day.setValue(9)
    q.gap.setValue(120)
    q.first.setTime(q.first.time().fromString("09:00", "HH:mm"))
    assert a.posts_per_day == 9
    notes = q.sched_notes.text()
    assert "Only 7 of 9 fit" in notes and "more than the 3" in notes and not q.sched_notes.isHidden()
    assert "each moved by up to 15 min" in q.slots.text()
    q.vary.setValue(0)
    assert a.vary_minutes == 0 and "moved" not in q.slots.text()
    q.warm_up.setChecked(True)
    assert a.warm_up_start == a.created_at  # the account was just added, so today is day 1
    assert "Warm-up day 1 of 14" in q.sched_notes.text()
    q.warm_up.setChecked(False)
    assert a.warm_up_start == 0.0 and "Warm-up" not in q.sched_notes.text()
    q.per_day.setValue(2)
    q.gap.setValue(180)
    assert q.sched_notes.text() == "" and q.sched_notes.isHidden()
