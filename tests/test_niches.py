"""Niches: working out each video's niche (ClipMonster's word, its folder, its neighbours, its channel…),
and showing one niche at a time on the Library, Ready, Queue and Account pages."""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokmanager import niches  # noqa: E402
from tokmanager.accounts import Account  # noqa: E402
from tokmanager.config import Settings  # noqa: E402
from tokmanager.store import Store, VideoItem  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)


GAMING = {"id": "g1", "name": "Gaming Videos", "account": "@GameClips", "enabled": True, "categories": "20",
          "channels": ""}
MONEY = {"id": "m1", "name": "Financial Advisor", "account": "@MoneyClips", "enabled": True, "categories": "",
         "channels": "https://www.youtube.com/@marktilbury\n@TheRamseyShow  # the highlights channel\n"
                     "https://www.youtube.com/channel/UCLe_q9axMaeTbjN0hy1Z9xA\nPlain Bagel"}


def clipmonster(tmp_path, niche_list=(GAMING, MONEY), picks=()):
    """ClipMonster's settings, research and picks files, as on Justyn's PC."""
    folder = tmp_path / "appdata" / "ClipMonster"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text(json.dumps({"niches": list(niche_list)}), encoding="utf-8")
    (folder / "research.json").write_text(json.dumps({"videos": {}, "sources": {"channels": {
        "@marktilbury": {"id": "UCxxxxxxxxxxxxxxxxxxxxx1", "title": "Mark Tilbury"},
        "@theramseyshow": {"id": "UC7eBNeDW1GQf2NJQ6G6gAxw", "title": "The Ramsey Show Highlights"},
        "@calebhammer": {"id": "UCLe_q9axMaeTbjN0hy1Z9xA", "title": "Caleb Hammer"},
    }}}), encoding="utf-8")
    (folder / "picks.json").write_text(json.dumps({"picks": list(picks)}), encoding="utf-8")
    return folder / "settings.json"


def item(path, **kw) -> VideoItem:
    return VideoItem(path=str(path), **kw)


# --- what ClipMonster knows ------------------------------------------------------------------------


def test_clipmonster_niches_and_their_channel_lists(tmp_path):
    info = niches.clipmonster_info(clipmonster(tmp_path))
    assert [(n.key, n.name, n.account) for n in info.niches] == [("g1", "Gaming Videos", "GameClips"),
                                                                ("m1", "Financial Advisor", "MoneyClips")]
    # a link, an @name (titles from the research file), a channel id and a bare name all count
    for name in ("Mark Tilbury", "marktilbury", "The Ramsey Show Highlights", "Caleb Hammer", "Plain Bagel"):
        assert info.channels[niches.fold(name)] == "m1", name
    assert info.chart_niche == "g1"  # the only niche that takes chart videos (it has a category)


def test_no_clipmonster_is_no_niches(tmp_path):
    info = niches.clipmonster_info(tmp_path / "nowhere" / "settings.json")
    assert info.niches == [] and info.channels == {} and info.chart_niche == ""
    assert niches.Index([item(tmp_path / "a.mp4")], cm=info).niches == []


def test_its_files_are_read_again_when_they_change(tmp_path):
    path = clipmonster(tmp_path)
    first = niches.clipmonster_info(path)
    assert niches.clipmonster_info(path) is first
    data = json.loads(path.read_text(encoding="utf-8"))
    data["niches"][0]["name"] = "Games"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (first.stamp[1] + 5, first.stamp[1] + 5))
    assert [n.name for n in niches.clipmonster_info(path).niches] == ["Games", "Financial Advisor"]


# --- working out a video's niche ---------------------------------------------------------------------


