"""Each account's schedule only takes the videos tagged with the niches it posts (Account.post_niches):
the rest wait in the queue without a posting time, so they're never posted, reminded of or sent to the phone."""

import datetime as dt
import json
from pathlib import Path

import test_niches
from test_niches import clipmonster, item, titles

from tokmanager import jobs, niches, ready
from tokmanager.accounts import Account, Accounts
from tokmanager.config import Settings
from tokmanager.store import Store

isolated_dirs, app, state = test_niches.isolated_dirs, test_niches.app, test_niches.state  # its fixtures


def ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi).timestamp()


MORNING = ts(2026, 10, 30, 8)


def cm_index(tmp_path, items, accounts=()):
    return niches.Index(items, accounts, [], niches.clipmonster_info(clipmonster(tmp_path)))


# --- the rule ---------------------------------------------------------------------------------------


def test_which_videos_an_account_schedules(tmp_path):
    game = item(tmp_path / "a.mp4", niche="Gaming Videos", niche_id="g1")
    money = item(tmp_path / "b.mp4", niche_id="m1")
    loose = item(tmp_path / "c.mp4")
    untagged_by_you = item(tmp_path / "d.mp4", niche="Gaming Videos", niche_set=niches.NONE)
    index = cm_index(tmp_path, [game, money, loose, untagged_by_you])
    every = [niches.EVERY]  # what an account starts with
    assert [index.waits(i, every) for i in (game, money, loose, untagged_by_you)] == ["", "", "none", "none"]
    assert all(index.schedules(i, [niches.EVERY, niches.NONE]) for i in (game, money, loose, untagged_by_you))
    assert [index.waits(i, ["g1"]) for i in (game, money, loose)] == ["", "other", "none"]
    assert [index.schedules(i, ["g1", niches.NONE]) for i in (game, money, loose)] == [True, False, True]
    assert [index.schedules(i, [niches.NONE]) for i in (game, money, loose)] == [False, False, True]
    # A niche ClipMonster has since deleted can't match anything any more
    assert index.waits(game, ["gone"]) == "other"


def test_a_library_with_no_niches_schedules_everything(tmp_path):
    a, b = item(tmp_path / "a.mp4"), item(tmp_path / "b.mp4")
    index = niches.Index([a, b])  # no ClipMonster niches, nothing tagged: nothing could be
    assert not index.niches and index.schedules(a, [niches.EVERY]) and index.waits(b, ["g1"]) == ""


def test_ticking_niches():
    every = [niches.EVERY]
    assert niches.post_niches(every, "g1", True) == ["g1"]  # a niche by name: no longer every niche
    assert niches.post_niches(["g1"], "m1", True) == ["g1", "m1"]
    assert niches.post_niches(["g1", "m1"], "g1", False) == ["m1"]
    assert niches.post_niches(["g1", niches.NONE], niches.EVERY, True) == [niches.EVERY, niches.NONE]
    assert niches.post_niches(every, niches.NONE, True) == [niches.EVERY, niches.NONE]
    assert niches.post_niches([niches.NONE, niches.EVERY], niches.NONE, False) == [niches.EVERY]
    assert niches.post_niches(["g1"], "g1", False) == []  # the page never lets the last one go


def test_describing_them(tmp_path):
    index = cm_index(tmp_path, [])
    assert index.describe([niches.EVERY]) == "Every niche"
    assert index.describe([niches.EVERY, niches.NONE]) == "Every video"
    assert index.describe(["g1"]) == "Gaming Videos"
    assert index.describe(["g1", "m1", niches.NONE]) == "Gaming Videos and Financial Advisor, and videos with no niche"
    assert index.describe([niches.NONE]) == "Only videos with no niche"
    assert index.describe(["gone"]) == "Nothing"


def test_the_niches_an_account_posts_are_saved(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": [{"id": "old", "username": "iclipx2"},  # from before 1.10
                                             {"id": "bad", "post_niches": "g1"},
                                             {"id": "dup", "post_niches": ["g1", "g1", 3, "", "m1"]}]}),
                    encoding="utf-8")
    accs = Accounts.load(Settings(), path)
    assert [a.post_niches for a in accs] == [["*"], ["*"], ["g1", "m1"]]
    accs.get("old").post_niches = ["g1", niches.NONE]
    accs.save()
    assert Accounts.load(Settings(), path).get("old").post_niches == ["g1", "-"]
    assert Account().post_niches == ["*"]


# --- the schedule ----------------------------------------------------------------------------------


