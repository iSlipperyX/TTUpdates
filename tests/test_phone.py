"""The phone folder: today's clips with their captions, kept up to date as you post, reorder and delete."""

import datetime as dt

import pytest

from tokmanager import jobs, phone, ready
from tokmanager.accounts import Accounts
from tokmanager.config import Settings
from tokmanager.store import Store


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


NOW = dt.datetime(2026, 10, 27, 10, 0).timestamp()  # a Tuesday; slots 15:00 and 21:00 left, then Wed 09:00


def setup(tmp_path, names=("alpha", "bravo", "charlie"), accounts=1):
    clips = tmp_path / "clips"
    clips.mkdir(exist_ok=True)
    for n, name in enumerate(names):
        (clips / f"{name}.mp4").write_bytes(b"v" * (100 + n))
    accs = Accounts.load(Settings())
    a = accs.items[0]
    a.username = "cats"
    for n in range(1, accounts):
        accs.add(f"Account {n + 1}", copy_schedule_from=a)
    store = Store(tmp_path / "lib.json")
    items = sorted(store.add_paths([clips]), key=lambda i: i.title)
    store.enqueue([i.id for i in items], a.id)
    jobs.replan(store, accs, Settings(), NOW)
    return store, accs, a, items, tmp_path / "Phone"


def files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()
                  and p.name != phone.MANIFEST)


def test_the_next_24_hours_with_captions_in_posting_order(tmp_path):
    store, accs, a, items, folder = setup(tmp_path, names=("alpha", "bravo", "charlie", "delta"))
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert files(folder) == ["01 Tue 15.00 - alpha.mp4", "01 Tue 15.00 - alpha.txt",
                             "02 Tue 21.00 - bravo.mp4", "02 Tue 21.00 - bravo.txt",
                             "03 Wed 09.00 - charlie.mp4", "03 Wed 09.00 - charlie.txt"]  # delta is Wednesday 15:00
    assert r.copied == 3 and r.on_phone == [i.id for i in items[:3]]
    assert (folder / "01 Tue 15.00 - alpha.txt").read_text(encoding="utf-8") == \
        ready.post_text(items[0], a, Settings()) + "\n"
    before = {f: (folder / f).stat().st_mtime_ns for f in files(folder)}
    again = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert again.copied == again.removed == 0
    assert {f: (folder / f).stat().st_mtime_ns for f in files(folder)} == before  # nothing rewritten


def test_posted_clips_leave_and_the_rest_are_renamed_not_copied_again(tmp_path):
    store, accs, a, items, folder = setup(tmp_path, names=("alpha", "bravo", "charlie", "delta"))
    phone.sync(store, accs, Settings(), folder, now=NOW)
    ready.mark_posted(store, items[0].id, now=NOW)
    jobs.replan(store, accs, Settings(), NOW)
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    # the rest move up a slot (renamed, not copied); delta now fits on Wednesday morning and is copied
    assert files(folder) == ["01 Tue 15.00 - bravo.mp4", "01 Tue 15.00 - bravo.txt",
                             "02 Tue 21.00 - charlie.mp4", "02 Tue 21.00 - charlie.txt",
                             "03 Wed 09.00 - delta.mp4", "03 Wed 09.00 - delta.txt"]
    assert (r.copied, r.removed) == (1, 1)


def test_two_clips_can_swap_places(tmp_path):
    store, accs, a, items, folder = setup(tmp_path, names=("same", "same "))  # two clips, one name
    for it in items:
        it.title = "Same hook"
    phone.sync(store, accs, Settings(), folder, now=NOW)
    sizes = {p.name: p.stat().st_size for p in folder.glob("*.mp4")}
    store.set_order([items[1].id, items[0].id])
    jobs.replan(store, accs, Settings(), NOW)
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    swapped = {p.name: p.stat().st_size for p in folder.glob("*.mp4")}
    assert r.copied == 0 and sorted(swapped) == sorted(sizes)
    assert swapped["01 Tue 15.00 - Same hook.mp4"] == sizes["02 Tue 21.00 - Same hook.mp4"]


def test_a_clip_deleted_on_the_phone_is_not_copied_back_until_asked(tmp_path):
    store, accs, a, items, folder = setup(tmp_path)
    phone.sync(store, accs, Settings(), folder, now=NOW)
    (folder / "01 Tue 15.00 - alpha.mp4").unlink()  # posted from the phone, then deleted there
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert r.gone == [items[0].id] and "01 Tue 15.00 - alpha.txt" not in files(folder)
    assert phone.sync(store, accs, Settings(), folder, now=NOW).gone == [items[0].id]  # stays gone
    phone.forget_gone(folder, [items[0].id])  # "Put it back in the folder"
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert r.gone == [] and "01 Tue 15.00 - alpha.mp4" in files(folder)
    (folder / "01 Tue 15.00 - alpha.mp4").unlink()
    phone.sync(store, accs, Settings(), folder, now=NOW)
    ready.mark_posted(store, items[0].id, now=NOW)  # "Mark as posted": it's forgotten
    jobs.replan(store, accs, Settings(), NOW)
    assert phone.sync(store, accs, Settings(), folder, now=NOW).gone == []


