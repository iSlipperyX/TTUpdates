"""Clean up window: lists the posted videos whose files can go, with the space each frees, and clears the
ticked ones after you confirm. Each gets a .txt record first (cleanup.py). Nothing is removed until
Clean up is pressed and the question after it is answered Yes.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QMessageBox,
                               QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .. import cleanup
from . import theme
from .state import AppState, Job
from .widgets import badge_for, label

ID_ROLE = Qt.UserRole + 1


def move_to_trash(path: Path) -> None:
    from PySide6.QtCore import QFile

    ok = QFile.moveToTrash(str(path))
    if isinstance(ok, tuple):  # some bindings also return the new path
        ok = ok[0]
    if not ok:
        raise OSError("couldn't move it to the Recycle Bin")


class CleanupDialog(QDialog):
    cleaned = Signal(str)  # the summary line, after a clean-up

    def __init__(self, state: AppState, account_id: str = "", parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Clean up posted videos")
        self.resize(880, 600)
        self.plan: cleanup.Plan | None = None
        self._picked: dict[str, bool] = {}  # rows you ticked or unticked: a new look keeps that
        self._job: Job | None = None
        s = state.settings
        root = QVBoxLayout(self)

        root.addWidget(label(
            "Frees the space your posted videos take. Each one first gets a .txt record with its title, account, "
            "post date, TikTok link, latest numbers, caption and length. Then its video file, thumbnail, "
            "ClipMonster's files for it and any copies are deleted. Its post history and stats stay on the "
            "Account page. Nothing goes until you press Clean up.", "muted", wrap=True))

        row = QHBoxLayout()
        row.addWidget(label("Posted more than"))
        self.days = QSpinBox()
        self.days.setRange(0, 365)
        self.days.setSuffix(" days ago")
        self.days.setSpecialValueText("any time")
        self.days.setValue(max(0, s.cleanup_days))
        self.days.setFixedWidth(150)
        self.days.setToolTip("Keep recent posts' files for a while, in case you want to post one again")
        self._debounce = QTimer(self, singleShot=True, interval=400, timeout=self.look)
        self.days.valueChanged.connect(lambda _v: self._debounce.start())
        row.addWidget(self.days)
        self.account = QComboBox()
        self.account.addItem("All accounts", "")
        for a in state.accounts:
            self.account.addItem(QIcon(badge_for(a, 18)), a.label, a.id)
        self.account.setCurrentIndex(max(0, self.account.findData(account_id)))
        self.account.setVisible(len(state.accounts) > 1)
        self.account.currentIndexChanged.connect(lambda _i: self._fill())
        row.addSpacing(10)
        row.addWidget(self.account)
        row.addStretch(1)
        all_btn = QPushButton("Tick all")
        all_btn.clicked.connect(lambda: self._tick_all(True))
        none_btn = QPushButton("Untick all")
        none_btn.clicked.connect(lambda: self._tick_all(False))
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        root.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setObjectName("panel")
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(("Video", "Account", "Posted", "Space", "Note"))
        head = self.tree.header()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4):
            head.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        head.setStretchLastSection(False)
        self.tree.setStyleSheet(  # the same tick boxes as everywhere else in the app
            f"QTreeWidget::item {{ padding: 3px 0; }}"
            f"QTreeWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {theme.BORDER};"
            f" background: {theme.BG}; }}"
            f"QTreeWidget::indicator:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT}; }}")
        self.tree.itemChanged.connect(self._ticked)
        root.addWidget(self.tree, 1)
        self.empty = label("", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty)
        self.notes = label("", "muted", wrap=True)
        root.addWidget(self.notes)

        where = QHBoxLayout()
        self.folder = label("", wrap=True)
        self.folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        where.addWidget(self.folder, 1)
        change = QPushButton("Change…")
        change.clicked.connect(self._change_folder)
        opener = QPushButton("Open")
        opener.setToolTip("Open the records folder")
        opener.clicked.connect(self._open_folder)
        where.addWidget(change)
        where.addWidget(opener)
        root.addLayout(where)

        self.trash = QCheckBox("Move to the Recycle Bin instead of deleting for good")
        self.trash.setToolTip("You can get them back from the Recycle Bin, but the space is only freed once it's "
                              "emptied")
        self.trash.setChecked(s.cleanup_trash)
        self.trash.toggled.connect(lambda _on: self._update_button())
        root.addWidget(self.trash)

        buttons = QHBoxLayout()
        self.status = label("", "muted")
        buttons.addWidget(self.status, 1)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.go = QPushButton("Clean up")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self._confirm)
        buttons.addWidget(self.close_btn)
        buttons.addWidget(self.go)
        root.addLayout(buttons)

        self._show_folder()
        self.look()

    # --- looking ----------------------------------------------------------------------

    def look(self) -> None:
        st = self.state
        try:
            self.plan = cleanup.plan(st.store, st.accounts, st.settings, older_than_days=self.days.value())
        except OSError as exc:  # a folder that can't be read
            self.plan = None
            self.empty.setText(f"Couldn't look at your posted videos: {exc}")
        self._fill()

    def _visible(self) -> list[cleanup.Target]:
        if self.plan is None:
            return []
        aid = self.account.currentData() or ""
        return [t for t in self.plan.targets if not aid or t.account_id == aid]

    def _fill(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        targets = self._visible()
        for t in targets:
            row = QTreeWidgetItem([t.title, t.account, _date(t.posted_at), cleanup.size_text(t.size) if t.size
                                   else "—", _note(t)])
            row.setData(0, ID_ROLE, t.item_id)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(0, Qt.Checked if self._picked.get(t.item_id, t.ticked) else Qt.Unchecked)
            row.setToolTip(0, t.path)
            row.setToolTip(4, "\n".join([*(str(f) for f in t.files), *t.kept]) or "Nothing left on disk: only "
                           "its record is written")
            if not t.on_tiktok:
                row.setForeground(4, QColor(theme.WARN))
            self.tree.addTopLevelItem(row)
        self.tree.blockSignals(False)
        self.tree.setVisible(bool(targets))
        p = self.plan
        if p is not None and not targets:
            wait = f" {p.waiting} posted more recently wait until they're older." if p.waiting else ""
            self.empty.setText("No posted videos to clean up." + wait)
        self.empty.setVisible(not targets)
        notes = []
        unlinked = sum(1 for t in targets if not t.on_tiktok)
        if unlinked:
            one = unlinked == 1
            notes.append(f"{unlinked} {'isn' if one else 'aren'}'t linked to a video on TikTok yet, so "
                         f"{'it starts' if one else 'they start'} unticked. Refresh the account's stats, or tick "
                         f"{'it' if one else 'them'} if you're sure {'it is' if one else 'they are'} up.")
        if p is not None and targets and p.waiting:
            notes.append(f"{p.waiting} posted less than {self.days.value()} days ago stay for now.")
        self.notes.setText(" ".join(notes))
        self.notes.setVisible(bool(notes))
        self._update_button()

    def _ticked(self, row: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self._picked[row.data(0, ID_ROLE)] = row.checkState(0) == Qt.Checked
            self._update_button()

    def _tick_all(self, on: bool) -> None:
        self.tree.blockSignals(True)
        for n in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(n)
            row.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
            self._picked[row.data(0, ID_ROLE)] = on
        self.tree.blockSignals(False)
        self._update_button()

    def ticked(self) -> list[str]:
        rows = (self.tree.topLevelItem(n) for n in range(self.tree.topLevelItemCount()))
        return [r.data(0, ID_ROLE) for r in rows if r.checkState(0) == Qt.Checked]

    def _ticked_size(self) -> int:
        ids = set(self.ticked())
        return sum(t.size for t in self._visible() if t.item_id in ids)

    def _update_button(self) -> None:
        n = len(self.ticked())
        running = self._job is not None
        self.go.setEnabled(bool(n) and not running)
        self.go.setText("Cleaning up…" if running else
                        f"Clean up {n} video{'s' * (n != 1)} ({cleanup.size_text(self._ticked_size())})" if n
                        else "Clean up")
        total = len(self._visible())
        self.status.setText(f"{n} of {total} ticked" if total else "")
        for w in (self.days, self.account, self.trash, self.tree, self.close_btn):
            w.setEnabled(not running)

    # --- the records folder -----------------------------------------------------------

    def _show_folder(self) -> None:
        self.folder.setText(f"Records go to {cleanup.records_folder(self.state.settings)}")

    def _change_folder(self) -> None:
        s = self.state.settings
        folder = QFileDialog.getExistingDirectory(self, "Where should the records go?",
                                                  str(cleanup.records_folder(s)))
        if folder:
            s.cleanup_folder = folder
            self.state.save_settings()
            if self.plan is not None:
                self.plan.folder = cleanup.records_folder(s)
            self._show_folder()

    def _open_folder(self) -> None:
        folder = cleanup.records_folder(self.state.settings)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --- clearing ---------------------------------------------------------------------

    def _confirm(self) -> None:
        ids = self.ticked()
        if not ids or self.plan is None:
            return
        n, size = len(ids), cleanup.size_text(self._ticked_size())
        what = f"{n} posted video{'s' * (n != 1)}"
        if self.trash.isChecked():
            question = (f"Move the files of {what} ({size}) to the Recycle Bin?\n\n"
                        "A .txt record of each is saved first. The space is freed once you empty the Recycle Bin.")
        else:
            question = (f"Delete the files of {what} ({size}) for good?\n\n"
                        "A .txt record of each is saved first. The videos can't be got back afterwards.")
        if QMessageBox.question(self, "Clean up", question) != QMessageBox.Yes:
            return
        s = self.state.settings
        s.cleanup_days, s.cleanup_trash = self.days.value(), self.trash.isChecked()
        self.state.save_settings()
        st, p, trash = self.state, self.plan, self.trash.isChecked()
        remove = move_to_trash if trash else None
        self._job = Job(f"Cleaning up {what}", lambda _j: cleanup.apply(st.store, st.accounts, s, p, ids, remove=remove),
                        kind="cleanup")
        st.run(self._job, on_success=lambda r: self._done(r, trash), on_failure=self._failed)
        self._update_button()

    def _done(self, r: cleanup.Result, trashed: bool) -> None:
        self._job = None
        st = self.state
        st.store.save()
        st.library_changed.emit()
        line = r.summary(trashed)
        st.log(f"🧹 {line}")
        for path, why in r.failed[:10]:
            st.log(f"⚠ Clean up couldn't remove {path}: {why}")
        self.cleaned.emit(line)
        if r.failed:
            more = f"\n…and {len(r.failed) - 10} more (see the Activity log)." if len(r.failed) > 10 else ""
            QMessageBox.warning(self, "Clean up", line + "\n\nSome files couldn't be removed (open in a player, "
                                "say). They're tried again next time:\n\n" +
                                "\n".join(f"{os.path.basename(path)}: {why}" for path, why in r.failed[:10]) + more)
        else:
            QMessageBox.information(self, "Clean up", line)
        self._picked.clear()
        self.look()

    def _failed(self, msg: str) -> None:
        self._job = None
        QMessageBox.warning(self, "Clean up", f"The clean-up stopped: {msg}")
        self.look()

    def reject(self) -> None:  # noqa: D102 - closing waits for a clean-up that's running
        if self._job is None:
            super().reject()


def _date(t: float) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%d %b %Y") if t else "—"


def _note(t: cleanup.Target) -> str:
    bits = [] if t.on_tiktok else ["Not seen on TikTok yet"]
    if t.kept:
        bits.append("video kept: still needed")
    elif not t.files:
        bits.append("files already gone")
    bits.append(t.origin)
    return " · ".join(bits)
