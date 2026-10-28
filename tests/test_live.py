"""The live view: reading an account through the official API and turning the changes into a feed."""

import json
import threading
import time

import pytest

from fake_tiktok_api import FakeTikTokAPI
from tokmanager import live, ready, results
from tokmanager.accounts import Accounts
from tokmanager.config import Settings
from tokmanager.store import Profile, Store, TikTokVideo, VideoItem, _add_point
from tokmanager.tiktok import api as api_mod
from tokmanager.tiktok import compose_caption, methods
from tokmanager.tiktok.api import ApiError, TikTokAPI, Tokens

HOUR = 3600


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


@pytest.fixture
def fake(monkeypatch):
    f = FakeTikTokAPI("cats")
    monkeypatch.setattr(api_mod, "API", f.base)
    yield f
    f.close()


def settings(**kw) -> Settings:
    return Settings(api_client_key="key", api_client_secret="secret", **kw)


def connected_account(username="cats", **kw):
    accs = Accounts.load(Settings())
    a = accs.items[0]
    a.username = username
    for k, v in kw.items():
        setattr(a, k, v)
    Tokens(access_token="act-0", refresh_token="rft-0", scope=FakeTikTokAPI().scope if False else
           "user.info.basic,user.info.profile,user.info.stats,video.list,video.publish,video.upload",
           expires_at=time.time() + HOUR, refresh_expires_at=time.time() + 300 * 86400).save(a.tokens_path())
    return a


def reading(at, followers=10, videos=(), full=False):
    vids = [TikTokVideo(id=v[0], description=v[1], create_time=v[2], views=v[3], likes=v[4] if len(v) > 4 else 0,
                        comments=v[5] if len(v) > 5 else 0, url=f"https://t/{v[0]}") for v in videos]
    return live.Reading(at=at, profile=Profile(username="cats", followers=followers, fetched_at=at), videos=vids,
                        full=full)


# --- reading through the API ------------------------------------------------------------


def test_read_gets_the_profile_and_newest_videos_then_all_of_them(fake):
    a = connected_account()
    now = time.time()
    for n in range(45):
        fake.add_video(f"v{n}", f"clip {n}", views=n * 10, create_time=now - n * HOUR)
    r = live.read(settings(), a)
    assert r.profile.username == "cats" and r.profile.followers == 10
    assert len(r.videos) == live.RECENT_VIDEOS and r.videos[0].id == "v0" and not r.full
    assert fake.video_calls() == 2  # two pages of 20
    full = live.read(settings(), a, full=True)
    assert len(full.videos) == 45 and full.full
    assert full.videos[-1].views == 440


def test_read_doesnt_wait_out_a_rate_limit_and_says_why(fake):
    a = connected_account()
    fake.fail_with, fake.status = "rate_limit_exceeded", 429
    t0 = time.time()
    with pytest.raises(ApiError) as e:
        live.read(settings(), a)
    assert e.value.code == "rate_limit_exceeded" and "Too many requests" in str(e.value)
    assert time.time() - t0 < 5  # the poster's 15 s wait is for posting only


def test_an_expired_access_token_is_refreshed_once_even_from_two_threads(fake):
    a = connected_account()
    t = Tokens.load(a.tokens_path())
    t.expires_at = time.time() - 10
    t.save(a.tokens_path())
    errors = []

    def go():
        try:
            TikTokAPI(settings(), a.tokens_path()).profile()
        except Exception as exc:  # pragma: no cover - shown by the assert
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert len([c for c in fake.token_calls if c["grant_type"] == "refresh_token"]) == 1
    assert Tokens.load(a.tokens_path()).access_token == "act-1"


def test_a_dead_refresh_token_disconnects_the_account(fake):
    a = connected_account()
    Tokens(access_token="x", refresh_token="dead", expires_at=0, refresh_expires_at=time.time() + 99).save(a.tokens_path())
    with pytest.raises(ApiError) as e:
        live.read(settings(), a)
    assert e.value.code == "invalid_grant" and not methods.api_connected(a)


