"""Ready to upload: watched folders, ClipMonster's index, sharing clips between accounts, posting by hand."""

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from tokmanager import clipmonster, ready
from tokmanager.accounts import Accounts
from tokmanager.config import Settings
from tokmanager.store import Store
from tokmanager.tiktok import compose_caption


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


def ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi).timestamp()


NOW = ts(2026, 10, 27, 10)


def clipmonster_folder(tmp_path, name="Pod/Talk [abc]", permission="Creative Commons licence: credit the creator",
                       license=""):
    folder = tmp_path / "Automation" / name
    (folder / "_rejected").mkdir(parents=True)
    for f in ("01 - good.mp4", "02 - fine.mp4", "_rejected/03 - dud.mp4"):
        (folder / f).write_bytes(b"x")
    (folder / "clips.json").write_text(json.dumps({
        "video": {"id": "abc", "title": "Big Talk", "channel": "Pod People", "license": license},
        "source": "https://youtu.be/abc",
        **({"automation": {"permission": permission}} if permission else {}),
        "clips": [
            {"file": "01 - good.mp4", "title": "good", "score": 81, "reason": "laughs", "hook": "He quit on air",
             "rating": 8, "watch": "https://youtu.be/abc?t=60", "qc": {"ok": True}},
            {"file": "02 - fine.mp4", "title": "fine", "score": 70, "qc": {"ok": True, "warnings": ["Ends mid-sentence"]}},
            {"file": "_rejected/03 - dud.mp4", "title": "dud", "score": 60,
             "qc": {"ok": False, "problems": ["Silent"]}},
        ]}), encoding="utf-8")
    return folder


def two_accounts():
    accs = Accounts.load(Settings())
    a = accs.items[0]
    a.username = "cats"
    b = accs.add("Second", copy_schedule_from=a)
    b.username = "dogs"
    return accs, a, b


def test_clipmonster_index_brings_hooks_ratings_warnings_and_skips_rejects(tmp_path):
    folder = clipmonster_folder(tmp_path)
    index = clipmonster.read_folder_index(folder)
    assert index["03 - dud.mp4"]["rejected"] and not index["01 - good.mp4"]["rejected"]
    store = Store(tmp_path / "lib.json")
    added = store.add_paths([tmp_path / "Automation"])
    assert sorted(i.title for i in added) == ["fine", "good"]  # the rejected clip stays out
    good = next(i for i in added if i.title == "good")
    assert good.hook == good.caption == "He quit on air"
    assert good.source_rating == 8 and good.source_channel == "Pod People"
    assert good.source_url == "https://youtu.be/abc?t=60"
    assert good.credit == "🎥 Pod People on YouTube (CC BY)"
    assert next(i for i in added if i.title == "fine").warnings == ["Ends mid-sentence"]


@pytest.mark.parametrize("license,credited", [
    ("Creative Commons Attribution license (reuse allowed)", True),
    ("", False),
])
def test_credit_for_videos_added_by_hand_comes_from_the_licence(tmp_path, license, credited):
    store = Store(tmp_path / "lib.json")
    added = store.add_paths([clipmonster_folder(tmp_path, permission="", license=license)])
    good = next(i for i in added if i.title == "good")
    assert good.credit == ("🎥 Pod People on YouTube (CC BY)" if credited else "")


def test_credit_goes_into_the_post_text_once(tmp_path):
    store = Store(tmp_path / "lib.json")
    good = next(i for i in store.add_paths([clipmonster_folder(tmp_path)]) if i.title == "good")
    text = compose_caption(good, Settings(default_hashtags="#fyp"))
    assert text == "He quit on air\n🎥 Pod People on YouTube (CC BY) #fyp"
    good.caption = "Mine 🎥 Pod People on YouTube (CC BY)"
    assert compose_caption(good, Settings(default_hashtags="")).count("Pod People") == 1


def test_no_credit_for_channels_that_allow_clipping(tmp_path):
    store = Store(tmp_path / "lib.json")
    added = store.add_paths([clipmonster_folder(tmp_path, permission="On your channel list: clipping programme")])
    assert all(not i.credit for i in added)


