"""Live page: the shown account's numbers as they come in, from TikTok's official API (live.py)."""

from __future__ import annotations

import datetime as dt
import html
import time
import webbrowser

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter, QStackedWidget, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import live
from ..tiktok import methods
from . import theme
from .library import SortItem
from .live_monitor import LiveMonitor
from .state import AppState
from .widgets import StatCard, badge_for, fmt_num, label, panel

URL_ROLE = Qt.UserRole + 2
TREND_ROLE = Qt.UserRole + 3
SHOWN_VIDEOS = 60  # the newest videos in the table
SHOWN_EVENTS = 400

DOT = {"live": theme.GOOD, "reading": theme.GOOD, "starting": theme.ACCENT_2, "retrying": theme.WARN,
       "stopped": theme.BAD, "off": theme.MUTED, "setup": theme.MUTED, "connect": theme.MUTED}
WORD = {"live": "Live", "reading": "Live", "starting": "Starting", "retrying": "Retrying", "stopped": "Stopped",
        "off": "Off", "setup": "Not set up", "connect": "Not connected"}
KIND_COLOUR = {"seen": theme.GOOD, "hot": theme.ACCENT, "milestone": theme.WARN, "error": theme.BAD,
               "followers": theme.ACCENT_2, "new": theme.ACCENT_2, "start": theme.MUTED, "away": theme.MUTED}


class TrendDelegate(QStyledItemDelegate):
    """A small line of a video's views over the last few hours (from the live view's readings)."""

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        pts = index.data(TREND_ROLE) or []
        if len(pts) < 2:
            return
        r = QRectF(option.rect).adjusted(6, 7, -6, -7)
        t0, t1 = pts[0][0], pts[-1][0]
        lo = min(v for _, v in pts)
        hi = max(v for _, v in pts)
        span_t, span_v = max(1.0, t1 - t0), max(1.0, hi - lo)
        path = QPainterPath()
        for i, (t, v) in enumerate(pts):
            p = QPointF(r.left() + (t - t0) / span_t * r.width(), r.bottom() - (v - lo) / span_v * r.height())
            path.moveTo(p) if i == 0 else path.lineTo(p)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.ACCENT_2 if hi > lo else theme.MUTED), 1.6))
        painter.drawPath(path)
        painter.restore()


