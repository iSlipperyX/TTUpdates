"""Manage accounts: add, rename, reorder and remove TikTok accounts and set each one's posting defaults.

Changes are saved as they're made. Each account's schedule is on the Queue page.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from .. import profile, schedule
from ..accounts import POSTING_MODES, Account
from ..tiktok import methods, normalize_hashtags
from .state import AppState
from .widgets import badge_for, label, panel

ID_ROLE = Qt.UserRole + 1
PRIVACY = [("public", "Everyone"), ("friends", "Friends"), ("private", "Only me")]


def account_status(state: AppState, a: Account) -> str:
    """'Logged in · 4 queued · autopilot, next Today 15:00' for lists and menus."""
    parts = [methods.short_status(state.settings, a)]
    q = [i for i in state.store.queue(a.id) if i.status == "queued"]
    failed = sum(1 for i in state.store.queue(a.id) if i.status == "failed")
    held = sum(1 for i in q if not i.planned_at)  # not in the niches it posts (jobs.replan)
    parts.append(f"{len(q)} queued" + (f", {held} on hold" if held else "") + (f", {failed} failed" if failed else ""))
    nxt = min((i.planned_at for i in q if i.planned_at), default=None)
    parts.append(("autopilot" if a.autopilot else "by hand") + (f", next {schedule.fmt_when(nxt)}" if nxt else ""))
    return " · ".join(parts)


class AccountsDialog(QDialog):
    add_requested = Signal()  # the main window adds the account so it can offer to log in
    profile_requested = Signal(str)  # account id: open its Profile… dialog

    def __init__(self, state: AppState, parent=None, select: str = ""):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Accounts")
        self.setMinimumSize(780, 520)
        self._loading = False
        root = QVBoxLayout(self)
        root.addWidget(label("Each account has its own TikTok login, queue, schedule and stats. The video library "
                             "is shared: queue a video for whichever account should post it.", "muted", wrap=True))

        body = QHBoxLayout()
        root.addLayout(body, 1)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setIconSize(QSize(28, 28))
        self.list.setSpacing(2)
        self.list.setMinimumWidth(330)
        self.list.currentItemChanged.connect(lambda *_: self._show_selected())
        self.list.itemDoubleClicked.connect(lambda _: self._show_in_window())
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("＋ Add account")
        add.setObjectName("primary")
        add.clicked.connect(self.add_requested.emit)
        self.up = QPushButton("↑")
        self.up.setToolTip("Move up in the account list")
        self.up.setFixedWidth(36)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down = QPushButton("↓")
        self.down.setToolTip("Move down in the account list")
        self.down.setFixedWidth(36)
        self.down.clicked.connect(lambda: self._move(1))
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self._remove)
        for w in (add, self.up, self.down):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.remove_btn)
        left.addLayout(row)
        body.addLayout(left, 1)

        box = panel()
        form_lay = QVBoxLayout(box)
        form_lay.setContentsMargins(14, 12, 14, 12)
        self.title = label("", "h2")
        self.status = label("", "muted", wrap=True)
        form_lay.addWidget(self.title)
        form_lay.addWidget(self.status)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setVerticalSpacing(9)
        self.name = QLineEdit()
        self.name.setPlaceholderText("optional, e.g. “Podcast clips”")
        self.username = QLineEdit()
        self.username.setPlaceholderText("filled in when you log in")
        self.privacy = QComboBox()
        for value, text in PRIVACY:
            self.privacy.addItem(text, value)
        self.mode = QComboBox()
        for value, text, tip in POSTING_MODES:
            self.mode.addItem(text, value)
            self.mode.setItemData(self.mode.count() - 1, tip, Qt.ToolTipRole)
        self.hashtags = QLineEdit()
        self.hashtags.setPlaceholderText("#fyp #yourniche")
        self.hashtags.setToolTip("Added to every post from this account (tags already in the caption aren't repeated)")
        self.niche = QLineEdit()
        self.niche.setPlaceholderText("e.g. podcast clips about business for 18-30 year olds")
        self.niche.setToolTip("Helps AI captions and ratings fit this account's audience")
        form.addRow("Name", self.name)
        form.addRow("TikTok @", self.username)
        form.addRow("Posting", self.mode)
        form.addRow("Who can watch", self.privacy)
        form.addRow("Always add hashtags", self.hashtags)
        form.addRow("Niche / audience", self.niche)
        form_lay.addLayout(form)
        prow = QHBoxLayout()
        self.profile_note = label("", "muted", wrap=True)
        prow.addWidget(self.profile_note, 1)
        self.profile_btn = QPushButton("Profile…")
        self.profile_btn.setToolTip("Profile picture, name and bio for this account on TikTok")
        self.profile_btn.clicked.connect(lambda: self.profile_requested.emit(self.selected_id()))
        prow.addWidget(self.profile_btn)
        form_lay.addLayout(prow)
        self.schedule_note = label("", "muted", wrap=True)
        form_lay.addWidget(self.schedule_note)
        form_lay.addStretch(1)
        self.show_btn = QPushButton("Show this account")
        self.show_btn.setToolTip("Switch the window to this account (double-clicking it in the list does the same)")
        self.show_btn.clicked.connect(self._show_in_window)
        form_lay.addWidget(self.show_btn, 0, Qt.AlignRight)
        self.form_box = box
        body.addWidget(box, 1)

        for w in (self.name, self.username, self.hashtags, self.niche):
            w.textEdited.connect(self._edited)
        self.hashtags.editingFinished.connect(self._tidy_tags)
        self.username.editingFinished.connect(self._check_username)
        self.privacy.currentIndexChanged.connect(self._edited)
        self.mode.currentIndexChanged.connect(self._mode_picked)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._saving = False
        state.account_changed.connect(self._account_changed)
        state.library_changed.connect(self._refresh_rows)
        state.jobs_changed.connect(self._update_buttons)
        self.reload(select or state.active.id)

    # --- list ---------------------------------------------------------------------

    def _account_changed(self) -> None:
        if not self._saving:  # our own edit: reloading would reset the field being typed in
            self.reload()

    def reload(self, select: str = "") -> None:
        select = select if isinstance(select, str) and select else self.selected_id() or self.state.active.id
        self.list.blockSignals(True)
        self.list.clear()
        for a in self.state.accounts:
            li = QListWidgetItem()
            li.setData(ID_ROLE, a.id)
            li.setSizeHint(QSize(0, 48))
            self.list.addItem(li)
            if a.id == select:
                self.list.setCurrentItem(li)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._refresh_rows()
        self._show_selected()

    def _refresh_rows(self) -> None:
        for r in range(self.list.count()):
            li = self.list.item(r)
            a = self.state.accounts.get(li.data(ID_ROLE))
            if not a:
                continue
            shown = "   (shown now)" if a.id == self.state.active.id else ""
            li.setText(f"{a.label}{shown}\n{account_status(self.state, a)}")
            li.setIcon(QIcon(badge_for(a, 28)))
        if not self._loading and self.selected_account():
            self.status.setText(account_status(self.state, self.selected_account()))

    def selected_id(self) -> str:
        li = self.list.currentItem()
        return li.data(ID_ROLE) if li else ""

    def selected_account(self) -> Account | None:
        return self.state.accounts.get(self.selected_id())

    # --- form ---------------------------------------------------------------------

    def _show_selected(self) -> None:
        a = self.selected_account()
        self.form_box.setEnabled(a is not None)
        if not a:
            return
        self._loading = True
        self.title.setText(a.label)
        self.status.setText(account_status(self.state, a))
        self.name.setText(a.name)
        self.username.setText(a.username)
        self.privacy.setCurrentIndex(max(0, self.privacy.findData(a.default_privacy)))
        self.mode.setCurrentIndex(max(0, self.mode.findData(a.mode)))
        self.mode.setToolTip(dict((v, t) for v, _, t in POSTING_MODES)[a.mode])
        self.hashtags.setText(a.default_hashtags)
        self.niche.setText(a.niche)
        self.profile_note.setText(profile.status(a))
        s = a.apply(self.state.settings)
        slots = ", ".join(t.strftime("%H:%M") for t in schedule.daily_slots(s))
        what = "reminders" if not a.autopilot and self.state.settings.remind_to_post else "posts"
        self.schedule_note.setText(f"Schedule: {a.posts_per_day} {what} a day at {slots}, at least "
                                   f"{a.min_gap_minutes} min apart. Change it on the Queue page while this account "
                                   "is shown.")
        self._loading = False
        self._update_buttons()

    def _edited(self) -> None:
        a = self.selected_account()
        if self._loading or not a:
            return
        a.name = self.name.text().strip()
        a.username = self.username.text().strip().lstrip("@")
        a.default_privacy = self.privacy.currentData()
        a.default_hashtags = self.hashtags.text().strip()
        a.niche = self.niche.text().strip()
        self.title.setText(a.label)
        self.state.save_accounts()
        self._refresh_rows()
        self._saving = True
        try:
            self.state.accounts_edited()
        finally:
            self._saving = False

    def _mode_picked(self) -> None:
        a = self.selected_account()
        if self._loading or not a:
            return
        self._saving = True
        try:
            self.state.set_autopilot(a.id, self.mode.currentData() == "autopilot")
        finally:
            self._saving = False
        self._show_selected()
        self._refresh_rows()

    def _tidy_tags(self) -> None:
        tidy = normalize_hashtags(self.hashtags.text())
        if tidy != self.hashtags.text().strip():
            self.hashtags.setText(tidy)
            self._edited()

    def _check_username(self) -> None:
        a = self.selected_account()
        if not a or not a.username:
            return
        other = self.state.accounts.find_username(a.username, exclude=a.id)
        if other:
            QMessageBox.warning(self, "Accounts", f"@{a.username} is already added as “{other.label}”. Two entries "
                                "for the same TikTok account would post twice as often as either schedule says.")

    def _update_buttons(self) -> None:
        a = self.selected_account()
        n = len(self.state.accounts)
        busy = bool(a) and self.state.account_busy(a.id)
        self.remove_btn.setEnabled(bool(a) and n > 1 and not busy)
        self.remove_btn.setToolTip("" if n > 1 else "There has to be at least one account.")
        idx = self.state.accounts.items.index(a) if a else -1
        self.show_btn.setEnabled(bool(a) and a.id != self.state.active.id)
        self.up.setEnabled(idx > 0)
        self.down.setEnabled(0 <= idx < n - 1)

    # --- actions ------------------------------------------------------------------

    def _show_in_window(self) -> None:
        a = self.selected_account()
        if a:
            self.state.switch_account(a.id)
            self._refresh_rows()
            self._update_buttons()

    def _move(self, delta: int) -> None:
        a = self.selected_account()
        if a:
            self.state.accounts.move(a.id, delta)
            self.state.save_accounts()
            self.reload(a.id)
            self.state.accounts_edited()

    def _remove(self) -> None:
        a = self.selected_account()
        if not a or len(self.state.accounts) <= 1:
            return
        if self.state.account_busy(a.id):
            QMessageBox.information(self, "Remove account", "Wait for this account's upload or login to finish.")
            return
        queued = len(self.state.store.queue(a.id))
        posted = len(self.state.store.posted(a.id))
        msg = (f"Remove “{a.label}” from TikTok Manager?\n\n"
               f"• Its login is deleted from this PC (your TikTok account itself isn't touched).\n"
               f"• {queued} video{'s' * (queued != 1)} in its queue go back to the library, unqueued.\n"
               f"• Its stats are forgotten. The {posted} video{'s' * (posted != 1)} it posted stay in the library "
               "as posted.")
        if QMessageBox.question(self, "Remove account", msg) != QMessageBox.Yes:
            return
        self.state.remove_account(a.id)
        self.reload()
