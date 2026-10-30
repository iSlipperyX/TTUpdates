"""Library page: add videos, score them, inspect the breakdown, edit captions, queue them."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QTableWidgetSelectionRange, QVBoxLayout, QWidget)

from ..jobs import analyze_item, ai_item
from ..media import VIDEO_EXTS
from ..store import VideoItem
from . import niche_menu, theme
from .editor import PostEditor
from .state import AppState, Job
from .widgets import (ScoreBreakdown, ScorePillDelegate, ScoreRing, Thumb, badge_for, fmt_dur, label, panel,
                      thumb_pixmap)

ID_ROLE = Qt.UserRole + 1
COLS = ["", "Video", "Score", "Length", "Format", "Status", "Niche", "Added"]
NICHE_COL = 6
STATUS_TEXT = {"new": "Not queued", "queued": "Queued", "uploading": "Posting…", "posted": "Posted", "failed": "Failed"}


class SortItem(QTableWidgetItem):
    """Sorts by a hidden key instead of the display text."""

    def __init__(self, text: str = "", key=None):
        super().__init__(text)
        self.key = key if key is not None else text

    def __lt__(self, other):  # noqa: D105
        try:
            return self.key < other.key
        except TypeError:
            return str(self.key) < str(other.key)


def format_label(info: dict) -> str:
    w, h = info.get("width") or 0, info.get("height") or 0
    if not w or not h:
        return "—"
    r = w / h
    shape = "9:16" if 0.54 <= r <= 0.59 else "Tall" if r < 0.54 else "4:5" if r <= 0.85 else "1:1" if r <= 1.1 else "16:9" if 1.7 <= r <= 1.8 else "Wide"
    return f"{shape} · {min(w, h)}p"


class LibraryPage(QWidget):
    cleanup_requested = Signal()  # open Clean up for posted videos

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Toolbar
        bar = QHBoxLayout()
        add_files = QPushButton("＋ Add videos")
        add_files.setObjectName("primary")
        add_files.clicked.connect(self.add_files_dialog)
        add_folder = QPushButton("Add folder")
        add_folder.setToolTip("Adds every video in a folder (and its subfolders). ClipMonster output folders "
                              "bring their clip titles and scores along.")
        add_folder.clicked.connect(self.add_folder_dialog)
        self.score_btn = QPushButton("Score")
        self.score_btn.setToolTip("Analyse the selected videos (or every unscored one if none is selected)")
        self.score_btn.clicked.connect(self.score_selected)
        self.ai_btn = QPushButton("✨ AI rate")
        self.ai_btn.setToolTip("The AI rates the selected videos and writes captions (needs an API key)")
        self.ai_btn.clicked.connect(self.ai_selected)
        self.queue_btn = QPushButton("Add to queue →")
        self.queue_btn.clicked.connect(lambda: self.queue_selected())
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.setToolTip("Remove from the library (the files on disk are not touched)")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.cleanup_btn = QPushButton("🧹 Clean up…")
        self.cleanup_btn.setToolTip("Free the space posted videos take: a .txt record of each is kept, then its "
                                    "files are deleted. You see the list and confirm first.")
        self.cleanup_btn.clicked.connect(self.cleanup_requested.emit)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self.refresh)
        self.filter = QComboBox()
        self._fill_filter()
        self.filter.currentIndexChanged.connect(self.refresh)
        for w in (add_files, add_folder, self.score_btn, self.ai_btn, self.queue_btn, self.remove_btn,
                  self.cleanup_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.filter)
        bar.addWidget(self.search)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        # Table
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(34, 56))
        self.table.verticalHeader().setDefaultSectionSize(64)
        self.table.setSortingEnabled(True)
        self.table.setItemDelegateForColumn(2, ScorePillDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(lambda _: self.open_file())
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 48), (2, 86), (3, 64), (4, 96), (5, 96), (NICHE_COL, 120), (7, 110)):
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, w)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.empty = label("Drop videos here, or click “Add videos”.\nClipMonster output folders work too.", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.table)
        ll.addWidget(self.empty)
        split.addWidget(left)

        # Detail panel
        self.detail = self._build_detail()
        split.addWidget(self.detail)
        split.setSizes([760, 380])
        split.setStretchFactor(0, 1)

        self.state.library_changed.connect(self.refresh)
        self.state.jobs_changed.connect(self._update_buttons)
        self.state.account_changed.connect(self._accounts_changed)
        self.state.account_switched.connect(self._accounts_changed)
        self.state.niche_switched.connect(self.refresh)
        self.refresh()

    def _fill_filter(self) -> None:
        """Status filters, plus one per account (its queue and posts) when there's more than one."""
        current = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        for key, text in (("all", "All videos"), ("new", "Not queued"), ("queued", "Queued"), ("posted", "Posted"),
                          ("failed", "Failed"), ("unscored", "Not scored"), ("cleaned", "Cleaned up")):
            self.filter.addItem(text, key)
        if len(self.state.accounts) > 1:
            self.filter.insertSeparator(self.filter.count())
            for a in self.state.accounts:
                self.filter.addItem(QIcon(badge_for(a, 18)), a.label, f"account:{a.id}")
        # findData(None) would match the separator, so a first fill (nothing chosen yet) starts at "All videos".
        self.filter.setCurrentIndex(max(0, self.filter.findData(current)) if current else 0)
        self.filter.blockSignals(False)

    def _accounts_changed(self) -> None:
        self._fill_filter()
        self.refresh()
        self.editor.set_item(self.editor.item)  # "Will post as…" uses the account's default hashtags

    # --- detail panel -------------------------------------------------------------

    def _build_detail(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        box = panel()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.d_thumb = Thumb(108, 192)
        top.addWidget(self.d_thumb)
        info = QVBoxLayout()
        self.d_title = label("", "h2", wrap=True)
        self.d_facts = label("", "muted", wrap=True)
        self.d_ring = ScoreRing(92)
        info.addWidget(self.d_title)
        info.addWidget(self.d_facts)
        info.addWidget(self.d_ring, 0, Qt.AlignLeft)
        info.addStretch(1)
        btns = QHBoxLayout()
        play = QPushButton("▶ Play")
        play.clicked.connect(self.open_file)
        folder = QPushButton("Show file")
        folder.clicked.connect(self.show_in_folder)
        btns.addWidget(play)
        btns.addWidget(folder)
        btns.addStretch(1)
        info.addLayout(btns)
        top.addLayout(info, 1)
        lay.addLayout(top)

        self.d_error = label("", wrap=True)
        self.d_error.setStyleSheet(f"color:{theme.BAD};")
        lay.addWidget(self.d_error)
        lay.addWidget(label("Score breakdown", "h2"))
        self.d_breakdown = ScoreBreakdown()
        lay.addWidget(self.d_breakdown)
        self.d_tips_title = label("How to improve it", "h2")
        lay.addWidget(self.d_tips_title)
        self.d_tips = label("", wrap=True)
        self.d_tips.setTextFormat(Qt.RichText)
        lay.addWidget(self.d_tips)
        self.editor = PostEditor(self.state)
        lay.addWidget(self.editor)
        self.d_queue = QPushButton("Add to queue →")
        self.d_queue.setObjectName("primary")
        self.d_queue.clicked.connect(lambda: self.queue_selected())
        lay.addWidget(self.d_queue)
        lay.addStretch(1)
        scroll.setWidget(box)
        return scroll

    def _show_detail(self, item: VideoItem | None) -> None:
        self.detail.widget().setEnabled(item is not None)
        if not item:
            self.d_thumb.set_path("")
            self.d_title.setText("No video selected")
            self.d_facts.setText("")
            self.d_ring.set_value(None)
            self.d_breakdown.set_score(None)
            self.d_tips.setText("")
            self.d_error.hide()
            self.d_tips_title.hide()
            self.editor.set_item(None)
            self.d_queue.hide()
            return
        self.d_thumb.set_path(item.thumb)
        self.d_title.setText(item.name)
        facts = []
        if item.info:
            facts.append(f"{fmt_dur(item.duration)} · {item.info.get('width')}x{item.info.get('height')}")
            size = item.info.get("size_bytes") or 0
            if size:
                facts.append(f"{size / 1e6:.1f} MB")
        facts.append(self._status_text(item))
        if item.source_reason:
            facts.append(f"Clip finder: “{item.source_reason}”")
        index = self.state.niches()
        if index.name_of(item):
            facts.append(f"Niche: {index.name_of(item)} ({index.why(item)})")
        if item.cleaned_at:
            facts.append(f"cleaned up {dt.datetime.fromtimestamp(item.cleaned_at):%d %b %Y}: its files were deleted "
                         "and a record of it kept (right-click → Open its record)")
        elif not item.exists():
            # ClipMonster's Clean up deletes posted clips' files; the post and its stats stay here
            facts.append("file deleted after posting (its stats are kept)" if item.status == "posted"
                         else "⚠ file missing")
        self.d_facts.setText(" · ".join(facts[:3]) + ("<br>" + "<br>".join(facts[3:]) if facts[3:] else ""))
        self.d_ring.set_value(item.total)
        self.d_breakdown.set_score(item.score)
        tips = (item.score or {}).get("tips", [])
        self.d_tips_title.setVisible(bool(tips))
        self.d_tips.setText("".join(f"• {t}<br>" for t in tips))
        self.d_error.setVisible(bool(item.analyze_error))
        self.d_error.setText(f"Couldn't analyse: {item.analyze_error}")
        self.editor.set_item(item)
        self.d_queue.setVisible(item.status == "new")

    # --- table --------------------------------------------------------------------

    def _visible_items(self) -> list[VideoItem]:
        f = self.filter.currentData()
        q = self.search.text().strip().lower()
        index = self.state.niches()
        out = []
        for it in self.state.in_niche(self.state.store.all()):
            if bool(it.cleaned_at) != (f == "cleaned"):  # cleaned-up posts only show under their own filter
                continue
            if f == "unscored" and it.score:
                continue
            if f.startswith("account:"):
                if it.account != f[8:]:
                    continue
            elif f not in ("all", "unscored", "cleaned") and it.status != f:
                continue
            if q and not any(q in t.lower() for t in (it.name, it.caption, it.path, index.name_of(it))):
                continue
            out.append(it)
        return out

    def refresh(self) -> None:
        selected = set(self.selected_ids())
        items = self._visible_items()
        index = self.state.niches()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(items))
        for r, it in enumerate(items):
            thumb = SortItem("", it.added_at)
            if it.thumb:
                thumb.setIcon(thumb_pixmap(it.thumb, 34, 56))
            thumb.setData(ID_ROLE, it.id)
            name = SortItem(it.name, it.name.lower())
            name.setToolTip(it.path)
            if it.caption:
                name.setText(f"{it.name}\n{it.caption[:90]}")
            score = SortItem("", it.total if it.total is not None else -1)
            score.setData(Qt.UserRole, it.total)
            if it.analyze_error:
                score.setText("error")
                score.setForeground(Qt.red)
            elif it.total is None:
                score.setText("scoring…" if self._is_scoring(it.id) else "—")
            length = SortItem(fmt_dur(it.duration) if it.duration else "—", it.duration)
            fmt = SortItem(format_label(it.info), it.info.get("width", 0) / max(1, it.info.get("height", 1)) if it.info else 9)
            status = SortItem(self._status_text(it), it.status)
            status.setForeground(QColor(theme.STATUS_COLOURS.get(it.status, theme.MUTED)))
            if it.status == "failed" and it.last_error:
                status.setToolTip(it.last_error)
            niche_name = index.name_of(it)
            niche = SortItem(niche_name or "—", niche_name.casefold() or "~")
            niche.setToolTip(f"{niche_name}: {index.why(it)}" if niche_name else
                             "No niche. Right-click → Niche to pick one.")
            if not niche_name:
                niche.setForeground(QColor(theme.MUTED))
            added = SortItem(dt.datetime.fromtimestamp(it.added_at).strftime("%d %b %H:%M"), it.added_at)
            for c, cell in enumerate((thumb, name, score, length, fmt, status, niche, added)):
                self.table.setItem(r, c, cell)
        self.table.setSortingEnabled(True)
        # Restore selection
        if selected:
            self.table.blockSignals(True)
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(ID_ROLE) in selected:
                    self.table.setRangeSelected(QTableWidgetSelectionRange(r, 0, r, len(COLS) - 1), True)
            self.table.blockSignals(False)
        self.table.setColumnHidden(NICHE_COL, not index.niches)
        has = self.table.rowCount() > 0
        self.table.setVisible(has or bool(self.state.store.items))
        self.empty.setVisible(not self.state.store.items)
        self._selection_changed()

    def _status_text(self, it: VideoItem) -> str:
        """'Queued' or, with several accounts, 'Queued · @name' so you can see whose queue it's in."""
        text = STATUS_TEXT.get(it.status, it.status)
        if it.cleaned_at:
            text = "Cleaned up"
        if it.account and it.status != "new" and len(self.state.accounts) > 1:
            text += f" · {self.state.accounts.label_for(it.account)}"
        return text

    def _is_scoring(self, item_id: str) -> bool:
        return any(j.kind == "analyze" and getattr(j, "item_id", None) == item_id for j in self.state.jobs)

    def selected_ids(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(ID_ROLE) for r in rows if self.table.item(r, 0)]

    def _selection_changed(self) -> None:
        ids = self.selected_ids()
        item = self.state.store.get(ids[0]) if len(ids) == 1 else None
        if item is None or item is not self.editor.item:
            self._show_detail(item)
        else:
            # Same item with fresh data (e.g. its score arrived): leave the caption being typed alone.
            self._refresh_detail_keep_editor(item)
        self._update_buttons()

    def _refresh_detail_keep_editor(self, item: VideoItem) -> None:
        self.d_thumb.set_path(item.thumb)
        self.d_ring.set_value(item.total)
        self.d_breakdown.set_score(item.score)
        tips = (item.score or {}).get("tips", [])
        self.d_tips_title.setVisible(bool(tips))
        self.d_tips.setText("".join(f"• {t}<br>" for t in tips))
        self.d_queue.setVisible(item.status == "new")
        self.editor._show_ai()

    def _update_buttons(self) -> None:
        ids = self.selected_ids()
        self.queue_btn.setEnabled(any(self.state.store.get(i) and self.state.store.get(i).status == "new" for i in ids))
        target = self.state.active
        multi = len(self.state.accounts) > 1
        for b in (self.queue_btn, self.d_queue):
            b.setText(f"Queue for {target.label} →" if multi else "Add to queue →")
            b.setToolTip(f"Add to the end of {target.label}'s queue. Right-click a video to pick another account."
                         if multi else "Add to the end of the queue")
        self.remove_btn.setEnabled(bool(ids))
        self.ai_btn.setEnabled(bool(ids))

    def _menu(self, pos) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        m = QMenu(self)
        m.addAction("Play", self.open_file)
        m.addAction("Show in folder", self.show_in_folder)
        m.addSeparator()
        items = [self.state.store.get(i) for i in ids]
        if len(self.state.accounts) > 1:
            fresh = any(it and it.status == "new" for it in items)
            posted = [it.id for it in items if it and it.status == "posted" and it.exists()]
            queue_for = m.addMenu("Add to the queue of")
            queue_for.setEnabled(fresh)
            also = m.addMenu("Also post to")
            also.setToolTip("Queues a copy for another account; the posted entry stays as it is")
            also.setEnabled(bool(posted))
            for a in self.state.accounts:
                icon = QIcon(badge_for(a, 18))
                queue_for.addAction(icon, a.label, lambda i=a.id: self.queue_selected(i))
                also.addAction(icon, a.label, lambda i=a.id, p=posted: self.copy_to(p, i))
        else:
            m.addAction("Add to queue", self.queue_selected)
        self._niche_menu(m.addMenu("Niche"), [it for it in items if it])
        m.addAction("Re-score", lambda: self.score(ids, force=True))
        m.addAction("AI rate", self.ai_selected)
        records = [it.record for it in items if it and it.record and Path(it.record).is_file()]
        if records:
            m.addAction("Open its record" if len(records) == 1 else "Open the first record",
                        lambda: os.startfile(records[0]))  # noqa: S606 - opens in Notepad
        m.addSeparator()
        m.addAction("Remove from library", self.remove_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _niche_menu(self, m: QMenu, items: list[VideoItem]) -> None:
        """Right-click → Niche: say which niche the selected videos are, when it was worked out wrong."""
        niche_menu.fill(m, self.state, items, self)

    def set_niche(self, items: list[VideoItem], name: str) -> None:
        niche_menu.set_niche(self.state, items, name)

    # --- actions ------------------------------------------------------------------

    def add_files_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", self.state.settings.last_folder,
                                                f"Videos ({exts})")
        if files:
            self.state.settings.last_folder = str(Path(files[0]).parent)
            self.state.save_settings()
            self.add_paths([Path(f) for f in files])

    def add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a folder of videos", self.state.settings.last_folder)
        if folder:
            self.state.settings.last_folder = folder
            self.state.save_settings()
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> None:
        added = self.state.store.add_paths(paths)
        if not added:
            self.state.log("No new videos found (already in the library, or not a video file).")
            return
        self.state.log(f"Added {len(added)} video{'s' * (len(added) != 1)}. Scoring…")
        self.state.save_soon()
        self.state.library_changed.emit()
        self.score([i.id for i in added])

    def score_selected(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.score(ids, force=True)
        else:
            self.score([i.id for i in self.state.store.all() if not i.score and not i.analyze_error])

    def score(self, ids: list[str], force: bool = False) -> None:
        busy = {getattr(j, "item_id", None) for j in self.state.jobs if j.kind == "analyze"}
        todo = [i for i in ids if i not in busy and (force or not self.state.store.get(i).score)]
        for item_id in todo:
            item = self.state.store.get(item_id)
            job = Job(f"Scoring {item.name}", lambda j, i=item_id: analyze_item(self.state.store, i, self.state.settings),
                      kind="analyze")
            job.item_id = item_id
            self.state.run(job, on_success=lambda _it: self.state.library_changed.emit(),
                           on_failure=lambda _m: self.state.library_changed.emit())
        if todo:
            self.refresh()

    def ai_selected(self) -> None:
        s = self.state.settings
        from ..ai import has_key

        if not has_key(s):
            QMessageBox.information(self, "AI rating", "Add a Claude or Gemini API key in Settings → AI to use AI rating.")
            return
        ids = self.selected_ids()
        if len(ids) > 10 and QMessageBox.question(
                self, "AI rating", f"Rate {len(ids)} videos with the AI? Each one is a separate API call.") != QMessageBox.Yes:
            return
        for item_id in ids:
            item = self.state.store.get(item_id)
            job = Job(f"AI rating {item.name}", lambda j, i=item_id, a=self.state.account_for(item):
                      ai_item(self.state.store, i, s, j.log, account=a), kind="ai")
            self.state.run(job, on_success=lambda _it: self.state.library_changed.emit())

    def queue_selected(self, account_id: str = "") -> None:
        """Add the selected unqueued videos to an account's queue (the shown account's by default)."""
        account = self.state.accounts.get(account_id) or self.state.active
        ids = [i for i in self.selected_ids() if self.state.store.get(i).status == "new"]
        if not ids:
            return
        self.state.store.enqueue(ids, account.id)
        self.state.save_soon()
        where = f" for {account.label}" if len(self.state.accounts) > 1 else ""
        self.state.log(f"Queued {len(ids)} video{'s' * (len(ids) != 1)}{where}.")
        self.state.library_changed.emit()

    def copy_to(self, ids: list[str], account_id: str) -> None:
        for i in ids:
            self.state.store.copy_for(i, account_id)
        self.state.save_soon()
        self.state.log(f"Queued {len(ids)} posted video{'s' * (len(ids) != 1)} for "
                       f"{self.state.accounts.label_for(account_id)} as well.")
        self.state.library_changed.emit()

    def remove_selected(self) -> None:
        ids = [i for i in self.selected_ids() if self.state.store.get(i).status != "uploading"]
        if not ids:
            return
        if QMessageBox.question(self, "Remove", f"Remove {len(ids)} video(s) from the library?\n"
                                "The files on your disk are not deleted.") != QMessageBox.Yes:
            return
        self.state.store.remove(ids)
        self.state.save_soon()
        self.state.library_changed.emit()

    def _current(self) -> VideoItem | None:
        ids = self.selected_ids()
        return self.state.store.get(ids[0]) if ids else None

    def open_file(self) -> None:
        it = self._current()
        if it and it.exists():
            os.startfile(it.path)  # noqa: S606 - opens in the user's default player

    def show_in_folder(self) -> None:
        it = self._current()
        if it and it.exists():
            import subprocess

            subprocess.Popen(["explorer", "/select,", os.path.normpath(it.path)])
