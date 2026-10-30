"""Right-click → Niche, on the Library and the Queue: say which niche videos are, when it was worked out
wrong or couldn't be (a video with no niche gets no posting time, niches.py)."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from .. import niches
from ..store import VideoItem
from .state import AppState


def fill(m: QMenu, state: AppState, items: list[VideoItem], parent: QWidget) -> None:
    index = state.niches()
    now = {index.of(it) or niches.NONE for it in items}
    for n in index.niches:
        act = m.addAction(n.label, lambda name=n.name: set_niche(state, items, name))
        act.setCheckable(True)
        act.setChecked(now == {n.key})
    if index.niches:
        m.addSeparator()
    m.addAction("New niche…", lambda: new_niche(state, items, parent))
    act = m.addAction("No niche", lambda: set_niche(state, items, niches.NONE))
    act.setCheckable(True)
    act.setChecked(now == {niches.NONE})
    if any(it.niche_set for it in items):
        m.addSeparator()
        m.addAction("Work it out again", lambda: set_niche(state, items, ""))


def new_niche(state: AppState, items: list[VideoItem], parent: QWidget) -> None:
    name, ok = QInputDialog.getText(parent, "New niche", "What are these videos? For example Gaming or Finance:")
    if ok and niches.fold(name):
        set_niche(state, items, name)


def set_niche(state: AppState, items: list[VideoItem], name: str) -> None:
    with state.store.lock:
        changed = niches.set_niche(items, name)
    if not changed:
        return
    what = ("worked out again" if not name else "set to no niche" if name == niches.NONE
            else f"set to {' '.join(name.split())}")
    state.log(f"Niche of {changed} video{'s' * (changed != 1)} {what}.")
    state.save_soon()
    state.library_changed.emit()
