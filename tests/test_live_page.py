"""The Live page and its monitor in a real (offscreen) window, against a stand-in TikTok API."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fake_tiktok_api import FakeTikTokAPI  # noqa: E402
from tokmanager.tiktok import api as api_mod  # noqa: E402
from tokmanager.tiktok.api import Tokens  # noqa: E402

SCOPES = "user.info.basic,user.info.profile,user.info.stats,video.list,video.publish,video.upload"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake(monkeypatch):
    f = FakeTikTokAPI("cats")
    monkeypatch.setattr(api_mod, "API", f.base)
    yield f
    f.close()


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

    def make(app_ready=True, connected=True):
        st = AppState()
        st.active.username = "cats"
        if app_ready:
            st.settings.api_client_key, st.settings.api_client_secret = "key", "secret"
        if connected:
            Tokens(access_token="act", refresh_token="rft", scope=SCOPES, expires_at=time.time() + 3600,
                   refresh_expires_at=time.time() + 200 * 86400).save(st.active.tokens_path())
        out.append(st)
        return st

    yield make
    for st in out:
        st.live_pool.waitForDone(5000)


def page_for(st, start=False):
    from tokmanager.gui.live_monitor import LiveMonitor
    from tokmanager.gui.live_page import LivePage

    mon = LiveMonitor(st, start=start)
    page = LivePage(st, mon)
    page.resize(1100, 700)
    page.show()
    return mon, page


def test_without_a_developer_app_it_explains_and_offers_the_setup(made):
    st = made(app_ready=False, connected=False)
    mon, page = page_for(st)
    assert page.stack.currentIndex() == 0 and "Not set up" in page.status.text()
    assert page.intro_setup.isVisible() and not page.intro_connect.isVisible()
    asked = []
    page.setup_requested.connect(lambda: asked.append(True))
    page.intro_setup.click()
    assert asked


def test_with_the_app_but_not_connected_it_offers_connect(made):
    st = made(connected=False)
    mon, page = page_for(st)
    assert page.stack.currentIndex() == 0 and page.intro_connect.isVisible()
    assert "✓" in page.intro_steps.text()  # step 1 done
    asked = []
    page.connect_requested.connect(lambda: asked.append(True))
    page.intro_connect.click()
    assert asked


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
    fake.user["follower_count"] = 13
    mon.check_now(st.active.id)
    assert wait_for(lambda: len(mon.watcher(st.active.id).log.events) >= 3)
    QTest.qWait(150)  # the page's coalesced refresh
    texts = [page.feed.item(r).text() for r in range(page.feed.count())]
    assert any("+250 views, +12 likes, +2 comments on “cat falls off the sofa”" in t for t in texts)
    assert page.c_views.value.text() == "+250" and page.c_followers.value.text() == "13"
    assert page.videos.rowCount() == 1 and page.videos.item(0, 2).text() == "350"
    assert "Live" in page.status.text()
    assert st.store.videos(st.active.id)["v1"].views == 350  # in the library's stats too


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


def test_a_lost_connection_stops_and_says_so(made, fake):
    st = made()
    mon, page = page_for(st)
    fake.fail_with = "access_token_invalid"
    mon.check_now(st.active.id)
    assert wait_for(lambda: mon.watcher(st.active.id).stopped)
    QTest.qWait(1100)
    assert "Stopped" in page.status.text() and "expired" in page.status.text()
    fake.fail_with = ""
    mon.resume(st.active.id)
    mon.tick()
    assert wait_for(lambda: mon.watcher(st.active.id).last_ok)


def test_watch_live_off_stops_the_readings_and_is_saved(made, fake):
    st = made()
    mon, page = page_for(st)
    page.watch_btn.click()
    QTest.qWait(150)
    assert not st.active.live and "Off" in page.status.text() and page.watch_btn.text() == "Watch live"
    mon.tick()
    QTest.qWait(200)
    assert fake.video_calls() == 0
    from tokmanager.accounts import Accounts

    assert not Accounts.load(st.settings).active.live
    page.watch_btn.click()
    assert st.active.live and wait_for(lambda: fake.video_calls() > 0)


def test_the_interval_choice_is_saved(made):
    st = made()
    mon, page = page_for(st)
    page.interval.setCurrentIndex(page.interval.findData(300))
    page.interval.activated.emit(page.interval.currentIndex())
    from tokmanager.config import Settings

    assert st.settings.live_interval == 300 and Settings.load().live_interval == 300


def test_the_account_page_shows_the_api_connection(made, fake):
    from tokmanager.gui.account import AccountPage

    st = made(connected=False)
    page = AccountPage(st)
    page.show()
    assert page.api_connect_btn.isVisible() and not page.api_disconnect_btn.isVisible()
    assert "not connected" in page.api_title.text()
    Tokens(access_token="act", refresh_token="rft", scope=SCOPES, expires_at=time.time() + 3600,
           refresh_expires_at=time.time() + 200 * 86400).save(st.active.tokens_path())
    st.active.api_username = "cats"
    page.refresh()
    assert page.api_title.text() == "Official TikTok API: connected as @cats"
    assert "Watching live" in page.api_status.text() and "199 more days" in page.api_status.text()
    assert page.api_test_btn.isVisible() and page.api_disconnect_btn.isVisible()


def test_manage_accounts_sets_how_each_account_posts(made):
    from tokmanager.accounts import Accounts
    from tokmanager.gui.accounts_dialog import AccountsDialog

    st = made(app_ready=False, connected=False)
    dlg = AccountsDialog(st)
    dlg.post_with.setCurrentIndex(dlg.post_with.findData("api_draft"))
    assert st.active.post_with == "api_draft"
    assert "no developer app is set up" in dlg.post_with_note.text()
    assert Accounts.load(st.settings).active.post_with == "api_draft"


def test_settings_api_tab_walks_through_the_setup(app):
    from tokmanager.config import Settings
    from tokmanager.gui.settings_dialog import SettingsDialog

    s = Settings()
    dlg = SettingsDialog(s, tab="TikTok API")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "TikTok API"
    assert "http://localhost:53682/callback/" in dlg.api_steps.text() and "Target users" in dlg.api_steps.text()
    dlg.port.setValue(53700)
    assert dlg.redirect.text() == "http://localhost:53700/callback/" and "53700" in dlg.api_steps.text()
    dlg.client_key.setText(" key ")
    dlg.client_secret.setText("secret")
    dlg.audited.setChecked(True)
    dlg.live_interval.setCurrentIndex(dlg.live_interval.findData("120"))
    dlg.apply()
    assert (s.api_client_key, s.api_audited, s.live_interval, s.api_redirect_port) == ("key", True, 120, 53700)
    assert not hasattr(dlg, "method")  # the app-wide "Upload with" is gone: each account has its own
