"""Queue page: the shown account's posting order (drag & drop), its schedule, and how it posts (by hand or on autopilot)."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from PySide6.QtCore import QDateTime, QRectF, QSize, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDateTimeEdit, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QStyle, QStyledItemDelegate, QTimeEdit, QVBoxLayout, QWidget)

from .. import jobs, niches, schedule
from ..accounts import POSTING_MODES
from ..scoring import grade
from ..store import VideoItem
from ..tiktok import methods
from . import theme
from .editor import PostEditor
from .state import AppState
from .widgets import Segmented, badge_for, fmt_dur, label, panel, thumb_pixmap

ID_ROLE = Qt.UserRole + 1
POS_ROLE = Qt.UserRole + 2  # its place in the whole queue (a niche's videos are only some of it)


class QueueDelegate(QStyledItemDelegate):
    """Paints one queue row: position, thumbnail, title, when/status line, score pill."""

    def __init__(self, page: "QueuePage"):
        super().__init__(page)
        self.page = page

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(200, 76)

    def paint(self, p: QPainter, option, index):
        item: VideoItem | None = self.page.state.store.get(index.data(ID_ROLE))
        if not item:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(4, 3, -4, -3)
        selected = option.state & QStyle.State_Selected
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SELECT if selected else theme.PANEL_2))
        p.drawRoundedRect(QRectF(r), 9, 9)

        f = QFont(option.font)
        f.setBold(True)
        f.setPointSizeF(12)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(r.x() + 6, r.y(), 34, r.height()), Qt.AlignCenter, str(index.data(POS_ROLE) or index.row() + 1))

        p.drawPixmap(r.x() + 42, r.y() + 5, thumb_pixmap(item.thumb, 38, r.height() - 10))

        x = r.x() + 92
        pill_w = 70
        text_w = r.right() - x - pill_w - 12
        f.setPointSizeF(10)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        title = p.fontMetrics().elidedText(item.headline, Qt.ElideRight, text_w)
        p.drawText(QRectF(x, r.y() + 8, text_w, 20), Qt.AlignLeft | Qt.AlignVCenter, title)

        f.setBold(False)
        f.setPointSizeF(9)
        p.setFont(f)
        now = time.time()
        if item.status == "uploading":
            line, col = "⏫ Posting now…", theme.WARN
        elif item.status == "failed":
            line, col = f"✖ Failed: {item.last_error or 'unknown error'}", theme.BAD
        else:
            when = schedule.fmt_when(item.planned_at, now)
            pin = "📌 " if item.pinned_at else "🕑 "
            late = item.planned_at and item.planned_at < now
            line = pin + when + ("  (due)" if late else "")
            col = theme.ACCENT_2 if not late else theme.WARN
        p.setPen(QColor(col))
        p.drawText(QRectF(x, r.y() + 29, text_w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(line, Qt.ElideRight, text_w))
        p.setPen(QColor(theme.MUTED))
        caption = " ".join(item.caption.split())
        extra = caption if caption and caption != item.headline else Path(item.path).name  # not the title twice
        st = self.page.state
        niche = st.niches().name_of(item) if not st.niche_label() else ""  # every niche shows: say which
        sub = (f"{fmt_dur(item.duration)}  ·  " + (f"{item.part_label}  ·  " if item.part_label else "") +
               (f"🏷 {niche}  ·  " if niche else "") + extra[:120])
        p.drawText(QRectF(x, r.y() + 48, text_w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(sub, Qt.ElideRight, text_w))

        total = item.total
        pill = QRectF(r.right() - pill_w - 8, r.center().y() - 12, pill_w, 24)
        c = QColor(theme.score_colour(total))
        bg = QColor(c)
        bg.setAlpha(40)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(pill, 12, 12)
        f.setBold(True)
        f.setPointSizeF(10)
        p.setFont(f)
        p.setPen(c)
        p.drawText(pill, Qt.AlignCenter, "—" if total is None else f"{total:.0f}  {grade(total)}")
        p.restore()


class QueueList(QListWidget):
    reordered = Signal()

    def dropEvent(self, e):  # noqa: N802
        super().dropEvent(e)
        self.reordered.emit()


class QueuePage(QWidget):
    post_requested = Signal(str)  # item id
    ready_requested = Signal()  # show the Ready page

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        bar = QHBoxLayout()
        self.badge = QLabel()
        bar.addWidget(self.badge)
        self.summary = label("", "h2")
        bar.addWidget(self.summary)
        bar.addStretch(1)
        sort_score = QPushButton("Sort by score")
        sort_score.setToolTip("Best score posts first")
        sort_score.clicked.connect(lambda: self._reorder(schedule.by_score))
        smart = QPushButton("Smart order")
        smart.setToolTip("Best first, but never two clips from the same source video back to back")
        smart.clicked.connect(lambda: self._reorder(schedule.spread))
        bar.addWidget(sort_score)
        bar.addWidget(smart)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QueueList()
        self.list.setItemDelegate(QueueDelegate(self))
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setSpacing(1)
        self.list.reordered.connect(self._dropped)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        ll.addWidget(self.list, 1)
        self.empty = label("", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        ll.addWidget(self.empty, 1)
        moves = QHBoxLayout()
        for text, tip, fn in (("⤒", "Move to top", self.move_top), ("↑", "Move up", lambda: self.move(-1)),
                              ("↓", "Move down", lambda: self.move(1)), ("⤓", "Move to bottom", self.move_bottom)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(40)
            b.clicked.connect(fn)
            moves.addWidget(b)
        moves.addSpacing(8)
        self.remove_btn = QPushButton("Remove from queue")
        self.remove_btn.clicked.connect(self.remove)
        moves.addWidget(self.remove_btn)
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setToolTip("Put failed videos back in line")
        self.retry_btn.clicked.connect(self.retry)
        moves.addWidget(self.retry_btn)
        moves.addStretch(1)
        self.post_btn = QPushButton("Post selected now")
        self.post_btn.setObjectName("primary")
        self.post_btn.clicked.connect(self.post_now)
        moves.addWidget(self.post_btn)
        ll.addLayout(moves)
        split.addWidget(left)

        split.addWidget(self._build_side())
        split.setSizes([720, 400])
        split.setStretchFactor(0, 1)

        self.state.library_changed.connect(self.refresh)
        self.state.jobs_changed.connect(self._update_buttons)
        self.state.account_switched.connect(self._account_switched)
        self.state.account_changed.connect(self._load_schedule)
        self.state.niche_switched.connect(self._niche_switched)
        self._load_schedule()
        self.refresh()

    # --- side panel ---------------------------------------------------------------

    def _build_side(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(370)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        sched = panel()
        sl = QVBoxLayout(sched)
        sl.setContentsMargins(14, 12, 14, 12)
        self.sched_title = label("Schedule", "h2")
        sl.addWidget(self.sched_title)
        self.per_day = QSpinBox(minimum=1, maximum=24)
        self.first = QTimeEdit()
        self.last = QTimeEdit()
        for t in (self.first, self.last):
            t.setDisplayFormat("HH:mm")
        self.gap = QSpinBox(minimum=0, maximum=24 * 60, singleStep=15, suffix=" min")
        self.vary = QSpinBox(minimum=0, maximum=30, singleStep=5, prefix="± ", suffix=" min")
        self.vary.setToolTip("Each post time moves by up to this much, differently every day, so the account "
                             "doesn't post at the exact same minute daily. 0 keeps the times exact.")
        self.warm_up = QCheckBox("New account: warm up")
        self.warm_up.setToolTip(f"For a TikTok account that's just been made: no posts for the first 3 days, "
                                f"then 1 a day, then 2 a day in week two. Your own number starts after "
                                f"{schedule.WARM_UP_DAYS} days.")
        grid = QGridLayout()
        grid.addWidget(label("Posts per day"), 0, 0)
        grid.addWidget(self.per_day, 0, 1)
        grid.addWidget(label("Minimum gap"), 0, 2)
        grid.addWidget(self.gap, 0, 3)
        grid.addWidget(label("First post"), 1, 0)
        grid.addWidget(self.first, 1, 1)
        grid.addWidget(label("Last post"), 1, 2)
        grid.addWidget(self.last, 1, 3)
        grid.addWidget(label("Vary times"), 2, 0)
        grid.addWidget(self.vary, 2, 1)
        grid.addWidget(self.warm_up, 2, 2, 1, 2)
        sl.addLayout(grid)
        self.slots = label("", "muted", wrap=True)
        sl.addWidget(self.slots)
        self.sched_notes = label("", wrap=True)
        self.sched_notes.setStyleSheet(f"color: {theme.WARN};")
        sl.addWidget(self.sched_notes)
        mode_row = QHBoxLayout()
        self.mode_title = label("Posting")
        mode_row.addWidget(self.mode_title)
        self.mode = Segmented(list(POSTING_MODES), on_pick=self._mode_picked)
        mode_row.addWidget(self.mode, 1)
        sl.addLayout(mode_row)
        self.next_label = label("", wrap=True)
        self.next_label.setTextFormat(Qt.RichText)
        self.next_label.linkActivated.connect(lambda _href: self.ready_requested.emit())
        sl.addWidget(self.next_label)
        for w in (self.per_day, self.gap, self.vary):
            w.valueChanged.connect(self._schedule_changed)
        self.warm_up.toggled.connect(self._warm_up_toggled)
        for w in (self.first, self.last):
            w.timeChanged.connect(self._schedule_changed)
        lay.addWidget(sched)

        pin_box = panel()
        pl = QVBoxLayout(pin_box)
        pl.setContentsMargins(14, 12, 14, 12)
        pl.addWidget(label("Fixed time for the selected video", "h2"))
        pl.addWidget(label("Pinned videos post at exactly this time; the rest fill the slots around them.",
                           "muted", wrap=True))
        self.pin_edit = QDateTimeEdit()
        self.pin_edit.setDisplayFormat("ddd dd MMM yyyy  HH:mm")
        self.pin_edit.setCalendarPopup(True)
        self.pin_edit.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        row = QHBoxLayout()
        row.addWidget(self.pin_edit, 1)
        self.pin_btn = QPushButton("Pin")
        self.pin_btn.clicked.connect(self.pin)
        self.unpin_btn = QPushButton("Unpin")
        self.unpin_btn.clicked.connect(self.unpin)
        row.addWidget(self.pin_btn)
        row.addWidget(self.unpin_btn)
        pl.addLayout(row)
        lay.addWidget(pin_box)

        ed_box = panel()
        el = QVBoxLayout(ed_box)
        el.setContentsMargins(14, 12, 14, 12)
        self.editor = PostEditor(self.state)
        self.editor.edited.connect(lambda _id: self.list.viewport().update())
        el.addWidget(self.editor)
        lay.addWidget(ed_box)
        lay.addStretch(1)
        scroll.setWidget(box)
        return scroll

    # --- data ---------------------------------------------------------------------

    def _load_schedule(self) -> None:
        """Show the shown account's schedule and posting mode (without saving them back)."""
        a = self.state.active
        widgets = (self.per_day, self.gap, self.vary, self.warm_up, self.first, self.last)
        for w in widgets:
            w.blockSignals(True)
        self.per_day.setValue(a.posts_per_day)
        self.gap.setValue(a.min_gap_minutes)
        self.vary.setValue(a.vary_minutes)
        self.warm_up.setChecked(schedule.warm_up_day(a.apply(self.state.settings), dt.date.today()) is not None)
        self.first.setTime(QTime.fromString(a.first_post_time, "HH:mm"))
        self.last.setTime(QTime.fromString(a.last_post_time, "HH:mm"))
        self.mode.set_value(a.mode)
        for w in widgets:
            w.blockSignals(False)
        multi = len(self.state.accounts) > 1
        self.sched_title.setText(f"Schedule for {a.label}" if multi else "Schedule")
        self._update_next()
        self.editor.set_item(self.editor.item)  # the account's default hashtags / privacy may have changed

    def _account_switched(self) -> None:
        self.list.clearSelection()
        self._load_schedule()
        self.refresh()

    def _niche_switched(self) -> None:
        self.list.clearSelection()
        self.refresh()

    def replan(self) -> None:
        self.state.replan()

    def refresh(self) -> None:
        self.replan()
        a = self.state.active
        selected = set(self.selected_ids())
        whole = self.state.store.queue(a.id)
        place = {it.id: n for n, it in enumerate(whole, 1)}
        q = self.state.in_niche(whole)
        niche = self.state.niche_label()
        self.list.blockSignals(True)
        self.list.clear()
        for it in q:
            li = QListWidgetItem()
            li.setData(ID_ROLE, it.id)
            li.setData(POS_ROLE, place[it.id])
            li.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            self.list.addItem(li)
            if it.id in selected:
                li.setSelected(True)
        self.list.blockSignals(False)
        self.list.setVisible(bool(q))
        self.empty.setVisible(not q)
        multi = len(self.state.accounts) > 1
        self.badge.setVisible(multi)
        self.badge.setPixmap(badge_for(a, 24))
        if niche and whole:
            whose = f"{a.label}'s" if multi else "the"
            self.empty.setText(f"Nothing from {niche} is in {whose} queue.\n"
                               f"{len(whole)} other video{'s are' if len(whole) != 1 else ' is'}: pick “All niches” "
                               "at the top to see them.")
        else:
            self.empty.setText((f"Nothing is queued for {a.label} yet." if multi else "The queue is empty.") +
                               "\nSelect videos in the Library and click “" + ("Queue for…" if multi else "Add to queue")
                               + "”.")
        queued = [i for i in q if i.status == "queued"]
        failed = sum(1 for i in q if i.status == "failed")
        days = 0.0
        if queued and queued[-1].planned_at:
            days = (queued[-1].planned_at - time.time()) / 86400
        txt = f"{a.label}: " if multi else ""
        txt += f"{niche}: {len(q)} of {len(whole)} in the queue" if niche else f"{len(q)} in the queue"
        if queued:
            txt += f" · runs until {schedule.fmt_when(queued[-1].planned_at)}" + (f" ({days:.0f} days)" if days >= 2 else "")
        if failed:
            txt += f" · {failed} failed"
        self.summary.setText(txt)
        self._update_next()
        self._selection_changed()

    def _update_next(self) -> None:
        """The daily times and what happens next, which depend on how the account posts."""
        a = self.state.active
        s = self.state.settings
        reminders = not a.autopilot and s.remind_to_post  # Settings → Ready to upload can turn them off
        what = "reminder times" if reminders else "posting times"
        sa = self.state.settings_for()
        vary = f", each moved by up to {sa.vary_minutes} min a day" if sa.vary_minutes else ""
        self.slots.setText(f"Daily {what}: " + ", ".join(t.strftime("%H:%M") for t in schedule.daily_slots(sa))
                           + vary)
        notes = schedule.describe(sa, dt.date.today())
        self.sched_notes.setText("\n".join(f"• {n}" for n in notes))
        self.sched_notes.setVisible(bool(notes))
        q = [i for i in self.state.store.queue(a.id) if i.status == "queued" and i.planned_at]
        if not a.autopilot:
            nxt = f" Next: <b>{_esc(q[0].headline)}</b> at {schedule.fmt_when(q[0].planned_at)}." if q else ""
            self.next_label.setText(f"You post these yourself from the <a href='ready' style='color:{theme.ACCENT_2}'>"
                                    f"Ready page</a>{', with a reminder at each time' if reminders else ''}.{nxt}")
        elif not jobs.can_autopost(a, s):
            why = methods.can_post(s, a)[1]
            self.next_label.setText(f"<span style='color:{theme.WARN}'>● On, but it can't post yet.</span> {_esc(why)}")
        elif not q:
            self.next_label.setText(f"<span style='color:{theme.GOOD}'>● On.</span> Nothing is queued.")
        else:
            nxt = q[0]
            self.next_label.setText(f"<span style='color:{theme.GOOD}'>● On.</span> Next: <b>{_esc(nxt.headline)}</b> "
                                    f"at {schedule.fmt_when(nxt.planned_at)} via your browser login.")

    def selected_ids(self) -> list[str]:
        return [li.data(ID_ROLE) for li in sorted(self.list.selectedItems(), key=self.list.row)]

    def select_item(self, item_id: str) -> None:
        """Select one video (e.g. "Edit…" on the Ready page) and scroll to it."""
        it = self.state.store.get(item_id)
        if it is not None and not self.state.in_niche([it]):  # in another niche: show them all
            self.state.set_niche_filter(niches.ALL)
        self.refresh()
        self.list.clearSelection()
        for r in range(self.list.count()):
            li = self.list.item(r)
            if li.data(ID_ROLE) == item_id:
                li.setSelected(True)
                self.list.scrollToItem(li)
                break

    def _order(self) -> list[str]:
        return [self.list.item(r).data(ID_ROLE) for r in range(self.list.count())]

    def _selection_changed(self) -> None:
        ids = self.selected_ids()
        item = self.state.store.get(ids[0]) if len(ids) == 1 else None
        if item is not self.editor.item:
            self.editor.set_item(item)
        if item:
            when = item.pinned_at or item.planned_at or (time.time() + 3600)
            self.pin_edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(when)))
        self._update_buttons()

    def _update_buttons(self) -> None:
        ids = self.selected_ids()
        items = [self.state.store.get(i) for i in ids]
        one = len(items) == 1 and items[0] is not None
        posting = self.state.busy("post")
        self.post_btn.setEnabled(bool(items) and not posting and all(i.status in ("queued", "failed") for i in items))
        self.post_btn.setText("Posting…" if posting else "Post selected now")
        self.remove_btn.setEnabled(bool(items) and all(i.status != "uploading" for i in items))
        self.retry_btn.setEnabled(any(i.status == "failed" for i in items))
        self.pin_btn.setEnabled(one and items[0].status == "queued")
        self.unpin_btn.setEnabled(one and bool(items[0].pinned_at))
        self.pin_edit.setEnabled(one)

    # --- actions ------------------------------------------------------------------

    def _commit_order(self, ids: list[str]) -> None:
        """Save the order the list shows. With one niche showing, its videos take the places they had
        between the others, which keep theirs."""
        if self.state.niche_label():
            ids = niches.merge_order([i.id for i in self.state.store.queue(self.state.active.id)], ids)
        self.state.store.set_order(ids)
        self.state.save_soon()
        self.state.library_changed.emit()

    def _dropped(self) -> None:
        self._commit_order(self._order())

    def _reorder(self, fn) -> None:
        q = self.state.in_niche(self.state.store.queue(self.state.active.id))
        self._commit_order([i.id for i in fn(q)])

    def move(self, delta: int) -> None:
        order = self._order()
        sel = self.selected_ids()
        rng = range(len(order)) if delta < 0 else range(len(order) - 1, -1, -1)
        for idx in rng:
            if order[idx] in sel:
                j = idx + delta
                if 0 <= j < len(order) and order[j] not in sel:
                    order[idx], order[j] = order[j], order[idx]
        self._commit_order(order)

    def move_top(self) -> None:
        sel = self.selected_ids()
        self._commit_order(sel + [i for i in self._order() if i not in sel])

    def move_bottom(self) -> None:
        sel = self.selected_ids()
        self._commit_order([i for i in self._order() if i not in sel] + sel)

    def remove(self) -> None:
        self.state.store.dequeue(self.selected_ids())
        self.state.save_soon()
        self.state.library_changed.emit()

    def retry(self) -> None:
        with self.state.store.lock:
            for i in self.selected_ids():
                it = self.state.store.get(i)
                if it and it.status == "failed":
                    it.status, it.last_error = "queued", ""
        self.state.save_soon()
        self.state.library_changed.emit()

    def pin(self) -> None:
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        ts = self.pin_edit.dateTime().toSecsSinceEpoch()
        if ts < time.time() - 60:
            QMessageBox.information(self, "Pin time", "Pick a time in the future.")
            return
        with self.state.store.lock:
            self.state.store.get(ids[0]).pinned_at = float(ts)
        self.state.save_soon()
        self.state.library_changed.emit()

    def unpin(self) -> None:
        with self.state.store.lock:
            for i in self.selected_ids():
                self.state.store.get(i).pinned_at = None
        self.state.save_soon()
        self.state.library_changed.emit()

    def post_now(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        names = "\n".join(f"• {self.state.store.get(i).headline}" for i in ids[:8])
        to = self.state.active.handle
        if QMessageBox.question(self, "Post now", f"Post {len(ids)} video(s) to {to} now?\n\n{names}") != QMessageBox.Yes:
            return
        for i in ids:
            self.post_requested.emit(i)

    def _schedule_changed(self) -> None:
        a = self.state.active
        a.posts_per_day = self.per_day.value()
        a.min_gap_minutes = self.gap.value()
        a.vary_minutes = self.vary.value()
        a.first_post_time = self.first.time().toString("HH:mm")
        a.last_post_time = self.last.time().toString("HH:mm")
        self.state.save_accounts()
        self.refresh()

    def _warm_up_toggled(self, on: bool) -> None:
        """Start the shown account's warm-up, counted from when it was added if that was recent, or end it."""
        a = self.state.active
        if on:
            a.start_warm_up()
        else:
            a.warm_up_start = 0.0
        self.state.save_accounts()
        self.refresh()

    def _mode_picked(self, mode: str) -> None:
        self.state.set_autopilot(self.state.active.id, mode == "autopilot")

    def _menu(self, pos) -> None:
        ids = self.selected_ids()
        others = [a for a in self.state.accounts if a.id != self.state.active.id]
        if not ids:
            return
        m = QMenu(self)
        m.addAction("Post now", self.post_now).setEnabled(self.post_btn.isEnabled())
        m.addAction("Remove from queue", self.remove).setEnabled(self.remove_btn.isEnabled())
        if others:
            move = m.addMenu("Move to another account's queue")
            for a in others:
                move.addAction(QIcon(badge_for(a, 18)), a.label, lambda i=a.id: self.move_to_account(i))
            move.setEnabled(self.remove_btn.isEnabled())
            copy = m.addMenu("Also queue for")
            for a in others:
                copy.addAction(QIcon(badge_for(a, 18)), a.label, lambda i=a.id: self.copy_to_account(i))
        m.exec(self.list.viewport().mapToGlobal(pos))

    def move_to_account(self, account_id: str) -> None:
        ids = self.selected_ids()
        self.state.store.move_to(ids, account_id)
        self.state.save_soon()
        self.state.log(f"Moved {len(ids)} video{'s' * (len(ids) != 1)} to "
                       f"{self.state.accounts.label_for(account_id)}'s queue.")
        self.state.library_changed.emit()

    def copy_to_account(self, account_id: str) -> None:
        ids = self.selected_ids()
        for i in ids:
            self.state.store.copy_for(i, account_id)
        self.state.save_soon()
        self.state.log(f"Queued {len(ids)} video{'s' * (len(ids) != 1)} for "
                       f"{self.state.accounts.label_for(account_id)} as well.")
        self.state.library_changed.emit()


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