def queued(tmp_path, *specs):
    """A store with one account queuing videos given as (title, niche) in that order."""
    clipmonster(tmp_path)
    store = Store(tmp_path / "lib.json")
    folder = tmp_path / "vids"
    for title, _ in specs:  # a folder each, so none takes its niche from the others (niches.py rule 4)
        (folder / title).mkdir(parents=True)
        (folder / title / f"{title}.mp4").write_bytes(b"x")
    items = sorted(store.add_paths([folder]), key=lambda i: [t for t, _ in specs].index(Path(i.path).stem))
    for it, (title, niche) in zip(items, specs):
        it.title, it.niche_set = title, niche
    a = Account(id="A", username="iclipx2", posts_per_day=3, first_post_time="09:00", last_post_time="21:00",
                min_gap_minutes=60, autopilot=True, logged_in=True)
    store.enqueue([i.id for i in items], a.id)
    return store, a, items


def test_only_tagged_videos_get_posting_times(tmp_path):
    store, a, (g1, loose, m1, g2) = queued(tmp_path, ("g1", "Gaming Videos"), ("loose", ""),
                                           ("m1", "Financial Advisor"), ("g2", "Gaming Videos"))
    jobs.replan(store, [a], Settings(), now=MORNING)
    # the untagged clip takes no slot: the next tagged one has its time
    assert [g1.planned_at, m1.planned_at, g2.planned_at] == [ts(2026, 10, 30, 9), ts(2026, 10, 30, 15),
                                                             ts(2026, 10, 30, 21)]
    assert loose.planned_at is None and loose.status == "queued" and loose.queue_pos == 1  # waits in its place
    a.post_niches = ["g1"]
    jobs.replan(store, [a], Settings(), now=MORNING)
    assert [g1.planned_at, g2.planned_at] == [ts(2026, 10, 30, 9), ts(2026, 10, 30, 15)]
    assert m1.planned_at is None and loose.planned_at is None
    loose.niche_set = "Gaming Videos"  # tagged: it joins the schedule where it stands in the queue
    jobs.replan(store, [a], Settings(), now=MORNING)
    assert [g1.planned_at, loose.planned_at, g2.planned_at] == [ts(2026, 10, 30, 9), ts(2026, 10, 30, 15),
                                                                ts(2026, 10, 30, 21)]


def test_waiting_videos_are_never_posted_or_reminded_of(tmp_path):
    store, a, (loose, game) = queued(tmp_path, ("loose", ""), ("game", "Gaming Videos"))
    loose.pinned_at = ts(2026, 10, 30, 9)  # even a fixed time waits for its niche
    game.pinned_at = ts(2026, 10, 30, 9, 30)
    noon = ts(2026, 10, 30, 12)
    acc, it = jobs.autopilot_pick(store, [a], Settings(), now=noon)
    assert it is game and loose.planned_at is None and loose.pinned_at
    assert jobs.next_post(store, a.id) is game
    a.autopilot = False
    assert [i.id for _, i in ready.reminders(store, [a], Settings(), now=noon)] == [game.id]
    store.dequeue([game.id])
    assert jobs.autopilot_pick(store, [a], Settings(), now=noon) is None
    assert ready.reminders(store, [a], Settings(), now=noon) == []


def test_summary_counts_the_waiting_ones_apart(tmp_path):
    store, a, _ = queued(tmp_path, ("g1", "Gaming Videos"), ("loose", ""), ("m1", "Financial Advisor"))
    jobs.replan(store, [a], Settings(), now=MORNING)
    assert ready.summary(store, [a], now=MORNING) == "2 for @iclipx2 (next Today 09:00), 1 on hold (not in its niches)"


def test_new_clips_go_to_the_account_that_posts_their_niche(tmp_path):
    clipmonster(tmp_path)
    store = Store(tmp_path / "lib.json")
    folder = tmp_path / "TTVIDEOS"
    for niche in ("Gaming Videos", "Financial Advisor", "Cooking"):
        (folder / niche).mkdir(parents=True)
        (folder / niche / "clip.mp4").write_bytes(b"x")
    gamer = Account(id="G", username="iclipx2", post_niches=["g1"])
    money = Account(id="M", username="moneyclips2")  # every niche: not picked by name, so it gets no extras
    items = store.add_paths([folder])
    got = ready.assign(store, [gamer, money], items, "M")
    by_folder = {Path(i.path).parent.name: i.account for i in items}
    assert by_folder == {"Gaming Videos": "G", "Financial Advisor": "M", "Cooking": "M"}
    assert got == {"G": 1, "M": 2}


def test_justyns_setup(tmp_path):
    """One account, iclipx2, with both of ClipMonster's niches queued on it (as on Justyn's PC)."""
    store, a, items = queued(tmp_path, ("g1", "Gaming Videos"), ("m1", "Financial Advisor"),
                             ("g2", "Gaming Videos"), ("m2", "Financial Advisor"))
    jobs.replan(store, [a], Settings(), now=MORNING)
    assert all(i.planned_at for i in items)  # every niche (the default): nothing changes
    a.post_niches = niches.post_niches(a.post_niches, "g1", True)  # Gaming only
    jobs.replan(store, [a], Settings(), now=MORNING)
    assert [i.title for i in items if i.planned_at] == ["g1", "g2"]
    assert [i.planned_at for i in items if i.planned_at] == [ts(2026, 10, 30, 9), ts(2026, 10, 30, 15)]