def test_autopilot_accounts_stay_out_and_several_accounts_get_subfolders(tmp_path):
    store, accs, a, items, folder = setup(tmp_path, accounts=2)
    b = accs.items[1]
    store.move_to([items[2].id], b.id)
    jobs.replan(store, accs, Settings(), NOW)
    phone.sync(store, accs, Settings(), folder, now=NOW)
    assert files(folder) == ["@cats/01 Tue 15.00 - alpha.mp4", "@cats/01 Tue 15.00 - alpha.txt",
                             "@cats/02 Tue 21.00 - bravo.mp4", "@cats/02 Tue 21.00 - bravo.txt",
                             "Account 2/01 Tue 15.00 - charlie.mp4", "Account 2/01 Tue 15.00 - charlie.txt"]
    b.autopilot = True  # posts by itself: nothing for the phone
    phone.sync(store, accs, Settings(), folder, now=NOW)
    assert files(folder) == ["01 Tue 15.00 - alpha.mp4", "01 Tue 15.00 - alpha.txt", "02 Tue 21.00 - bravo.mp4",
                             "02 Tue 21.00 - bravo.txt"]  # one account left: no subfolder
    assert not (folder / "@cats").exists() and not (folder / "Account 2").exists()


def test_files_the_app_did_not_put_there_are_left_alone(tmp_path):
    store, accs, a, items, folder = setup(tmp_path)
    folder.mkdir()
    (folder / "notes.txt").write_text("mine", encoding="utf-8")
    (folder / "Mine").mkdir()
    phone.sync(store, accs, Settings(), folder, now=NOW)
    for it in items:
        store.dequeue([it.id])
    phone.sync(store, accs, Settings(), folder, now=NOW)
    assert files(folder) == ["notes.txt"] and (folder / "Mine").is_dir()


def test_onedrive_is_found_from_the_environment(tmp_path, monkeypatch):
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    assert phone.suggested_folder() is None
    monkeypatch.setenv("OneDrive", str(tmp_path))
    assert phone.suggested_folder() == tmp_path / "TikTok Ready"


def test_captions_follow_their_clips_when_two_swap_places(tmp_path):
    store, accs, a, items, folder = setup(tmp_path, names=("same", "same "))
    for it, tag in zip(items, ("#one", "#two")):
        it.title, it.hashtags = "Same hook", tag
    phone.sync(store, accs, Settings(), folder, now=NOW)
    store.set_order([items[1].id, items[0].id])
    jobs.replan(store, accs, Settings(), NOW)
    phone.sync(store, accs, Settings(), folder, now=NOW)
    for name, it in (("01 Tue 15.00 - Same hook.txt", items[1]), ("02 Tue 21.00 - Same hook.txt", items[0])):
        assert (folder / name).read_text(encoding="utf-8") == ready.post_text(it, a, Settings()) + "\n"


def test_a_sync_that_fails_halfway_carries_on_next_time(tmp_path, monkeypatch):
    store, accs, a, items, folder = setup(tmp_path, names=("alpha", "bravo", "charlie", "delta"))
    phone.sync(store, accs, Settings(), folder, now=NOW)
    store.set_order([items[3].id] + [i.id for i in items[:3]])  # delta first: the others move down a slot
    jobs.replan(store, accs, Settings(), NOW)

    def full(*_a, **_k):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(phone.shutil, "copyfile", full)
        for _ in range(3):  # delta can't be copied, so alpha and bravo stay parked; their names mustn't grow
            with pytest.raises(OSError):
                phone.sync(store, accs, Settings(), folder, now=NOW)
    assert sorted(p.name for p in folder.glob("*.moving")) == ["01 Tue 15.00 - alpha.mp4.moving",
                                                                "02 Tue 21.00 - bravo.mp4.moving"]
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert files(folder) == ["01 Tue 15.00 - delta.mp4", "01 Tue 15.00 - delta.txt",
                             "02 Tue 21.00 - alpha.mp4", "02 Tue 21.00 - alpha.txt",
                             "03 Wed 09.00 - bravo.mp4", "03 Wed 09.00 - bravo.txt"]
    assert r.copied == 1 and r.gone == []  # alpha and bravo were renamed, not copied again


