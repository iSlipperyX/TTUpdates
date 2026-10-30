"""Google Drive: signing in, and bringing in the clips ClipMonster uploaded (against a fake Google)."""

import hashlib
import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from fake_google import FakeGoogle

CREATED = "2026-10-29T12:00:00.000Z"
NOW = 1793361600.0  # 2026-10-29 16:00 UTC, a few hours after CREATED


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-MAIN")
    from tokmanager import gdrive

    gdrive._tokens.clear()
    yield
    gdrive._tokens.clear()


@pytest.fixture
def google(monkeypatch):
    server = FakeGoogle()
    monkeypatch.setenv("TIKTOKMANAGER_GOOGLE_BASE", server.url)
    yield server
    server.stop()


def browser_that_allows(code="good-code"):
    """Stands in for the browser: Google's page says yes at once and goes back to TikTok Manager."""
    def open_browser(url: str) -> None:
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        assert q["scope"].split()[0].endswith("/drive.file") and q["code_challenge_method"] == "S256"
        back = q["redirect_uri"] + "/?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
        urllib.request.urlopen(back, timeout=10).read()
    return open_browser


def settings(tmp_path, google, **kw):
    from tokmanager.config import Settings

    s = Settings(drive_on=True, drive_client_id=google.client_id, drive_client_secret=google.client_secret,
                 drive_inbox=str(tmp_path / "inbox"))
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def signed_in(google):
    from tokmanager import gdrive

    return gdrive.sign_in(google.client_id, google.client_secret, open_browser=browser_that_allows())


def clip(n: int, size: int = 3000) -> bytes:
    return (f"clip {n} ".encode() * size)[:size]


def describe(title, score=80, source="Big Stream", url="https://www.youtube.com/watch?v=abc", watch=""):
    lines = [title, f"Score {score}/100. Great hook", f"From: {source} {url}"]
    if watch:
        lines.append(f"At: {watch}")
    return "\n".join(lines)


def clipmonster_drive(google, niche="Gaming Videos", channel="Streamer", video="Big Stream", n=2,
                      created=CREATED, index=None):
    """ClipMonster/Automation/<niche>/<channel>/<video>/01 - ….mp4, as ClipMonster 2.8 uploads them."""
    folder = google.folders("ClipMonster", "Automation", niche, channel, video)
    ids = [google.add(f"0{i} - clip.mp4", folder, clip(i), created=created,
                      description=describe(f"Clip {i} title", score=70 + i)) for i in range(1, n + 1)]
    if index is not None:
        google.add("clips.json", folder, json.dumps(index).encode(), created=created)
    return folder, ids


def sync(store, s, **kw):
    from tokmanager import drive_sync

    return drive_sync.sync(store, s, now=kw.pop("now", NOW), **kw)


# ---------------------------------------------------------------- signing in

def test_sign_in_saves_a_lasting_sign_in_and_gets_access_tokens(google):
    from tokmanager import gdrive

    account = signed_in(google)
    assert account.email == "justyn@example.com"
    saved = gdrive.load_account(google.client_id)
    assert saved is not None and saved.refresh_token == account.refresh_token
    assert gdrive.load_account("another-client") is None  # a sign-in is for the client it was made with
    gdrive._tokens.clear()
    assert gdrive.access_token(google.client_id, google.client_secret) == "access-2"  # from the refresh token


def test_a_wrong_client_secret_says_what_to_fix(google):
    from tokmanager import gdrive

    with pytest.raises(gdrive.DriveError) as err:
        gdrive.sign_in(google.client_id, "GOCSPX-wrong", open_browser=browser_that_allows())
    assert err.value.kind == "setup" and "ClipMonster uses" in str(err.value)


def test_sign_in_can_be_cancelled(google):
    from tokmanager import gdrive

    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    with pytest.raises(gdrive.DriveError) as err:
        gdrive.sign_in(google.client_id, google.client_secret, open_browser=lambda url: None, cancelled=stop.is_set)
    assert err.value.kind == "cancelled"


