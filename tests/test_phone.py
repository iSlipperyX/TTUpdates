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
