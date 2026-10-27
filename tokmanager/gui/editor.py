"""Caption / hashtags / privacy editor for one video, with a live preview of the final post text."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ..ai import has_key
from ..jobs import ai_hashtags, ai_item
from ..store import VideoItem
from ..tiktok import MAX_CAPTION, compose_caption, normalize_hashtags
from . import theme
from .state import AppState, Job
from .widgets import label

PRIVACY_CHOICES = [("", "Default"), ("public", "Everyone"), ("friends", "Friends"), ("private", "Only me")]


class PostEditor(QWidget):
    edited = Signal(str)  # item id

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.item: VideoItem | None = None
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label("Post details", "h2"))
        head.addStretch(1)
        self.ai_btn = QPushButton("✨ AI caption")
        self.ai_btn.setToolTip("Have Claude watch the clip, rate it and write a caption + hashtags")
        self.ai_btn.clicked.connect(self._ai)
        self.tags_btn = QPushButton("✨ Hashtags")
        self.tags_btn.setToolTip("Just the hashtags: Claude reads what's known about the clip (quicker and cheaper "
                                 "than a full AI caption) and replaces the hashtags below")
        self.tags_btn.clicked.connect(self._ai_tags)
        head.addWidget(self.tags_btn)
        head.addWidget(self.ai_btn)
        lay.addLayout(head)

        self.caption = QPlainTextEdit()
        self.caption.setPlaceholderText("Caption (the title is used if left empty)")
        self.caption.setFixedHeight(76)
        self.hashtags = QLineEdit()
        self.hashtags.setPlaceholderText("#hashtags separated by spaces")
        self.privacy = QComboBox()
        for value, text in PRIVACY_CHOICES:
            self.privacy.addItem(text, value)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("Caption", self.caption)
        form.addRow("Hashtags", self.hashtags)
        form.addRow("Who can watch", self.privacy)
        lay.addLayout(form)

        self.preview = label("", "muted", wrap=True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setStyleSheet(f"background:{theme.BG}; border:1px dashed {theme.BORDER}; border-radius:7px; padding:6px;")
        lay.addWidget(self.preview)
        self.ai_box = label("", wrap=True)
        self.ai_box.setStyleSheet("background:#1B1630; border:1px solid #3B2F6B; border-radius:7px; padding:8px;")
        self.ai_box.setTextFormat(Qt.RichText)
        lay.addWidget(self.ai_box)

        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        self.caption.textChanged.connect(self._changed)
        self.hashtags.textChanged.connect(self._changed)
        self.privacy.currentIndexChanged.connect(self._changed)
        self.hashtags.editingFinished.connect(self._tidy_tags)
        self.set_item(None)

    def set_item(self, item: VideoItem | None) -> None:
        if self.item and self._save_timer.isActive():
            self._save_timer.stop()
            self._save()
        self.item = item
        self._loading = True
        self.setEnabled(item is not None and item.status != "posted")
        self.caption.setPlainText(item.caption if item else "")
        self.hashtags.setText(item.hashtags if item else "")
        idx = max(0, self.privacy.findData(item.privacy if item else ""))
        self.privacy.setCurrentIndex(idx)
        default = self.state.account_for(item).default_privacy
        self.privacy.setItemText(0, f"Default ({dict(PRIVACY_CHOICES).get(default, '')})")
        self._loading = False
        self._refresh_preview()
        self._show_ai()

    def _changed(self) -> None:
        if self._loading or not self.item:
            return
        self._refresh_preview(from_fields=True)
        self._save_timer.start()

    def _tidy_tags(self) -> None:
        tidy = normalize_hashtags(self.hashtags.text())
        if tidy != self.hashtags.text().strip():
            self.hashtags.setText(tidy)

    def _save(self) -> None:
        if not self.item:
            return
        with self.state.store.lock:
            self.item.caption = self.caption.toPlainText().strip()
            self.item.hashtags = self.hashtags.text().strip()
            self.item.privacy = self.privacy.currentData() or ""
        self.state.save_soon()
        self.edited.emit(self.item.id)

    def _refresh_preview(self, from_fields: bool = False) -> None:
        if not self.item:
            self.preview.setText("Select a video to edit its caption.")
            return
        tmp = replace(self.item)
        if from_fields:
            tmp.caption, tmp.hashtags = self.caption.toPlainText().strip(), self.hashtags.text()
        account = self.state.account_for(self.item)
        text = compose_caption(tmp, account.apply(self.state.settings))
        n = len(text)
        warn = f" <span style='color:{theme.BAD}'>(too long)</span>" if n >= MAX_CAPTION else ""
        to = f" to {_html(account.label)}" if len(self.state.accounts) > 1 else ""
        self.preview.setText(f"<b>Will post{to} as:</b> {_html(text)}<br><span style='font-size:8pt'>{n}/{MAX_CAPTION}"
                             f" characters</span>{warn}")

    def _show_ai(self) -> None:
        ai = self.item.ai if self.item else {}
        self.ai_btn.setVisible(True)
        if not ai:
            self.ai_box.hide()
            return
        ideas = "".join(f"<li>{_html(i)}</li>" for i in ai.get("ideas", []))
        self.ai_box.setText(
            f"<b>AI rating {ai.get('appeal', '?')}/100</b> · hook {ai.get('hook', '?')}/100<br>"
            f"{_html(ai.get('verdict', ''))}" + (f"<ul style='margin:4px 0 0 -18px'>{ideas}</ul>" if ideas else ""))
        self.ai_box.show()

    def _ai(self) -> None:
        if not self.item:
            return
        s = self.state.settings
        if not has_key(s):
            self.state.log("Add your Anthropic API key in Settings → AI first.")
            return
        item_id = self.item.id
        # A fresh request should replace the old caption suggestion, not keep the stale one.
        with self.state.store.lock:
            if self.item.ai and self.item.caption == self.item.ai.get("caption"):
                self.item.caption = ""
            if self.item.ai and self.item.hashtags == " ".join("#" + h for h in self.item.ai.get("hashtags", [])):
                self.item.hashtags = ""
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("Thinking…")

        def done(_=None):
            self.ai_btn.setEnabled(True)
            self.ai_btn.setText("✨ AI caption")
            self.state.library_changed.emit()
            if self.item and self.item.id == item_id:
                self.set_item(self.item)

        account = self.state.account_for(self.item)
        job = Job("AI rating", lambda j: ai_item(self.state.store, item_id, s, j.log, account=account), kind="ai")
        self.state.run(job, on_success=done, on_failure=done)


    def _ai_tags(self) -> None:
        if not self.item:
            return
        s = self.state.settings
        if not has_key(s):
            self.state.log("Add your Anthropic API key in Settings → AI first.")
            return
        if self._save_timer.isActive():  # keep what was just typed in the caption
            self._save_timer.stop()
            self._save()
        item_id = self.item.id
        self.tags_btn.setEnabled(False)
        self.tags_btn.setText("Writing…")

        def done(_=None):
            self.tags_btn.setEnabled(True)
            self.tags_btn.setText("✨ Hashtags")
            self.state.save_soon()
            self.state.library_changed.emit()
            if self.item and self.item.id == item_id:
                self.set_item(self.item)

        account = self.state.account_for(self.item)
        job = Job("AI hashtags", lambda j: ai_hashtags(self.state.store, item_id, s, account, j.log), kind="ai")
        self.state.run(job, on_success=done, on_failure=done)


def _html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
