"""Clean up: a record of each posted video, then its files go; nothing something else needs."""

import datetime as dt
import json
from pathlib import Path

import pytest

from tokmanager import cleanup, ready, results
from tokmanager.accounts import Accounts
from tokmanager.config import Settings, thumbs_dir
from tokmanager.store import Store, TikTokVideo

DAY = 86400


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(cleanup, "documents_dir", lambda: tmp_path / "Documents")


NOW = dt.datetime(2026, 10, 28, 12).timestamp()


def clip_folder(root: Path, niche="Gaming", video="Big Talk [abc]", clips=("01 - good.mp4", "02 - fine.mp4")):
    folder = root / niche / "Pod People" / video
    (folder / "thumbnails").mkdir(parents=True)
    rows = []
    for rank, name in enumerate(clips, 1):
        (folder / name).write_bytes(b"v" * 1000 * rank)
        (folder / name).with_suffix(".srt").write_text("1\n00:00 --> 00:01\nhi\n", encoding="utf-8")
        (folder / "thumbnails" / f"{rank:02d}.jpg").write_bytes(b"j" * 10)
        rows.append({"file": name, "rank": rank, "title": Path(name).stem, "score": 80, "hook": f"Hook {rank}",
                     "qc": {"ok": True}})
    (folder / "clips.json").write_text(json.dumps({
        "video": {"id": "abc", "title": "Big Talk", "channel": "Pod People"}, "source": "https://youtu.be/abc",
        "automation": {"niche": niche, "niche_id": "n1", "account": "cats"},
        "qc": {"passed": len(clips), "rejected": 0, "warned": 0}, "clips": rows}), encoding="utf-8")
    readme = ["# Big Talk", "", "| # | Clip |", "|---|---|"]
    for rank, name in enumerate(clips, 1):
        readme += [f"| {rank} | [{Path(name).stem}](<{name}>) |", "| | more about it |"]
    (folder / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return folder


def setup(tmp_path, **kw):
    root = tmp_path / "TTVIDEOS"
    folder = clip_folder(root, **kw)
    s = Settings(watch_folders=[str(root)])
    accs = Accounts.load(s)
    a = accs.items[0]
    a.name, a.username = "Cat Clips", "cats"
    store = Store(tmp_path / "library.json")
    items = ready.import_watched(store, [str(root)], now=NOW)
    for it in items:
        thumb = thumbs_dir() / f"{it.id}.jpg"
        thumb.write_bytes(b"t" * 100)
        it.thumb = str(thumb)
        it.info = {"duration": 34.4, "width": 1080, "height": 1920, "fps": 30.0, "size_bytes": 1000}
    return store, s, accs, a, folder, root, sorted(items, key=lambda i: i.path)


def post(store, accs, it, when=NOW - 3 * DAY, linked=True):
    store.enqueue([it.id], accs.items[0].id)
    ready.mark_posted(store, it.id, now=when, via="browser")
    if linked:
        v = TikTokVideo(id=f"7{it.id}", description=f"{it.hook} #fyp", create_time=when,
                        url=f"https://www.tiktok.com/@cats/video/7{it.id}", views=1234, likes=56, comments=7, shares=2)
        store.update_tiktok_videos(accs.items[0].id, [v], now=when + DAY)
        assert it.tiktok_id


def test_posted_clip_leaves_a_record_and_its_files_go(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    post(store, accs, good)
    p = cleanup.plan(store, accs, s, now=NOW)
    assert [t.item_id for t in p.targets] == [good.id]
    t = p.targets[0]
    assert t.ticked and t.origin == "ClipMonster clip" and t.account == "Cat Clips"
    names = {f.name for f in t.files}
    assert names == {"01 - good.mp4", "01 - good.srt", "01.jpg", f"{good.id}.jpg"}
    assert t.size == 1000 + (folder / "01 - good.srt").stat().st_size + 10 + 100

    r = cleanup.apply(store, accs, s, p, [good.id], now=NOW)
    assert r.cleaned == 1 and r.files == 4 and not r.failed and r.freed == t.size
    assert not (folder / "01 - good.mp4").exists() and not (folder / "01 - good.srt").exists()
    assert not (folder / "thumbnails" / "01.jpg").exists() and not Path(good.thumb or "x").exists()
    assert (folder / "02 - fine.mp4").exists() and (folder / "thumbnails" / "02.jpg").exists()
    data = json.loads((folder / "clips.json").read_text(encoding="utf-8"))
    assert [c["file"] for c in data["clips"]] == ["02 - fine.mp4"] and data["qc"]["passed"] == 1
    readme = (folder / "README.md").read_text(encoding="utf-8")
    assert "01 - good" not in readme and "02 - fine" in readme and readme.count("more about it") == 1

    record = r.records[0]
    assert record.parent == tmp_path / "Documents" / "TikTok Manager" / "Posted videos" / "Cat Clips"
    assert record.name == "2026-10-25 12.00 - Hook 1.txt"
    text = record.read_text(encoding="utf-8")
    for bit in ("Title:        Hook 1", "Account:      Cat Clips (@cats)", "posted by TikTok Manager",
                f"https://www.tiktok.com/@cats/video/7{good.id}", "Hook 1 #fyp", "1,234 views", "56 likes",
                "Length:       0:34", "1080×1920, 30 fps", "Channel:      Pod People", "Niche:        Gaming",
                "https://youtu.be/abc", "the video and its copies were deleted"):
        assert bit in text, bit

    # The entry stays, so its history, stats and what ClipMonster learns carry on
    assert good.cleaned_at == NOW and good.record == str(record) and good.thumb == ""
    assert good.status == "posted" and store.video_for(good) is not None
    others = [TikTokVideo(id=f"other{n}", create_time=NOW - 9 * DAY, views=500) for n in range(2)]
    store.update_tiktok_videos(a.id, others, now=NOW)
    assert [o.item_id for o in results.outcomes(store, now=NOW)] == [good.id]
    store.save()
    again = Store(tmp_path / "library.json")
    assert again.get(good.id).cleaned_at == NOW and again.get(good.id).record == str(record)
    # Nothing comes back in, and a second look finds nothing to do
    assert ready.import_watched(again, [str(root)], now=NOW + DAY) == []
    assert cleanup.plan(again, accs, s, now=NOW).targets == []


def test_the_last_clip_takes_its_folder_but_not_the_niche_or_watched_folder(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    post(store, accs, good)
    post(store, accs, fine)
    p = cleanup.plan(store, accs, s, now=NOW)
    r = cleanup.apply(store, accs, s, p, [t.item_id for t in p.targets], now=NOW)
    assert r.cleaned == 2 and not r.failed
    assert not folder.exists() and not folder.parent.exists()  # the video's and the channel's folders
    assert (root / "Gaming").is_dir() and root.is_dir()  # the niche's folder and the watched folder stay
    assert len(r.records) == 2


def test_a_file_still_queued_for_another_account_stays(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    b = accs.add("Dogs", copy_schedule_from=a)
    post(store, accs, good)
    store.copy_for(good.id, b.id)  # the same file, queued for another account
    p = cleanup.plan(store, accs, s, now=NOW)
    t = p.targets[0]
    assert t.files == [] and t.kept == ["The video stays: it's also queued."]
    r = cleanup.apply(store, accs, s, p, [good.id], now=NOW)
    assert r.cleaned == 1 and r.kept == 1 and r.files == 0
    assert Path(good.path).exists() and (folder / "01 - good.srt").exists()
    assert "The video stays: it's also queued." in r.records[0].read_text(encoding="utf-8")
    assert len(json.loads((folder / "clips.json").read_text(encoding="utf-8"))["clips"]) == 2


def test_two_posted_copies_share_a_file_until_both_are_cleared(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    b = accs.add("Dogs", copy_schedule_from=a)
    post(store, accs, good)
    twin = store.copy_for(good.id, b.id)
    ready.mark_posted(store, twin.id, now=NOW - 2 * DAY)
    p = cleanup.plan(store, accs, s, now=NOW)
    assert len(p.targets) == 2
    cleanup.apply(store, accs, s, p, [good.id], now=NOW)  # the other one unticked
    assert Path(good.path).exists()
    p = cleanup.plan(store, accs, s, now=NOW)
    assert [t.item_id for t in p.targets] == [twin.id] and Path(good.path) in p.targets[0].files
    cleanup.apply(store, accs, s, p, [twin.id], now=NOW)
    assert not Path(good.path).exists()


def test_videos_not_seen_on_tiktok_start_unticked_and_recent_ones_wait(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    post(store, accs, good, linked=False)
    post(store, accs, fine, when=NOW - 3600)
    p = cleanup.plan(store, accs, s, now=NOW)
    assert {t.item_id: t.ticked for t in p.targets} == {good.id: False, fine.id: True}
    p = cleanup.plan(store, accs, s, older_than_days=1, now=NOW)
    assert [t.item_id for t in p.targets] == [good.id] and p.waiting == 1
    rec = cleanup.record_text(store, good, a, s, NOW)
    assert "not found on TikTok yet" in rec and "Caption as posted:" in rec


def test_a_record_that_cant_be_written_keeps_the_video(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    post(store, accs, good)
    p = cleanup.plan(store, accs, s, now=NOW)
    p.folder.parent.mkdir(parents=True)
    p.folder.write_text("in the way", encoding="utf-8")  # a file where the folder should be
    r = cleanup.apply(store, accs, s, p, [good.id], now=NOW)
    assert r.cleaned == 0 and r.failed and "record" in r.failed[0][1]
    assert Path(good.path).exists() and not good.cleaned_at


def test_a_video_that_cant_be_deleted_keeps_its_clips_json_row(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    post(store, accs, good)
    p = cleanup.plan(store, accs, s, now=NOW)

    def remove(path: Path) -> None:
        if path.suffix == ".mp4":
            raise OSError("in use")
        path.unlink()

    r = cleanup.apply(store, accs, s, p, [good.id], remove=remove, now=NOW)
    assert r.failed == [(str(Path(good.path)), "in use")] and r.cleaned == 0
    assert Path(good.path).exists() and not (folder / "01 - good.srt").exists()
    assert len(json.loads((folder / "clips.json").read_text(encoding="utf-8"))["clips"]) == 2
    # It stays on the list, and the next clean-up finishes it, keeping the one record
    assert not good.cleaned_at and Path(good.record).is_file()
    p = cleanup.plan(store, accs, s, now=NOW)
    assert [f.name for f in p.targets[0].files] == ["01 - good.mp4"]
    r = cleanup.apply(store, accs, s, p, [good.id], now=NOW)
    assert r.cleaned == 1 and not Path(good.path).exists() and good.cleaned_at
    assert len(list(Path(good.record).parent.iterdir())) == 1
    assert len(json.loads((folder / "clips.json").read_text(encoding="utf-8"))["clips"]) == 1


def test_copies_made_with_copy_to_a_folder_go_with_their_captions(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    s.ready_folder = str(tmp_path / "Ready")
    store.enqueue([good.id], a.id)
    out, n = ready.export_ready(store, a, s, now=NOW - 4 * DAY)
    assert n == 1 and len(list(out.iterdir())) == 2
    ready.mark_posted(store, good.id, now=NOW - 3 * DAY)
    p = cleanup.plan(store, accs, s, now=NOW)
    assert {f.name for f in p.targets[0].files} >= {"01 - Hook 1.mp4", "01 - Hook 1.txt"}
    cleanup.apply(store, accs, s, p, [good.id], now=NOW)
    assert not out.exists() and not out.parent.exists() and (tmp_path / "Ready").is_dir()


def test_a_video_already_deleted_still_gets_its_record(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    post(store, accs, good)
    Path(good.path).unlink()  # ClipMonster's own Clean up got there first
    p = cleanup.plan(store, accs, s, now=NOW)
    assert [f.name for f in p.targets[0].files] == [f"{good.id}.jpg"]
    r = cleanup.apply(store, accs, s, p, [good.id], now=NOW)
    assert r.cleaned == 1 and r.records[0].is_file() and good.cleaned_at


def test_only_ticked_videos_in_the_plan_are_cleared(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    post(store, accs, good)
    p = cleanup.plan(store, accs, s, now=NOW)
    # fine is not posted: an id outside the plan is ignored
    r = cleanup.apply(store, accs, s, p, [fine.id], now=NOW)
    assert r.cleaned == 0 and Path(fine.path).exists() and Path(good.path).exists()


def test_records_with_the_same_name_dont_overwrite_each_other(tmp_path):
    store, s, accs, a, folder, root, (good, fine) = setup(tmp_path)
    fine.hook = fine.caption = good.hook
    post(store, accs, good)
    post(store, accs, fine)
    p = cleanup.plan(store, accs, s, now=NOW)
    r = cleanup.apply(store, accs, s, p, [t.item_id for t in p.targets], now=NOW)
    assert sorted(f.name for f in r.records) == ["2026-10-25 12.00 - Hook 1 (2).txt", "2026-10-25 12.00 - Hook 1.txt"]


def test_summary_lines():
    r = cleanup.Result(cleaned=2, freed=3 * 1024 ** 2, folder=Path("Docs"))
    assert r.summary() == "Cleaned up 2 posted videos: 3.0 MB freed. A record of each is in Docs."
    assert "Recycle Bin" in r.summary(trashed=True)
    assert cleanup.Result().summary() == "Nothing was cleaned up."
    assert cleanup.size_text(512) == "512 bytes" and cleanup.size_text(5 * 1024 ** 3) == "5.0 GB"
