"""Ready page: the shown account's videos waiting to be posted, each with its caption ready to copy.

For posting by hand: copy the caption, drag the file into TikTok's upload page, then click
"Mark as posted". Clips can arrive here by themselves from watched folders (Settings → Ready to upload).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import ready, schedule
from ..scoring import grade
from ..store import VideoItem
from . import theme
from .state import AppState
from .widgets import Thumb, badge_for, fmt_dur, label

MAX_CARDS = 60
UNDO_SECONDS = 20


def reveal(path: str) -> None:
    """Show the file selected in Explorer (or its folder elsewhere), ready to drag into TikTok."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


class ReadyCard(QFrame):
    posted = Signal(str)
    edit = Signal(str)
    unqueue = Signal(str)

    def __init__(self, page: "ReadyPage", item: VideoItem, position: int):
        super().__init__()
        self.setObjectName("panel")
        self.item = item
        st = page.state
        account = st.account_for(item)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(14)
        thumb = Thumb(81, 144)
        thumb.set_path(item.thumb)
        lay.addWidget(thumb, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(6)
        head = QHBoxLayout()
        title = label(f"{position}. {item.name}", "h2", wrap=True)
        head.addWidget(title, 1)
        total = item.total
        pill = QLabel("—" if total is None else f"{total:.0f}  {grade(total)}")
        colour = theme.score_colour(total)
        pill.setStyleSheet(f"color:{colour}; border:1px solid {colour}; border-radius:10px; padding:2px 10px;"
                           "font-weight:700;")
        head.addWidget(pill, 0, Qt.AlignTop)
        body.addLayout(head)

        now = time.time()
        facts = []
        if item.status == "failed":
            facts.append(f"<span style='color:{theme.BAD}'>✖ {_esc(item.last_error or 'Failed')}</span>")
        elif item.planned_at:
            if item.planned_at <= now + 60:
                facts.append(f"<span style='color:{theme.WARN}'>⏰ Time to post it</span>")
            else:
                facts.append(f"<span style='color:{theme.ACCENT_2}'>🕑 Post {schedule.fmt_when(item.planned_at, now)}"
                             "</span>")
        if item.duration:
            facts.append(fmt_dur(item.duration))
        if item.source_rating:
            facts.append(f"AI {item.source_rating}/10")
        if item.source_channel:
            facts.append(f"from {_esc(item.source_channel)}")
        if not item.exists():
            facts.append(f"<span style='color:{theme.BAD}'>file missing</span>")
        info = label(" · ".join(facts), "muted", wrap=True)
        info.setTextFormat(Qt.RichText)
        body.addWidget(info)
        notes = []
        if item.credit:
            notes.append(f"Creative Commons: the caption credits {_esc(item.source_channel)}. Keep that line.")
        notes += [_esc(w) for w in item.warnings]
        if notes:
            warn = label("⚠ " + " · ".join(notes), wrap=True)
            warn.setTextFormat(Qt.RichText)
            warn.setStyleSheet(f"color:{theme.WARN};")
            body.addWidget(warn)

        self.text = ready.post_text(item, account, st.settings)
        caption = label(_esc(self.text), wrap=True)
        caption.setTextFormat(Qt.RichText)
        caption.setTextInteractionFlags(Qt.TextSelectableByMouse)
        caption.setStyleSheet(f"background:{theme.BG}; border:1px dashed {theme.BORDER}; border-radius:7px; "
                              "padding:8px;")
        body.addWidget(caption)

        buttons = QHBoxLayout()
        self.copy_btn = QPushButton("Copy caption")
        self.copy_btn.setToolTip("Caption, credit and hashtags, exactly as they should go in TikTok")
        self.copy_btn.clicked.connect(lambda: self._copy(self.copy_btn, self.text))
        tags = ready.hashtags_text(item, account)
        self.tags_btn = QPushButton("Copy hashtags")
        self.tags_btn.setEnabled(bool(tags))
        self.tags_btn.clicked.connect(lambda: self._copy(self.tags_btn, tags))
        show = QPushButton("Show file")
        show.setToolTip("Opens the folder with the video selected, so you can drag it into TikTok's upload page")
        show.clicked.connect(lambda: reveal(item.path))
        play = QPushButton("▶ Play")
        play.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(item.path)))
        edit = QPushButton("Edit…")
        edit.setToolTip("Change the caption, hashtags or posting time on the Queue page")
        edit.clicked.connect(lambda: self.edit.emit(item.id))
        back = QPushButton("Not this one")
        back.setObjectName("danger")
        back.setToolTip("Take it out of the queue (it stays in the Library)")
        back.clicked.connect(lambda: self.unqueue.emit(item.id))
        done = QPushButton("✓ Mark as posted")
        done.setObjectName("primary")
        done.setToolTip("You've posted it on TikTok: it moves to the account's post history")
        done.clicked.connect(lambda: self.posted.emit(item.id))
        for b in (self.copy_btn, self.tags_btn, show, play, edit, back):
            buttons.addWidget(b)
            b.setEnabled(b.isEnabled() and (item.exists() or b in (edit, back, self.copy_btn, self.tags_btn)))
        buttons.addStretch(1)
        buttons.addWidget(done)
        body.addLayout(buttons)
        lay.addLayout(body, 1)

    def _copy(self, button: QPushButton, text: str) -> None:
        QApplication.clipboard().setText(text)
        old = button.text()
        button.setText("Copied ✓")
        QTimer.singleShot(1500, lambda: button.setText(old))