# --- turning readings into events -------------------------------------------------------


def test_first_reading_says_its_watching_then_changes_become_events(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    s = settings()
    t0 = time.time() - 600
    ev = w.apply(st, a, s, reading(t0, 10, [("v1", "cat jumps", t0 - 5 * HOUR, 100, 5)]))
    assert [e.kind for e in ev] == ["start"] and "Watching @cats live" in ev[0].text
    ev = w.apply(st, a, s, reading(t0 + 60, 12, [("v1", "cat jumps", t0 - 5 * HOUR, 220, 13, 1)]))
    kinds = [e.kind for e in ev]
    assert kinds == ["moved", "followers"]
    assert ev[0].text == "+120 views, +8 likes, +1 comment on “cat jumps”"
    assert ev[1].text == "+2 followers (12 now)"
    assert st.videos(a.id)["v1"].views == 220 and st.profile(a.id).followers == 12
    totals = live.today(w.log.events, now=t0 + 61)
    assert totals["views"] == 120 and totals["followers"] == 2


def test_quiet_readings_add_nothing_and_many_videos_are_summed_up(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 900
    vids = [(f"v{n}", f"clip number {n}", t0 - n * HOUR, 100) for n in range(10)]
    w.apply(st, a, settings(), reading(t0, 10, vids))
    assert w.apply(st, a, settings(), reading(t0 + 60, 10, vids)) == []
    moved = [(v[0], v[1], v[2], 100 + 10 * (n + 1)) for n, v in enumerate(vids)]
    ev = w.apply(st, a, settings(), reading(t0 + 120, 10, moved))
    assert [e.kind for e in ev].count("moved") == live.FEED_VIDEOS
    assert ev[0].video_id == "v9"  # the biggest gain first
    more = ev[-1]
    assert more.kind == "more" and more.text == "+100 views across 4 more videos"
    assert live.today(w.log.events, now=t0 + 121)["views"] == sum(10 * (n + 1) for n in range(10))


def test_milestones_alert_once_when_crossed(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 900
    w.apply(st, a, settings(), reading(t0, 95, [("v1", "dog", t0 - HOUR, 900)]))
    ev = w.apply(st, a, settings(), reading(t0 + 60, 101, [("v1", "dog", t0 - HOUR, 1200)]))
    alerts = [e.text for e in ev if e.alert]
    assert "🏆 “dog” passed 1K views." in alerts and "🏆 @cats reached 100 followers." in alerts
    ev = w.apply(st, a, settings(), reading(t0 + 120, 102, [("v1", "dog", t0 - HOUR, 1300)]))
    assert not [e for e in ev if e.alert]


def test_a_video_taking_off_is_called_out(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 3 * HOUR
    views = 1000
    for n in range(0, 120):  # two hours at a steady 10 views a minute
        w.apply(st, a, settings(), reading(t0 + n * 60, 10, [("v1", "the clip", t0 - HOUR, views)]))
        views += 10
    hot = []
    for n in range(120, 131):  # then 100 a minute
        views += 100
        hot += [e for e in w.apply(st, a, settings(), reading(t0 + n * 60, 10, [("v1", "the clip", t0 - HOUR, views)]))
                if e.kind == "hot"]
    assert len(hot) == 1 and hot[0].alert and "“the clip” is taking off" in hot[0].text


def test_after_a_gap_the_change_is_one_line_and_yesterdays_part_isnt_counted_today(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    now = time.time()
    yesterday = live.day_start(now) - 2 * HOUR
    w.apply(st, a, settings(), reading(yesterday, 10, [("v1", "a", yesterday - HOUR, 100), ("v2", "b", yesterday - HOUR, 50)]))
    ev = w.apply(st, a, settings(), reading(now, 14, [("v1", "a", yesterday - HOUR, 400), ("v2", "b", yesterday - HOUR, 70)]))
    assert [e.kind for e in ev] == ["away", "followers"]
    assert ev[0].text.startswith("Since ") and "+320 views across 2 videos" in ev[0].text
    assert live.today(w.log.events, now=now)["views"] == 0


def test_a_new_run_compares_with_the_stats_already_saved(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    t0 = time.time() - 300
    st.update_profile(a.id, Profile(username="cats", followers=10, fetched_at=t0), now=t0)
    st.update_tiktok_videos(a.id, [TikTokVideo(id="v1", description="a", create_time=t0 - HOUR, views=100)], now=t0)
    w = live.Watcher(a.id)  # the app was restarted
    ev = w.apply(st, a, settings(), reading(t0 + 120, 11, [("v1", "a", t0 - HOUR, 130)]))
    assert [e.kind for e in ev] == ["moved", "followers"] and ev[0].views == 30


def test_older_videos_first_seen_in_a_full_reading_arent_new(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 600
    w.apply(st, a, settings(), reading(t0, 10, [("v1", "a", t0 - HOUR, 100)]))
    ev = w.apply(st, a, settings(), reading(t0 + 60, 10, [("v1", "a", t0 - HOUR, 100), ("old", "b", t0 - 90 * 86400, 5)],
                                             full=True))
    assert ev == []
    ev = w.apply(st, a, settings(), reading(t0 + 120, 10, [("fresh", "c", t0 + 100, 3), ("v1", "a", t0 - HOUR, 100)]))
    assert [e.kind for e in ev] == ["new"] and "New video on @cats: “c”" in ev[0].text


# --- posted from the phone: found on TikTok, marked posted ------------------------------


def queued(st, a, caption, added_at, hashtags=""):
    it = VideoItem(path="C:/clips/x.mp4", title="x", caption=caption, hashtags=hashtags, added_at=added_at,
                   hook=caption)
    st.items[it.id] = it
    st.enqueue([it.id], a.id)
    return it


def test_a_queued_clip_found_on_tiktok_is_marked_posted(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    s = settings()
    w = live.Watcher(a.id)
    t0 = time.time() - 600
    it = queued(st, a, "He quit on air and nobody noticed", t0 - 3 * HOUR, "#podcast")
    other = queued(st, a, "A totally different clip about cats", t0 - 3 * HOUR)
    w.apply(st, a, s, reading(t0, 10, [("v1", "older", t0 - 5 * HOUR, 100)]))
    pasted = compose_caption(it, a.apply(s))  # exactly what the Ready page gave to paste
    assert "#podcast" in pasted
    ev = w.apply(st, a, s, reading(t0 + 60, 10, [("v2", pasted, t0 + 30, 0), ("v1", "older", t0 - 5 * HOUR, 100)]))
    seen = [e for e in ev if e.kind == "seen"]
    assert len(seen) == 1 and seen[0].alert and "“He quit on air and nobody noticed” is on TikTok" in seen[0].text
    assert it.status == "posted" and it.tiktok_id == "v2" and it.posted_via == "seen" and it.posted_at == t0 + 30
    assert it.queue_pos is None and other.status == "queued"
    assert [i.id for i in ready.ready(st, a.id)] == [other.id]


def test_a_caption_tiktok_cut_or_changed_after_the_start_still_matches(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 600
    it = queued(st, a, "Watch what happens when the drummer misses the cue", t0 - HOUR)
    w.apply(st, a, settings(), reading(t0, 10, []))
    w.apply(st, a, settings(), reading(t0 + 60, 10, [("v9", "Watch what happens when the drummer misses the cue 😂 #fyp #music", t0 + 50, 0)]))
    assert it.status == "posted" and it.tiktok_id == "v9"


def test_two_clips_that_fit_equally_match_neither(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 600
    one = queued(st, a, "Same opening line for both of these clips, part one", t0 - HOUR)
    two = queued(st, a, "Same opening line for both of these clips, part two", t0 - HOUR)
    w.apply(st, a, settings(), reading(t0, 10, []))
    ev = w.apply(st, a, settings(), reading(t0 + 60, 10, [("v5", "Same opening line for both of these clips", t0 + 50, 0)]))
    assert not [e for e in ev if e.kind == "seen"]
    assert one.status == two.status == "queued"


def test_a_video_from_before_the_clip_arrived_isnt_it(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 600
    it = queued(st, a, "Repost of an old favourite moment", t0)
    w.apply(st, a, settings(), reading(t0, 10, []))
    w.apply(st, a, settings(), reading(t0 + 60, 10, [("v5", "Repost of an old favourite moment", t0 - 5 * HOUR, 0)]))
    assert it.status == "queued"


def test_the_first_reading_catches_clips_posted_earlier_today(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    now = time.time()
    it = queued(st, a, "Posted from my phone this morning", now - 10 * HOUR)
    ev = w.apply(st, a, settings(), reading(now, 10, [("v7", "Posted from my phone this morning #fyp", now - 3 * HOUR, 40)]))
    assert [e.kind for e in ev] == ["start", "seen"] and it.tiktok_id == "v7"


# --- errors, backoff and the feed on disk -----------------------------------------------


def test_errors_back_off_and_a_lost_connection_stops_until_resumed(tmp_path):
    a = connected_account()
    w = live.Watcher(a.id)
    s = settings(live_interval=60)
    now = 1_000_000.0
    ev = w.failed(s, "TikTok returned HTTP 503", "", now)
    assert w.next_at == now + 120 and len(ev) == 1 and "Trying again in 120 s" in ev[0].text
    assert w.failed(s, "again", "", now) == []  # not every failure fills the feed
    assert w.next_at == now + 240
    for _ in range(10):
        w.failed(s, "again", "", now)
    assert w.next_at == now + live.MAX_BACKOFF
    ev = w.failed(s, "The TikTok API login has run out", "invalid_grant", now)
    assert w.stopped and ev[0].alert and ev[0].text.startswith("Stopped watching")
    w.resume()
    assert not w.stopped and w.failures == 0 and w.next_at == 0


def test_the_feed_is_kept_on_disk_and_survives_a_torn_line(tmp_path):
    a = connected_account()
    st = Store(tmp_path / "lib.json")
    w = live.Watcher(a.id)
    t0 = time.time() - 600
    w.apply(st, a, settings(), reading(t0, 10, [("v1", "a", t0 - HOUR, 1)]))
    w.apply(st, a, settings(), reading(t0 + 60, 11, [("v1", "a", t0 - HOUR, 5)]))
    path = w.log.path
    with path.open("a", encoding="utf-8") as f:
        f.write('{"at": 1, "kind": "mov')  # the app was killed mid-write
    again = live.EventLog(a.id)
    assert [e.kind for e in again.events] == ["start", "moved", "followers"]
    lines = [json.dumps({"at": n, "kind": "moved", "text": str(n)}) for n in range(int(live.MAX_EVENTS * 1.6))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    trimmed = live.EventLog(a.id)
    assert len(trimmed.events) == live.MAX_EVENTS
    assert len(path.read_text(encoding="utf-8").splitlines()) == live.MAX_EVENTS


def test_live_readings_keep_history_points_half_an_hour_apart():
    h = []
    for n in range(10):  # a reading a minute for 10 minutes: still two points
        h = _add_point(h, [n * 60.0, n, 0, 0, 0], 1800)
    assert [p[0] for p in h] == [0.0, 540.0]
    h = _add_point(h, [1900.0, 99, 0, 0, 0], 1800)
    assert [p[0] for p in h] == [0.0, 1900.0]
    h = _add_point(h, [1960.0, 100, 0, 0, 0], 1800)
    assert [p[0] for p in h] == [0.0, 1900.0, 1960.0]
    assert _add_point([[0, 1]], [5, 2], 0) == [[0, 1], [5, 2]]  # a manual refresh always adds one


def test_api_stats_keep_what_only_the_browser_reads(tmp_path):
    st = Store(tmp_path / "lib.json")
    st.update_tiktok_videos("A", [TikTokVideo(id="v1", views=10, saves=7)])
    st.update_profile("A", Profile(username="cats", bio="my bio", followers=1))
    st.update_tiktok_videos("A", [TikTokVideo(id="v1", views=20)])  # the API has no saves
    st.update_profile("A", Profile(username="cats", followers=2))  # nor the bio, without its scope
    assert st.videos("A")["v1"].saves == 7 and st.profile("A").bio == "my bio"


# --- the front door: stats and posting go each account's own way ------------------------


def test_stats_come_from_the_api_when_connected(fake):
    from tokmanager import tiktok

    a = connected_account()
    fake.add_video("v1", "hello", views=42)
    profile, vids = tiktok.refresh_account(settings(), a, print, lambda: False)
    assert profile.username == "cats" and [v.views for v in vids] == [42]


def test_when_the_api_fails_stats_fall_back_to_the_browser_login(fake, monkeypatch):
    from tokmanager import tiktok
    from tokmanager.tiktok import browser

    a = connected_account(logged_in=True)
    fake.fail_with = "internal_error"
    used = []

    class FakeBrowser:
        def __init__(self, *args, **kw):
            used.append(True)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch_account(self, username):
            return Profile(username=username, followers=5), []

    monkeypatch.setattr(browser, "TikTokBrowser", FakeBrowser)
    lines = []
    profile, _ = tiktok.refresh_account(settings(), a, lines.append, lambda: False)
    assert used and profile.followers == 5 and "reading them through the browser" in lines[0]
    a.logged_in = False
    with pytest.raises(ApiError):
        tiktok.refresh_account(settings(), a, lines.append, lambda: False)


def test_an_api_draft_account_posts_to_the_tiktok_app_inbox(fake, tmp_path, monkeypatch):
    from tokmanager import tiktok

    monkeypatch.setattr(api_mod, "POLL_SECONDS", 0)
    a = connected_account(post_with="api_draft")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 1000)
    item = VideoItem(path=str(clip), caption="hi", account=a.id)
    out = tiktok.post(item, a.apply(settings()), a, print, lambda: False)
    assert out["via"] == "api_draft" and out["publish_id"] == "pub-1"
    assert fake.inits[0]["path"] == "/post/publish/inbox/video/init/" and fake.uploads == [1000]


def test_unaudited_direct_posts_go_up_as_only_me(fake, tmp_path, monkeypatch):
    from tokmanager import tiktok

    monkeypatch.setattr(api_mod, "POLL_SECONDS", 0)
    a = connected_account(post_with="api")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 10)
    item = VideoItem(path=str(clip), caption="hi", account=a.id)
    out = tiktok.post(item, a.apply(settings()), a, print, lambda: False)
    assert fake.inits[0]["post_info"]["privacy_level"] == "SELF_ONLY" and out["tiktok_id"] == "7001"
    fake.inits.clear()
    tiktok.post(item, a.apply(settings(api_audited=True)), a, print, lambda: False)
    assert fake.inits[0]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"


def test_an_api_account_without_a_connection_says_what_to_do(tmp_path):
    from tokmanager import tiktok

    accs = Accounts.load(Settings())
    a = accs.items[0]
    a.post_with = "api"
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    item = VideoItem(path=str(clip), account=a.id)
    with pytest.raises(RuntimeError, match="no developer app is set up"):
        tiktok.post(item, a.apply(Settings()), a, print, lambda: False)
    with pytest.raises(RuntimeError, match="Connect API"):
        tiktok.post(item, a.apply(settings()), a, print, lambda: False)


def test_results_for_clipmonster_carry_the_niche(tmp_path):
    st = Store(tmp_path / "lib.json")
    now = time.time()
    for n in range(4):
        v = TikTokVideo(id=f"t{n}", create_time=now - 5 * 86400, views=100 * (n + 1))
        st.update_tiktok_videos("A", [v])
    it = VideoItem(path="C:/x.mp4", status="posted", account="A", posted_at=now - 5 * 86400, tiktok_id="t3",
                   source_channel="Pod", niche_id="n-gaming")
    st.items[it.id] = it
    rows = results.outcomes(st, now)
    assert rows and rows[0].niche == "n-gaming"