def test_clipmonster_says_and_a_renamed_niche_shows_its_new_name(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    it = item(tmp_path / "x.mp4", niche="Gaming", niche_id="g1")  # made before "Gaming" became "Gaming Videos"
    index = niches.Index([it], cm=cm)
    assert index.of(it) == "g1" and index.name_of(it) == "Gaming Videos"
    assert index.why(it) == niches.WHY["clipmonster"]


def test_a_deleted_niche_still_counts_by_its_name(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    it = item(tmp_path / "x.mp4", niche="Cooking", niche_id="c9", for_account="FoodClips")
    index = niches.Index([it], cm=cm)
    assert [n.name for n in index.niches] == ["Gaming Videos", "Financial Advisor", "Cooking"]
    assert index.name_of(it) == "Cooking" and index.get("c9").account == "FoodClips"


def test_the_folder_it_is_in(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    root = tmp_path / "TTVIDEOS"
    it = item(root / "Financial Advisor" / "Mark Tilbury" / "2026-10-24 Money [hriu0irRcpE]" / "02 - clip.mp4")
    windows = item(r"C:\Users\JGill\OneDrive\Documents\TTVIDEOS\Gaming Videos\JEV\2026-10-22 MW4\01 - x.mp4")
    index = niches.Index([it, windows], watch_folders=[str(root)], cm=cm)
    assert index.name_of(it) == "Financial Advisor" and index.why(it) == niches.WHY["folder"]
    assert index.name_of(windows) == "Gaming Videos"  # saved on Windows, read anywhere


def test_only_folders_inside_the_watched_folder_count(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    root = tmp_path / "Gaming Videos" / "watched"  # the watched folder itself sits in a folder named like a niche
    it = item(root / "Jynxzi" / "01.mp4")
    assert niches.Index([it], watch_folders=[str(root)], cm=cm).of(it) == ""
    assert niches.Index([it], cm=cm).of(it) == "g1"  # not in a watched folder: any folder it's in
    niche_folder = tmp_path / "TTVIDEOS" / "Financial Advisor"  # a niche's own folder, watched by itself
    money = item(niche_folder / "Caleb Hammer" / "01.mp4")
    assert niches.Index([money], watch_folders=[str(niche_folder)], cm=cm).of(money) == "m1"


def test_the_other_clips_from_its_folder(tmp_path):
    folder = tmp_path / "Ramsey" / "2026-10-28 Debt [nxIhQ6J0gxE]"
    tagged = [item(folder / f"0{n}.mp4", niche="Financial Advisor", niche_id="m1") for n in (2, 3)]
    lost = item(folder / "01.mp4")  # its clips.json entry was missing
    index = niches.Index(tagged + [lost], cm=niches.clipmonster_info(clipmonster(tmp_path)))
    assert index.of(lost) == "m1" and index.why(lost) == niches.WHY["siblings"]


def test_its_channel(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    listed = item(tmp_path / "a" / "1.mp4", source_channel="Mark Tilbury")
    known = item(tmp_path / "b" / "1.mp4", source_channel="Jynxzi", niche="Gaming Videos", niche_id="g1")
    same = item(tmp_path / "c" / "1.mp4", source_channel="jynxzi")
    index = niches.Index([listed, known, same], cm=cm)
    assert index.of(listed) == "m1" and index.why(listed) == niches.WHY["channel"]
    assert index.of(same) == "g1"


def test_a_channel_in_two_niches_decides_nothing(tmp_path):
    a = item(tmp_path / "a" / "1.mp4", source_channel="Both", niche_id="g1", niche="Gaming Videos")
    b = item(tmp_path / "b" / "1.mp4", source_channel="Both", niche_id="m1", niche="Financial Advisor")
    c = item(tmp_path / "c" / "1.mp4", source_channel="Both")
    assert niches.Index([a, b, c], cm=niches.clipmonster_info(clipmonster(tmp_path))).of(c) == ""


def test_the_account_it_is_for(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    acc = Account(id="acc1", username="MoneyClips")
    it = item(tmp_path / "x.mp4", account="acc1")
    index = niches.Index([it], [acc, Account(id="acc2", username="someone")], cm=cm)
    assert index.of(it) == "m1" and index.why(it) == niches.WHY["account"]


def test_picked_for_a_niche_or_from_the_charts(tmp_path):
    path = clipmonster(tmp_path, picks=[{"id": "nxIhQ6J0gxE", "niche": "m1", "origins": ["watchlist"]},
                                        {"id": "u0iATr7A5KM", "origins": ["chart"]}])
    cm = niches.clipmonster_info(path)
    picked = item(tmp_path / "a" / "1.mp4", source_group="nxIhQ6J0gxE")
    chart = item(tmp_path / "b" / "1.mp4", source_url="https://www.youtube.com/watch?v=u0iATr7A5KM&t=30")
    other = item(tmp_path / "c" / "1.mp4", source_group="zzzzzzzzzzz")
    index = niches.Index([picked, chart, other], cm=cm)
    assert index.of(picked) == "m1" and index.why(picked) == niches.WHY["picked"]
    assert index.of(chart) == "g1" and index.why(chart) == niches.WHY["charts"]
    assert index.of(other) == ""


def test_two_niches_taking_chart_videos_is_no_guess(tmp_path):
    path = clipmonster(tmp_path, [GAMING, dict(MONEY, keywords="money")], picks=[{"id": "u0iATr7A5KM",
                                                                                  "origins": ["chart"]}])
    cm = niches.clipmonster_info(path)
    it = item(tmp_path / "1.mp4", source_group="u0iATr7A5KM")
    assert cm.chart_niche == "" and niches.Index([it], cm=cm).of(it) == ""


def test_what_you_pick_wins(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    it = item(tmp_path / "Gaming Videos" / "1.mp4", niche="Gaming Videos", niche_id="g1")
    assert niches.set_niche([it], "financial advisor") == 1
    index = niches.Index([it], cm=cm)
    assert index.of(it) == "m1" and index.why(it) == niches.WHY["you"]
    niches.set_niche([it], niches.NONE)
    index = niches.Index([it], cm=cm)
    assert index.of(it) == niches.NONE and index.name_of(it) == "" and index.why(it) == ""
    assert index.matches(it, niches.NONE) and not index.matches(it, "g1")
    niches.set_niche([it], "  Cats   and dogs ")
    index = niches.Index([it], cm=cm)
    assert index.name_of(it) == "Cats and dogs" and index.of(it) == "name:catsanddogs"
    assert niches.set_niche([it], "") == 1 and niches.Index([it], cm=cm).of(it) == "g1"


def test_filtering_and_counting(tmp_path):
    cm = niches.clipmonster_info(clipmonster(tmp_path))
    g = item(tmp_path / "1.mp4", niche_id="g1", niche="Gaming Videos")
    m = item(tmp_path / "2.mp4", niche_id="m1", niche="Financial Advisor")
    n = item(tmp_path / "3.mp4")
    index = niches.Index([g, m, n], cm=cm)
    assert index.keep([g, m, n], niches.ALL) == [g, m, n]
    assert index.keep([g, m, n], "g1") == [g]
    assert index.keep([g, m, n], niches.NONE) == [n]
    assert index.counts([g, m, n]) == {"g1": 1, "m1": 1, niches.NONE: 1}
    assert index.valid("m1") and index.valid(niches.NONE) and not index.valid("gone")


def test_merge_order_keeps_the_hidden_videos_in_place():
    assert niches.merge_order(list("abcdef"), list("eca")) == list("ebcdaf")
    assert niches.merge_order(list("abc"), []) == list("abc")
    assert niches.merge_order(list("abc"), ["c", "x", "a"]) == list("cba")  # one that's gone is ignored


def test_justyns_library(tmp_path):
    """Clips like the ones on Justyn's PC: tagged by ClipMonster, in a niche's folder with no tag, and
    from before niches (chart picks in a channel's folder)."""
    path = clipmonster(tmp_path, picks=[{"id": "u0iATr7A5KM", "origins": ["chart"]},
                                        {"id": "Wf9irsB_ETs", "origins": ["chart"]},
                                        {"id": "nxIhQ6J0gxE", "niche": "m1", "origins": ["watchlist"]}])
    root = tmp_path / "TTVIDEOS"
    items = [
        item(root / "Jynxzi" / "2026-10-26 Your SUS Clips [u0iATr7A5KM]" / "01.mp4", source_group="u0iATr7A5KM",
             source_channel="Jynxzi"),
        item(root / "gameranx" / "2026-10-26 Phantom Blade [Wf9irsB_ETs]" / "02.mp4", source_group="Wf9irsB_ETs",
             source_channel="gameranx"),
        item(root / "Financial Advisor" / "Mark Tilbury" / "x [hriu0irRcpE]" / "02 - clip.mp4"),
        item(root / "Financial Advisor" / "The Ramsey Show Highlights" / "y [nxIhQ6J0gxE]" / "02.mp4",
             niche="Financial Advisor", niche_id="m1", for_account="iSlipX4"),
        item(root / "Gaming Videos" / "Freshlol" / "z [mCLCxkg9-WE]" / "01.mp4", niche="Gaming Videos",
             niche_id="g1", for_account="iSlipX2"),
    ]
    index = niches.Index(items, [Account(username="iclipx2")], [str(root)], niches.clipmonster_info(path))
    assert [index.name_of(i) for i in items] == ["Gaming Videos", "Gaming Videos", "Financial Advisor",
                                                 "Financial Advisor", "Gaming Videos"]


# --- saved with the library and the settings ---------------------------------------------------------


def test_the_niche_you_pick_and_the_filter_are_saved(tmp_path):
    store = Store()
    (tmp_path / "a.mp4").write_bytes(b"x")
    it = store.add_paths([tmp_path / "a.mp4"])[0]
    it.niche_set = "Gaming Videos"
    store.save()
    assert Store().get(it.id).niche_set == "Gaming Videos"
    s = Settings()
    s.niche_filter = "g1"
    s.save()
    assert Settings.load().niche_filter == "g1"


# --- the pages -----------------------------------------------------------------------------------------


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def state(tmp_path, app):
    """One account with three gaming and two finance clips queued, gaming and finance in turn."""
    from tokmanager.gui.state import AppState

    clipmonster(tmp_path)
    st = AppState()
    st.settings.auto_check_updates = False
    root = tmp_path / "TTVIDEOS"
    files = []
    for n, (niche, name) in enumerate((("Gaming Videos", "g-a"), ("Financial Advisor", "m-a"),
                                       ("Gaming Videos", "g-b"), ("Financial Advisor", "m-b"),
                                       ("Gaming Videos", "g-c"))):
        f = root / niche / "chan" / f"{n} {name}.mp4"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        files.append(f)
    (root / "Other").mkdir()
    (root / "Other" / "loose.mp4").write_bytes(b"x")
    st.settings.watch_folders = [str(root)]
    items = st.store.add_paths(files + [root / "Other" / "loose.mp4"])
    for it in items:
        it.title = Path(it.path).stem.split(" ", 1)[-1]
    st.store.enqueue([i.id for i in items[:5]], st.active.id)
    return st


def titles(items):
    return [i.title for i in items]


def test_app_state_filters_and_remembers(state):
    assert titles(state.in_niche(state.store.queue(state.active.id))) == ["g-a", "m-a", "g-b", "m-b", "g-c"]
    seen = []
    state.niche_switched.connect(lambda: seen.append(state.niche_filter))
    state.set_niche_filter("m1")
    assert seen == ["m1"] and state.niche_label() == "Financial Advisor"
    assert titles(state.in_niche(state.store.queue(state.active.id))) == ["m-a", "m-b"]
    assert Settings.load().niche_filter == "m1"
    state.settings.niche_filter = "deleted-niche"  # ClipMonster no longer has it: everything shows
    assert state.niche_filter == niches.ALL and state.niche_label() == ""


def test_the_niche_index_follows_changes(state):
    first = state.niches()
    assert state.niches() is first  # nothing changed
    loose = next(i for i in state.store.all() if i.title == "loose")
    assert first.of(loose) == ""
    loose.niche_set = "Gaming Videos"
    state.library_changed.emit()
    assert state.niches().of(loose) == "g1"


def test_header_switcher(state):
    from tokmanager.gui.main_window import MainWindow

    w = MainWindow(state)
    try:
        assert w.niche_btn.isVisibleTo(w) and w.niche_btn.text() == "All niches"
        w._fill_niche_menu()
        acts = [a for a in w.niche_menu.actions() if a.isCheckable()]
        assert [a.text() for a in acts] == ["All niches\t6", "Gaming Videos (@GameClips)\t3",
                                            "Financial Advisor (@MoneyClips)\t2", "No niche\t1"]
        assert acts[0].isChecked()
        acts[1].trigger()
        assert state.niche_filter == "g1" and w.niche_btn.text() == "Gaming Videos"
        assert w.niche_btn.property("filtered") is True
        assert titles(w.queue.state.in_niche(state.store.queue(state.active.id))) == ["g-a", "g-b", "g-c"]
        assert w.queue.list.count() == 3 and w.ready.summary.text().startswith("Gaming Videos: 3 videos ready")
    finally:
        for timer in (w.autopilot_timer, w.watch_timer, w.update_timer, w.clock, w.phone_timer, w.phone_soon,
                      w.live_monitor.timer, w.live.clock):
            timer.stop()


def test_no_niches_no_switcher(tmp_path, app):
    from tokmanager.gui.main_window import MainWindow
    from tokmanager.gui.state import AppState

    st = AppState()
    st.settings.auto_check_updates = False
    w = MainWindow(st)
    try:
        assert not w.niche_btn.isVisibleTo(w)
    finally:
        for timer in (w.autopilot_timer, w.watch_timer, w.update_timer, w.clock, w.phone_timer, w.phone_soon,
                      w.live_monitor.timer, w.live.clock):
            timer.stop()


def test_library_shows_one_niche_and_sets_one(state):
    from tokmanager.gui.library import NICHE_COL, LibraryPage

    page = LibraryPage(state)
    assert page.table.rowCount() == 6 and not page.table.isColumnHidden(NICHE_COL)
    state.set_niche_filter("m1")
    assert sorted(i.title for i in page._visible_items()) == ["m-a", "m-b"]
    state.set_niche_filter(niches.NONE)
    assert titles(page._visible_items()) == ["loose"]
    loose = page._visible_items()[0]
    page.set_niche([loose], "Financial Advisor")
    assert page._visible_items() == [] and loose.niche_set == "Financial Advisor"
    state.set_niche_filter(niches.ALL)
    page.search.setText("financial")
    assert sorted(i.title for i in page._visible_items()) == ["loose", "m-a", "m-b"]


def test_library_niche_menu(state, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMenu

    from tokmanager.gui.library import LibraryPage

    page = LibraryPage(state)
    g = [i for i in state.store.all() if i.title.startswith("g-")]
    m = QMenu()
    page._niche_menu(m, g)
    acts = {a.text(): a for a in m.actions() if a.text()}
    assert list(acts) == ["Gaming Videos (@GameClips)", "Financial Advisor (@MoneyClips)", "New niche…", "No niche"]
    assert acts["Gaming Videos (@GameClips)"].isChecked() and not acts["No niche"].isChecked()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Speedruns", True))
    acts["New niche…"].trigger()
    assert {i.niche_set for i in g} == {"Speedruns"}
    assert "Speedruns" in [n.name for n in state.niches().niches]
    m = QMenu()
    page._niche_menu(m, g)
    again = [a for a in m.actions() if a.text() == "Work it out again"]
    again[0].trigger()
    assert {state.niches().of(i) for i in g} == {"g1"}


def test_ready_page_shows_one_niche(state):
    from tokmanager.gui.ready_page import ReadyPage

    page = ReadyPage(state)
    assert [c.item.title for c in page.cards()] == ["g-a", "m-a", "g-b", "m-b", "g-c"]
    assert "🏷 Gaming Videos" in page.cards()[0].info.text()
    state.set_niche_filter("m1")
    assert [c.item.title for c in page.cards()] == ["m-a", "m-b"]
    assert page.summary.text() == "Financial Advisor: 2 videos ready to post · 3 more in other niches"
    assert "🏷" not in page.cards()[0].info.text()  # it's the niche being shown
    page.mark_posted(page.cards()[0].item.id)
    page.mark_posted(page.cards()[0].item.id)
    assert page.cards() == [] and "3 videos are waiting in other niches" in page.empty.text()


def test_ready_page_takes_only_the_shown_niches_new_clips(state):
    from tokmanager.gui.ready_page import ReadyPage

    for it in state.store.all():
        it.imported_by = "watch"
    loose = next(i for i in state.store.all() if i.title == "loose")
    loose.niche_set = "Financial Advisor"
    gaming_new = next(i for i in state.store.all() if i.title == "g-c")
    state.store.dequeue([gaming_new.id])
    state.library_changed.emit()
    state.set_niche_filter("m1")
    page = ReadyPage(state)
    assert page.take_btn.text() == "Queue 1 new clip here"
    page.take_new_clips()
    assert loose.status == "queued" and gaming_new.status == "new"


def test_queue_page_reorders_one_niche_around_the_others(state):
    from tokmanager.gui.queue_page import POS_ROLE, QueuePage

    page = QueuePage(state)
    state.set_niche_filter("g1")
    assert [page.list.item(r).data(POS_ROLE) for r in range(page.list.count())] == [1, 3, 5]
    assert page.summary.text().startswith("Gaming Videos: 3 of 5 in the queue")
    page.list.item(2).setSelected(True)
    page.move_top()  # g-c to the front of the gaming videos; the finance ones keep their places
    assert titles(state.store.queue(state.active.id)) == ["g-c", "m-a", "g-a", "m-b", "g-b"]
    page._reorder(lambda q: list(reversed(q)))
    assert titles(state.store.queue(state.active.id)) == ["g-b", "m-a", "g-a", "m-b", "g-c"]
    state.set_niche_filter(niches.NONE)
    assert page.list.count() == 0 and "5 other videos are" in page.empty.text()
    page.select_item(state.store.queue(state.active.id)[0].id)  # "Edit…" for a video in another niche
    assert state.niche_filter == niches.ALL and len(page.selected_ids()) == 1


def test_account_page_shows_one_niches_posts(state):
    from tokmanager.gui.account import AccountPage

    for it in state.store.queue(state.active.id)[:3]:  # g-a, m-a, g-b
        it.status, it.posted_at, it.queue_pos = "posted", 1_000_000.0, None
    state.store._renumber()
    page = AccountPage(state)
    assert page.history.rowCount() == 3
    state.set_niche_filter("g1")
    assert sorted(page.history.item(r, 1).text() for r in range(page.history.rowCount())) == ["g-a", "g-b"]
    assert page.tabs.tabText(1) == "Posted from this app · Gaming Videos"
    state.set_niche_filter(niches.ALL)
    assert page.tabs.tabText(1) == "Posted from this app"