class LivePage(QWidget):
    connect_requested = Signal()  # Connect API for the shown account (the main window does it)
    setup_requested = Signal()  # Settings → TikTok API

    def __init__(self, state: AppState, monitor: LiveMonitor):
        super().__init__()
        self.state = state
        self.monitor = monitor
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Top bar: who, whether it's live, and the controls
        bar = panel()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.badge = QLabel()
        bl.addWidget(self.badge)
        bl.addSpacing(4)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.who = label("", "h2")
        self.status = label("", "muted")
        self.status.setTextFormat(Qt.RichText)
        col.addWidget(self.who)
        col.addWidget(self.status)
        bl.addLayout(col, 1)
        self.watch_btn = QPushButton("Watch live")
        self.watch_btn.setCheckable(True)
        self.watch_btn.setToolTip("Read this account from TikTok's API every so often and show what changes")
        self.watch_btn.clicked.connect(lambda on: self.monitor.set_watching(self.state.active.id, on))
        self.interval = QComboBox()
        for secs, text in live.INTERVALS:
            self.interval.addItem(text, secs)
        self.interval.setToolTip("How often TikTok is read, for every account you watch. TikTok's numbers "
                                 "themselves can trail the app by a few minutes.")
        self.interval.activated.connect(self._interval_picked)
        self.now_btn = QPushButton("⟳ Check now")
        self.now_btn.clicked.connect(lambda: self.monitor.check_now(self.state.active.id))
        self.open_btn = QPushButton("Open on TikTok")
        self.open_btn.clicked.connect(self._open_profile)
        for w in (self.watch_btn, self.interval, self.now_btn, self.open_btn):
            bl.addWidget(w)
        root.addWidget(bar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_intro())
        self.stack.addWidget(self._build_board())

        # Several signals can come at once (a reading updates the stats and the feed): one refresh for
        # them all, and none while the page is hidden (it catches up when shown).
        self._dirty = True
        self._soon = QTimer(self, singleShot=True, interval=60, timeout=self.refresh)
        self.monitor.updated.connect(self._updated)
        self.state.stats_changed.connect(self._updated)
        self.state.account_switched.connect(self._changed)
        self.state.account_changed.connect(self._changed)
        self.state.library_changed.connect(self._changed)
        self.state.jobs_changed.connect(self._update_status)
        # "Read 12 s ago · next in 48 s" counts by itself while the page is showing
        self.clock = QTimer(self, interval=1000, timeout=self._update_status)
        self.clock.start()
        self.refresh()

    # --- layout -------------------------------------------------------------------

    def _build_intro(self) -> QWidget:
        """What the Live page does, and the one or two things to do before it can."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box = panel()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(22, 18, 22, 18)
        bl.setSpacing(8)
        bl.addWidget(label("Watch your account live", "title"))
        bl.addWidget(label(
            "TikTok Manager reads the account from TikTok's official API every minute (no browser window) and "
            "shows what changes: views, likes, comments and shares on each video, new followers, videos taking "
            "off, and milestones. When a clip from the Ready page shows up on TikTok, it's marked as posted by "
            "itself, so posting from your phone needs no clicks here.", wrap=True))
        self.intro_steps = label("", wrap=True)
        self.intro_steps.setTextFormat(Qt.RichText)
        bl.addWidget(self.intro_steps)
        row = QHBoxLayout()
        self.intro_setup = QPushButton("Set up the TikTok API…")
        self.intro_setup.clicked.connect(self.setup_requested.emit)
        self.intro_connect = QPushButton("Connect API")
        self.intro_connect.setObjectName("primary")
        self.intro_connect.clicked.connect(self.connect_requested.emit)
        row.addWidget(self.intro_setup)
        row.addWidget(self.intro_connect)
        row.addStretch(1)
        bl.addLayout(row)
        bl.addWidget(label("TikTok's API lists public videos only, and its numbers can trail the TikTok app by a "
                           "few minutes. It doesn't give comment text, only how many.", "muted", wrap=True))
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _build_board(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        cards = QHBoxLayout()
        self.c_followers = StatCard("Followers")
        self.c_views = StatCard("Views today")
        self.c_likes = StatCard("Likes today")
        self.c_comments = StatCard("Comments today")
        self.c_shares = StatCard("Shares today")
        for c in (self.c_followers, self.c_views, self.c_likes, self.c_comments, self.c_shares):
            c.setToolTip("Counted from what the live view has seen since midnight")
            cards.addWidget(c)
        lay.addLayout(cards)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(label("Videos", "h2"))
        cols = ["Video", "Posted", "Views", "Last hour", "Likes", "Comments", "Shares", "Trend"]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().hide()
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.setSortingEnabled(True)
        t.verticalHeader().setDefaultSectionSize(32)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(7, QHeaderView.Fixed)
        t.setColumnWidth(7, 110)
        t.setItemDelegateForColumn(7, TrendDelegate(t))
        t.horizontalHeaderItem(3).setToolTip("Views gained in the last hour (once the live view has been on that long)")
        t.horizontalHeaderItem(7).setToolTip("Views over the last few hours, from the live view's readings")
        t.doubleClicked.connect(self._open_video)
        self.videos = t
        ll.addWidget(t, 1)
        ll.addWidget(label("Double-click a video to open it on TikTok.", "muted"))
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(label("Activity", "h2"))
        self.feed = QListWidget()
        self.feed.setWordWrap(True)
        self.feed.setSelectionMode(QAbstractItemView.NoSelection)
        self.feed.setSpacing(1)
        self.feed.itemDoubleClicked.connect(self._open_event)
        rl.addWidget(self.feed, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)
        return w

    # --- refresh ------------------------------------------------------------------

    def _updated(self, account_id: str) -> None:
        if account_id == self.state.active.id:
            self._changed()

    def _changed(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._soon.start()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        a = self.state.active
        st = self.state.store
        s = self.state.settings
        p = st.profile(a.id)
        self.badge.setPixmap(badge_for(a, 34))
        self.who.setText(f"{a.label}" + (f" (@{a.username})" if a.username and a.name else ""))
        status = self.monitor.status(a)
        ready = status.state not in ("setup", "connect")
        self.stack.setCurrentIndex(1 if ready else 0)
        self.watch_btn.setVisible(ready)
        self.watch_btn.setChecked(a.live)
        self.watch_btn.setText("● Watching live" if a.live else "Watch live")
        self.interval.setVisible(ready)
        self.interval.setCurrentIndex(max(0, self.interval.findData(s.live_interval)))
        self.now_btn.setVisible(ready and a.live)
        self.open_btn.setEnabled(bool(a.username or p.username))
        self._update_status()
        if not ready:
            self._fill_intro(status.state)
            return
        w = self.monitor.watcher(a.id)

        totals = live.today(w.log.events)
        self.c_followers.set(fmt_num(p.followers) if p.fetched_at else "—",
                             *(_delta(totals["followers"], "today") if p.fetched_at else ("", "")))
        for card, key in ((self.c_views, "views"), (self.c_likes, "likes"), (self.c_comments, "comments"),
                          (self.c_shares, "shares")):
            n = totals[key]
            card.set(f"+{fmt_num(n)}" if n > 0 else fmt_num(n), "")

        with st.lock:
            vids = sorted(st.videos(a.id).values(), key=lambda v: v.create_time, reverse=True)[:SHOWN_VIDEOS]
            by_tid = {i.tiktok_id: i for i in st.items.values() if i.tiktok_id and i.account == a.id}
        t = self.videos
        t.setSortingEnabled(False)
        t.setRowCount(len(vids))
        for r, v in enumerate(vids):
            item = by_tid.get(v.id)
            hour = w.last_hour(v.id)
            name = " ".join(((item.headline if item else "") or v.description or "(no caption)").split())
            cells = [
                SortItem(name, name.lower()),
                SortItem(_when(v.create_time), v.create_time),
                SortItem(fmt_num(v.views), v.views),
                SortItem("—" if hour is None else f"+{fmt_num(hour)}" if hour > 0 else fmt_num(hour),
                         hour if hour is not None else -1),
                SortItem(fmt_num(v.likes), v.likes),
                SortItem(fmt_num(v.comments), v.comments),
                SortItem(fmt_num(v.shares), v.shares),
                QTableWidgetItem(""),
            ]
            if hour:
                cells[3].setForeground(QColor(theme.GOOD if hour > 0 else theme.BAD))
            cells[0].setToolTip(v.description)
            cells[0].setData(URL_ROLE, v.url)
            cells[7].setData(TREND_ROLE, w.trend(v.id) or [(h[0], h[1]) for h in v.history[-12:]])
            for c, cell in enumerate(cells):
                t.setItem(r, c, cell)
        t.setSortingEnabled(True)
        self._fill_feed(w.log.events, {v.id: v for v in vids})

    def _fill_intro(self, state: str) -> None:
        a = self.state.active
        app = methods.app_ready(self.state.settings)
        done, todo = f"<span style='color:{theme.GOOD}'>✓</span>", "○"
        self.intro_steps.setText(
            f"{done if app else todo} <b>1.</b> Make your free developer app on developers.tiktok.com and put its "
            "keys in Settings → TikTok API (it walks you through it, about 10 minutes).<br>"
            f"{todo} <b>2.</b> Connect {html.escape(a.label)} to it: TikTok asks you to approve TikTok Manager, "
            "once. The login lasts a year.")
        self.intro_setup.setVisible(True)
        self.intro_setup.setObjectName("" if app else "primary")
        self.intro_setup.setText("TikTok API settings…" if app else "Set up the TikTok API…")
        self.intro_setup.style().unpolish(self.intro_setup)
        self.intro_setup.style().polish(self.intro_setup)
        self.intro_connect.setVisible(app and state == "connect")
        busy = self.state.account_busy(a.id, ("login", "api"))
        self.intro_connect.setEnabled(not busy)
        self.intro_connect.setText("Connecting…" if busy else f"Connect {a.label}")

    def _fill_feed(self, events: list[live.Event], videos: dict) -> None:
        f = self.feed
        f.clear()
        if not events:
            li = QListWidgetItem("Nothing yet. The first reading shows up in a moment.")
            li.setForeground(QColor(theme.MUTED))
            f.addItem(li)
            return
        day = ""
        today_s = dt.date.today()
        for e in reversed(events[-SHOWN_EVENTS:]):
            d = dt.date.fromtimestamp(e.at)
            name = "Today" if d == today_s else "Yesterday" if (today_s - d).days == 1 else d.strftime("%a %d %b")
            if name != day:
                day = name
                head = QListWidgetItem(name)
                head.setFlags(Qt.NoItemFlags)
                font = head.font()
                font.setBold(True)
                head.setFont(font)
                head.setForeground(QColor(theme.MUTED))
                f.addItem(head)
            li = QListWidgetItem(f"{time.strftime('%H:%M', time.localtime(e.at))}   {e.text}")
            li.setToolTip(e.text)
            if e.kind in KIND_COLOUR:
                li.setForeground(QColor(KIND_COLOUR[e.kind]))
            v = videos.get(e.video_id)
            if v is not None and v.url:
                li.setData(URL_ROLE, v.url)
            f.addItem(li)

    def _update_status(self) -> None:
        if not self.isVisible() and self.status.text():
            return
        status = self.monitor.status(self.state.active)
        if (status.state in ("setup", "connect")) != (self.stack.currentIndex() == 0):
            self._changed()  # connected or set up meanwhile: show the other view
        colour = DOT.get(status.state, theme.MUTED)
        self.status.setText(f"<span style='color:{colour}'>●</span> <b>{WORD.get(status.state, '')}</b> · "
                            f"{html.escape(status.text)}")
        self.now_btn.setEnabled(status.state not in ("reading",))

    # --- actions ------------------------------------------------------------------

    def _interval_picked(self) -> None:
        secs = self.interval.currentData()
        s = self.state.settings
        if secs and secs != s.live_interval:
            s.live_interval = int(secs)
            self.state.save_settings()
            for w in self.monitor.watchers.values():  # the new pace starts from the next reading
                w.next_at = min(w.next_at, time.time() + secs)
            self._update_status()

    def _open_profile(self) -> None:
        a = self.state.active
        name = a.username or self.state.store.profile(a.id).username
        if name:
            webbrowser.open(f"https://www.tiktok.com/@{name}")

    def _open_video(self, idx) -> None:
        cell = self.videos.item(idx.row(), 0)
        url = cell.data(URL_ROLE) if cell else ""
        if url:
            webbrowser.open(url)

    def _open_event(self, li: QListWidgetItem) -> None:
        url = li.data(URL_ROLE)
        if url:
            webbrowser.open(url)


def _delta(n: int, when: str) -> tuple[str, str]:
    if not n:
        return f"no change {when}", ""
    return f"{'+' if n > 0 else ''}{fmt_num(n)} {when}", theme.GOOD if n > 0 else theme.BAD


def _when(ts: float) -> str:
    if not ts:
        return "—"
    d = time.time() - ts
    if d < 86400:
        return live.ago(d) + " ago"
    return dt.datetime.fromtimestamp(ts).strftime("%d %b %H:%M")
