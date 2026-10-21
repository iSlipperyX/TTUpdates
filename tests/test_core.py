"""Tests for the parts that don't need TikTok: scheduling, captions, store, scoring maths."""

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from tokmanager import schedule
from tokmanager.config import Settings
from tokmanager.store import Store, TikTokVideo, VideoItem
from tokmanager.tiktok import compose_caption, normalize_hashtags
from tokmanager.tiktok.api import MB, _chunking, _new_pkce
from tokmanager.tiktok.browser import _short_number


def ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi).timestamp()


def item(name, score=None, group="", **kw):
    it = VideoItem(path=f"C:/x/{name}.mp4", title=name, source_group=group, status="queued", **kw)
    if score is not None:
        it.score = {"total": score, "parts": {}}
    return it


# --- schedule ----------------------------------------------------------------------


def test_daily_slots_spread_evenly():
    s = Settings(posts_per_day=3, first_post_time="09:00", last_post_time="21:00")
    assert [t.strftime("%H:%M") for t in schedule.daily_slots(s)] == ["09:00", "15:00", "21:00"]
    s.posts_per_day = 1
    assert [t.strftime("%H:%M") for t in schedule.daily_slots(s)] == ["09:00"]


def test_plan_fills_slots_in_queue_order_and_rolls_to_next_day():
    s = Settings(posts_per_day=2, first_post_time="10:00", last_post_time="18:00", min_gap_minutes=60)
    q = [item("a"), item("b"), item("c")]
    plan = schedule.plan(q, s, now=ts(2026, 10, 20, 12), last_post=None)
    assert plan[q[0].id] == ts(2026, 10, 20, 18)
    assert plan[q[1].id] == ts(2026, 10, 21, 10)
    assert plan[q[2].id] == ts(2026, 10, 21, 18)


def test_plan_respects_gap_after_last_post_and_pins():
    s = Settings(posts_per_day=4, first_post_time="08:00", last_post_time="20:00", min_gap_minutes=180)
    pinned = item("pinned", pinned_at=ts(2026, 10, 20, 16))
    q = [item("a"), pinned, item("b")]
    # Last post at 9:30 -> nothing before 12:30; slots are 8, 12, 16, 20.
    plan = schedule.plan(q, s, now=ts(2026, 10, 20, 9, 45), last_post=ts(2026, 10, 20, 9, 30))
    assert plan[pinned.id] == ts(2026, 10, 20, 16)
    assert plan[q[0].id] == ts(2026, 10, 20, 20)  # 16:00 is the pin; 12:00 is inside the gap
    assert plan[q[2].id] == ts(2026, 10, 21, 8)


def test_due_picks_first_ready_queued_item():
    a, b = item("a"), item("b")
    a.planned_at, b.planned_at = 200.0, 100.0
    b.status = "failed"
    assert schedule.due([a, b], now=150) is None
    assert schedule.due([a, b], now=250) is a


def test_spread_keeps_same_source_apart():
    q = [item("a1", 90, "A"), item("a2", 85, "A"), item("b1", 60, "B"), item("a3", 50, "A")]
    order = [i.name for i in schedule.spread(q)]
    assert order == ["a1", "b1", "a2", "a3"]
    assert [i.name for i in schedule.by_score(q)] == ["a1", "a2", "b1", "a3"]


# --- captions ------------------------------------------------------------------------


def test_hashtags_are_normalised_and_deduplicated():
    assert normalize_hashtags("fyp, #FYP #funny! cats") == "#fyp #funny #cats"


def test_caption_composition():
    s = Settings(default_hashtags="#fyp", caption_template="{caption} {hashtags}")
    it = item("My clip", caption="Wait for it #fyp", hashtags="funny cats")
    assert compose_caption(it, s) == "Wait for it #fyp #funny #cats"
    it.caption = ""
    assert compose_caption(it, s).startswith("My clip ")
    s.caption_template = "{oops}"
    assert compose_caption(it, s) == "My clip #funny #cats #fyp"


# --- TikTok helpers ------------------------------------------------------------------