def test_sign_out_forgets_the_sign_in_without_cancelling_it_at_google(google):
    from tokmanager import gdrive

    signed_in(google)
    gdrive.sign_out()
    assert gdrive.load_account() is None
    assert not any("revoke" in path for _, path in google.requests)  # ClipMonster shares the client


def test_a_sign_in_removed_at_google_asks_to_sign_in_again(google, tmp_path):
    from tokmanager.store import Store

    from tokmanager import gdrive

    signed_in(google)
    google.refresh_tokens.clear()
    gdrive._tokens.clear()  # an hour on: the access token from signing in has run out
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert "Sign in again" in res.problem


# ---------------------------------------------------------------- which client

def write_clipmonster_settings(tmp_path, **values):
    folder = tmp_path / "appdata" / "ClipMonster"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text(json.dumps(values), encoding="utf-8")


def test_clipmonsters_client_and_folder_are_used_when_none_is_typed(tmp_path, google):
    from tokmanager import drive_sync
    from tokmanager.config import Settings

    write_clipmonster_settings(tmp_path, gdrive_client_id=google.client_id, gdrive_client_secret=google.client_secret,
                               gdrive_folder="My Clips", upload_target="gdrive")
    s = Settings(drive_on=True)
    assert drive_sync.client(s) == (google.client_id, google.client_secret, True)
    assert drive_sync.folder_name(s) == "My Clips"
    assert "Sign in" in drive_sync.problem(s)
    signed_in(google)
    assert drive_sync.problem(s) == "" and drive_sync.ready_to_sync(s)
    s.drive_folder = "Other"
    assert drive_sync.folder_name(s) == "Other"


def test_without_a_client_it_says_where_to_get_one(tmp_path):
    from tokmanager import drive_sync
    from tokmanager.config import Settings

    assert "ClipMonster → Settings → Upload" in drive_sync.problem(Settings(drive_on=True))


def test_the_client_secret_is_kept_encrypted_like_the_ai_keys():
    from tokmanager.config import SECRET_FIELDS

    assert "drive_client_secret" in SECRET_FIELDS


# ---------------------------------------------------------------- bringing clips in

def test_new_clips_are_downloaded_in_their_folders_and_come_in_with_title_score_and_niche(tmp_path, google):
    from tokmanager import ready
    from tokmanager.store import Store

    signed_in(google)
    _, ids = clipmonster_drive(google)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    res = sync(store, s)
    assert res.problem == "" and res.failed == []
    assert sorted(res.downloaded) == ["01 - clip.mp4", "02 - clip.mp4"] and res.in_drive == 2
    folder = tmp_path / "inbox" / "Automation" / "Gaming Videos" / "Streamer" / "Big Stream"
    assert (folder / "01 - clip.mp4").read_bytes() == clip(1)
    assert not list(folder.glob("*.part"))
    index = json.loads((folder / "clips.json").read_text(encoding="utf-8"))
    assert index["automation"]["niche"] == "Gaming Videos"
    # each clip is marked as this PC's, so TikTok Manager elsewhere leaves it alone
    assert all(google.files[i]["appProperties"]["tm_pc"] == "DESKTOP-MAIN" for i in ids)

    assert str(tmp_path / "inbox") in ready.watch_roots(s)
    items = ready.import_watched(store, ready.watch_roots(s), now=NOW)
    by_name = {Path(i.path).name: i for i in items}
    assert set(by_name) == {"01 - clip.mp4", "02 - clip.mp4"}
    one = by_name["01 - clip.mp4"]
    assert one.title == "Clip 1 title" and one.source_score == 71 and one.source_reason == "Great hook"
    assert one.niche == "Gaming Videos" and one.source_url == "https://www.youtube.com/watch?v=abc"
    assert one.imported_by == "watch"
    assert one.source_group == by_name["02 - clip.mp4"].source_group  # one video: spread apart in Smart order