# --- the pages ----------------------------------------------------------------------------------------


def test_queue_page_picks_the_niches_and_shows_who_waits(state):
    from tokmanager.gui.queue_page import QueuePage

    page = QueuePage(state)
    a = state.active
    assert page.niche_row.isVisibleTo(page) and page.niche_pick.text() == "Every niche"
    assert "waiting" not in page.summary.text() and "wait" not in page.niche_note.text()

    page._fill_niche_pick()
    acts = {act.text(): act for act in page.niche_pick_menu.actions() if act.text()}
    assert list(acts) == ["Every niche", "Gaming Videos (@GameClips)  (3 queued)",
                          "Financial Advisor (@MoneyClips)  (2 queued)", "Videos with no niche"]
    assert acts["Every niche"].isChecked() and not acts["Every niche"].isEnabled()  # the last one ticked
    acts["Gaming Videos (@GameClips)  (3 queued)"].trigger()
    assert a.post_niches == ["g1"] and Accounts.load(state.settings).active.post_niches == ["g1"]
    assert page.niche_pick.text() == "Gaming Videos"
    assert page.summary.text().endswith(" · 2 on hold: not in its niches")
    assert "2 from other niches wait in the queue" in page.niche_note.text()
    q = state.store.queue(a.id)
    assert [i.title for i in q if i.planned_at] == ["g-a", "g-b", "g-c"]
    assert page.waits(q[1]).startswith(f"No posting time: {a.label} doesn't post Financial Advisor")

    page._select_waiting()
    assert titles(state.store.get(i) for i in page.selected_ids()) == ["m-a", "m-b"]

    # Tagging one from the queue's right-click menu gives it a posting time
    from tokmanager.gui import niche_menu

    niche_menu.set_niche(state, [q[1]], "Gaming Videos")
    assert q[1].planned_at and page.summary.text().endswith(" · 1 on hold: not in its niches")


def test_queue_page_says_how_to_tag_a_video_with_no_niche(state):
    from tokmanager.gui.queue_page import QueuePage

    loose = next(i for i in state.store.all() if i.title == "loose")
    state.store.enqueue([loose.id], state.active.id)
    state.library_changed.emit()
    page = QueuePage(state)
    assert loose.planned_at is None
    assert page.waits(loose) == "No posting time: it has no niche. Right-click → Niche to tag it."
    assert "1 with no niche waits in the queue" in page.niche_note.text() and "Select it" in page.niche_note.text()
    page.set_post_niche(niches.NONE, True)
    assert state.active.post_niches == [niches.EVERY, niches.NONE] and loose.planned_at
    assert page.niche_pick.text() == "Every video" and "with a niche or without" in page.niche_note.text()


def test_queue_page_menu_tags_videos(state):
    from PySide6.QtWidgets import QMenu

    from tokmanager.gui import niche_menu

    loose = next(i for i in state.store.all() if i.title == "loose")
    m = QMenu()
    niche_menu.fill(m, state, [loose], None)
    assert [a.text() for a in m.actions() if a.text()] == ["Gaming Videos (@GameClips)", "Financial Advisor (@MoneyClips)",
                                                           "New niche…", "No niche"]


def test_ready_page_says_why_a_video_has_no_time(state):
    from tokmanager.gui.ready_page import ReadyPage

    state.active.post_niches = ["m1"]
    page = ReadyPage(state)
    cards = {c.item.title: c for c in page.cards()}
    assert "doesn't post Gaming Videos" in cards["g-a"].info.text()
    assert "Post " in cards["m-a"].info.text() and "No posting time" not in cards["m-a"].info.text()
    # the ones with a time come first, and the summary counts the rest apart
    assert titles(c.item for c in page.cards()) == ["m-a", "m-b", "g-a", "g-b", "g-c"]
    assert page.summary.text() == "2 videos ready to post · 3 on hold: not in its niches"


def test_no_niches_no_picker(tmp_path, app):
    from tokmanager.gui.queue_page import QueuePage
    from tokmanager.gui.state import AppState

    st = AppState()
    (tmp_path / "a.mp4").write_bytes(b"x")
    it = st.store.add_paths([tmp_path / "a.mp4"])[0]
    st.store.enqueue([it.id], st.active.id)
    page = QueuePage(st)
    assert not page.niche_row.isVisibleTo(page) and it.planned_at and not page.waits(it)