def _ago(t: float, now: float | None = None) -> str:
    mins = int(((now or time.time()) - t) // 60)
    return "just now" if mins < 1 else f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago"


def _folder_name(folder: str) -> str:
    return Path(folder).name or folder


def watch_summary(reports: list, checked_at: float, now: float | None = None) -> str:
    """'TTVIDEOS: 5 clips brought in, 13 set aside by ClipMonster's quality check · checked 2 min ago'."""
    parts = []
    for r in reports:
        if not r.exists:
            parts.append(f"<span style='color:{theme.BAD}'>{_esc(_folder_name(r.folder))} can't be found</span>")
            continue
        bits = [f"{r.in_app} clip{'s' * (r.in_app != 1)} brought in"]
        if r.waiting:
            bits.append(f"{r.waiting} still being made")
        if r.rejected:
            bits.append(f"{r.rejected} set aside by ClipMonster's quality check")
        if r.removed:
            bits.append(f"{r.removed} you removed")
        parts.append(f"{_esc(_folder_name(r.folder))}: " + ", ".join(bits))
    text = " · ".join(parts)
    return f"{text} · checked {_ago(checked_at, now)}" if text and checked_at else text


class ClipMonsterOffer(QDialog):
    """First start (or "Watch it" on the Ready page): bring ClipMonster's clips in by themselves."""

    def __init__(self, parent: QWidget, folder: Path, accounts: list[tuple[str, str]], active: str):
        super().__init__(parent)
        self.setWindowTitle("Bring in ClipMonster's clips")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(label(f"<b>ClipMonster saves its Automation clips to</b><br>{_esc(str(folder))}", wrap=True))
        lay.addWidget(label("TikTok Manager can watch that folder: every few minutes, new clips that pass "
                            "ClipMonster's quality check come in, get scored and wait on the Ready page with "
                            "their caption ready to copy.", "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(QLabel("New clips go to"))
        self.where = QComboBox()
        for aid, name in accounts:
            self.where.addItem(f"{name}'s Ready page", aid)
        if len(accounts) > 1:
            self.where.addItem("Shared out between my accounts", "spread")
        self.where.addItem("Only the Library (I queue them myself)", "library")
        self.where.setCurrentIndex(max(0, self.where.findData(active)))
        row.addWidget(self.where, 1)
        lay.addLayout(row)
        buttons = QDialogButtonBox()
        ok = buttons.addButton("Watch this folder", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        buttons.addButton("Not now", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def target(self) -> str:
        return self.where.currentData()


class ReadyPage(QWidget):
    edit_requested = Signal(str)  # item id: show it on the Queue page
    import_requested = Signal()
    settings_requested = Signal()
    watch_clipmonster_requested = Signal()

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self._undo: tuple[str, str] | None = None  # (item id, account id) of the last "Mark as posted"
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        bar = QHBoxLayout()
        self.badge = QLabel()
        bar.addWidget(self.badge)
        self.summary = label("", "h2")
        bar.addWidget(self.summary)
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.hide()
        self.undo_btn.clicked.connect(self.undo)
        self._undo_timer = QTimer(self, singleShot=True, interval=UNDO_SECONDS * 1000,
                                  timeout=lambda: self.undo_btn.hide())
        bar.addWidget(self.undo_btn)
        bar.addStretch(1)
        self.import_btn = QPushButton("Check for new clips")
        self.import_btn.setToolTip("Look in your watched folders now (they're checked every few minutes anyway)")
        self.import_btn.clicked.connect(self.import_requested.emit)
        self.take_btn = QPushButton()
        self.take_btn.setToolTip("Clips your watched folders brought in that are only in the Library: queue them "
                                 "for this account, best first")
        self.take_btn.clicked.connect(self.take_new_clips)
        bar.addWidget(self.take_btn)
        self.export_btn = QPushButton("Copy to a folder")
        self.export_btn.setToolTip("Copies these videos, in order, with a .txt caption beside each. Handy for "
                                   "posting from your phone through a synced folder")
        self.export_btn.clicked.connect(self.export)
        upload = QPushButton("Open TikTok upload")
        upload.setObjectName("primary")
        upload.setToolTip(ready.UPLOAD_URL)
        upload.clicked.connect(lambda: webbrowser.open(ready.UPLOAD_URL))
        for w in (self.import_btn, self.export_btn, upload):
            bar.addWidget(w)
        root.addLayout(bar)

        self.hint = label("", "muted", wrap=True)
        self.hint.setTextFormat(Qt.RichText)
        self.hint.linkActivated.connect(self._link)
        root.addWidget(self.hint)
        self.counts = label("", "muted", wrap=True)
        self.counts.setTextFormat(Qt.RichText)
        root.addWidget(self.counts)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        root.addWidget(self.scroll, 1)
        self.empty = label("", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setTextFormat(Qt.RichText)
        self.empty.linkActivated.connect(self._link)
        root.addWidget(self.empty, 1)

        self._sig = None
        state.library_changed.connect(self.refresh)
        state.account_switched.connect(self.refresh)
        state.account_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        st = self.state
        a = st.active
        st.replan()
        items = ready.ready(st.store, a.id)
        multi = len(st.accounts) > 1
        self.badge.setVisible(multi)
        self.badge.setPixmap(badge_for(a, 24))
        queued = [i for i in items if i.status == "queued"]
        self.summary.setText((f"{a.label}: " if multi else "") +
                             (f"{len(queued)} video{'s' * (len(queued) != 1)} ready to post" if queued
                              else "Nothing waiting"))
        folders = [f for f in st.settings.watch_folders if f]
        self._show_hint(folders)
        self.export_btn.setEnabled(bool(items))
        self.import_btn.setEnabled(bool(folders))
        if not folders:
            more = (f"Queue videos from the Library, or <a href='settings' style='color:{theme.ACCENT_2}'>watch a "
                    "folder</a> so new clips arrive by themselves.")
        elif self.take_btn.isVisibleTo(self):
            more = f"New clips are waiting in the Library: press “{_esc(self.take_btn.text())}” to bring them here."
        else:
            more = "New clips show up here by themselves once ClipMonster (or you) put them in the watched folder."
        self.empty.setText(f"Nothing is waiting for {_esc(a.label)}.<br>{more}")
        self.scroll.setVisible(bool(items))
        self.empty.setVisible(not items)
        sig = [(i.id, i.status, i.caption, i.hashtags, i.planned_at, i.total, i.thumb, i.last_error) for i in items]
        sig.append((a.id, a.default_hashtags, st.settings.caption_template))
        if sig == self._sig:
            return
        self._sig = sig
        pos = self.scroll.verticalScrollBar().value()
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)
        for n, it in enumerate(items[:MAX_CARDS], 1):
            card = ReadyCard(self, it, n)
            card.posted.connect(self.mark_posted)
            card.edit.connect(self.edit_requested.emit)
            card.unqueue.connect(self.unqueue)
            lay.addWidget(card)
        if len(items) > MAX_CARDS:
            lay.addWidget(label(f"…and {len(items) - MAX_CARDS} more on the Queue page.", "muted"))
        lay.addStretch(1)
        self.scroll.setWidget(box)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(pos))

    def _show_hint(self, folders: list[str]) -> None:
        """Where new clips come from and go to, and what's become of them, so a gap is never silent."""
        st = self.state
        s, a = st.settings, st.active
        link = f"style='color:{theme.ACCENT_2}'"
        where = ", ".join(_folder_name(f) for f in folders[:3]) + ("…" if len(folders) > 3 else "")
        target = s.watch_target
        warn = False
        cm = ready.clipmonster_unwatched(s)
        if cm is not None:
            warn = True
            text = (f"ClipMonster saves its clips to <b>{_esc(str(cm))}</b>, which TikTok Manager isn't watching, "
                    f"so they don't come in. <a href='watch-cm' {link}>Watch it</a>")
        elif not folders:
            text = ("Tip: watch ClipMonster's Automation folder and its clips arrive here by themselves. "
                    f"<a href='settings' {link}>Set it up</a>")
        elif target == "library":
            warn = True
            text = (f"New clips from {_esc(where)} go to the <b>Library</b>, not here (New clips is set to "
                    f"“Leave them in the Library”). <a href='target-here' {link}>Send them here from now on</a>")
        elif target not in ("spread", a.id) and st.accounts.get(target):
            text = (f"New clips from {_esc(where)} go to {_esc(st.accounts.label_for(target))}'s queue. "
                    f"<a href='settings' {link}>Change</a>")
        else:
            shared = " (shared out between your accounts)" if target == "spread" else ""
            text = f"New clips from {_esc(where)} come in here every few minutes{shared}. <a href='settings' {link}>Change</a>"
        self.hint.setText(text)
        self.hint.setStyleSheet(f"color:{theme.WARN};" if warn else "")
        self.counts.setText(watch_summary(st.watch_reports, st.watch_checked_at) if folders else "")
        self.counts.setVisible(bool(self.counts.text()))
        waiting = ready.unqueued_watched(st.store)
        self.take_btn.setText(f"Queue {len(waiting)} new clip{'s' * (len(waiting) != 1)} here")
        self.take_btn.setVisible(bool(waiting))

    def _link(self, href: str) -> None:
        st = self.state
        if href == "watch-cm":
            self.watch_clipmonster_requested.emit()
        elif href == "target-here":
            st.settings.watch_target = st.active.id
            st.save_settings()
            st.log(f"New clips from your watched folders now go to {st.active.label}'s Ready page.")
            self.refresh()
        else:
            self.settings_requested.emit()

    def take_new_clips(self) -> None:
        """Queue the watched folders' clips that are only in the Library for the shown account."""
        st = self.state
        a = st.active
        waiting = ready.unqueued_watched(st.store)
        if not waiting:
            return
        best = sorted(waiting, key=lambda i: -(i.total if i.total is not None else i.source_score or 0))
        st.store.enqueue([i.id for i in best], a.id)
        st.log(f"Queued {len(best)} new clip{'s' * (len(best) != 1)} for {a.label}.")
        st.save_soon()
        st.library_changed.emit()

    def cards(self) -> list[ReadyCard]:
        box = self.scroll.widget()
        return box.findChildren(ReadyCard) if box else []

    # --- actions ------------------------------------------------------------------

    def mark_posted(self, item_id: str) -> None:
        st = self.state
        item = st.store.get(item_id)
        account_id = item.account if item else ""
        if ready.mark_posted(st.store, item_id) is None:
            return
        self._undo = (item_id, account_id)
        self.undo_btn.setText(f"Undo “{item.name[:30]}”")
        self.undo_btn.show()
        self._undo_timer.start()
        st.log(f"✔ Marked “{item.name}” as posted on {st.accounts.label_for(account_id)}.")
        st.save_soon()
        st.library_changed.emit()

    def undo(self) -> None:
        if not self._undo:
            return
        item_id, account_id = self._undo
        self._undo = None
        self.undo_btn.hide()
        if ready.unmark_posted(self.state.store, item_id, account_id):
            self.state.log("Put it back at the top of the queue.")
            self.state.save_soon()
            self.state.library_changed.emit()

    def unqueue(self, item_id: str) -> None:
        st = self.state
        st.store.dequeue([item_id])
        st.save_soon()
        st.library_changed.emit()

    def export(self) -> None:
        st = self.state
        a = st.active
        try:
            folder, n = ready.export_ready(st.store, a, st.settings)
        except OSError as exc:
            QMessageBox.warning(self, "Copy to a folder", f"Couldn't copy the videos.\n\n{exc}")
            return
        st.log(f"Copied {n} video{'s' * (n != 1)} with captions to {folder}.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