def test_watched_folders_wait_for_finished_files_and_respect_removals(tmp_path):
    folder = clipmonster_folder(tmp_path)
    loose = tmp_path / "Automation" / "loose.mp4"
    loose.write_bytes(b"x")
    os.utime(loose, (NOW - 10, NOW - 10))  # written 10 s ago: may still be growing
    store = Store(tmp_path / "lib.json")
    first = ready.import_watched(store, [str(tmp_path / "Automation"), str(tmp_path / "missing")], now=NOW)
    assert sorted(i.title for i in first) == ["fine", "good"] and all(i.imported_by == "watch" for i in first)
    assert [i.title for i in ready.import_watched(store, [str(tmp_path / "Automation")], now=NOW + 120)] == ["loose"]
    good = next(i for i in first if i.title == "good")
    store.remove([good.id])
    store.save()
    again = Store(tmp_path / "lib.json")
    assert ready.import_watched(again, [str(tmp_path / "Automation")], now=NOW + 300) == []  # stays removed
    assert [i.title for i in again.add_paths([folder / "01 - good.mp4"])] == ["good"]  # adding by hand brings it back


def test_assign_spreads_between_accounts_or_picks_one(tmp_path):
    accs, a, b = two_accounts()
    store = Store(tmp_path / "lib.json")
    items = store.add_paths([clipmonster_folder(tmp_path)])
    assert ready.assign(store, accs, items, "library") == {} and all(i.status == "new" for i in items)
    assert ready.assign(store, accs, items, "spread") == {a.id: 1, b.id: 1}
    more = store.add_paths([clipmonster_folder(tmp_path, "Other [def]")])
    assert ready.assign(store, accs, more, b.id) == {b.id: 2}
    assert len(ready.ready(store, b.id)) == 3
    assert ready.assign(store, accs, [], "spread") == {}
    assert ready.assign(store, accs, items, "gone-account") == {}


def test_mark_posted_moves_it_to_the_history(tmp_path):
    accs, a, _ = two_accounts()
    store = Store(tmp_path / "lib.json")
    items = store.add_paths([clipmonster_folder(tmp_path)])
    store.enqueue([i.id for i in items], a.id)
    first = ready.ready(store, a.id)[0]
    assert ready.mark_posted(store, first.id, now=NOW) is first
    assert first.status == "posted" and first.posted_at == NOW and first.queue_pos is None
    assert [i.id for i in store.posted(a.id)] == [first.id]
    assert ready.ready(store, a.id)[0].queue_pos == 0
    assert ready.mark_posted(store, first.id) is None  # already posted


def test_post_text_and_hashtags_use_the_accounts_defaults(tmp_path):
    accs, a, b = two_accounts()
    b.default_hashtags = "#dogs #fyp"
    store = Store(tmp_path / "lib.json")
    good = next(i for i in store.add_paths([clipmonster_folder(tmp_path, permission="ok")]) if i.title == "good")
    good.hashtags = "#podcast"
    assert ready.post_text(good, b, Settings()) == "He quit on air #podcast #dogs #fyp"
    assert ready.hashtags_text(good, b) == "#podcast #dogs #fyp"


def test_export_copies_ready_clips_with_caption_files(tmp_path):
    accs, a, _ = two_accounts()
    store = Store(tmp_path / "lib.json")
    items = store.add_paths([clipmonster_folder(tmp_path, permission="ok")])
    store.enqueue([i.id for i in sorted(items, key=lambda i: i.title, reverse=True)], a.id)  # good, then fine
    folder, n = ready.export_ready(store, a, Settings(), tmp_path / "Ready", now=NOW)
    assert n == 2 and folder == tmp_path / "Ready" / "@cats" / "2026-10-27"
    # Named by ClipMonster's hook when it wrote one (that's how the Ready page shows them), else the title
    assert sorted(p.name for p in folder.iterdir()) == ["01 - He quit on air.mp4", "01 - He quit on air.txt",
                                                         "02 - fine.mp4", "02 - fine.txt"]
    assert "He quit on air" in (folder / "01 - He quit on air.txt").read_text(encoding="utf-8")
    assert ready.export_ready(store, a, Settings(), tmp_path / "Ready", now=NOW)[1] == 2  # again: no errors


