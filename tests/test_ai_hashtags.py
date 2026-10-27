"""AI hashtags: the quick text-only call, learning from the account's best posts, and the Ready page button."""

import json
import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tokmanager import ai, jobs  # noqa: E402
from tokmanager.accounts import Accounts  # noqa: E402
from tokmanager.config import Settings  # noqa: E402
from tokmanager.store import Store, TikTokVideo  # noqa: E402
from test_ready import clipmonster_folder  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


@pytest.fixture
def claude(monkeypatch):
    """A stand-in for the anthropic package: records each request and answers with `reply`."""
    calls = []
    reply = {"hashtags": ["Jynxzi", "#r6 siege", "fyp", "gaming", "rainbowsix", "clutch"]}

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            block = types.SimpleNamespace(type="text", text=json.dumps(reply))
            return types.SimpleNamespace(stop_reason="end_turn", content=[block])

    class Anthropic:
        def __init__(self, api_key, max_retries=0):
            calls.append({"api_key": api_key})
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    return calls


def account():
    a = Accounts.load(Settings()).items[0]
    a.niche, a.default_hashtags = "Rainbow Six streamer clips", "#fyp #gaming"
    return a


def test_hashtags_fit_the_clip_and_skip_spam_and_the_accounts_own_tags(tmp_path, claude):
    store = Store(tmp_path / "lib.json")
    item = store.add_paths([clipmonster_folder(tmp_path)])[0]
    a = account()
    got = jobs.ai_hashtags(store, item.id, Settings(ai_api_key="sk-ant-x", ai_hashtag_count=4), a)
    assert got.hashtags == "#Jynxzi #r6siege #rainbowsix #clutch"  # no #fyp / #gaming (already added), max 4
    prompt = claude[-1]["messages"][0]["content"]
    assert "From the video: Big Talk" in prompt and "Channel / creator: Pod People" in prompt
    assert "The account posts: Rainbow Six streamer clips" in prompt
    assert "don't repeat): #fyp #gaming" in prompt and "exactly 4 hashtags" in prompt
    assert claude[0]["api_key"] == "sk-ant-x"


def test_clips_that_already_have_hashtags_keep_them_when_imported(tmp_path, claude):
    store = Store(tmp_path / "lib.json")
    item = store.add_paths([clipmonster_folder(tmp_path)])[0]
    item.hashtags = "#mine"
    jobs.ai_hashtags(store, item.id, Settings(ai_api_key="sk-ant-x"), account(), only_if_empty=True)
    assert item.hashtags == "#mine" and claude == []


def test_no_key_says_where_to_add_one_and_clipmonsters_key_is_used(tmp_path, claude):
    store = Store(tmp_path / "lib.json")
    item = store.add_paths([clipmonster_folder(tmp_path)])[0]
    with pytest.raises(RuntimeError, match="Settings → AI"):
        jobs.ai_hashtags(store, item.id, Settings(), account())
    cm = tmp_path / "appdata" / "ClipMonster"
    cm.mkdir(parents=True)
    (cm / "settings.json").write_text(json.dumps({"ai_api_key": " sk-ant-from-clipmonster "}), encoding="utf-8")
    assert ai.has_key(Settings())
    jobs.ai_hashtags(store, item.id, Settings(), account())
    assert claude[0]["api_key"] == "sk-ant-from-clipmonster"
    assert ai.api_key(Settings(ai_api_key="sk-ant-mine")) == "sk-ant-mine"  # its own key wins


def test_best_hashtags_come_from_posts_with_the_most_views(tmp_path):
    store = Store(tmp_path / "lib.json")
    items = store.add_paths([clipmonster_folder(tmp_path), clipmonster_folder(tmp_path, name="Other [def]")])
    tagged = [("#r6 #clutch", 9000), ("#r6 #fail", 3000), ("#cooking", 100), ("#R6", 6000)]
    videos = []
    for n, (it, (tags, views)) in enumerate(zip(items, tagged)):
        it.account, it.status, it.hashtags, it.tiktok_id, it.posted_at = "acc", "posted", tags, str(n), 1000.0 + n
        videos.append(TikTokVideo(id=str(n), views=views))
    store.update_tiktok_videos("acc", videos)
    # r6 was used three times (9000, 3000, 6000 views): its median is 6000; the newest spelling is kept
    assert jobs.proven_tags(store, "acc") == [("clutch", 9000), ("R6", 6000), ("fail", 3000), ("cooking", 100)]
    assert jobs.proven_tags(store, "acc", min_posts=5) == []  # too few posts to tell yet


def test_ready_page_button_writes_hashtags(tmp_path, claude):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from tokmanager.gui.ready_page import ReadyPage
    from tokmanager.gui.state import AppState

    app = QApplication.instance() or QApplication([])
    st = AppState()
    st.settings.ai_api_key = "sk-ant-x"
    items = st.store.add_paths([clipmonster_folder(tmp_path)])
    st.store.enqueue([i.id for i in items], st.active.id)
    page = ReadyPage(st)
    assert page.all_tags_btn.isVisibleTo(page) and page.all_tags_btn.text() == "✨ Hashtags for 2"
    page.cards()[0].ai_tags_btn.click()
    assert page.cards()[0].ai_tags_btn.text() == "Writing…"
    st.analysis_pool.waitForDone(5000)
    QThreadPool.globalInstance().waitForDone(1000)
    for _ in range(20):
        app.processEvents()
    first = page.cards()[0]
    assert first.item.hashtags.startswith("#Jynxzi") and first.ai_tags_btn.text() == "✨ Hashtags"
    assert "#Jynxzi" in first.text and not page.all_tags_btn.isVisibleTo(page)  # only one left without
