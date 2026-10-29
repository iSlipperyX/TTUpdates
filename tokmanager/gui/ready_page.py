"""Ready page: the shown account's videos waiting to be posted, each with its caption ready to copy.

For posting by hand: copy the caption, drag the file into TikTok's upload page, then click
"Mark as posted". Clips can arrive here by themselves from watched folders (Settings → Ready to upload),
and the next day's can wait in a phone folder too (phone.py). Everything works from the keyboard (KEYS).
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import ai, drive_sync, phone, ready, schedule
from ..scoring import grade
from ..store import VideoItem
from . import theme
from .state import AppState, Job
from .widgets import Thumb, badge_for, fmt_dur, label, short

MAX_CARDS = 60
UNDO_SECONDS = 20
# Keyboard shortcuts on the Ready page: keys, what they do (ReadyPage._key), how the hint line says it
KEYS = (
    (("Down", "J"), "next", "↓ J next"),
    (("Up", "K"), "previous", "↑ K previous"),
    (("C",), "copy", "C copy caption"),
    (("H",), "tags", "H hashtags"),
    (("F",), "show", "F show file"),
    (("P",), "play", "P play"),
    (("T",), "ai", "T ✨ hashtags"),
    (("E",), "edit", "E edit"),
    (("Return", "Enter"), "posted", "Enter mark as posted"),
    (("Ctrl+Z",), "undo", "Ctrl+Z undo"),
)


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
        self.page = page
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
        part = f" <span style='color:{theme.ACCENT_2}'>· {item.part_label}</span>" if item.part_label else ""
        self.title = label(f"{position}. {_esc(item.headline)}{part}", "h2", wrap=True)
        self.title.setTextFormat(Qt.RichText)
        self.title.setToolTip(Path(item.path).name)
        head.addWidget(self.title, 1)
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
        niche = st.niches().name_of(item)
        if niche and not st.niche_label():  # when every niche shows, say which this is
            facts.append(f"🏷 {_esc(niche)}")
        if not item.exists():
            facts.append(f"<span style='color:{theme.BAD}'>file missing</span>")
        elif item.id in page.on_phone:
            facts.append("📱 in your phone folder")
        self.info = label(" · ".join(facts), "muted", wrap=True)
        self.info.setTextFormat(Qt.RichText)
        body.addWidget(self.info)
        notes = []
        if item.credit:
            notes.append(f"Creative Commons: the caption credits {_esc(item.source_channel)}. Keep that line.")
        notes += [_esc(w) for w in item.warnings]
        if notes:
            warn = label("⚠ " + " · ".join(notes), wrap=True)
            warn.setTextFormat(Qt.RichText)
            warn.setStyleSheet(f"color:{theme.WARN};")
            body.addWidget(warn)
        self.gone: QLabel | None = None
        if item.id in page.gone:
            link = f"style='color:{theme.ACCENT_2}'"
            self.gone = label(f"📱 You deleted this from your phone folder. Posted it? <a href='posted' {link}>"
                              f"Mark as posted</a> · <a href='copy-back' {link}>Put it back in the folder</a>", wrap=True)
            self.gone.setTextFormat(Qt.RichText)
            self.gone.linkActivated.connect(lambda href: self.posted.emit(item.id) if href == "posted"
                                            else page.copy_back(item.id))
            body.addWidget(self.gone)

        self.text = ready.post_text(item, account, st.settings)
        self.caption = label(_esc(self.text), wrap=True)
        self.caption.setTextFormat(Qt.RichText)
        self.caption.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.caption.setStyleSheet(f"background:{theme.BG}; border:1px dashed {theme.BORDER}; border-radius:7px; "
                                   "padding:8px;")
        body.addWidget(self.caption)

        buttons = QHBoxLayout()
        self.copy_btn = QPushButton("Copy caption")
        self.copy_btn.setToolTip("Caption, credit and hashtags, exactly as they should go in TikTok")
        self.copy_btn.clicked.connect(lambda: self._copy(self.copy_btn, self.text))
        tags = ready.hashtags_text(item, account)
        self.tags_btn = QPushButton("Copy hashtags")
        self.tags_btn.setEnabled(bool(tags))
        self.tags_btn.clicked.connect(lambda: self._copy(self.tags_btn, tags))
        busy = item.id in page.tagging
        self.ai_tags_btn = QPushButton("Writing…" if busy else "✨ Hashtags")
        self.ai_tags_btn.setEnabled(not busy)
        self.ai_tags_btn.setToolTip("The AI writes hashtags that fit this clip and the account's niche, learning "
                                    "from your best posts (replaces the clip's own hashtags)")
        self.ai_tags_btn.clicked.connect(lambda: page.ai_hashtags([item.id]))
        self.show_btn = QPushButton("Show file")
        self.show_btn.setToolTip("Opens the folder with the video selected, so you can drag it into TikTok's "
                                 "upload page")
        self.show_btn.clicked.connect(lambda: reveal(item.path))
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(item.path)))
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.setToolTip("Change the caption, hashtags or posting time on the Queue page")
        self.edit_btn.clicked.connect(lambda: self.edit.emit(item.id))
        back = QPushButton("Not this one")
        back.setObjectName("danger")
        back.setToolTip("Take it out of the queue (it stays in the Library)")
        back.clicked.connect(lambda: self.unqueue.emit(item.id))
        self.done_btn = QPushButton("✓ Mark as posted")
        self.done_btn.setObjectName("primary")
        self.done_btn.setToolTip("You've posted it on TikTok: it moves to the account's post history")
        self.done_btn.clicked.connect(lambda: self.posted.emit(item.id))
        for b in (self.show_btn, self.play_btn):
            b.setEnabled(item.exists())
        for b in (self.copy_btn, self.tags_btn, self.ai_tags_btn, self.show_btn, self.play_btn, self.edit_btn, back):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.done_btn)
        body.addLayout(buttons)
        lay.addLayout(body, 1)
        for w in self.findChildren(QWidget):  # clicking or tabbing anywhere on the card makes it the current one
            w.installEventFilter(self)

    def set_current(self, on: bool) -> None:
        """The card the keyboard shortcuts act on is outlined."""
        name = "panelCurrent" if on else "panel"
        if self.objectName() != name:
            self.setObjectName(name)
            self.style().unpolish(self)
            self.style().polish(self)

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.page.set_current(self.item.id)
        super().mousePressEvent(e)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 - Qt API
        """The keys act on the card you're on: one you click anywhere in (the caption too) or tab into."""
        if e.type() == QEvent.MouseButtonPress or (
                e.type() == QEvent.FocusIn and e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)):
            self.page.set_current(self.item.id)
        return False

    def _copy(self, button: QPushButton, text: str) -> None:
        QApplication.clipboard().setText(text)
        old = button.text()
        button.setText("Copied ✓")
        QTimer.singleShot(1500, button, lambda: button.setText(old))  # dropped if the cards are rebuilt meanwhile


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
    phone_sync_requested = Signal()  # update the phone folder now
    drive_settings_requested = Signal()  # Settings → Google Drive
    drive_check_requested = Signal()  # look in Google Drive now

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self._undo: tuple[str, str] | None = None  # (item id, account id) of the last "Mark as posted"
        self.tagging: set[str] = set()  # items the AI is writing hashtags for
        self.current = ""  # id of the card the keys act on
        self._current_pos = 0  # its place, so the next card takes over when it leaves
        self._current_account = ""  # the account it belongs to: another account starts again at its first card
        self.on_phone: set[str] = set()  # items in the phone folder (phone.sync's last result)
        self.gone: set[str] = set()  # items deleted from the phone folder, waiting to be marked posted
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
        self._undo_timer = QTimer(self, singleShot=True, interval=UNDO_SECONDS * 1000, timeout=self._undo_expired)
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
        self.all_tags_btn = QPushButton()
        self.all_tags_btn.setToolTip("The AI writes hashtags for each waiting video that has none of its own "
                                     "(one quick call per video)")
        self.all_tags_btn.clicked.connect(lambda: self.ai_hashtags([i.id for i in self._untagged()]))
        bar.addWidget(self.all_tags_btn)
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
        self.phone_line = label("", "muted", wrap=True)
        self.phone_line.setTextFormat(Qt.RichText)
        self.phone_line.linkActivated.connect(self._link)
        root.addWidget(self.phone_line)
        self.drive_line = label("", "muted", wrap=True)
        self.drive_line.setTextFormat(Qt.RichText)
        self.drive_line.linkActivated.connect(self._link)
        root.addWidget(self.drive_line)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        root.addWidget(self.scroll, 1)
        self.empty = label("", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setTextFormat(Qt.RichText)
        self.empty.linkActivated.connect(self._link)
        root.addWidget(self.empty, 1)
        self.keys_hint = label("Keys: " + "  ·  ".join(hint for _, _, hint in KEYS), "muted", wrap=True)
        self.keys_hint.setStyleSheet("font-size: 8.5pt;")
        root.addWidget(self.keys_hint)
        # Only while this page is showing (Qt skips shortcuts of hidden widgets), so typing elsewhere is safe.
        for keys, action, _ in KEYS:
            for key in keys:
                sc = QShortcut(QKeySequence(key), self, activated=lambda a=action: self._key(a))
                sc.setAutoRepeat(action in ("next", "previous"))  # holding Enter marks one video, not several

        self._sig = None
        state.library_changed.connect(self.refresh)
        state.account_switched.connect(self.refresh)
        state.account_changed.connect(self.refresh)
        state.phone_synced.connect(self.refresh)
        state.niche_switched.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        st = self.state
        a = st.active
        st.replan()
        every = ready.ready(st.store, a.id)
        items = st.in_niche(every)
        niche = st.niche_label()
        hidden = len(every) - len(items)  # waiting, but in another niche
        multi = len(st.accounts) > 1
        self.badge.setVisible(multi)
        self.badge.setPixmap(badge_for(a, 24))
        queued = [i for i in items if i.status == "queued"]
        today = dt.datetime.combine(dt.date.today(), dt.time.min).timestamp()
        done = len(st.in_niche([i for i in st.store.posted(a.id) if (i.posted_at or 0) >= today]))
        text = f"{len(queued)} video{'s' * (len(queued) != 1)} ready to post" if queued else \
            f"{len(items)} failed video{'s' * (len(items) != 1)} to look at" if items else \
            "All done for now" if done else "Nothing waiting"
        self.summary.setText((f"{a.label}: " if multi else "") + (f"{niche}: " if niche else "") + text +
                             (f" · {done} posted today ✓" if done else "") +
                             (f" · {hidden} more in other niches" if hidden else ""))
        folders = ready.watch_roots(st.settings)
        self._show_hint(folders)
        self._show_phone()
        self._show_drive()
        self.export_btn.setEnabled(bool(items))
        self.export_btn.setVisible(not st.settings.phone_folder)  # the phone folder does this by itself
        self.import_btn.setEnabled(bool(folders))
        if not folders:
            more = (f"Queue videos from the Library, or <a href='settings' style='color:{theme.ACCENT_2}'>watch a "
                    "folder</a> so new clips arrive by themselves.")
        elif self.take_btn.isVisibleTo(self):
            more = f"New clips are waiting in the Library: press “{_esc(self.take_btn.text())}” to bring them here."
        else:
            more = "New clips show up here by themselves once ClipMonster (or you) put them in the watched folder."
        if niche and hidden:
            more = (f"{hidden} video{'s are' if hidden != 1 else ' is'} waiting in other niches: pick “All niches” "
                    "at the top to see them.")
        self.empty.setText(f"Nothing{f' from {_esc(niche)}' if niche else ''} is waiting for {_esc(a.label)}."
                           f"<br>{more}")
        self.scroll.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.keys_hint.setVisible(bool(items))
        untagged = self._untagged(items)
        index = st.niches()
        self.all_tags_btn.setText(f"✨ Hashtags for {len(untagged)}")
        self.all_tags_btn.setVisible(len(untagged) > 1 and ai.has_key(st.settings))
        sig = [(i.id, i.status, i.caption, i.hashtags, i.planned_at, i.total, i.thumb, i.last_error,
                i.id in self.tagging, i.id in self.on_phone, i.id in self.gone, index.name_of(i)) for i in items]
        sig.append((a.id, a.default_hashtags, st.settings.caption_template, st.niche_filter))
        ids = [i.id for i in items[:MAX_CARDS]]
        if a.id != self._current_account:
            self._current_account, self.current, self._current_pos = a.id, "", 0
        if self.current not in ids:  # it was posted or taken out: the one after it (now in its place) takes over
            self.current = ids[min(self._current_pos, len(ids) - 1)] if ids else ""
        if sig == self._sig:
            self._mark_current()
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
        self._mark_current()
        QTimer.singleShot(0, self.scroll, lambda: self.scroll.verticalScrollBar().setValue(pos))

    def _show_hint(self, folders: list[str]) -> None:
        """Where new clips come from and go to, and what's become of them, so a gap is never silent."""
        st = self.state
        s, a = st.settings, st.active
        link = f"style='color:{theme.ACCENT_2}'"
        box = str(drive_sync.inbox(s)) if s.drive_on else ""
        where = ", ".join("Google Drive" if box and ready.same_folder(f, box) else _folder_name(f)
                          for f in folders[:3]) + ("…" if len(folders) > 3 else "")
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
        waiting = st.in_niche(ready.unqueued_watched(st.store))
        self.take_btn.setText(f"Queue {len(waiting)} new clip{'s' * (len(waiting) != 1)} here")
        self.take_btn.setVisible(bool(waiting))

    def _show_phone(self) -> None:
        """The phone folder line: what's in it, or a way to start using one."""
        st = self.state
        s = st.settings
        link = f"style='color:{theme.ACCENT_2}'"
        result = st.phone_result
        folder = s.phone_folder
        if result is not None and not ready.same_folder(result.folder, folder or ""):
            result = None  # from before the folder was changed
        self.on_phone = set(result.on_phone) if result else set()
        self.gone = set(result.gone) if result else set()
        if not folder:
            onedrive = phone.suggested_folder()
            text = (f"📱 Post from your phone: <a href='phone-on' {link}>keep the next day's clips and captions in "
                    f"OneDrive › {phone.FOLDER_NAME}</a>" if onedrive else "")
        elif st.phone_error:
            text = (f"<span style='color:{theme.WARN}'>📱 The phone folder couldn't be updated: "
                    f"{_esc(st.phone_error)}</span> <a href='settings' {link}>Change it</a>")
        elif result is None:
            text = f"📱 Getting the next clips ready in {_esc(_folder_name(folder))}…"
        else:
            n = len(result.on_phone)
            what = f"{n} clip{'s' * (n != 1)} due in the next 24 hours, with captions" if n else \
                "nothing due in the next 24 hours"
            text = (f"📱 Phone folder {_esc(_folder_name(folder))}: {what} · updated "
                    f"{_ago(result.at)} · <a href='open-phone' {link}>Open</a>")
        self.phone_line.setText(text)
        self.phone_line.setVisible(bool(text))

    def _show_drive(self) -> None:
        """The Google Drive line, while clips are brought in from Drive: the last look, or what stops it."""
        st = self.state
        res = st.drive_result
        link = f"style='color:{theme.ACCENT_2}'"
        if not st.settings.drive_on:
            text = ""
        elif res is None:
            text = "☁ Looking in Google Drive for ClipMonster's clips…"
        elif res.problem:
            text = (f"<span style='color:{theme.WARN}'>☁ Google Drive: {_esc(res.problem)}</span> "
                    f"<a href='drive-settings' {link}>Settings</a> · <a href='drive-check' {link}>Try again</a>")
        else:
            text = (f"☁ Google Drive: {_esc(res.text())} Checked {_ago(res.at)} · "
                    f"<a href='drive-check' {link}>Check now</a>")
        self.drive_line.setText(text)
        self.drive_line.setVisible(bool(text))

    def copy_back(self, item_id: str) -> None:
        """A clip deleted from the phone folder that wasn't posted after all: copy it there again."""
        folder = self.state.settings.phone_folder
        if folder:
            try:
                phone.forget_gone(Path(folder), [item_id])
            except OSError as exc:
                QMessageBox.warning(self, "Phone folder", f"Couldn't change the phone folder.\n\n{exc}")
                return
            self.gone.discard(item_id)
            self.phone_sync_requested.emit()

    def _link(self, href: str) -> None:
        st = self.state
        if href == "phone-on":
            folder = phone.suggested_folder()
            if folder is None:
                return
            st.settings.phone_folder = str(folder)
            st.save_settings()
            st.log(f"📱 The next day's clips will wait in {folder}, each with its caption. Open that folder in the "
                   "OneDrive app on your phone.")
            self.refresh()
            self.phone_sync_requested.emit()
        elif href == "open-phone":
            QDesktopServices.openUrl(QUrl.fromLocalFile(st.settings.phone_folder))
        elif href == "drive-settings":
            self.drive_settings_requested.emit()
        elif href == "drive-check":
            self.drive_check_requested.emit()
        elif href == "watch-cm":
            self.watch_clipmonster_requested.emit()
        elif href == "target-here":
            st.settings.watch_target = st.active.id
            st.save_settings()
            st.log(f"New clips from your watched folders now go to {st.active.label}'s Ready page.")
            self.refresh()
        else:
            self.settings_requested.emit()

    def _untagged(self, items: list[VideoItem] | None = None) -> list[VideoItem]:
        """Waiting videos without hashtags of their own (only the account's always-added ones)."""
        items = self.state.in_niche(ready.ready(self.state.store, self.state.active.id)) if items is None else items
        return [i for i in items if not i.hashtags.strip() and i.id not in self.tagging]

    def ai_hashtags(self, ids: list[str]) -> None:
        """Have the AI write hashtags for these videos, one quick call each."""
        st = self.state
        if not ai.has_key(st.settings):
            QMessageBox.information(self, "AI hashtags", "Add a Claude or Gemini API key in Settings → AI (or in "
                                    "ClipMonster) to have the AI write hashtags.")
            return
        from .. import jobs

        for item_id in ids:
            item = st.store.get(item_id)
            if item is None or item_id in self.tagging:
                continue
            self.tagging.add(item_id)
            account = st.account_for(item)

            def finished(_=None, i=item_id) -> None:
                self.tagging.discard(i)
                st.save_soon()
                st.library_changed.emit()

            st.run(Job(f"AI hashtags for {short(item.headline)}",
                       lambda j, i=item_id, a=account: jobs.ai_hashtags(st.store, i, st.settings, a, j.log), kind="ai"),
                   on_success=finished, on_failure=finished)
        self.refresh()

    def take_new_clips(self) -> None:
        """Queue the watched folders' clips that are only in the Library for the shown account."""
        st = self.state
        a = st.active
        waiting = st.in_niche(ready.unqueued_watched(st.store))  # only the shown niche's, when one is picked
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

    # --- keyboard -----------------------------------------------------------------

    def current_card(self) -> ReadyCard | None:
        return next((c for c in self.cards() if c.item.id == self.current), None)

    def set_current(self, item_id: str) -> None:
        self.current = item_id
        self._mark_current()

    def _mark_current(self) -> None:
        cards = self.cards()
        for n, c in enumerate(cards):
            c.set_current(c.item.id == self.current)
            if c.item.id == self.current:
                self._current_pos = n

    def _key(self, action: str) -> None:
        cards = self.cards()
        if action == "undo":
            self.undo()
            return
        card = self.current_card()
        if not cards:
            return
        if action in ("next", "previous"):
            n = cards.index(card) if card else -1
            n = max(0, min(len(cards) - 1, n + (1 if action == "next" else -1)))
            self.set_current(cards[n].item.id)
            self.scroll.ensureWidgetVisible(cards[n], 0, 40)
            return
        if card is None:
            return
        button = {"copy": card.copy_btn, "tags": card.tags_btn, "show": card.show_btn, "play": card.play_btn,
                  "ai": card.ai_tags_btn, "edit": card.edit_btn, "posted": card.done_btn}[action]
        if button.isEnabled():
            button.click()

    # --- actions ------------------------------------------------------------------

    def mark_posted(self, item_id: str) -> None:
        st = self.state
        item = st.store.get(item_id)
        account_id = item.account if item else ""
        if ready.mark_posted(st.store, item_id) is None:
            return
        self._undo = (item_id, account_id)
        self.undo_btn.setText(f"Undo “{short(item.headline, 30)}”".replace("&", "&&"))  # a single & would underline
        self.undo_btn.show()
        self._undo_timer.start()
        st.log(f"✔ Marked “{item.headline}” as posted on {st.accounts.label_for(account_id)}.")
        st.save_soon()
        st.library_changed.emit()

    def _undo_expired(self) -> None:
        self._undo = None  # Ctrl+Z only undoes while the button shows
        self.undo_btn.hide()

    def undo(self) -> None:
        if not self._undo:
            return
        item_id, account_id = self._undo
        self._undo = None
        self.undo_btn.hide()
        if ready.unmark_posted(self.state.store, item_id, account_id):
            self.current = item_id
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
            folder, n = ready.export_ready(st.store, a, st.settings,
                                           items=st.in_niche(ready.ready(st.store, a.id)))
        except OSError as exc:
            QMessageBox.warning(self, "Copy to a folder", f"Couldn't copy the videos.\n\n{exc}")
            return
        st.log(f"Copied {n} video{'s' * (n != 1)} with captions to {folder}.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