def test_reminders_once_per_slot_and_only_without_autopilot(tmp_path):
    accs, a, b = two_accounts()
    b.autopilot = True
    store = Store(tmp_path / "lib.json")
    items = store.add_paths([clipmonster_folder(tmp_path)])
    store.enqueue([items[0].id], a.id)
    store.enqueue([items[1].id], b.id)
    slot = ts(2026, 10, 27, 15)
    for it in items:
        it.pinned_at = slot
    s = Settings()
    assert ready.reminders(store, accs, s, now=slot - 600) == []
    due = ready.reminders(store, accs, s, now=slot + 30)
    assert [(acc.id, it.id) for acc, it in due] == [(a.id, items[0].id)]  # b posts on autopilot
    assert ready.reminders(store, accs, s, now=slot + 60) == []  # once


def test_morning_summary(tmp_path):
    accs, a, b = two_accounts()
    a.first_post_time, b.first_post_time = "09:00", "11:00"
    s = Settings()
    assert ready.summary_due(s, accs, now=ts(2026, 10, 27, 8, 59)) == ""
    assert ready.summary_due(s, accs, now=ts(2026, 10, 27, 9, 1)) == "2026-10-27"
    s.last_summary_day = "2026-10-27"
    assert ready.summary_due(s, accs, now=ts(2026, 10, 27, 12)) == ""
    assert ready.summary_due(Settings(morning_summary=False), accs, now=ts(2026, 10, 27, 12)) == ""
    store = Store(tmp_path / "lib.json")
    assert ready.summary(store, accs, now=NOW) == ""
    items = store.add_paths([clipmonster_folder(tmp_path)])
    store.enqueue([i.id for i in items], a.id)
    ready.reminders(store, accs, s, now=NOW)  # plans the times
    assert ready.summary(store, accs, now=NOW).startswith("2 for @cats (next Today")


def test_clipmonster_output_folders(tmp_path):
    cm = tmp_path / "appdata" / "ClipMonster"
    cm.mkdir(parents=True)
    assert clipmonster.output_folders()[0].name == "Automation"
    (cm / "settings.json").write_text(json.dumps({"output_dir": str(tmp_path / "Clips")}), encoding="utf-8")
    assert clipmonster.output_folders() == [tmp_path / "Clips" / "Automation", tmp_path / "Clips"]
    (cm / "settings.json").write_text(json.dumps({"output_dir": str(tmp_path / "Clips"),
                                                  "auto_output_dir": str(tmp_path / "Auto")}), encoding="utf-8")
    assert clipmonster.output_folders()[0] == tmp_path / "Auto"


def test_settings_round_trip_keeps_watch_folders(tmp_path):
    s = Settings(watch_folders=[str(tmp_path / "a")], watch_target="spread")
    s.save()
    again = Settings.load()
    assert again.watch_folders == [str(tmp_path / "a")] and again.watch_target == "spread"


def test_undo_mark_posted_puts_it_back_on_top(tmp_path):
    accs, a, _ = two_accounts()
    store = Store(tmp_path / "lib.json")
    items = store.add_paths([clipmonster_folder(tmp_path)])
    store.enqueue([i.id for i in items], a.id)
    second = ready.ready(store, a.id)[1]
    ready.mark_posted(store, second.id, now=NOW)
    assert ready.unmark_posted(store, second.id, a.id)
    assert ready.ready(store, a.id)[0] is second and second.status == "queued" and second.posted_at is None
    assert not ready.unmark_posted(store, second.id, a.id)  # it isn't posted


def test_watch_report_counts_what_came_in_and_what_clipmonster_set_aside(tmp_path):
    folder = clipmonster_folder(tmp_path)
    late = tmp_path / "Automation" / "late.mp4"
    late.write_bytes(b"x")
    os.utime(late, (NOW - 5, NOW - 5))  # still being written
    store = Store(tmp_path / "lib.json")
    root = str(tmp_path / "Automation")
    [before] = ready.watch_report(store, [root], now=NOW)
    assert (before.clips, before.in_app, before.rejected, before.waiting) == (2, 0, 1, 1)
    items = ready.import_watched(store, [root], now=NOW)
    store.remove([next(i.id for i in items if i.title == "fine")])
    [after] = ready.watch_report(store, [root, ""], now=NOW)
    assert (after.clips, after.in_app, after.removed, after.rejected, after.waiting) == (2, 1, 1, 1, 1)
    [gone] = ready.watch_report(store, [str(tmp_path / "nope")], now=NOW)
    assert not gone.exists
    assert folder.is_dir()


