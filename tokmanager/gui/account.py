"""Account page: the shown account's TikTok login, profile numbers, per-video stats and post history."""

from __future__ import annotations

import datetime as dt
import html
import os
import statistics
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QTableWidget, QTabWidget, QVBoxLayout, QWidget)

from .. import results
from ..store import VideoItem
from . import theme
from .library import SortItem
from .state import AppState
from .widgets import StatCard, badge_for, fmt_num, label, panel

URL_ROLE = Qt.UserRole + 2
ID_ROLE = Qt.UserRole + 1


class AccountPage(QWidget):
    login_requested = Signal()
    logout_requested = Signal()
    refresh_requested = Signal()
    check_requested = Signal()  # test TikTok's upload page without posting
    requeue_requested = Signal(str)
    manage_requested = Signal(str)  # account id
    profile_requested = Signal(str)  # account id
    live_requested = Signal()  # show the Live page
    cleanup_requested = Signal(str)  # account id: Clean up its posted videos

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Connection bar
        conn = panel()
        cl = QHBoxLayout(conn)
        cl.setContentsMargins(14, 10, 14, 10)
        self.badge = QLabel()
        cl.addWidget(self.badge)
        cl.addSpacing(4)
        self.who = label("", "h2")
        self.how = label("", "muted")
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(self.who)
        col.addWidget(self.how)
        self.check_status = label("", "muted")
        col.addWidget(self.check_status)
        self.profile_status = label("", "muted")
        col.addWidget(self.profile_status)
        cl.addLayout(col, 1)
        cl.addWidget(label("@"))
        self.username = QLineEdit()
        self.username.setPlaceholderText("TikTok username")
        self.username.setFixedWidth(180)
        self.username.setToolTip("Filled in automatically after you log in; you can also type it.")
        self.username.editingFinished.connect(self._username_changed)
        cl.addWidget(self.username)
        self.login_btn = QPushButton("Log in")
        self.login_btn.setObjectName("primary")
        self.login_btn.clicked.connect(self.login_requested.emit)
        self.logout_btn = QPushButton("Log out")
        self.logout_btn.clicked.connect(self._confirm_logout)
        self.refresh_btn = QPushButton("⟳ Refresh stats")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        self.check_btn = QPushButton("Test posting")
        self.check_btn.setToolTip("Walks TikTok Studio's upload page with a 3-second black test clip, up to just "
                                  "before Post, then throws the clip away. Nothing is posted.")
        self.check_btn.clicked.connect(self.check_requested.emit)
        self.profile_btn = QPushButton("Profile…")
        self.profile_btn.setToolTip("Profile picture, name and bio for this account, applied to TikTok for you")
        self.profile_btn.clicked.connect(lambda: self.profile_requested.emit(self.state.active.id))
        self.manage_btn = QPushButton("Account settings…")
        self.manage_btn.setToolTip("Name, posting defaults and niche for this account; add or remove accounts")
        self.manage_btn.clicked.connect(lambda: self.manage_requested.emit(self.state.active.id))
        for b in (self.login_btn, self.refresh_btn, self.check_btn, self.logout_btn, self.profile_btn, self.manage_btn):
            cl.addWidget(b)
        root.addWidget(conn)

        # A line about the live view, linking to the Live page
        self.live_line = label("", "muted")
        self.live_line.setTextFormat(Qt.RichText)
        self.live_line.linkActivated.connect(lambda _l: self.live_requested.emit())
        root.addWidget(self.live_line)

        # Stat cards
        cards = QHBoxLayout()
        self.c_followers = StatCard("Followers")
        self.c_likes = StatCard("Likes")
        self.c_videos = StatCard("Videos")
        self.c_views = StatCard("Views (videos read)")
        self.c_avg = StatCard("Median views, last 10")
        for c in (self.c_followers, self.c_likes, self.c_videos, self.c_views, self.c_avg):
            cards.addWidget(c)
        root.addLayout(cards)
        self.insight = label("", wrap=True)
        self.insight.setTextFormat(Qt.RichText)
        root.addWidget(self.insight)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        self.videos = self._table(["Posted", "Caption", "Views", "Likes", "Comments", "Shares", "Engagement",
                                   "Views lately", "Score"])
        self.videos.horizontalHeaderItem(7).setToolTip("Views gained since the reading before the last one (at least "
                                                       "half an hour apart while the live view is on)")
        self.videos.doubleClicked.connect(lambda idx: self._open_url(self.videos, idx.row()))
        self.history = self._table(["Posted", "Video", "Score", "Status", "Views", "Likes", "Details"])
        self.history.doubleClicked.connect(lambda idx: self._open_url(self.history, idx.row()))
        self.history.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu)
        tabs.addTab(self.videos, "Videos on TikTok")
        tabs.addTab(self.history, "Posted from this app")
        foot = QHBoxLayout()
        foot.addWidget(label("Double-click a row to open it on TikTok.", "muted"), 1)
        self.cleanup_btn = QPushButton("🧹 Clean up posted videos…")
        self.cleanup_btn.setToolTip("Free the space this account's posted videos take: a .txt record of each is "
                                    "kept, then its files are deleted. You see the list and confirm first.")
        self.cleanup_btn.clicked.connect(lambda: self.cleanup_requested.emit(self.state.active.id))
        foot.addWidget(self.cleanup_btn)
        root.addLayout(foot)

        self.state.account_changed.connect(self.refresh)
        self.state.account_switched.connect(self.refresh)
        self.state.library_changed.connect(self.refresh)
        self.state.stats_changed.connect(self._stats_changed)
        self.state.jobs_changed.connect(self._update_buttons)
        self.refresh()

    def _table(self, cols: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().hide()
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.setSortingEnabled(True)
        t.verticalHeader().setDefaultSectionSize(34)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        return t

    # --- refresh ------------------------------------------------------------------

    def refresh(self) -> None:
        s, st, a = self.state.settings, self.state.store, self.state.active
        p = st.profile(a.id)
        if not self.username.hasFocus():
            self.username.setText(a.username)
        self.badge.setPixmap(badge_for(a, 34))
        browser = "Edge" if s.browser_channel == "msedge" else "Chrome"
        connected = a.logged_in
        self.how.setText(f"Posts through TikTok's website in {browser} (this account's own profile)")
        self.login_btn.setText("Log in again" if a.logged_in else "Log in")
        self.login_btn.setObjectName("" if a.logged_in else "primary")
        self.login_btn.style().unpolish(self.login_btn)
        self.login_btn.style().polish(self.login_btn)
        self.logout_btn.setVisible(a.logged_in)
        self._refresh_live()
        self.check_btn.setVisible(a.logged_in)
        tested = a.upload_check_at
        verdict = {"ok": "✓ Posting test passed", "warn": "⚠ Posting test passed with a warning",
                   "fail": "✖ Posting test failed"}.get(a.upload_check, "Posting test ran")
        self.check_status.setText(f"{verdict} {_ago(a.upload_check_at)}" if tested else "")
        self.check_status.setToolTip(a.upload_check_detail if tested else "")
        self.check_status.setVisible(bool(tested))
        self.check_status.setStyleSheet(f"color: {theme.BAD};" if tested and a.upload_check == "fail" else "")
        from .. import profile

        self.profile_status.setText(profile.status(a) if profile.is_set(a) else "")
        self.profile_status.setVisible(profile.is_set(a))
        self.profile_status.setStyleSheet(f"color: {theme.BAD};" if a.profile_error and profile.pending(a) else "")
        title, handle = a.name or p.nickname, f"@{a.username}" if a.username else ""
        name = f"{title} ({handle})" if title and handle else title or handle or "New account"
        if not connected:
            name = f"Not logged in{' · ' + name if name else ''}"
        when = f" · updated {_ago(p.fetched_at)}" if p.fetched_at else ""
        self.who.setText(f"{name}{when}")

        prev, since = _day_before(p.history)
        known = bool(p.fetched_at) and p.has_stats
        self.c_followers.set(fmt_num(p.followers) if known else "—",
                             *self._delta(p.followers, prev[1] if prev else None, since) if known else ("", ""))
        self.c_likes.set(fmt_num(p.likes) if known else "—",
                         *self._delta(p.likes, prev[2] if prev else None, since) if known else ("", ""))
        self.c_videos.set(fmt_num(p.video_count) if known else "—")
        vids = sorted(st.videos(a.id).values(), key=lambda v: v.create_time, reverse=True)
        total_views = sum(v.views for v in vids)
        self.c_views.set(fmt_num(total_views) if vids else "—", f"across {len(vids)} videos" if vids else "")
        recent = [v.views for v in vids[:10]]
        self.c_avg.set(fmt_num(statistics.median(recent)) if recent else "—")

        # Videos table
        by_tid = {i.tiktok_id: i for i in st.items.values() if i.tiktok_id and i.account == a.id}
        self.videos.setSortingEnabled(False)
        self.videos.setRowCount(len(vids))
        for r, v in enumerate(vids):
            item = by_tid.get(v.id)
            change = v.views_change()
            cells = [
                SortItem(dt.datetime.fromtimestamp(v.create_time).strftime("%d %b %Y %H:%M") if v.create_time else "—", v.create_time),
                SortItem(v.description or "(no caption)", v.description.lower()),
                SortItem(fmt_num(v.views), v.views), SortItem(fmt_num(v.likes), v.likes),
                SortItem(fmt_num(v.comments), v.comments), SortItem(fmt_num(v.shares), v.shares),
                SortItem(f"{v.engagement:.1f}%", v.engagement),
                SortItem("—" if change is None else f"+{fmt_num(change)}" if change >= 0 else fmt_num(change),
                         change if change is not None else -1),
                SortItem(f"{item.total:.0f}" if item and item.total is not None else "", item.total if item and item.total else -1),
            ]
            if change:
                cells[7].setForeground(QColor(theme.GOOD if change > 0 else theme.BAD))
            cells[1].setToolTip(v.description)
            cells[0].setData(URL_ROLE, v.url)
            for c, cell in enumerate(cells):
                self.videos.setItem(r, c, cell)
        self.videos.setSortingEnabled(True)

        # App history
        posted = [i for i in st.items.values() if i.account == a.id and (i.status in ("posted", "failed") or i.attempts)]
        posted.sort(key=lambda i: i.posted_at or i.added_at, reverse=True)
        self.history.setSortingEnabled(False)
        self.history.setRowCount(len(posted))
        for r, it in enumerate(posted):
            v = st.video_for(it)
            status = {"posted": "Posted", "failed": "Failed", "queued": "Queued", "uploading": "Posting…"}.get(it.status, it.status)
            if it.cleaned_at:
                status = "Posted · cleaned up"
            detail = it.last_error if it.status == "failed" else (it.tiktok_url or ("Waiting for stats refresh to link"
                                                                                     if it.status == "posted" else ""))
            cells = [
                SortItem(dt.datetime.fromtimestamp(it.posted_at).strftime("%d %b %Y %H:%M") if it.posted_at else "—", it.posted_at or 0),
                SortItem(it.name, it.name.lower()),
                SortItem(f"{it.total:.0f}" if it.total is not None else "—", it.total or -1),
                SortItem(status, it.status),
                SortItem(fmt_num(v.views) if v else "—", v.views if v else -1),
                SortItem(fmt_num(v.likes) if v else "—", v.likes if v else -1),
                SortItem(detail, detail),
            ]
            cells[3].setForeground(QColor(theme.STATUS_COLOURS.get(it.status, theme.MUTED)))
            cells[6].setToolTip(detail)
            cells[0].setData(URL_ROLE, it.tiktok_url or (v.url if v else ""))
            cells[0].setData(ID_ROLE, it.id)
            for c, cell in enumerate(cells):
                self.history.setItem(r, c, cell)
        self.history.setSortingEnabled(True)
        self.insight.setText(self._insight())
        self._update_buttons()

    def _refresh_live(self) -> None:
        a, s = self.state.active, self.state.settings
        if not (a.logged_in or a.username):
            self.live_line.setText("")
            self.live_line.setVisible(False)
            return
        link = f"<a href='live' style='color:{theme.ACCENT_2}'>" + ("● Watching live" if a.live else "Live view off") + \
            "</a>"
        every = f" · its profile is read every {s.live_interval // 60} min, out of sight" if a.live else ""
        self.live_line.setText(link + every)
        self.live_line.setVisible(True)

    def _delta(self, now: int, before: float | None, since: str = "") -> tuple[str, str]:
        if before is None:
            return "", ""
        d = int(now - before)
        if d == 0:
            return f"no change {since}", ""
        return (f"{'+' if d > 0 else ''}{fmt_num(d)} {since}", theme.GOOD if d > 0 else theme.BAD)

    def _insight(self) -> str:
        """Does a higher score actually mean more views on this account? Compare halves once there's data."""
        st, aid = self.state.store, self.state.active.id
        pairs = [(i.total, st.video_for(i).views) for i in st.items.values()
                 if i.account == aid and i.total is not None and st.video_for(i) and time.time() - (i.posted_at or 0) > 86400]
        learned = "".join(f"<br>🎬 {html.escape(line)}" for line in
                          results.insights([o for o in results.outcomes(st) if o.account == aid]))
        if len(pairs) < 4:
            return ("<span style='color:%s'>Post a few videos from the queue and refresh stats: this page will show "
                    "whether high-scoring clips really get more views on your account.</span>" % theme.MUTED) + learned
        pairs.sort()
        half = len(pairs) // 2
        low = statistics.median(v for _, v in pairs[:half])
        high = statistics.median(v for _, v in pairs[half:])
        if high > low * 1.15:
            msg = f"Your higher-scoring posts get <b>{fmt_num(high)}</b> median views vs <b>{fmt_num(low)}</b> for lower ones: the score is a good guide for your order."
        elif low > high * 1.15:
            msg = (f"Lower-scoring posts are doing better ({fmt_num(low)} vs {fmt_num(high)} median views). Try adjusting "
                   f"the score weights in Settings, e.g. more weight on the AI rating or the clip finder.")
        else:
            msg = f"Score and views aren't strongly linked yet ({len(pairs)} posts). Keep posting to build more data."
        return f"📈 {msg}{learned}"

    def _stats_changed(self, account_id: str) -> None:
        """New numbers (the live view reads every minute): only redraw when they're on screen."""
        if account_id == self.state.active.id and self.isVisible():
            self.refresh()
        elif account_id == self.state.active.id:
            self._stale = True

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if getattr(self, "_stale", False):
            self._stale = False
            self.refresh()

    def _update_buttons(self) -> None:
        busy = self.state.account_busy(self.state.active.id, ("account", "login"))
        self.refresh_btn.setEnabled(not busy)
        self.login_btn.setEnabled(not busy)
        self.logout_btn.setEnabled(not busy)
        checking = self.state.account_busy(self.state.active.id, ("check",))
        self.check_btn.setEnabled(not busy and not checking and not self.state.busy("post"))
        self.check_btn.setText("Testing…" if checking else "Test posting")
        self.refresh_btn.setText("Refreshing…" if self.state.account_busy(self.state.active.id, ("account",))
                                 else "⟳ Refresh stats")

    # --- actions ------------------------------------------------------------------

    def _username_changed(self) -> None:
        a = self.state.active
        name = self.username.text().strip().lstrip("@")
        if name == a.username:
            return
        other = self.state.accounts.find_username(name, exclude=a.id)
        if other:
            QMessageBox.warning(self, "Account", f"@{name} is already added as “{other.label}”.")
            self.username.setText(a.username)
            return
        a.username = name
        self.state.save_accounts()
        self.state.accounts_edited()

    def _confirm_logout(self) -> None:
        a = self.state.active
        if QMessageBox.question(self, "Log out", f"Log {a.label} out of TikTok in TikTok Manager?\n"
                                "Autopilot can't post for it until you log in again. Your other accounts stay "
                                "logged in.") == QMessageBox.Yes:
            self.logout_requested.emit()

    def _open_url(self, table: QTableWidget, row: int) -> None:
        cell = table.item(row, 0)
        url = cell.data(URL_ROLE) if cell else ""
        if url:
            webbrowser.open(url)

    def _history_menu(self, pos) -> None:
        row = self.history.rowAt(pos.y())
        if row < 0:
            return
        item: VideoItem | None = self.state.store.get(self.history.item(row, 0).data(ID_ROLE))
        if not item:
            return
        m = QMenu(self)
        url = self.history.item(row, 0).data(URL_ROLE)
        if url:
            m.addAction("Open on TikTok", lambda: webbrowser.open(url))
        m.addAction("Open TikTok Studio", lambda: webbrowser.open("https://www.tiktok.com/tiktokstudio/content"))
        if item.status == "failed":
            m.addAction("Put back in the queue", lambda: self.requeue_requested.emit(item.id))
        if item.record and Path(item.record).is_file():
            m.addAction("Open its record", lambda: os.startfile(item.record))  # noqa: S606 - opens in Notepad
        if item.status == "posted" and item.exists():
            m.addAction("Post it again (re-queue)", lambda: self.requeue_requested.emit(item.id))
            others = [a for a in self.state.accounts if a.id != item.account]
            if others:
                also = m.addMenu("Also post to")
                for a in others:
                    also.addAction(QIcon(badge_for(a, 18)), a.label,
                                   lambda i=a.id: self._copy_to(item.id, i))
        m.exec(self.history.viewport().mapToGlobal(pos))


    def _copy_to(self, item_id: str, account_id: str) -> None:
        self.state.store.copy_for(item_id, account_id)
        self.state.save_soon()
        self.state.log(f"Queued it for {self.state.accounts.label_for(account_id)} too.")
        self.state.library_changed.emit()


def _day_before(history: list[list[float]]) -> tuple[list[float] | None, str]:
    """The point to compare the newest one with: the last from a day before it ("in 24 h"), else the
    oldest there is ("since 3 h ago"). The live view adds points every half hour, so the one just
    before would only be a few minutes of change."""
    if len(history) < 2:
        return None, ""
    newest = history[-1][0]
    day = [h for h in history[:-1] if newest - h[0] >= 86400]
    if day:
        return day[-1], "in 24 h" if newest - day[-1][0] < 1.5 * 86400 else f"since {_ago(day[-1][0])}"
    return history[0], f"since {_ago(history[0][0])}"


def _ago(ts: float) -> str:
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{d / 60:.0f} min ago"
    if d < 86400:
        return f"{d / 3600:.0f} h ago"
    return f"{d / 86400:.0f} days ago"
