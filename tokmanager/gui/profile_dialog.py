"""Profile…: the photo, name and bio an account should have on TikTok, and applying them.

Nothing is changed on TikTok until you press "Save and apply to TikTok"; the main window then
runs the job that fills in TikTok's Edit profile box (MainWindow.apply_profile).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from .. import profile
from ..accounts import Account
from . import theme
from .state import AppState, Job
from .widgets import account_badge, label, round_photo

PHOTO_PX = 128
FILTER = "Pictures (" + " ".join(f"*{e}" for e in sorted(profile.PHOTO_EXTS)) + ")"


class ProfileDialog(QDialog):
    apply_requested = Signal(str)  # account id: saved, now put it on TikTok

    def __init__(self, state: AppState, account: Account, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.a = account
        self.setWindowTitle(f"Profile for {account.label}")
        self.setMinimumWidth(620)
        self._photo: Path | None = profile.photo_path(account)  # what the dialog shows
        self._prepared: list[Path] = []  # photos made in this dialog, deleted unless saved

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(label("How this account should look on TikTok. Save keeps it in TikTok Manager; "
                             "Save and apply also puts it on TikTok in the account's browser.", "muted", wrap=True))

        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body)

        # Photo
        pcol = QVBoxLayout()
        pcol.setSpacing(6)
        self.photo = QLabel()
        self.photo.setFixedSize(PHOTO_PX, PHOTO_PX)
        self.photo.setAlignment(Qt.AlignCenter)
        pcol.addWidget(self.photo, 0, Qt.AlignHCenter)
        choose = QPushButton("Choose picture…")
        choose.clicked.connect(self._choose_photo)
        self.remove_photo = QPushButton("Remove")
        self.remove_photo.setToolTip("Don't change the picture on TikTok")
        self.remove_photo.clicked.connect(self._clear_photo)
        pcol.addWidget(choose)
        pcol.addWidget(self.remove_photo)
        pcol.addWidget(label("Cropped to a square\nfrom the middle.", "muted"), 0, Qt.AlignHCenter)
        pcol.addStretch(1)
        body.addLayout(pcol)

        # Name and bio
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setVerticalSpacing(8)
        self.name = QLineEdit(account.profile_name)
        self.name.setPlaceholderText("leave empty to keep the name on TikTok")
        self.name.setMaxLength(profile.NAME_MAX)
        self.name_count = label("", "muted")
        self.bio = QPlainTextEdit(account.profile_bio)
        self.bio.setPlaceholderText("leave empty to keep the bio on TikTok")
        self.bio.setFixedHeight(84)
        self.bio_count = label("", "muted")
        name_row = QHBoxLayout()
        name_row.addWidget(self.name, 1)
        name_row.addWidget(self.name_count)
        form.addRow("Name", name_row)
        form.addRow("Bio", self.bio)
        bio_row = QHBoxLayout()
        bio_row.addWidget(self.bio_count, 1)
        self.suggest_btn = QPushButton("✨ Suggest bios")
        self.suggest_btn.setToolTip("Three bios written by the AI from this account's niche and recent captions "
                                    "(needs a Claude or Gemini key in Settings → AI)")
        self.suggest_btn.clicked.connect(self._suggest)
        bio_row.addWidget(self.suggest_btn)
        form.addRow("", bio_row)
        self.suggestions = QVBoxLayout()
        self.suggestions.setSpacing(4)
        form.addRow(self.suggestions)
        body.addLayout(form, 1)

        # What TikTok shows now (from the last stats refresh)
        now = state.store.profile(account.id)
        if now.username or now.nickname or now.bio:
            text = f"On TikTok at the last refresh: <b>{_esc(now.nickname) or '(no name)'}</b>"
            text += f" · {_esc(now.bio)}" if now.bio else " · (no bio)"
            root.addWidget(label(text, "muted", wrap=True))
        self.status = label(profile.status(account), "muted", wrap=True)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(lambda: self._save(apply=False))
        self.apply_btn = QPushButton("Save and apply to TikTok")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(lambda: self._save(apply=True))
        for b in (cancel, save, self.apply_btn):
            buttons.addWidget(b)
        root.addLayout(buttons)

        self.name.textChanged.connect(self._counts)
        self.bio.textChanged.connect(self._counts)
        self._show_photo()
        self._counts()

    # --- photo --------------------------------------------------------------------

    def _show_photo(self) -> None:
        if self._photo:
            self.photo.setPixmap(round_photo(str(self._photo), PHOTO_PX))
        else:
            self.photo.setPixmap(account_badge(self.a.id, self.a.label, PHOTO_PX))
        self.remove_photo.setEnabled(self._photo is not None)

    def _choose_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Profile picture", str(Path.home() / "Pictures"), FILTER)
        if not path:
            return
        try:
            prepared = profile.prepare_photo(Path(path), self.a.id)
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Profile picture", str(exc))
            return
        self._prepared.append(prepared)
        self._photo = prepared
        self._show_photo()

    def _clear_photo(self) -> None:
        self._photo = None
        self._show_photo()

    # --- text ---------------------------------------------------------------------

    def _bio_text(self) -> str:
        return self.bio.toPlainText().strip()

    def _counts(self) -> None:
        n, b = len(self.name.text().strip()), len(self._bio_text())
        self.name_count.setText(f"{n}/{profile.NAME_MAX}")
        over = b > profile.BIO_MAX
        self.bio_count.setText(f"{b}/{profile.BIO_MAX}" + ("  too long for TikTok" if over else ""))
        self.bio_count.setStyleSheet(f"color:{theme.BAD};" if over else "")

    def _suggest(self) -> None:
        from .. import ai

        a, st = self.a, self.state
        examples = [i.caption or i.name for i in st.store.posted(a.id)][-8:]
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.setText("Writing…")

        def work(_j: Job) -> list[str]:
            return ai.suggest_bios(st.settings, a.niche, a.username, self.name.text().strip(),
                                   self._bio_text(), examples)

        def done(bios: list[str]) -> None:
            self._reset_suggest()
            while self.suggestions.count():
                w = self.suggestions.takeAt(0).widget()
                if w:
                    w.deleteLater()
            for text in bios:
                b = QPushButton(text)
                b.setToolTip("Use this bio")
                b.setStyleSheet("text-align:left; padding:6px 10px;")
                b.clicked.connect(lambda _=False, t=text: self.bio.setPlainText(t))
                self.suggestions.addWidget(b)

        def failed(msg: str) -> None:
            self._reset_suggest()
            if msg != "Cancelled":
                QMessageBox.warning(self, "Suggest bios", msg)

        st.run(Job(f"Suggesting bios for {a.label}", work, kind="ai"), on_success=done, on_failure=failed,
               quiet=True)

    def _reset_suggest(self) -> None:
        try:
            self.suggest_btn.setEnabled(True)
            self.suggest_btn.setText("✨ Suggest bios")
        except RuntimeError:  # the dialog was closed meanwhile
            pass

    # --- save -----------------------------------------------------------------------

    def _save(self, apply: bool) -> None:
        name, bio = self.name.text().strip(), self._bio_text()
        issues = profile.problems(name, bio)
        if issues:
            QMessageBox.warning(self, "Profile", "\n".join(issues))
            return
        if apply and not (name or bio or self._photo):
            QMessageBox.information(self, "Profile", "Set a picture, a name or a bio first.")
            return
        a = self.a
        a.profile_name, a.profile_bio = name, bio
        if self._photo != profile.photo_path(a):
            profile.use_photo(a, self._photo)
        self._prepared = [p for p in self._prepared if p != self._photo]
        self.state.save_accounts()
        self.state.accounts_edited()
        if apply:
            self.apply_requested.emit(a.id)
        self.accept()

    def done(self, result: int) -> None:  # every way the dialog closes
        for p in self._prepared:  # pictures chosen but not saved
            p.unlink(missing_ok=True)
        self._prepared = []
        super().done(result)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