def test_watched_folder_paths_match_however_they_are_written(tmp_path):
    assert ready.same_folder(f"{tmp_path}/TTVIDEOS/", str(tmp_path / "TTVIDEOS"))
    assert ready.watched([str(tmp_path / "TTVIDEOS")], tmp_path / "TTVIDEOS" / "zanny")
    assert not ready.watched([str(tmp_path / "TTVIDEOS")], tmp_path / "Other")
    if os.name == "nt":
        assert ready.same_folder("C:/Users/J/OneDrive/Documents/TTVIDEOS", r"c:\users\j\onedrive\documents\ttvideos")


def test_clipmonster_folder_not_watched_is_noticed(tmp_path):
    s = Settings()
    assert ready.clipmonster_unwatched(s) is None  # ClipMonster isn't on this PC
    cm = tmp_path / "appdata" / "ClipMonster"
    cm.mkdir(parents=True)
    auto = tmp_path / "OneDrive" / "Documents" / "TTVIDEOS"
    (cm / "settings.json").write_text(json.dumps({"output_dir": str(tmp_path / "Videos"),
                                                  "auto_output_dir": auto.as_posix()}), encoding="utf-8")
    assert ready.clipmonster_unwatched(s) is None  # Automation hasn't made anything yet
    auto.mkdir(parents=True)
    assert ready.clipmonster_unwatched(s) == auto
    s.watch_folders = [str(tmp_path / "Videos" / "Automation")]  # the default place, not where it saves now
    assert ready.clipmonster_unwatched(s) == auto
    s.watch_folders.append(str(auto) + os.sep)
    assert ready.clipmonster_unwatched(s) is None


def test_clips_only_in_the_library_can_be_found_to_queue(tmp_path):
    folder = clipmonster_folder(tmp_path)
    store = Store(tmp_path / "lib.json")
    items = ready.import_watched(store, [str(tmp_path / "Automation")], now=NOW)
    store.add_paths([folder / "loose.mp4"])  # not from a watched folder: never offered
    assert sorted(i.title for i in ready.unqueued_watched(store)) == ["fine", "good"]
    store.enqueue([items[0].id], "acc1")
    assert len(ready.unqueued_watched(store)) == 1


def test_a_single_watch_folder_saved_as_text_still_works(tmp_path):
    path = Settings.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"watch_folders": str(tmp_path / "TTVIDEOS")}), encoding="utf-8")
    assert Settings.load().watch_folders == [str(tmp_path / "TTVIDEOS")]


# --- ClipMonster 2.3 niches and 2.4 part series ------------------------------------------


def niche_folder(tmp_path, name, account, niche="Gaming", series=False):
    folder = tmp_path / "Automation" / niche / "Chan" / name
    folder.mkdir(parents=True)
    clips = []
    for n in (1, 2, 3):
        f = f"0{n} - clip{n}.mp4"
        (folder / f).write_bytes(b"x")
        rec = {"file": f, "title": f"{name} {n}", "score": 90 - n * 10, "qc": {"ok": True}}
        if series:
            rec.update({"series": "series-1", "part": 4 - n, "parts": 3})  # files not in part order
        clips.append(rec)
    (folder / "clips.json").write_text(json.dumps({
        "video": {"id": name, "title": name, "channel": "Chan"},
        "automation": {"permission": "allowed", "niche": niche, "niche_id": f"id-{niche.lower()}",
                       "account": account},
        "clips": clips}), encoding="utf-8")
    return folder


@pytest.mark.parametrize("named", ["dogs", "@Dogs", " DOGS "])
def test_niche_clips_go_to_the_account_their_niche_names(tmp_path, named):
    accs, a, b = two_accounts()
    niche_folder(tmp_path, "vid1", named)
    store = Store(tmp_path / "lib.json")
    items = ready.import_watched(store, [str(tmp_path / "Automation")])
    assert {i.niche for i in items} == {"Gaming"} and {i.niche_id for i in items} == {"id-gaming"}
    got = ready.assign(store, accs, items, "library")  # the niche wins over "leave them in the Library"
    assert got == {b.id: 3}


