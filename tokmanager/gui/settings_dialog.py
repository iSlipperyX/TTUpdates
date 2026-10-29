"""Settings dialog: scoring weights, posting defaults, AI, the Live page, Google Drive, updates."""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import fields
from typing import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QProgressDialog,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import clipmonster, diagnostics, phone, ready, updater
from ..config import VERSION, Settings, Weights, app_dir
from ..gdrive import SETUP_HELP as DRIVE_HELP
from ..scoring import PART_LABELS
from . import theme
from .widgets import label


def _combo(options: list[tuple[str, str]], current: str) -> QComboBox:
    c = QComboBox()
    for value, text in options:
        c.addItem(text, value)
    c.setCurrentIndex(max(0, c.findData(current)))
    return c


def _form(tab: QTabWidget, title: str, intro: str = "") -> QFormLayout:
    """A tab holding a form; it scrolls when the window is smaller than the form, instead of squashing it."""
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
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(w)
    tab.addTab(scroll, title)
    return form


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None, on_check_updates: Callable[[], None] | None = None,
                 accounts: list[tuple[str, str]] | None = None, tab: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(760, 620)
        self.s = settings
        self.setup_requested = False  # "Run setup again" was pressed: the main window opens it after saving
        self.drive_check_requested = False  # "Save and check now" on the Google Drive tab
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
        f = _form(tabs, "AI", "Optional. The AI watches frames from each clip, predicts how it will do on "
                              "TikTok and writes a caption and hashtags; ✨ Hashtags writes just the hashtags "
                              "(quicker and cheaper). Each one is a single API call. Use Claude (an Anthropic "
                              "API key from console.anthropic.com) or Gemini (a free Gemini API key from "
                              "aistudio.google.com/apikey; Google may use what free keys send to improve its "
                              "products).")
        from ..clipmonster import ai_key as clipmonster_key
        from ..gemini import MODELS as GEMINI_MODELS

        self.ai_provider = _combo([("claude", "Claude (Anthropic)"), ("gemini", "Gemini (Google)")],
                                  settings.ai_provider)
        f.addRow("AI to use", self.ai_provider)
        self.ai_fallback = QCheckBox("If it fails, try the other AI")
        self.ai_fallback.setToolTip("When the AI above can't answer (no key, a rejected key, a used-up quota, busy "
                                    "servers), the other AI is asked instead, if it has a key")
        self.ai_fallback.setChecked(settings.ai_fallback)
        f.addRow("", self.ai_fallback)
        shared = [n for n, p in (("Claude", "claude"), ("Gemini", "gemini")) if clipmonster_key(p)]
        self.ai_key = QLineEdit(settings.ai_api_key)
        self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_key.setPlaceholderText("leave empty to use ClipMonster's key" if "Claude" in shared else "sk-ant-…")
        f.addRow("Anthropic API key (Claude)", self.ai_key)
        self.ai_model = _combo([("claude-opus-5-5", "Claude Opus 5.5 (best)"), ("claude-sonnet-5-5", "Claude Sonnet 5.5 (cheaper)"),
                                ("claude-haiku-5-5", "Claude Haiku 5.5 (cheapest)")], settings.ai_model)
        f.addRow("Claude model", self.ai_model)
        self.gemini_key = QLineEdit(settings.gemini_api_key)
        self.gemini_key.setEchoMode(QLineEdit.Password)
        self.gemini_key.setPlaceholderText("leave empty to use ClipMonster's key" if "Gemini" in shared else "AIza…")
        f.addRow("Gemini API key", self.gemini_key)
        self.gemini_model = _combo(list(GEMINI_MODELS), settings.gemini_model)
        f.addRow("Gemini model", self.gemini_model)
        if shared:
            f.addRow("", label(f"ClipMonster on this PC has a {' and a '.join(shared)} key, so the AI works without "
                               "one here. A key typed above is used instead.", "muted", wrap=True))
        test_row = QHBoxLayout()
        test = QPushButton("Test keys")
        test.setToolTip("Checks each key, and that it can use the model picked for it. Uses no tokens.")
        self.key_result = label("", "muted", wrap=True)
        test.clicked.connect(self._test_keys)
        test_row.addWidget(test)
        test_row.addWidget(self.key_result, 1)
        f.addRow("", test_row)
        f.addRow("Niche / audience", label("Set for each account in Manage accounts, so captions fit "
                                           "each account's audience.", "muted", wrap=True))
        self.tag_count = QSpinBox(minimum=0, maximum=10, value=settings.ai_hashtag_count)
        f.addRow("Hashtags per video", self.tag_count)

        # Live
        self._build_live_tab(tabs, settings)

        # Google Drive
        self._build_drive_tab(tabs, settings)

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
        ustate = updater.load_state()
        last, result = ustate.get("last_check"), ustate.get("result") or {}
        check_row = QHBoxLayout()
        check_btn = QPushButton("Check now")
        check_btn.setEnabled(on_check_updates is not None)
        if on_check_updates:
            check_btn.clicked.connect(lambda: (self._apply_update_fields(), on_check_updates()))
        check_row.addWidget(check_btn)
        when = dt.datetime.fromtimestamp(result.get("at") or last).strftime("%d %b %H:%M") if (result or last) else ""
        outcome = label(f"Last checked {when}: {result['text']}" if result.get("text") else
                        f"Last checked {when}" if when else "Not checked yet", "muted", wrap=True)
        if result.get("kind") == "error":
            outcome.setStyleSheet(f"color:{theme.BAD};")
        check_row.addWidget(outcome, 1)
        check_row.addStretch(1)
        f.addRow("", check_row)
        if settings.skipped_version:
            unskip = QPushButton(f"Stop skipping {settings.skipped_version}")
            unskip.clicked.connect(lambda: (setattr(settings, "skipped_version", ""), unskip.hide()))
            f.addRow("", unskip)
        help_row = QHBoxLayout()
        copy_diag = QPushButton("Copy diagnostics")
        copy_diag.setToolTip("Copies the version, setup, recent log and latest error report (no keys or logins), "
                             "for pasting when asking for help")
        copied = label("", "muted")
        copy_diag.clicked.connect(lambda: (QApplication.clipboard().setText(diagnostics.report()),
                                           copied.setText("Copied. Paste it into your message.")))
        open_logs = QPushButton("Open log folder")
        open_logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_dir()))))
        help_row.addWidget(copy_diag)
        help_row.addWidget(open_logs)
        help_row.addWidget(copied)
        help_row.addStretch(1)
        f.addRow("Problems?", help_row)

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
        add_cm = QPushButton("Add ClipMonster's folder")
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
        self.watch_ai.setToolTip("One AI call per clip, with the key on the AI tab. ClipMonster's hook is used "
                                 "as the caption otherwise")
        self.watch_ai.setChecked(settings.watch_ai)
        f.addRow("", self.watch_ai)
        self.watch_tags = QCheckBox("Write AI hashtags for each new clip")
        self.watch_tags.setToolTip("A quick text-only AI call per clip (much cheaper than an AI caption), with "
                                   "the key on the AI tab. Clips that already have hashtags keep them")
        self.watch_tags.setChecked(settings.watch_hashtags)
        f.addRow("", self.watch_tags)
        self.remind = QCheckBox("Remind me when it's time to post a video (accounts I post to by hand)")
        self.remind.setChecked(settings.remind_to_post)
        f.addRow("", self.remind)
        self.morning = QCheckBox("Each morning, tell me what's ready for each account")
        self.morning.setChecked(settings.morning_summary)
        f.addRow("", self.morning)
        self.share = QCheckBox("Tell ClipMonster how its clips do, so it picks and writes hooks like the hits")
        self.share.setToolTip("After each stats refresh, views of clips that are 2+ days old go to "
                              "ClipMonster's results.json. Nothing leaves this PC")
        self.share.setChecked(settings.share_results)
        f.addRow("", self.share)
        self.ready_folder = QLineEdit(settings.ready_folder)
        self.ready_folder.setPlaceholderText(str(ready.default_ready_folder()))
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._pick_ready_folder)
        row = QHBoxLayout()
        row.addWidget(self.ready_folder, 1)
        row.addWidget(browse)
        f.addRow("“Copy to a folder” goes to", row)

        self.phone_folder = QLineEdit(settings.phone_folder)
        self.phone_folder.setPlaceholderText("Off")
        self.phone_folder.setToolTip("The next 24 hours' clips for the accounts you post to by hand wait here, in posting "
                                     "order, each with a .txt of its caption. Posted ones are taken out by themselves")
        row = QHBoxLayout()
        row.addWidget(self.phone_folder, 1)
        onedrive = phone.suggested_folder()
        if onedrive is not None:
            use = QPushButton("Use OneDrive")
            use.setToolTip(str(onedrive))
            use.clicked.connect(lambda: self.phone_folder.setText(str(onedrive)))
            row.addWidget(use)
        pick = QPushButton("…")
        pick.setFixedWidth(34)
        pick.clicked.connect(self._pick_phone_folder)
        off = QPushButton("Off")
        off.clicked.connect(self.phone_folder.clear)
        row.addWidget(pick)
        row.addWidget(off)
        f.addRow("Phone folder", row)
        f.addRow("", label("Pick a folder that syncs to your phone (OneDrive, say). Open it in the OneDrive app, "
                           "save a clip, paste its caption into TikTok, then delete it there or mark it posted "
                           "here. Clips you delete there aren't copied again.", "muted", wrap=True))
        again = QPushButton("Run first-run setup again")
        again.setToolTip("Account and login, where clips come from, how you post and the phone folder, one step "
                         "at a time")
        again.clicked.connect(self._run_setup)
        f.addRow("", again)

    def _build_live_tab(self, tabs: QTabWidget, settings: Settings) -> None:
        from .. import live

        f = _form(tabs, "Live", "The Live page reads each logged-in account's TikTok profile in the background, "
                                "in its own browser login with the window hidden: new views, likes and "
                                "followers, and the clips you post from your phone.")
        self.live_interval = _combo([(str(secs), text.capitalize()) for secs, text in live.INTERVALS],
                                    str(settings.live_interval))
        self.live_interval.setToolTip("How often each account you watch is read. Each reading opens the browser "
                                      "for a few seconds, after any post that's due.")
        f.addRow("Read accounts", self.live_interval)
        self.live_alerts = QCheckBox("Notify me when a video takes off, hits a milestone, or a queued clip shows "
                                     "up on TikTok")
        self.live_alerts.setChecked(settings.live_alerts)
        f.addRow("", self.live_alerts)
        self.live_show = QCheckBox("Show the browser while reading (only if TikTok keeps asking for a captcha)")
        self.live_show.setChecked(settings.live_show_browser)
        f.addRow("", self.live_show)

    def _build_drive_tab(self, tabs: QTabWidget, settings: Settings) -> None:
        from .. import drive_sync

        f = _form(tabs, "Google Drive", "ClipMonster can upload every clip it finishes to your Google Drive (its "
                                        "Settings → Upload). Turn this on and TikTok Manager downloads the clips "
                                        "it hasn't got from there every few minutes, with their titles, scores "
                                        "and niches, and they come in like clips from a watched folder. Handy on "
                                        "a PC that isn't the one ClipMonster runs on, or after ClipMonster's "
                                        "Clean up cleared its copies.")
        self.drive_on = QCheckBox("Bring in the clips ClipMonster uploads to Google Drive")
        self.drive_on.setChecked(settings.drive_on)
        f.addRow("", self.drive_on)
        cm_id, _ = clipmonster.drive_client()
        self.drive_id = QLineEdit(settings.drive_client_id)
        self.drive_id.setPlaceholderText("leave empty to use ClipMonster's" if cm_id else
                                         "….apps.googleusercontent.com")
        f.addRow("Google OAuth client ID", self.drive_id)
        self.drive_secret = QLineEdit(settings.drive_client_secret)
        self.drive_secret.setEchoMode(QLineEdit.Password)
        self.drive_secret.setPlaceholderText("leave empty to use ClipMonster's" if cm_id else "GOCSPX-…")
        f.addRow("Google OAuth client secret", self.drive_secret)
        link = f"style='color:{theme.ACCENT_2}'"
        how = label("ClipMonster on this PC uploads to Google Drive, so TikTok Manager uses the same Google "
                    "sign-in details: leave these empty and sign in below." if cm_id else
                    "Paste the client ID and secret from ClipMonster → Settings → Upload, on the PC ClipMonster "
                    "runs on. They must be the same ones: Google only lets TikTok Manager see ClipMonster's clips "
                    f"if both use the same Google Cloud project. <a href='{DRIVE_HELP}' {link}>How to</a>",
                    "muted", wrap=True)
        how.setTextFormat(Qt.RichText)
        how.setOpenExternalLinks(True)
        f.addRow("", how)
        self.drive_status = label("", "muted", wrap=True)
        sign_in = QPushButton("Sign in to Google Drive…")
        sign_in.setToolTip("Opens Google's sign-in page in your browser. TikTok Manager only gets to see the files "
                           "ClipMonster (and TikTok Manager) put in your Drive, nothing else")
        sign_in.clicked.connect(self._drive_sign_in)
        self.drive_out = QPushButton("Sign out")
        self.drive_out.setToolTip("Forgets the sign-in on this PC. ClipMonster stays signed in")
        self.drive_out.clicked.connect(self._drive_sign_out)
        row = QHBoxLayout()
        row.addWidget(sign_in)
        row.addWidget(self.drive_out)
        row.addWidget(self.drive_status, 1)
        f.addRow("Account", row)
        self.drive_folder = QLineEdit(settings.drive_folder)
        self.drive_folder.setPlaceholderText(clipmonster.drive_folder() or "ClipMonster")
        self.drive_folder.setToolTip("The folder at the top of your Drive that ClipMonster uploads to (ClipMonster → "
                                     "Settings → Upload → Folder in your Drive)")
        f.addRow("Folder in your Drive", self.drive_folder)
        self.drive_inbox = QLineEdit(settings.drive_inbox)
        self.drive_inbox.setPlaceholderText(str(drive_sync.default_inbox()))
        self.drive_inbox.setToolTip("Clips are downloaded here, in the same folders as in Drive. Clean up posted "
                                    "videos clears them again once they're posted")
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._pick_drive_inbox)
        row = QHBoxLayout()
        row.addWidget(self.drive_inbox, 1)
        row.addWidget(browse)
        f.addRow("Download clips to", row)
        self.drive_days = QSpinBox(minimum=0, maximum=365, value=max(0, settings.drive_days), suffix=" days")
        self.drive_days.setSpecialValueText("Any time")
        self.drive_days.setToolTip("Clips uploaded before this stay in Drive, so connecting to a Drive full of old "
                                   "clips doesn't fill this PC with them")
        f.addRow("Only clips uploaded in the last", self.drive_days)
        f.addRow("", label("Clips already in TikTok Manager aren't downloaded again, and nor are clips you removed "
                           "or cleaned up. Each clip downloaded is marked in Drive with this PC's name, so TikTok "
                           "Manager on another PC leaves it alone and nothing gets posted twice.", "muted",
                           wrap=True))
        last = drive_sync.last()
        self.drive_last = label("", "muted", wrap=True)
        if last.get("at"):
            when = dt.datetime.fromtimestamp(last["at"]).strftime("%d %b %H:%M")
            self.drive_last.setText(f"Last checked {when}: {last.get('text', '')}")
            if last.get("problem"):
                self.drive_last.setStyleSheet(f"color:{theme.WARN};")
        check = QPushButton("Save and check now")
        check.clicked.connect(self._drive_check_now)
        row = QHBoxLayout()
        row.addWidget(check)
        row.addWidget(self.drive_last, 1)
        f.addRow("", row)
        self.drive_id.textChanged.connect(self._drive_show_account)
        self.drive_secret.textChanged.connect(self._drive_show_account)
        self._drive_show_account()

    def _drive_client(self) -> tuple[str, str]:
        """The OAuth client as typed (not yet saved), else ClipMonster's."""
        from copy import copy

        from .. import drive_sync

        t = copy(self.s)
        t.drive_client_id, t.drive_client_secret = self.drive_id.text().strip(), self.drive_secret.text().strip()
        cid, secret, _ = drive_sync.client(t)
        return cid, secret

    def _drive_show_account(self) -> None:
        from .. import gdrive

        cid, secret = self._drive_client()
        account = gdrive.load_account(cid) if cid else None
        if account is not None:
            self.drive_status.setText(f"✓ Signed in{' as ' + account.email if account.email else ''}")
            self.drive_status.setStyleSheet(f"color:{theme.GOOD};")
        else:
            self.drive_status.setText("Not signed in" if cid and secret else
                                      "Needs the client ID and secret first")
            self.drive_status.setStyleSheet("")
        self.drive_out.setEnabled(account is not None)

    def _drive_sign_in(self) -> None:
        """Google's sign-in page in the browser; this waits (with Cancel) on a thread of its own."""
        from .. import gdrive

        cid, secret = self._drive_client()
        if not cid or not secret:
            self.drive_status.setText("Paste the client ID and secret first (from ClipMonster → Settings → Upload).")
            self.drive_status.setStyleSheet(f"color:{theme.BAD};")
            return
        box = QProgressDialog("Google's sign-in page is open in your browser. Pick your account, allow access, "
                              "then come back here.", "Cancel", 0, 0, self)
        box.setWindowTitle("Sign in to Google Drive")
        box.setWindowModality(Qt.WindowModal)
        box.setMinimumDuration(0)
        stop = threading.Event()
        box.canceled.connect(stop.set)
        result: dict = {}

        def work() -> None:
            try:
                result["account"] = gdrive.sign_in(cid, secret, cancelled=stop.is_set)
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=work, name="google-sign-in", daemon=True)
        timer = QTimer(self, interval=150)

        def check() -> None:
            if thread.is_alive():
                return
            timer.stop()
            box.reset()
            box.hide()
            box.deleteLater()
            self._drive_signed_in(result)

        timer.timeout.connect(check)
        thread.start()
        timer.start()
        box.show()
        self._drive_waiting = (thread, stop)

    def _drive_signed_in(self, result: dict) -> None:
        self._drive_waiting = None
        err = result.get("error")
        if err is not None:
            if getattr(err, "kind", "") == "cancelled":
                self._drive_show_account()
                return
            self.drive_status.setText(f"✗ {err}")
            self.drive_status.setStyleSheet(f"color:{theme.BAD};")
            return
        self._drive_show_account()
        if not self.drive_on.isChecked():
            self.drive_on.setChecked(True)  # signing in means you want the clips

    def _drive_sign_out(self) -> None:
        from .. import gdrive

        gdrive.sign_out()
        self._drive_show_account()

    def _drive_check_now(self) -> None:
        self.drive_check_requested = True
        self.accept()

    def _pick_drive_inbox(self) -> None:
        from .. import drive_sync

        folder = QFileDialog.getExistingDirectory(self, "Download clips from Google Drive to",
                                                  self.drive_inbox.text() or str(drive_sync.default_inbox()))
        if folder:
            self.drive_inbox.setText(folder)

    def done(self, result: int) -> None:
        """Closing the window gives up on a sign-in still waiting for Google's page."""
        waiting = getattr(self, "_drive_waiting", None)
        if waiting:
            waiting[1].set()
        super().done(result)

    def _test_keys(self) -> None:
        """Check the keys as typed (not yet saved): the chosen AI first, then the other one if it has a key."""
        from copy import copy

        from .. import ai

        t = copy(self.s)
        t.ai_provider, t.ai_model, t.gemini_model = (self.ai_provider.currentData(), self.ai_model.currentData(),
                                                     self.gemini_model.currentData())
        t.ai_api_key, t.gemini_api_key = self.ai_key.text().strip(), self.gemini_key.text().strip()
        self.key_result.setText("Checking…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        lines, ok = [], True
        try:
            first = ai.chosen(t)
            for provider in [first] + [p for p in ai.NAMES if p != first and ai.api_key(t, p)]:
                try:
                    lines.append(f"✓ {ai.check_key(t, provider)}")
                except Exception as exc:
                    lines.append(f"✗ {ai.name(provider)}: {exc}")
                    ok = ok and provider != first
        finally:
            QApplication.restoreOverrideCursor()
        self.key_result.setText("\n".join(lines))
        self.key_result.setStyleSheet(f"color:{theme.GOOD if ok else theme.BAD};")

    def _add_watch(self, path: str) -> None:
        existing = {self.watch_list.item(i).text() for i in range(self.watch_list.count())}
        if path and path not in existing:
            self.watch_list.addItem(path)

    def _add_watch_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Watch a folder for new clips")
        if folder:
            self._add_watch(folder)

    def _pick_phone_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Keep the next clips for my phone in",
                                                  self.phone_folder.text() or str(phone.onedrive() or ""))
        if folder:
            self.phone_folder.setText(folder)

    def _run_setup(self) -> None:
        self.setup_requested = True
        self.accept()

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
        s.browser_channel = self.channel.currentData()
        s.show_browser = self.show_browser.isChecked()
        s.allow_comments, s.allow_duet, s.allow_stitch = (self.comments.isChecked(), self.duet.isChecked(),
                                                          self.stitch.isChecked())
        s.ai_generated_label = self.ai_label.isChecked()
        s.caption_template = self.template.text().strip() or "{caption} {hashtags}"
        s.minimize_to_tray = self.tray.isChecked()
        s.start_with_windows = self.startup.isChecked()
        s.ai_provider = self.ai_provider.currentData()
        s.ai_fallback = self.ai_fallback.isChecked()
        s.ai_api_key = self.ai_key.text().strip()
        s.ai_model = self.ai_model.currentData()
        s.gemini_api_key = self.gemini_key.text().strip()
        s.gemini_model = self.gemini_model.currentData()
        s.ai_hashtag_count = self.tag_count.value()
        s.live_interval = int(self.live_interval.currentData())
        s.live_alerts = self.live_alerts.isChecked()
        s.live_show_browser = self.live_show.isChecked()
        s.watch_folders = [self.watch_list.item(i).text() for i in range(self.watch_list.count())]
        s.watch_target = self.watch_target.currentData()
        s.watch_ai = self.watch_ai.isChecked()
        s.watch_hashtags = self.watch_tags.isChecked()
        s.remind_to_post = self.remind.isChecked()
        s.morning_summary = self.morning.isChecked()
        s.share_results = self.share.isChecked()
        s.ready_folder = self.ready_folder.text().strip()
        s.phone_folder = self.phone_folder.text().strip()
        s.drive_on = self.drive_on.isChecked()
        s.drive_client_id = self.drive_id.text().strip()
        s.drive_client_secret = self.drive_secret.text().strip()
        s.drive_folder = self.drive_folder.text().strip().strip("/\\")
        s.drive_inbox = self.drive_inbox.text().strip()
        s.drive_days = self.drive_days.value()
        self._apply_update_fields()

    def _apply_update_fields(self) -> None:
        s = self.s
        s.update_repo = self.update_repo.text().strip().strip("/") or Settings().update_repo
        s.auto_check_updates = self.auto_check.isChecked()
        s.auto_install_updates = self.auto_install.isChecked()
