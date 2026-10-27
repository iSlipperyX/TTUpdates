"""The box shown while you log in to TikTok in the browser window the app opened."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme


class LoginDialog(QDialog):
    """Non-modal: says what to do, then closes by itself when the login job ends.

    "I'm logged in" sets `finish` (the job then closes the browser window); "Cancel" cancels the job.
    """

    def __init__(self, parent: QWidget, account_label: str, cancel):
        super().__init__(parent)
        self.setWindowTitle("Log in to TikTok")
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(440)
        self.finish = threading.Event()
        self._cancel = cancel
        self._ended = False

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        title = QLabel(f"<b>Log in to TikTok for {account_label}</b>")
        title.setStyleSheet("font-size:15px;")
        lay.addWidget(title)
        body = QLabel("A browser window has opened on TikTok's login page. Log in there the way you "
                      "normally would (the QR code works too).<br><br>TikTok Manager notices as soon as "
                      "you're in, closes that window and connects the account. You don't need to do "
                      "anything else.")
        body.setWordWrap(True)
        lay.addWidget(body)
        self.status = QLabel("Opening the browser…")
        self.status.setStyleSheet(f"color:{theme.MUTED};")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch(1)
        self.done_btn = QPushButton("I'm logged in")
        self.done_btn.setToolTip("Use this if the window stays open after you've logged in.")
        self.done_btn.clicked.connect(self._done)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.done_btn)
        row.addWidget(cancel_btn)
        lay.addLayout(row)

    def show_line(self, msg: str) -> None:
        """Mirror the login job's progress lines."""
        if msg.startswith("Log in to TikTok in the"):
            self.status.setText("Waiting for you to log in…")
        elif msg.startswith(("You're logged in", "Closing the", "Checking", "Logged in as")):
            self.status.setText(msg)

    def _done(self) -> None:
        self.finish.set()
        self.done_btn.setEnabled(False)
        self.status.setText("Closing the browser window and checking the login…")

    def reject(self) -> None:  # Cancel button, Esc or the window's close box
        if not self._ended:
            self._cancel()
        super().reject()

    def end(self) -> None:
        """The login job finished (either way): close without cancelling anything."""
        self._ended = True
        self.accept()