def test_a_sync_stopped_before_it_could_save_isnt_taken_for_deletions(tmp_path):
    store, accs, a, items, folder = setup(tmp_path)
    phone.sync(store, accs, Settings(), folder, now=NOW)
    clip = folder / "01 Tue 15.00 - alpha.mp4"
    clip.replace(clip.with_name(clip.name + ".moving"))  # parked, then the PC switched off
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert r.gone == [] and clip.exists() and not list(folder.glob("*.moving"))


def test_a_posted_clip_that_cant_be_deleted_yet_goes_next_time(tmp_path, monkeypatch):
    from pathlib import Path

    store, accs, a, items, folder = setup(tmp_path)
    phone.sync(store, accs, Settings(), folder, now=NOW)
    ready.mark_posted(store, items[0].id, now=NOW)
    jobs.replan(store, accs, Settings(), NOW)
    unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self.name == "01 Tue 15.00 - alpha.mp4":
            raise PermissionError(13, "The file is open in another program")
        return unlink(self, missing_ok=missing_ok)

    with monkeypatch.context() as m:
        m.setattr(Path, "unlink", locked)
        r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert "01 Tue 15.00 - alpha.mp4" in files(folder) and "01 Tue 15.00 - alpha.txt" not in files(folder)
    assert "01 Tue 15.00 - bravo.mp4" in files(folder) and r.removed == 0  # the rest still caught up
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert "01 Tue 15.00 - alpha.mp4" not in files(folder) and r.removed == 1


def test_accounts_with_names_alike_get_their_own_subfolders(tmp_path):
    store, accs, a, items, folder = setup(tmp_path, accounts=2)
    b = accs.items[1]
    a.name, b.name = "Cats", "cats:"  # Windows sees "Cats" and "cats" as one folder
    store.move_to([items[2].id], b.id)
    jobs.replan(store, accs, Settings(), NOW)
    r = phone.sync(store, accs, Settings(), folder, now=NOW)
    assert files(folder) == ["Cats/01 Tue 15.00 - alpha.mp4", "Cats/01 Tue 15.00 - alpha.txt",
                             "Cats/02 Tue 21.00 - bravo.mp4", "Cats/02 Tue 21.00 - bravo.txt",
                             "cats (2)/01 Tue 15.00 - charlie.mp4", "cats (2)/01 Tue 15.00 - charlie.txt"]
    assert phone.sync(store, accs, Settings(), folder, now=NOW).gone == [] and len(r.on_phone) == 3


def test_a_phone_folder_inside_a_watched_folder_isnt_imported_again(tmp_path):
    import time

    store, accs, a, items, folder = setup(tmp_path, accounts=2)
    clips = tmp_path / "clips"
    folder = clips / "Phone"
    store.move_to([items[2].id], accs.items[1].id)
    jobs.replan(store, accs, Settings(), NOW)
    later = time.time() + 3600  # long after the copies were written
    phone.sync(store, accs, Settings(), folder, now=NOW)  # in subfolders, one per account
    assert len(list(folder.rglob("*.mp4"))) == 3 and ready.import_watched(store, [str(clips)], now=later) == []
    accs.items[1].autopilot = True
    phone.sync(store, accs, Settings(), folder, now=NOW)  # one account left: in the folder itself
    (folder / "mine.mp4").write_bytes(b"m" * 50)  # one you put there yourself does come in
    assert [i.title for i in ready.import_watched(store, [str(clips)], now=later)] == ["mine"]
    (report,) = ready.watch_report(store, [str(clips)], now=later)
    assert (report.clips, report.in_app, report.waiting) == (4, 4, 0)


def test_another_pcs_clips_in_the_same_folder_are_left_alone(tmp_path):
    import json

    store, accs, a, items, folder = setup(tmp_path)
    folder.mkdir()
    theirs = folder / "01 Mon 09.00 - theirs.mp4"
    theirs.write_bytes(b"t" * 10)
    (folder / ".tiktokmanager-phone-OTHER-PC.json").write_text(json.dumps(
        {"version": 1, "items": {"abc": {"video": theirs.name, "text": "", "size": 10}}}), encoding="utf-8")
    phone.sync(store, accs, Settings(), folder, now=NOW)
    for it in items:
        store.dequeue([it.id])
    phone.sync(store, accs, Settings(), folder, now=NOW)
    assert theirs.exists() and phone.MANIFEST != ".tiktokmanager-phone-OTHER-PC.json"
    assert phone.is_copy(theirs)  # still not one to import


def test_file_names_dont_depend_on_the_language():
    import locale

    before = locale.setlocale(locale.LC_TIME)
    for name in ("de_DE.UTF-8", "fr_FR.UTF-8", "German_Germany.1252"):  # whichever this machine has
        try:
            locale.setlocale(locale.LC_TIME, name)
            break
        except locale.Error:
            continue
    try:
        assert phone._when(NOW) == "Tue 10.00"  # not "Di 10.00"
    finally:
        locale.setlocale(locale.LC_TIME, before)
