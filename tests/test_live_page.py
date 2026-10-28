"""The Live page and its monitor in a real (offscreen) window, against a stand-in for the account's browser."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fake_tiktok_browser import FakeTikTok  # noqa: E402
from tokmanager.tiktok.browser import BrowserError  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake(monkeypatch):
    return FakeTikTok("cats").install(monkeypatch)


def wait_for(condition, ms=8000):
    for _ in range(ms // 20):
        if condition():
            return True
        QTest.qWait(20)
    return condition()


@pytest.fixture
def made(app):
    from tokmanager.gui.state import AppState

    out = []

    def make(logged_in=True):
        st = AppState()
        if logged_in:
            st.active.username, st.active.logged_in = "cats", True
        out.append(st)
        return st

    yield make
    for st in out:
        st.tiktok_pool.waitForDone(5000)


def page_for(st, start=False):
    from tokmanager.gui.live_monitor import LiveMonitor
    from tokmanager.gui.live_page import LivePage

    mon = LiveMonitor(st, start=start)
    page = LivePage(st, mon)
    page.resize(1100, 700)
    page.show()
    return mon, page


def test_without_a_login_it_explains_and_offers_to_log_in(made):
    st = made(logged_in=False)
    mon, page = page_for(st)
    assert page.stack.currentIndex() == 0 and "Not logged in" in page.status.text()
    assert page.intro_login.isVisible() and page.intro_login.text() == f"Log in {st.active.label}…"
    asked = []
    page.login_requested.connect(lambda: asked.append("login"))
    page.settings_requested.connect(lambda: asked.append("settings"))
    page.intro_login.click()
    page.intro_settings.click()
    assert asked == ["login", "settings"]
    assert not page.interval.isVisible() and not page.watch_btn.isVisible()


def test_readings_fill_the_feed_the_numbers_and_the_table(made, fake):
    st = made()
    now = time.time()
    fake.add_video("v1", "cat falls off the sofa", views=100, likes=5, create_time=now - 7200)
    mon, page = page_for(st)
    assert page.stack.currentIndex() == 1
    mon.check_now(st.active.id)
    assert wait_for(lambda: mon.watcher(st.active.id).last_ok)
    QTest.qWait(150)  # the page's coalesced refresh
    assert "Watching @cats live" in page.feed.item(1).text()
    fake.bump("v1", views=250, likes=12, comments=2)
    fake.followers = 13
    mon.check_now(st.active.id)
    assert wait_for(lambda: len(mon.watcher(st.active.id).log.events) >= 3)
    QTest.qWait(150)  # the page's coalesced refresh
    texts = [page.feed.item(r).text() for r in range(page.feed.count())]
    assert any("+250 views, +12 likes, +2 comments on “cat falls off the sofa”" in t for t in texts)
    assert page.c_views.value.text() == "+250" and page.c_followers.value.text() == "13"
    assert page.videos.rowCount() == 1 and page.videos.item(0, 2).text() == "350"
    assert "Live" in page.status.text()
    assert st.store.videos(st.active.id)["v1"].views == 350  # in the library's stats too
    assert all(o["headless"] and o["profile_dir"] == st.active.browser_dir() for o in fake.opened)


def test_a_clip_posted_from_the_phone_leaves_the_ready_page_by_itself(made, fake, tmp_path):
    from tokmanager import ready
    from tokmanager.store import VideoItem
    from tokmanager.tiktok import compose_caption

    st = made()
    a = st.active
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    it = VideoItem(path=str(clip), caption="The goalkeeper scores from his own box", added_at=time.time() - 3600)
    st.store.items[it.id] = it
    st.store.enqueue([it.id], a.id)
    mon, page = page_for(st)
    alerts, changed = [], []
    mon.alert.connect(lambda title, text: alerts.append(title))
    st.library_changed.connect(lambda: changed.append(True))
    mon.check_now(a.id)
    assert wait_for(lambda: mon.watcher(a.id).last_ok)
    fake.add_video("v9", compose_caption(it, a.apply(st.settings)), views=3)
    mon.check_now(a.id)
    assert wait_for(lambda: it.status == "posted")
    assert it.tiktok_id == "v9" and it.posted_via == "seen" and ready.ready(st.store, a.id) == []
    assert alerts == [f"Posted on {a.handle}"] and changed


def test_watch_live_off_stops_the_readings_and_is_saved(made, fake):
    st = made()
    mon, page = page_for(st)
    page.watch_btn.click()
    QTest.qWait(150)
    assert not st.active.live and "Off" in page.status.text() and page.watch_btn.text() == "Watch live"
    mon.tick()
    QTest.qWait(200)
    assert fake.readings() == 0
    from tokmanager.accounts import Accounts

    assert not Accounts.load(st.settings).active.live
    page.watch_btn.click()
    assert st.active.live and wait_for(lambda: fake.readings() > 0)


def test_the_interval_choice_is_saved(made):
    st = made()
    mon, page = page_for(st)
    assert page.interval.currentData() == 300  # every 5 min unless changed
    page.interval.setCurrentIndex(page.interval.findData(600))
    page.interval.activated.emit(page.interval.currentIndex())
    from tokmanager.config import Settings

    assert st.settings.live_interval == 600 and Settings.load().live_interval == 600


def test_videos_are_listed_newest_first_and_captions_show_as_written(made):
    from tokmanager.store import TikTokVideo

    st = made()
    now = time.time()
    st.store.update_tiktok_videos(st.active.id, [
        TikTokVideo(id=str(n), description=d, create_time=now - n * 3600, views=v)
        for n, (d, v) in enumerate([("banana <b>newest</b>", 5), ("apple", 50), ("zebra", 7)])])
    mon, page = page_for(st)
    mon.watcher(st.active.id).last_ok = now
    page.refresh()
    assert [page.videos.item(r, 0).text() for r in range(3)] == ["banana <b>newest</b>", "apple", "zebra"]
    assert "&lt;b&gt;" in page.videos.item(0, 0).toolTip()


def test_several_alerts_from_one_reading_come_as_one(made):
    from tokmanager import live

    st = made()
    mon, page = page_for(st)
    got = []
    mon.alert.connect(lambda title, text: got.append((title, text)))
    events = [live.Event(at=1, kind="milestone", text=f"🏆 video {n} passed 1K views.", alert=True) for n in range(5)]
    mon._notify(st.active, events)
    assert len(got) == 1 and got[0][0] == "5 updates on @cats" and "3 more" in got[0][1]


def test_live_readings_keep_clipmonsters_results_current(made, monkeypatch):
    from tokmanager import results

    st = made()
    mon, page = page_for(st)
    shared = []
    monkeypatch.setattr(results, "share", lambda store: shared.append(store) or 1)
    st.settings.share_results = True
    mon._share(False)
    mon._share(False)  # not again within half an hour
    mon._share(True)  # a clip just showed up as posted: now
    assert len(shared) == 2
    st.settings.share_results = False
    mon._share(True)
    assert len(shared) == 2


def test_a_lost_login_stops_and_says_so_until_resumed(made, fake):
    st = made()
    mon, page = page_for(st)
    fake.fail_with = BrowserError("Couldn't tell which account is logged in. Log in again on the Account page.")
    mon.check_now(st.active.id)
    assert wait_for(lambda: mon.watcher(st.active.id).stopped)
    QTest.qWait(1100)
    assert "Stopped" in page.status.text() and "Log in again" in page.status.text()
    fake.fail_with = None
    mon.resume(st.active.id)
    mon.tick()
    assert wait_for(lambda: mon.watcher(st.active.id).last_ok)


def test_a_reading_waits_while_a_post_has_the_browser(made, fake):
    from tokmanager.gui.state import Job

    st = made()
    mon, page = page_for(st)
    post = Job("Posting", lambda j: None, kind="post")
    post.account_id = st.active.id
    st.jobs.append(post)
    mon.tick()
    QTest.qWait(150)
    assert fake.readings() == 0 and mon.status(st.active).state == "waiting"
    st.jobs.remove(post)
    mon.tick()
    assert wait_for(lambda: mon.watcher(st.active.id).last_ok) and fake.readings() == 1


def test_the_account_page_says_its_watched_and_links_to_live(made):
    from tokmanager.gui.account import AccountPage

    st = made()
    page = AccountPage(st)
    page.show()
    assert page.live_line.isVisible() and "Watching live" in page.live_line.text()
    assert "every 5 min" in page.live_line.text()
    went = []
    page.live_requested.connect(lambda: went.append(True))
    page.live_line.linkActivated.emit("live")
    assert went
    st.active.live = False
    page.refresh()
    assert "Live view off" in page.live_line.text()
    st.active.username, st.active.logged_in = "", False
    page.refresh()
    assert not page.live_line.isVisible()


def test_settings_live_tab(app):
    from tokmanager.config import Settings
    from tokmanager.gui.settings_dialog import SettingsDialog

    s = Settings()
    dlg = SettingsDialog(s, tab="Live")
    tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Live" and "TikTok API" not in tabs
    assert dlg.live_interval.currentData() == "300" and not dlg.live_show.isChecked()
    dlg.live_interval.setCurrentIndex(dlg.live_interval.findData("900"))
    dlg.live_show.setChecked(True)
    dlg.apply()
    assert (s.live_interval, s.live_show_browser) == (900, True)


def test_a_queued_reading_shows_as_waiting_until_it_starts(made, fake):
    from tokmanager.gui.state import Job

    st = made()
    mon, page = page_for(st)
    j = Job("Reading", lambda j: None, kind="live")
    j.account_id = st.active.id
    st.jobs.append(j)
    assert mon.status(st.active).state == "waiting"
    j.started = True
    assert mon.status(st.active).state == "reading"
    st.jobs.remove(j)


def test_watched_accounts_are_read_one_at_a_time(made, fake):
    st = made()
    b = st.accounts.add("")
    b.username, b.logged_in = "dogs", True
    mon, page = page_for(st)
    mon.tick()
    assert sum(j.kind == "live" for j in st.jobs) == 1
    assert wait_for(lambda: not st.jobs)
    mon.tick()
    assert wait_for(lambda: fake.readings() == 2)
    dirs = {o["profile_dir"] for o in fake.opened}
    assert dirs == {st.active.browser_dir(), b.browser_dir()}


def test_a_login_that_ran_out_is_noticed_and_said(made, fake):
    st = made()
    mon, page = page_for(st)
    fake.session = False
    lines = []
    st.message.connect(lines.append)
    mon.check_now(st.active.id)
    assert wait_for(lambda: mon.watcher(st.active.id).last_ok)
    assert not st.active.logged_in and any("login has run out" in m for m in lines)
    assert mon.status(st.active).state == "live"  # still read through the public profile


def test_removing_an_account_cancels_its_reading_and_deletes_its_profile_after(made, fake, monkeypatch):
    from tokmanager.accounts import Accounts
    from tokmanager.gui.state import Job

    st = made()
    b = st.accounts.add("")
    b.username, b.logged_in = "dogs", True
    deleted = []
    monkeypatch.setattr(Accounts, "delete_login", staticmethod(lambda a: deleted.append(a.id)))
    mon, page = page_for(st)
    blocker = Job("Posting", lambda j: time.sleep(0.4), kind="post")  # keeps the tiktok pool busy
    st.run(blocker, pool="tiktok", quiet=True)
    mon.check_now(b.id)
    st.remove_account(b.id)
    QTest.qWait(100)
    assert not deleted  # the reading hasn't let go of the profile yet
    assert wait_for(lambda: deleted == [b.id])
    assert all(o["profile_dir"] != b.browser_dir() for o in fake.opened)  # the queued reading never opened it
