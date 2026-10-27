"""Settings dialog: scoring weights, posting defaults, AI, the optional official TikTok API, updates."""

from __future__ import annotations

import datetime as dt
from dataclasses import fields
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSlider, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import clipmonster, ready, updater
from ..config import VERSION, Settings, Weights
from ..scoring import PART_LABELS
from .widgets import label


def _combo(options: list[tuple[str, str]], current: str) -> QComboBox:
    c = QComboBox()
    for value, text in options:
        c.addItem(text, value)
    c.setCurrentIndex(max(0, c.findData(current)))
    return c


def _form(tab: QTabWidget, title: str, intro: str = "") -> QFormLayout:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(16, 14, 16, 14)
    if intro:
        lay.addWidget(label(intro, "muted", wrap=True))
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignRight)
    form.setVerticalSpacing(9)
    lay.addLayout(form)
    lay.addStretch(1)
    tab.addTab(w, title)
    return form


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None, on_check_updates: Callable[[], None] | None = None,
                 accounts: list[tuple[str, str]] | None = None, tab: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(620, 560)
        self.s = settings
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        self.tabs = tabs
        root.addWidget(tabs, 1)

        # Scoring
        f = _form(tabs, "Scoring", "How much each part counts toward a video's score. Only the relative sizes "
                                   "matter. 'Clip finder' and 'AI rating' only count for videos that have them.")
        self.weight_sliders: dict[str, QSlider] = {}
        for fl in fields(Weights):
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 100)
            sl.setValue(int(round(getattr(settings.weights, fl.name) * 100)))
            val = QLabel()
            val.setFixedWidth(30)
            sl.valueChanged.connect(lambda v, lab=val: lab.setText(str(v)))
            val.setText(str(sl.value()))
            row = QHBoxLayout()
            row.addWidget(sl, 1)
            row.addWidget(val)
            f.addRow(PART_LABELS[fl.name], row)
            self.weight_sliders[fl.name] = sl
        self.min_len = QDoubleSpinBox(minimum=1, maximum=600, value=settings.ideal_min_len, suffix=" s", decimals=0)
        self.max_len = QDoubleSpinBox(minimum=1, maximum=600, value=settings.ideal_max_len, suffix=" s", decimals=0)
        lens = QHBoxLayout()
        lens.addWidget(self.min_len)
        lens.addWidget(label("to"))
        lens.addWidget(self.max_len)
        lens.addStretch(1)
        f.addRow("Ideal length", lens)
        self.lufs = QDoubleSpinBox(minimum=-30, maximum=-6, value=settings.target_lufs, suffix=" LUFS", decimals=0)
        f.addRow("Target loudness", self.lufs)

        # Posting
        f = _form(tabs, "Posting")
        self.method = _combo([("browser", "Browser: my TikTok login in Edge/Chrome (recommended)"),
                              ("api", "Official TikTok API (needs a developer app)")], settings.upload_method)
        f.addRow("Upload with", self.method)
        self.channel = _combo([("msedge", "Microsoft Edge"), ("chrome", "Google Chrome")], settings.browser_channel)
        f.addRow("Browser", self.channel)
        self.show_browser = QCheckBox("Show the browser window while posting")
        self.show_browser.setChecked(settings.show_browser)
        self.show_browser.setToolTip("Recommended: TikTok is more likely to block hidden browsers, and you can solve a "
                                     "captcha if one appears.")
        f.addRow("", self.show_browser)
        self.comments = QCheckBox("Comments")
        self.duet = QCheckBox("Duet")
        self.stitch = QCheckBox("Stitch")
        row = QHBoxLayout()
        for cb, on in ((self.comments, settings.allow_comments), (self.duet, settings.allow_duet),
                       (self.stitch, settings.allow_stitch)):
            cb.setChecked(on)
            row.addWidget(cb)
        row.addStretch(1)
        f.addRow("Allow", row)
        self.ai_label = QCheckBox("Label posts as AI-generated content")
        self.ai_label.setChecked(settings.ai_generated_label)
        f.addRow("", self.ai_label)
        self.template = QLineEdit(settings.caption_template)
        self.template.setToolTip("{caption} = the caption, {hashtags} = the video's + default hashtags, {title} = the title")
        f.addRow("Caption layout", self.template)
        f.addRow("", label("Who can watch, hashtags added to every post and the niche are set for each account: "
                           "account button (top left) → Manage accounts.", "muted", wrap=True))
        self.tray = QCheckBox("Closing the window keeps the app running in the system tray")
        self.tray.setChecked(settings.minimize_to_tray)
        f.addRow("", self.tray)
        self.startup = QCheckBox("Start with Windows (in the tray) so autopilot keeps posting after a restart")
        self.startup.setChecked(settings.start_with_windows)
        f.addRow("", self.startup)

        # Ready to upload
        self._build_ready_tab(tabs, settings, accounts or [])

        # AI
        f = _form(tabs, "AI", "Optional. Claude watches frames from each clip, predicts how it will do on "
                              "TikTok and writes a caption and hashtags. Uses your Anthropic API key "
                              "(console.anthropic.com); each rating is one API call.")
        self.ai_key = QLineEdit(settings.ai_api_key)
        self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_key.setPlaceholderText("sk-ant-…")
        f.addRow("Anthropic API key", self.ai_key)
        self.ai_model = _combo([("claude-opus-5-5", "Claude Opus 5.5 (best)"), ("claude-sonnet-5-5", "Claude Sonnet 5.5 (cheaper)"),
                                ("claude-haiku-5-5", "Claude Haiku 5.5 (cheapest)")], settings.ai_model)
        f.addRow("Model", self.ai_model)
        f.addRow("Niche / audience", label("Set for each account in Manage accounts, so captions fit "
                                           "each account's audience.", "muted", wrap=True))
        self.tag_count = QSpinBox(minimum=0, maximum=10, value=settings.ai_hashtag_count)
        f.addRow("Hashtags to suggest", self.tag_count)

        # API
        f = _form(tabs, "TikTok API", "Only needed if you choose 'Official TikTok API' above. Create an app at "
                                      "developers.tiktok.com with Login Kit (Desktop) and Content Posting API, "
                                      "add the redirect URI below, and paste its keys here. Until TikTok audits "
                                      "your app it can only post 'Only me' videos to a private account.")
        self.client_key = QLineEdit(settings.api_client_key)
        f.addRow("Client key", self.client_key)
        self.client_secret = QLineEdit(settings.api_client_secret)
        self.client_secret.setEchoMode(QLineEdit.Password)
        f.addRow("Client secret", self.client_secret)
        self.port = QSpinBox(minimum=1024, maximum=65535, value=settings.api_redirect_port)
        self.redirect = QLineEdit()
        self.redirect.setReadOnly(True)
        self.port.valueChanged.connect(lambda p: self.redirect.setText(f"http://localhost:{p}/callback/"))
        self.redirect.setText(f"http://localhost:{settings.api_redirect_port}/callback/")
        f.addRow("Local port", self.port)
        f.addRow("Redirect URI", self.redirect)
        self.post_mode = _combo([("direct", "Publish directly"),
                                 ("inbox", "Send as a draft to my TikTok app (I finish posting on my phone)")],
                                settings.api_post_mode)
        f.addRow("Posting", self.post_mode)

        # Updates
        f = _form(tabs, "Updates", "New versions are published as releases on GitHub. Installing one replaces only "
                                   "the app folder; your library, queues, settings and TikTok logins are kept. If a new "
                                   "version fails to start, the previous one is restored automatically.")
        where = updater.install_dir()
        f.addRow("This version", label(f"{VERSION}" + ("" if where else "  (running from source: updates open the "
                                                                          "download page instead)")))
        self.update_repo = QLineEdit(settings.update_repo)
        self.update_repo.setPlaceholderText("owner/repository")
        f.addRow("Update repository", self.update_repo)
        self.auto_check = QCheckBox("Check for updates automatically")
        self.auto_check.setChecked(settings.auto_check_updates)
        f.addRow("", self.auto_check)
        self.auto_install = QCheckBox("Install updates by myself when the app is in the tray and no post is due soon")
        self.auto_install.setChecked(settings.auto_install_updates)
        f.addRow("", self.auto_install)
        last = updater.load_state().get("last_check")
        check_row = QHBoxLayout()
        check_btn = QPushButton("Check now")
        check_btn.setEnabled(on_check_updates is not None)
        if on_check_updates:
            check_btn.clicked.connect(lambda: (self._apply_update_fields(), on_check_updates()))
        check_row.addWidget(check_btn)
        check_row.addWidget(label("Last checked " + (dt.datetime.fromtimestamp(last).strftime("%d %b %H:%M")
                                                     if last else "never"), "muted"))
        check_row.addStretch(1)
        f.addRow("", check_row)
        if settings.skipped_version:
            unskip = QPushButton(f"Stop skipping {settings.skipped_version}")
            unskip.clicked.connect(lambda: (setattr(settings, "skipped_version", ""), unskip.hide()))
            f.addRow("", unskip)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        for i in range(tabs.count()):
            if tabs.tabText(i) == tab:
                tabs.setCurrentIndex(i)

    def _build_ready_tab(self, tabs: QTabWidget, settings: Settings, accounts: list[tuple[str, str]]) -> None:
        f = _form(tabs, "Ready to upload", "For posting by hand. Watched folders are checked every few minutes; "
                                           "new clips are scored and land on the Ready page with their caption "
                                           "ready to copy. ClipMonster's rejected clips are left out, and so is "
                                           "anything you remove from the Library.")
        self.watch_list = QListWidget()
        self.watch_list.setFixedHeight(96)
        self.watch_list.addItems([w for w in settings.watch_folders if w])
        add = QPushButton("Add folder…")
        add.clicked.connect(self._add_watch_folder)
        cm = clipmonster.output_folders()[0]
        add_cm = QPushButton("Add ClipMonster's Automation folder")
        add_cm.setToolTip(str(cm))
        add_cm.clicked.connect(lambda: self._add_watch(str(cm)))
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: [self.watch_list.takeItem(self.watch_list.row(i))
                                        for i in self.watch_list.selectedItems()])
        col = QVBoxLayout()
        col.addWidget(self.watch_list)
        row = QHBoxLayout()
        for b in (add, add_cm, remove):
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        f.addRow("Watched folders", col)
        options = [("library", "Leave them in the Library"), ("spread", "Share them out between my accounts")]
        options += [(aid, f"Queue them for {name}") for aid, name in accounts]
        self.watch_target = _combo(options, settings.watch_target)
        f.addRow("New clips", self.watch_target)
        self.watch_ai = QCheckBox("Write an AI caption for each new clip")
        self.watch_ai.setToolTip("One Claude call per clip, with the key on the AI tab. ClipMonster's hook is used "
                                 "as the caption otherwise")
        self.watch_ai.setChecked(settings.watch_ai)
        f.addRow("", self.watch_ai)
        self.remind = QCheckBox("Remind me when it's time to post a video (accounts without autopilot)")
        self.remind.setChecked(settings.remind_to_post)
        f.addRow("", self.remind)
        self.morning = QCheckBox("Each morning, tell me what's ready for each account")
        self.morning.setChecked(settings.morning_summary)
        f.addRow("", self.morning)
        self.ready_folder = QLineEdit(settings.ready_folder)
        self.ready_folder.setPlaceholderText(str(ready.default_ready_folder()))
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._pick_ready_folder)
        row = QHBoxLayout()
        row.addWidget(self.ready_folder, 1)
        row.addWidget(browse)
        f.addRow("“Copy to a folder” goes to", row)

    def _add_watch(self, path: str) -> None:
        existing = {self.watch_list.item(i).text() for i in range(self.watch_list.count())}
        if path and path not in existing:
            self.watch_list.addItem(path)

    def _add_watch_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Watch a folder for new clips")
        if folder:
            self._add_watch(folder)

    def _pick_ready_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Copy ready videos to", self.ready_folder.text())
        if folder:
            self.ready_folder.setText(folder)

    def apply(self) -> None:
        s = self.s
        for name, sl in self.weight_sliders.items():
            setattr(s.weights, name, sl.value() / 100)
        s.ideal_min_len = min(self.min_len.value(), self.max_len.value())
        s.ideal_max_len = max(self.min_len.value(), self.max_len.value())
        s.target_lufs = self.lufs.value()
        s.upload_method = self.method.currentData()
        s.browser_channel = self.channel.currentData()
        s.show_browser = self.show_browser.isChecked()
        s.allow_comments, s.allow_duet, s.allow_stitch = (self.comments.isChecked(), self.duet.isChecked(),
                                                          self.stitch.isChecked())
        s.ai_generated_label = self.ai_label.isChecked()
        s.caption_template = self.template.text().strip() or "{caption} {hashtags}"
        s.minimize_to_tray = self.tray.isChecked()
        s.start_with_windows = self.startup.isChecked()
        s.ai_api_key = self.ai_key.text().strip()
        s.ai_enabled = bool(s.ai_api_key)
        s.ai_model = self.ai_model.currentData()
        s.ai_hashtag_count = self.tag_count.value()
        s.api_client_key = self.client_key.text().strip()
        s.api_client_secret = self.client_secret.text().strip()
        s.api_redirect_port = self.port.value()
        s.api_post_mode = self.post_mode.currentData()
        s.watch_folders = [self.watch_list.item(i).text() for i in range(self.watch_list.count())]
        s.watch_target = self.watch_target.currentData()
        s.watch_ai = self.watch_ai.isChecked()
        s.remind_to_post = self.remind.isChecked()
        s.morning_summary = self.morning.isChecked()
        s.ready_folder = self.ready_folder.text().strip()
        self._apply_update_fields()

    def _apply_update_fields(self) -> None:
        s = self.s
        s.update_repo = self.update_repo.text().strip().strip("/") or Settings().update_repo
        s.auto_check_updates = self.auto_check.isChecked()
        s.auto_install_updates = self.auto_install.isChecked()
