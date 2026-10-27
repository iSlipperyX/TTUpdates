"""First-run setup: three short steps that leave the app ready to use.

1. The first account: a name and, if wanted now, the TikTok login.
2. Where clips come from: ClipMonster's Automation folder (found by itself) or any folder, and
   whether new clips wait on the Ready page or in the Library.
3. How the account posts (by hand or autopilot), how often, and the phone folder.

It isn't modal, so the login window and the main window stay usable while it's open. Closing it
counts as skipping; Settings → Ready to upload has "Run first-run setup again".
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import clipmonster, phone, ready
from ..accounts import POSTING_MODES
from . import theme
from .state import AppState
from .widgets import label

STEPS = ("Your TikTok account", "Where clips come from", "How you post")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SetupWizard(QDialog):
    login_requested = Signal()  # log the account in (the main window runs it, with this window as parent)
    setup_finished = Signal(bool)  # True: finished, False: skipped

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.account = state.active
        self.setWindowTitle("Set up TikTok Manager")
        self.setMinimumSize(600, 470)
        self._closing = False
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(12)
        self.step_label = label("", "muted")
        self.title = label("", "title")
        root.addWidget(self.step_label)
        root.addWidget(self.title)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        for build in (self._account_page, self._clips_page, self._posting_page):
            self.pages.addWidget(build())

        buttons = QHBoxLayout()
        self.skip_btn = QPushButton("Skip setup")
        self.skip_btn.setToolTip("Set things up yourself later (Settings → Ready to upload → Run first-run setup again)")
        self.skip_btn.clicked.connect(self.reject)
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("primary")
        self.next_btn.setDefault(True)  # Enter goes on, even straight after typing the name
        self.next_btn.clicked.connect(self._next)
        buttons.addWidget(self.skip_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.back_btn)
        buttons.addWidget(self.next_btn)
        root.addLayout(buttons)

        state.account_changed.connect(self._login_status)
        state.jobs_changed.connect(self._login_status)
        self._login_status()
        self.go(0)

    # --- pages --------------------------------------------------------------------

    def _page(self, intro: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label(intro, wrap=True))
        return w, lay

    def _account_page(self) -> QWidget:
        a = self.account
        w, lay = self._page("Welcome! Three quick steps and TikTok Manager is ready: your account, where your clips "
                            "come from, and how you'd like to post them.")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.name = QLineEdit(a.name)
        self.name.setPlaceholderText("optional, e.g. “Podcast clips”. Your @username is used otherwise")
        form.addRow("Name", self.name)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.login_btn = QPushButton("Log in to TikTok…")
        self.login_btn.setAutoDefault(False)
        self.login_btn.clicked.connect(self._login)
        row.addWidget(self.login_btn)
        self.login_label = label("", wrap=True)
        self.login_label.setTextFormat(Qt.RichText)
        row.addWidget(self.login_label, 1)
        lay.addLayout(row)
        lay.addWidget(label("The login lets TikTok Manager read your stats and, on autopilot, post for you. It "
                            "stays in its own browser profile on this PC. Posting by hand works without it, and you "
                            "can log in later on the Account page.", "muted", wrap=True))
        lay.addStretch(1)
        return w

    def _clips_page(self) -> QWidget:
        s = self.state.settings
        w, lay = self._page("TikTok Manager can watch a folder: new clips that turn up there are scored and wait "
                            "for you with their caption ready.")
        self.cm_folder = clipmonster.output_folders()[0] if clipmonster.installed() else None
        self.watch_cm = QCheckBox()
        if self.cm_folder is not None:
            self.watch_cm.setText(f"Watch ClipMonster's Automation folder\n{self.cm_folder}")
            self.watch_cm.setChecked(True)
            if ready.watched(s.watch_folders, self.cm_folder):
                self.watch_cm.setText(f"ClipMonster's Automation folder is watched already\n{self.cm_folder}")
                self.watch_cm.setEnabled(False)
            lay.addWidget(self.watch_cm)
        else:
            self.watch_cm.hide()
            lay.addWidget(label("ClipMonster isn't set up on this PC. Pick the folder your clips are saved to, or "
                                "skip this and drop videos on the Library page.", "muted", wrap=True))
        row = QHBoxLayout()
        self.other = QLineEdit()
        self.other.setPlaceholderText("another folder (optional)")
        pick = QPushButton("Choose…")
        pick.setAutoDefault(False)
        pick.clicked.connect(self._pick_folder)
        row.addWidget(self.other, 1)
        row.addWidget(pick)
        lay.addLayout(row)
        target_row = QHBoxLayout()
        target_row.addWidget(label("New clips go to"))
        self.target = QComboBox()
        self.target.addItem(f"{self.account.label}'s Ready page, best clips first", self.account.id)
        other = self.state.accounts.get(s.watch_target)
        if other is not None and other.id != self.account.id:  # chosen before: kept unless you change it
            self.target.addItem(f"{other.label}'s Ready page", other.id)
        if len(self.state.accounts) > 1:
            self.target.addItem("Shared out between my accounts", "spread")
        self.target.addItem("Only the Library (I queue them myself)", "library")
        # With folders watched already, keep where their clips go now. Otherwise "library" is only the
        # default for a new install, so suggest the Ready page.
        if s.watch_folders or s.watch_target != "library":
            self.target.setCurrentIndex(max(0, self.target.findData(s.watch_target)))
        target_row.addWidget(self.target, 1)
        lay.addLayout(target_row)
        lay.addStretch(1)
        return w

    def _posting_page(self) -> QWidget:
        a = self.account
        w, lay = self._page("Posting can be fully hands-off, or you post yourself and TikTok Manager has everything "
                            "ready. You can change this any time on the Queue page.")
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        for value, text, what in POSTING_MODES:
            rb = QRadioButton(text)
            rb.setStyleSheet("font-weight: 600;")
            self.mode_group.addButton(rb)
            self.mode_buttons[value] = rb
            lay.addWidget(rb)
            note = label(what, "muted", wrap=True)
            note.setContentsMargins(26, 0, 0, 4)
            lay.addWidget(note)
        self.mode_buttons[a.mode].setChecked(True)
        row = QHBoxLayout()
        row.addWidget(label("Posts a day"))
        self.per_day = QSpinBox(minimum=1, maximum=24, value=a.posts_per_day)
        row.addWidget(self.per_day)
        self.times = label("", "muted")
        row.addWidget(self.times, 1)
        lay.addLayout(row)
        self.per_day.valueChanged.connect(self._show_times)
        current = self.state.settings.phone_folder
        self.phone_folder = Path(current) if current else phone.suggested_folder()
        self.use_phone = QCheckBox()
        if self.phone_folder is not None:
            where = f"OneDrive › {phone.FOLDER_NAME}" if not current else self.phone_folder.name or current
            self.use_phone.setText(f"Keep each day's clips and captions in {where}, for posting from my phone")
            self.use_phone.setToolTip(str(self.phone_folder))
            self.use_phone.setChecked(bool(current) or a.mode == "hand")
            lay.addWidget(self.use_phone)
        else:
            self.use_phone.hide()
        for rb in self.mode_buttons.values():
            rb.toggled.connect(self._mode_changed)
        self._mode_changed()
        self._show_times()
        lay.addStretch(1)
        return w

    # --- behaviour ----------------------------------------------------------------

    def go(self, index: int) -> None:
        index = max(0, min(len(STEPS) - 1, index))
        if self.pages.currentIndex() == 0 and index > 0:
            self._save_name()
        self.pages.setCurrentIndex(index)
        if self.target.itemData(0) == self.account.id:  # the name may have changed on step 1
            self.target.setItemText(0, f"{self.account.label}'s Ready page, best clips first")
        self.step_label.setText(f"Step {index + 1} of {len(STEPS)}")
        self.title.setText(STEPS[index])
        self.back_btn.setVisible(index > 0)
        self.next_btn.setText("Finish" if index == len(STEPS) - 1 else "Next")

    def _next(self) -> None:
        if self.pages.currentIndex() == len(STEPS) - 1:
            self.finish()
        else:
            self.go(self.pages.currentIndex() + 1)

    def _save_name(self) -> None:
        a = self.account
        name = self.name.text().strip()
        if name != a.name:
            a.name = name
            self.state.save_accounts()
            self.state.accounts_edited()

    def _login(self) -> None:
        self._save_name()
        self.login_requested.emit()

    def _login_status(self) -> None:
        a = self.account
        busy = self.state.account_busy(a.id, ("login",))
        self.login_btn.setEnabled(not busy)
        if busy:
            text = "Logging in… finish in the browser window that opened."
        elif a.logged_in:
            who = f" as <b>@{_esc(a.username)}</b>" if a.username else ""
            text = f"<span style='color:{theme.GOOD}'>✓ Logged in{who}</span>"
            self.login_btn.setText("Log in again…")
        else:
            text = "<span style='color:" + theme.MUTED + "'>Not logged in yet.</span>"
        self.login_label.setText(text)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Watch a folder for new clips", self.other.text())
        if folder:
            self.other.setText(folder)

    def _mode_changed(self) -> None:
        hand = self.mode() == "hand"
        self.use_phone.setEnabled(hand)  # autopilot posts by itself, so nothing goes to the phone
        self._show_times()

    def _show_times(self) -> None:
        from dataclasses import replace

        from .. import schedule

        s = replace(self.state.settings_for(self.account), posts_per_day=self.per_day.value())
        times = ", ".join(t.strftime("%H:%M") for t in schedule.daily_slots(s))
        reminders = self.mode() == "hand" and self.state.settings.remind_to_post
        self.times.setText(("reminders at " if reminders else "at ") + times)

    def mode(self) -> str:
        return next((v for v, rb in self.mode_buttons.items() if rb.isChecked()), "hand")

    def folders(self) -> list[str]:
        """The folders to watch from step 2 that aren't watched yet."""
        s = self.state.settings
        out = []
        if self.cm_folder is not None and self.watch_cm.isEnabled() and self.watch_cm.isChecked():
            out.append(str(self.cm_folder))
        other = self.other.text().strip().strip('"').strip()  # pasted with "Copy as path", say
        if other and not ready.watched(s.watch_folders + out, other):
            out.append(other)
        return out

    def finish(self) -> None:
        st = self.state
        s = st.settings
        a = self.account
        self._save_name()
        new = self.folders()
        for folder in new:
            try:
                Path(folder).mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # a drive that isn't there, a name Windows refuses…
                QMessageBox.warning(self, "Can't use that folder", f"{folder}\n\n{exc}")
                self.go(1)
                return
        s.watch_folders = [f for f in s.watch_folders if f] + new
        if s.watch_folders:
            s.watch_target = self.target.currentData()
        if self.phone_folder is not None and self.mode() == "hand":  # autopilot: nothing goes to the phone anyway
            s.phone_folder = str(self.phone_folder) if self.use_phone.isChecked() else ""
        a.posts_per_day = self.per_day.value()
        st.set_autopilot(a.id, self.mode() == "autopilot")
        s.setup_done = True
        if self.cm_folder is not None:  # asked here, so it isn't offered again by itself
            s.clipmonster_offered = str(self.cm_folder)
        st.save_settings()
        st.save_accounts()
        st.accounts_edited()
        bits = [f"watching {', '.join(Path(f).name or f for f in new)}"] if new else []
        bits.append("posting by hand" if self.mode() == "hand" else "posting on autopilot")
        if s.phone_folder:
            bits.append(f"the next day's clips in {s.phone_folder}")
        st.log("Setup done: " + "; ".join(bits) + ".")
        self._closing = True
        self.accept()
        self.setup_finished.emit(True)

    def reject(self) -> None:
        if not self._closing:
            self._closing = True
            self.state.settings.setup_done = True
            self.state.save_settings()
            self.state.log("Setup skipped. Run it again any time: Settings → Ready to upload.")
            super().reject()
            self.setup_finished.emit(False)
        else:
            super().reject()