def test_chunking_follows_tiktok_rules():
    assert _chunking(3 * MB) == (3 * MB, 1)
    assert _chunking(64 * MB) == (64 * MB, 1)
    chunk, count = _chunking(205 * MB)
    assert chunk == 10 * MB and count == 20
    last = 205 * MB - chunk * (count - 1)
    assert 5 * MB <= last <= 128 * MB


def test_pkce_is_hex_sha256():
    import hashlib

    verifier, challenge = _new_pkce()
    assert 43 <= len(verifier) <= 128
    assert challenge == hashlib.sha256(verifier.encode()).hexdigest()


@pytest.mark.parametrize("text,value", [("845", 845), ("1.2K", 1200), ("3.4M", 3400000), ("", 0), ("1,234", 1234)])
def test_short_numbers(text, value):
    assert _short_number(text) == value


# --- store ---------------------------------------------------------------------------


def test_store_queue_order_and_persistence(tmp_path):
    vids = tmp_path / "vids"
    vids.mkdir()
    for n in ("one", "two", "three"):
        (vids / f"{n}.mp4").write_bytes(b"x")
    st = Store(tmp_path / "lib.json")
    added = st.add_paths([vids])
    assert len(added) == 3
    assert st.add_paths([vids]) == []  # no duplicates
    ids = [i.id for i in added]
    st.enqueue(ids)
    st.set_order([ids[2], ids[0], ids[1]])
    st.dequeue([ids[0]])
    assert [i.id for i in st.queue()] == [ids[2], ids[1]]
    st.save()
    again = Store(tmp_path / "lib.json")
    assert [i.id for i in again.queue()] == [ids[2], ids[1]]
    assert again.get(ids[0]).status == "new"


def test_clipmonster_index_is_read(tmp_path):
    import json

    (tmp_path / "01 - clip.mp4").write_bytes(b"x")
    (tmp_path / "clips.json").write_text(json.dumps({
        "video": {"id": "abc123", "title": "Source"},
        "clips": [{"rank": 1, "file": "01 - clip.mp4", "score": 77.5, "title": "Great moment", "reason": "Big laugh"}],
    }), encoding="utf-8")
    st = Store(tmp_path / "lib.json")
    (it,) = st.add_paths([tmp_path])
    assert (it.title, it.source_score, it.source_group, it.source_reason) == ("Great moment", 77.5, "abc123", "Big laugh")


def test_posted_items_link_to_account_videos(tmp_path):
    st = Store(tmp_path / "lib.json")
    it = item("x", caption="This changed everything")
    it.status, it.posted_at = "posted", ts(2026, 10, 20, 12)
    st.items[it.id] = it
    st.update_tiktok_videos([
        TikTokVideo(id="1", description="Something else", create_time=ts(2026, 10, 20, 12, 1)),
        TikTokVideo(id="2", description="This changed everything #fyp", create_time=ts(2026, 10, 20, 12, 2)),
    ])
    assert it.tiktok_id == "2"


def test_crash_mid_upload_requeues(tmp_path):
    st = Store(tmp_path / "lib.json")
    it = item("x", queue_pos=0)
    it.status = "uploading"
    st.items[it.id] = it
    st.save()
    assert Store(tmp_path / "lib.json").get(it.id).status == "queued"


# --- scoring end to end ------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_videos(tmp_path_factory):
    from tokmanager.media import ffmpeg_exe

    d = tmp_path_factory.mktemp("samples")
    ff = ffmpeg_exe()
    good, bad = d / "good.mp4", d / "bad.mp4"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=540x960:r=30:d=25",
                    "-f", "lavfi", "-i", "sine=f=330:d=25,volume=4", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(good)], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=120",
                    "-c:v", "libx264", "-preset", "ultrafast", str(bad)], check=True)
    return good, bad


def test_vertical_with_sound_beats_silent_landscape(sample_videos):
    from tokmanager.media import analyze
    from tokmanager.scoring import score

    good, bad = sample_videos
    s = Settings()
    g, b = score(analyze(good), s), score(analyze(bad), s)
    assert g.total > b.total + 25
    assert g.parts["format"] == pytest.approx(100 * 0.7 + 30 * 0.3, abs=15)
    assert b.parts["audio"] <= 10 and b.total <= 45
    assert any("9:16" in t for t in b.tips)
    assert any("No audio" in t for t in b.tips)