def test_niche_clips_without_a_matching_account_follow_the_new_clips_setting(tmp_path):
    accs, a, b = two_accounts()
    niche_folder(tmp_path, "vid1", "", niche="Blank")
    niche_folder(tmp_path, "vid2", "@nobody", niche="Other")
    store = Store(tmp_path / "lib.json")
    items = ready.import_watched(store, [str(tmp_path / "Automation")])
    assert ready.assign(store, accs, items, "library") == {}
    assert ready.assign(store, accs, items, a.id) == {a.id: 6}


def test_series_parts_are_queued_in_order_one_after_another(tmp_path):
    accs, a, b = two_accounts()
    niche_folder(tmp_path, "vid1", "", series=True)
    niche_folder(tmp_path, "vid2", "", niche="Other")
    store = Store(tmp_path / "lib.json")
    items = ready.import_watched(store, [str(tmp_path / "Automation")])
    parts = [i for i in items if i.series]
    assert len(parts) == 3 and len({i.series for i in parts}) == 1 and parts[0].part_label.endswith("of 3")
    ready.assign(store, accs, items, "spread")
    q = store.queue(a.id) + store.queue(b.id)
    in_series = [i for i in q if i.series]
    assert len({i.account for i in in_series}) == 1  # all parts on one account
    order = [i.part for i in store.queue(in_series[0].account) if i.series]
    assert order == [1, 2, 3]
    pos = [i.queue_pos for i in store.queue(in_series[0].account) if i.series]
    assert pos == list(range(pos[0], pos[0] + 3))  # back to back


def test_smart_order_keeps_a_series_together(tmp_path):
    from tokmanager import schedule
    from tokmanager.store import VideoItem

    def item(score, group, series="", part=None):
        return VideoItem(path=f"C:/{score}.mp4", score={"total": score}, source_group=group, series=series, part=part)

    p1, p2, p3 = item(60, "S", "S#1", 1), item(95, "S", "S#1", 2), item(50, "S", "S#1", 3)
    x, y = item(80, "X"), item(70, "Y")
    out = schedule.spread([x, p3, y, p1, p2])
    assert out == [p1, p2, p3, x, y]  # placed by its best part (95), in part order


def test_series_ids_from_two_videos_dont_mix(tmp_path):
    niche_folder(tmp_path, "vid1", "", series=True)
    niche_folder(tmp_path, "vid2", "", series=True)
    store = Store(tmp_path / "lib.json")
    items = ready.import_watched(store, [str(tmp_path / "Automation")])
    assert len({i.series for i in items}) == 2  # both say "series-1"


def test_clips_cleaned_up_after_posting_cause_no_errors_or_reimport(tmp_path):
    import shutil

    accs, a, b = two_accounts()
    folder = niche_folder(tmp_path, "vid1", "cats")
    root = str(tmp_path / "Automation")
    store = Store(tmp_path / "lib.json")
    items = ready.import_watched(store, [root])
    ready.assign(store, accs, items, "library")
    first, second, third = sorted(store.queue(a.id), key=lambda i: i.queue_pos)
    ready.mark_posted(store, first.id)
    store.remove([third.id])  # removed in TikTok Manager: ClipMonster's Clean up deletes it too
    # Clean up deletes the posted and removed clips' files, then the whole folder once it's empty
    (folder / Path(first.path).name).unlink()
    (folder / Path(third.path).name).unlink()
    assert ready.import_watched(store, [root]) == []
    ready.mark_posted(store, second.id)
    shutil.rmtree(folder)
    assert ready.import_watched(store, [root]) == []
    reports = ready.watch_report(store, [root])
    assert reports[0].exists and reports[0].clips == 0
    assert store.get(first.id).status == "posted" and len(store.posted(a.id)) == 2
    store.save()
    again = Store(tmp_path / "lib.json")
    assert {i.id for i in again.posted(a.id)} == {first.id, second.id}
    assert ready.import_watched(again, [root]) == []