def test_a_second_look_downloads_nothing_again(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    clipmonster_drive(google)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    sync(store, s)
    gets = len([p for m, p in google.requests if "alt=media" in p])
    res = sync(store, s)
    assert res.downloaded == [] and res.here == 2
    assert len([p for m, p in google.requests if "alt=media" in p]) == gets


def test_clipmonsters_own_clips_json_gives_hooks_and_part_series(tmp_path, google):
    from tokmanager import ready
    from tokmanager.store import Store

    signed_in(google)
    index = {"video": {"id": "abc", "title": "Big Stream", "channel": "Streamer"},
             "automation": {"niche": "Gaming Videos", "niche_id": "gaming", "account": "@iSlipX2"},
             "clips": [{"file": "01 - clip.mp4", "title": "One", "score": 90, "hook": "He did WHAT?",
                        "series": "s1", "part": 1, "parts": 2},
                       {"file": "02 - clip.mp4", "title": "Two", "score": 88, "series": "s1", "part": 2, "parts": 2}]}
    clipmonster_drive(google, index=index)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    sync(store, s)
    items = {Path(i.path).name: i for i in ready.import_watched(store, ready.watch_roots(s), now=NOW)}
    one = items["01 - clip.mp4"]
    assert one.hook == "He did WHAT?" and one.caption == "He did WHAT?" and one.title == "One"
    assert one.niche_id == "gaming" and one.for_account == "iSlipX2"
    assert one.part == 1 and one.parts == 2 and one.series == items["02 - clip.mp4"].series


def test_clipmonsters_clips_json_replaces_one_made_here_once_it_is_uploaded(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    folder, _ = clipmonster_drive(google, n=1)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    sync(store, s)
    local = tmp_path / "inbox" / "Automation" / "Gaming Videos" / "Streamer" / "Big Stream" / "clips.json"
    assert json.loads(local.read_text(encoding="utf-8"))["made_by"].startswith("TikTok Manager")
    google.add("clips.json", folder, json.dumps({"clips": [{"file": "01 - clip.mp4", "hook": "Hook"}]}).encode())
    google.add("03 - clip.mp4", folder, clip(3), created=CREATED, description=describe("Three"))
    sync(store, s)
    data = json.loads(local.read_text(encoding="utf-8"))
    assert "made_by" not in data and data["clips"][0]["hook"] == "Hook"


def test_a_clip_clipmonster_made_on_this_pc_isnt_downloaded_again(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    _, ids = clipmonster_drive(google, n=2)
    local = tmp_path / "TTVIDEOS" / "Gaming Videos" / "Streamer" / "Big Stream"
    local.mkdir(parents=True)
    (local / "01 - clip.mp4").write_bytes(clip(1))
    store = Store(tmp_path / "lib.json")
    store.add_paths([local / "01 - clip.mp4"])
    res = sync(store, settings(tmp_path, google))
    assert res.downloaded == ["02 - clip.mp4"] and res.here == 1
    assert google.files[ids[0]]["appProperties"]["tm_pc"] == "DESKTOP-MAIN"  # claimed: it's this PC's


def test_a_clip_another_pc_took_is_left_alone(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    folder = google.folders("ClipMonster", "Automation", "Gaming Videos", "Streamer", "Big Stream")
    google.add("01 - clip.mp4", folder, clip(1), created=CREATED, props={"tm_pc": "LAPTOP"})
    google.add("02 - clip.mp4", folder, clip(2), created=CREATED, props={"tm_pc": "DESKTOP-MAIN"})
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert res.taken == {"LAPTOP": 1} and res.downloaded == ["02 - clip.mp4"]
    assert "1 taken by LAPTOP" in res.text()


def test_only_recent_clips_are_taken_unless_asked_for_all(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    clipmonster_drive(google, video="Old one", n=1, created="2026-09-01T12:00:00.000Z")
    clipmonster_drive(google, video="New one", n=1)
    store = Store(tmp_path / "lib.json")
    res = sync(store, settings(tmp_path, google, drive_days=14))
    assert res.downloaded == ["01 - clip.mp4"] and res.older == 1
    assert (tmp_path / "inbox" / "Automation" / "Gaming Videos" / "Streamer" / "New one" / "01 - clip.mp4").is_file()
    res = sync(store, settings(tmp_path, google, drive_days=0))
    assert res.downloaded == ["01 - clip.mp4"] and res.older == 0


def test_rejected_clips_are_never_downloaded(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    folder = google.folders("ClipMonster", "Automation", "Gaming Videos", "Streamer", "Big Stream", "_rejected")
    google.add("09 - bad.mp4", folder, clip(9), created=CREATED)
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert res.in_drive == 0 and res.downloaded == []


def test_clips_you_removed_or_cleaned_up_stay_out(tmp_path, google):
    from tokmanager import ready
    from tokmanager.store import Store

    signed_in(google)
    clipmonster_drive(google, n=2)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    sync(store, s)
    items = {Path(i.path).name: i for i in ready.import_watched(store, ready.watch_roots(s), now=NOW)}
    store.remove([items["01 - clip.mp4"].id])  # removed from the library
    Path(items["01 - clip.mp4"].path).unlink()
    Path(items["02 - clip.mp4"].path).unlink()  # cleaned up after posting
    res = sync(store, s)
    assert res.downloaded == [] and res.updated == []
    assert not Path(items["01 - clip.mp4"].path).exists() and not Path(items["02 - clip.mp4"].path).exists()


def test_a_removed_clip_never_downloaded_stays_in_drive(tmp_path, google):
    """The list of what was downloaded was lost, but you'd removed the clip: it isn't fetched again."""
    from tokmanager import drive_sync
    from tokmanager.store import Store, _key

    signed_in(google)
    clipmonster_drive(google, n=1)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    dest = tmp_path / "inbox" / "Automation" / "Gaming Videos" / "Streamer" / "Big Stream" / "01 - clip.mp4"
    store.ignored.add(_key(dest))
    res = sync(store, s)
    assert res.removed == 1 and res.downloaded == [] and not dest.exists()
    assert drive_sync.last()["text"] == res.text()


def test_a_clip_exported_again_is_updated_while_it_waits_to_be_posted(tmp_path, google):
    from tokmanager import ready
    from tokmanager.store import Store

    signed_in(google)
    _, ids = clipmonster_drive(google, n=1)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    sync(store, s)
    items = ready.import_watched(store, ready.watch_roots(s), now=NOW)
    google.files[ids[0]]["content"] = b"a better cut" * 100
    res = sync(store, s)
    assert res.updated == ["01 - clip.mp4"]
    assert Path(items[0].path).read_bytes() == b"a better cut" * 100
    # once it's posted, a new version in Drive changes nothing here
    items[0].status = "posted"
    google.files[ids[0]]["content"] = b"third cut" * 100
    assert sync(store, s).updated == []


def test_a_dropped_download_carries_on_where_it_stopped(tmp_path, google):
    from tokmanager import gdrive
    from tokmanager.store import Store

    signed_in(google)
    _, ids = clipmonster_drive(google, n=1)
    google.cut_downloads[ids[0]] = 1000
    waits = []
    s = settings(tmp_path, google)
    drive = gdrive.Drive(google.client_id, google.client_secret, sleep=waits.append)
    res = sync(Store(tmp_path / "lib.json"), s, drive=drive)
    assert res.downloaded == ["01 - clip.mp4"], res.failed
    dest = tmp_path / "inbox" / "Automation" / "Gaming Videos" / "Streamer" / "Big Stream" / "01 - clip.mp4"
    assert hashlib.md5(dest.read_bytes()).hexdigest() == hashlib.md5(clip(1)).hexdigest()
    ranges = [p for m, p in google.requests if "alt=media" in p]
    assert len(ranges) == 2 and waits  # the second asked for the rest only (see the fake's Range handling)


def test_a_damaged_download_is_thrown_away_and_tried_again_later(tmp_path, google, monkeypatch):
    from tokmanager import gdrive
    from tokmanager.store import Store

    signed_in(google)
    clipmonster_drive(google, n=1)
    monkeypatch.setattr(gdrive, "md5_of", lambda path: "0" * 32)
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert res.downloaded == [] and "checksum" in res.failed[0]
    assert not list((tmp_path / "inbox").rglob("*.mp4*"))  # nor a .part to carry on from
    monkeypatch.undo()


def test_many_files_are_read_page_by_page(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    google.page_size = 3
    clipmonster_drive(google, n=7)
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert len(res.downloaded) == 7


def test_no_clipmonster_folder_says_why_it_might_be(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert "different Google Cloud project" in res.problem and "ClipMonster" in res.problem


def test_the_drive_api_switched_off_says_how_to_turn_it_on(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    google.drive_api_off = True
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert "Google Drive API" in res.problem and "Enable" in res.problem


def test_a_full_disk_stops_before_downloading(tmp_path, google, monkeypatch):
    import shutil

    from tokmanager.store import Store

    signed_in(google)
    clipmonster_drive(google, n=1)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google))
    assert "free space" in res.problem and res.downloaded == []


def test_cancelling_stops_and_leaves_nothing_half_written(tmp_path, google):
    from tokmanager.store import Store

    signed_in(google)
    clipmonster_drive(google, n=3)
    res = sync(Store(tmp_path / "lib.json"), settings(tmp_path, google), cancelled=lambda: True)
    assert res.downloaded == [] and res.problem == ""
    assert not list((tmp_path / "inbox").rglob("*.mp4"))


# ---------------------------------------------------------------- the pieces

def test_descriptions_clipmonster_writes_are_read_back():
    from tokmanager import drive_sync

    d = drive_sync.describe("He did WHAT\nScore 87/100. Big reaction, clear payoff\n"
                            "From: Best of 2026 https://www.youtube.com/watch?v=xyz\nAt: https://youtu.be/xyz?t=97")
    assert d == {"title": "He did WHAT", "score": 87.0, "reason": "Big reaction, clear payoff",
                 "source_title": "Best of 2026", "source": "https://www.youtube.com/watch?v=xyz",
                 "watch": "https://youtu.be/xyz?t=97"}
    assert drive_sync.describe("") == {}


def test_names_from_drive_are_made_safe_for_windows(tmp_path):
    from tokmanager import drive_sync

    r = drive_sync.Remote("x", 'a:b?.mp4', ["Automation", "..", 'Q"uote', "dots..."], "p")
    path = drive_sync.local_path(tmp_path, r)
    assert path == tmp_path / "Automation" / "_" / "Q_uote" / "dots" / "a_b_.mp4"
    assert tmp_path in path.parents


def test_the_inbox_is_one_of_the_folders_clips_come_in_from(tmp_path):
    from tokmanager import ready
    from tokmanager.config import Settings

    s = Settings(watch_folders=[str(tmp_path / "TTVIDEOS")], drive_inbox=str(tmp_path / "inbox"))
    assert ready.watch_roots(s) == [str(tmp_path / "TTVIDEOS")]
    s.drive_on = True
    assert ready.watch_roots(s) == [str(tmp_path / "TTVIDEOS"), str(tmp_path / "inbox")]
    assert (tmp_path / "inbox").is_dir()  # there from the start, so it never reads as missing
    s.watch_folders.append(str(tmp_path))  # already inside a watched folder: not added twice
    assert ready.watch_roots(s) == [str(tmp_path / "TTVIDEOS"), str(tmp_path)]


def test_the_result_reads_well():
    from tokmanager import drive_sync

    r = drive_sync.Result(in_drive=12, downloaded=["a", "b"], here=9, older=1)
    assert r.text() == "12 clips in Drive: 2 new clips downloaded, 9 already here, 1 older clip left in Drive."


def test_a_damaged_update_keeps_the_clip_that_was_there(tmp_path, google, monkeypatch):
    from tokmanager import gdrive, ready
    from tokmanager.store import Store

    signed_in(google)
    _, ids = clipmonster_drive(google, n=1)
    store = Store(tmp_path / "lib.json")
    s = settings(tmp_path, google)
    sync(store, s)
    item = ready.import_watched(store, ready.watch_roots(s), now=NOW)[0]
    google.files[ids[0]]["content"] = b"new cut" * 100
    monkeypatch.setattr(gdrive, "md5_of", lambda path: "0" * 32)
    res = sync(store, s)
    assert res.updated == [] and "checksum" in res.failed[0]
    assert Path(item.path).read_bytes() == clip(1)
