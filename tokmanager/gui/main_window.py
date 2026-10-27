"""Main window: navigation, account switcher, activity log, tray icon, autopilot, and the TikTok actions."""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QStackedWidget, QSystemTrayIcon, QTextBrowser, QVBoxLayout,
                               QWidget)

from .. import jobs, ready, safefile, updater
from ..accounts import Account
from ..config import APP_NAME, VERSION
from ..media import VIDEO_EXTS
from . import theme
from .account import AccountPage
from .accounts_dialog import AccountsDialog, account_status
from .library import LibraryPage
from .queue_page import QueuePage
from .ready_page import ReadyPage
from .settings_dialog import SettingsDialog
from .state import AppState, Job
from .widgets import account_badge

AUTOPILOT_CHECK_MS = 30_000
WATCH_CHECK_MS = 3 * 60_000
FIRST_WATCH_CHECK_MS = 8_000
UPDATE_CHECK_MS = 6 * 3600 * 1000
FIRST_UPDATE_CHECK_MS = 20_000
AUTO_INSTALL_QUIET_MINUTES = 20  # don't restart for an update if a post is due this soon
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_start_with_windows(on: bool) -> None:
    """Add/remove a per-user startup entry that opens the app hidden in the tray."""
    if os.name != "nt":
        return
    import winreg

    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}" --tray'
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        cmd = f'"{pyw if pyw.exists() else sys.executable}" -m tokmanager --tray'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, "TikTokManager", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "TikTokManager")
            except FileNotFoundError:
                pass


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, start_hidden: bool = False):
        super().__init__()
        self.state = state
        self._quitting = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1280, 820)
        self.setMinimumSize(1000, 640)
        self.setAcceptDrops(True)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # Header
        header = QWidget()
        header.setObjectName("header")
        header.setStyleSheet(f"QWidget#header {{ background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER}; }}")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 8, 16, 8)
        icon = QLabel()
        icon.setPixmap(theme.app_icon_pixmap(30))
        hl.addWidget(icon)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        brand.setToolTip(f"Version {VERSION}")
        hl.addWidget(brand)
        hl.addSpacing(14)
        self.account_btn = QPushButton()
        self.account_btn.setObjectName("accountSwitch")
        self.account_btn.setIconSize(QSize(22, 22))
        self.account_btn.setMinimumWidth(150)
        self.account_menu = QMenu(self)
        self.account_menu.aboutToShow.connect(self._fill_account_menu)
        self.account_btn.setMenu(self.account_menu)
        hl.addWidget(self.account_btn)
        hl.addSpacing(10)
        self.nav = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.library = LibraryPage(state)
        self.ready = ReadyPage(state)
        self.queue = QueuePage(state)
        self.account = AccountPage(state)
        self.nav_buttons: list[QPushButton] = []
        pages = (("Library", self.library), ("Ready", self.ready), ("Queue", self.queue), ("Account", self.account))
        for i, (text, page) in enumerate(pages):
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            hl.addWidget(b)
            self.stack.addWidget(page)
        self.nav.idClicked.connect(self.stack.setCurrentIndex)
        self.nav_buttons[0].setChecked(True)
        hl.addStretch(1)
        self.job_label = QLabel("")
        self.job_label.setStyleSheet(f"color:{theme.MUTED};")
        self.job_bar = QProgressBar()
        self.job_bar.setRange(0, 0)
        self.job_bar.setFixedWidth(110)
        self.job_bar.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel_jobs)
        settings_btn = QPushButton("⚙ Settings")
        settings_btn.clicked.connect(self.open_settings)
        for w in (self.job_label, self.job_bar, self.cancel_btn, settings_btn):
            hl.addWidget(w)
        root.addWidget(header)
        self.update_banner = self._build_update_banner()
        root.addWidget(self.update_banner)
        root.addWidget(self.stack, 1)

        # Activity log
        foot = QWidget()
        foot.setObjectName("footer")
        foot.setStyleSheet(f"QWidget#footer {{ background:{theme.PANEL}; border-top:1px solid {theme.BORDER}; }}")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(12, 4, 12, 6)
        fl.setSpacing(4)
        row = QHBoxLayout()
        self.log_toggle = QPushButton("▸ Activity")
        self.log_toggle.setStyleSheet("border:none; background:transparent; font-weight:600;")
        self.log_toggle.clicked.connect(self._toggle_log)
        self.last_line = QLabel("")
        self.last_line.setStyleSheet(f"color:{theme.MUTED};")
        row.addWidget(self.log_toggle)
        row.addWidget(self.last_line, 1)
        fl.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFixedHeight(150)
        self.log_view.hide()
        fl.addWidget(self.log_view)
        root.addWidget(foot)

        # Wiring
        state.message.connect(self._log)
        state.jobs_changed.connect(self._jobs_changed)
        self.queue.post_requested.connect(self.post)
        self.ready.edit_requested.connect(self._edit_in_queue)
        self.ready.import_requested.connect(lambda: self.watch_tick(manual=True))
        self.ready.settings_requested.connect(lambda: self.open_settings("Ready to upload"))
        self.account.login_requested.connect(self.login)
        self.account.logout_requested.connect(self.logout)
        self.account.refresh_requested.connect(self.refresh_account)
        self.account.check_requested.connect(lambda: self.check_posting(self.state.active, full=True))
        self.account.requeue_requested.connect(self.requeue)
        self.account.manage_requested.connect(self.manage_accounts)
        state.account_switched.connect(self._account_switched)
        state.account_changed.connect(self._update_account_button)
        state.library_changed.connect(self._update_account_button)
        for n in range(1, 10):  # Ctrl+1…9 shows account 1…9
            QShortcut(QKeySequence(f"Ctrl+{n}"), self, activated=lambda n=n: self._switch_to_number(n))
        self._warned_cant_post: set[str] = set()
        self._update_account_button()

        self._make_tray()
        self.autopilot_timer = QTimer(self, interval=AUTOPILOT_CHECK_MS, timeout=self.autopilot_tick)
        self.autopilot_timer.start()
        # Keep "Today 15:00 (due)" style labels current.
        self.clock = QTimer(self, interval=60_000, timeout=self.queue.refresh)
        self.clock.start()
        QTimer.singleShot(3000, self.autopilot_tick)
        # Watched folders: new clips from ClipMonster (or anywhere) come in by themselves.
        self.watch_timer = QTimer(self, interval=WATCH_CHECK_MS, timeout=self.watch_tick)
        self.watch_timer.start()
        QTimer.singleShot(FIRST_WATCH_CHECK_MS, self.watch_tick)
        # Updates: a first check shortly after start, then every few hours.
        self.pending_release: updater.Release | None = None
        self._staged: tuple[Path, updater.Release] | None = None
        self.update_timer = QTimer(self, interval=UPDATE_CHECK_MS, timeout=self.check_for_updates)
        self.update_timer.start()
        QTimer.singleShot(FIRST_UPDATE_CHECK_MS, self.check_for_updates)
        self.auto_install_timer = QTimer(self, interval=60_000, timeout=self._maybe_auto_install)

        unscored = [i.id for i in state.store.all() if not i.score and not i.analyze_error and i.exists()]
        if unscored:
            QTimer.singleShot(500, lambda: self.library.score(unscored))
        n_acc = len(state.accounts)
        self._log(f"{APP_NAME} {VERSION} ready. {len(state.store.items)} videos in the library, "
                  f"{len(state.store.queue())} queued" + (f" across {n_acc} accounts." if n_acc > 1 else "."))
        if state.accounts.migrated and state.store.items:
            self._log("Your existing queue, schedule, stats and TikTok login are now the first account. "
                      "Add more with the account button at the top left.")
        if start_hidden and self.tray:
            self.hide()
        else:
            self.show()
        if safefile.notices:
            QTimer.singleShot(600, self._show_recovered)

    def _show_recovered(self) -> None:
        """A saved file was damaged and came back from a backup (or couldn't): say so once."""
        lines, safefile.notices[:] = list(safefile.notices), []
        for line in lines:
            self._log("⚠ " + line)
        if self.isVisible():
            QMessageBox.warning(self, "A saved file was damaged", "\n\n".join(lines))
        elif self.tray:
            self.tray.showMessage("A saved file was damaged", lines[0], QSystemTrayIcon.Warning, 15000)

    # --- log / jobs -------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {msg}")
        self.last_line.setText(msg)

    def _toggle_log(self) -> None:
        vis = not self.log_view.isVisible()
        self.log_view.setVisible(vis)
        self.log_toggle.setText(("▾" if vis else "▸") + " Activity")

    def _jobs_changed(self) -> None:
        jobs_ = [j for j in self.state.jobs if j.kind not in ("update", "watch")]  # background checks stay silent
        if not jobs_:
            self.job_label.setText("")
            self.job_bar.hide()
            self.cancel_btn.hide()
            return
        kinds = [j.kind for j in jobs_]
        if "post" in kinds:
            text = next(j.label for j in jobs_ if j.kind == "post")
        elif "login" in kinds or "account" in kinds:
            text = next(j.label for j in jobs_ if j.kind in ("login", "account"))
        else:
            n = kinds.count("analyze")
            text = f"Scoring {n} video{'s' * (n != 1)}…" if n else f"{jobs_[0].label}…"
        self.job_label.setText(text)
        self.job_bar.show()
        self.cancel_btn.show()

    def _cancel_jobs(self) -> None:
        self.state.cancel_all()
        self._log("Cancelling…")

    # --- TikTok actions -------------------------------------------------------------

    def post(self, item_id: str) -> None:
        st = self.state
        item = st.store.get(item_id)
        if not item or item.status in ("uploading", "posted"):
            return
        if any(getattr(j, "item_id", None) == item_id for j in st.jobs if j.kind == "post"):
            return
        account = st.accounts.get(item.account)
        if account is None:
            QMessageBox.warning(self, "Not queued", f"“{item.name}” isn't in an account's queue.")
            return
        if not item.exists():
            QMessageBox.warning(self, "Missing file", f"The video file is missing:\n{item.path}")
            return
        job = Job(f"Posting “{item.name}” to {account.handle}",
                  lambda j: jobs.post_item(st.store, item_id, st.settings, account, j.log, j.cancelled, j.progress),
                  kind="post")
        job.item_id = item_id
        job.account_id = account.id
        job.signals.progress.connect(lambda m: self.job_label.setText(f"{account.handle}: {m}"))

        def ok(it):
            self._log(f"✔ Posted “{it.name}” to {account.handle}.")
            self._notify(f"Posted to {account.handle}", it.name)
            st.library_changed.emit()

        def fail(msg):
            if "log in" in msg.lower() and st.settings.upload_method == "browser":
                account.logged_in = False
                st.save_accounts()
                st.account_changed.emit()
            st.library_changed.emit()
            if msg != "Cancelled":
                self._notify(f"Couldn't post to {account.handle}", msg, error=True)

        self._log(f"Posting “{item.name}” to {account.handle}…")
        st.run(job, pool="tiktok", on_success=ok, on_failure=fail)
        st.library_changed.emit()

    def autopilot_tick(self) -> None:
        st = self.state
        self._ready_tick()
        if not any(a.autopilot for a in st.accounts) or st.busy("post") or st.busy("login"):
            return
        skipped: list[str] = []
        pick = jobs.autopilot_pick(st.store, st.accounts, st.settings, log=skipped.append)
        for msg in skipped:  # due videos whose file is gone were marked failed
            self._log(msg)
        if skipped:
            st.save_soon()
            st.library_changed.emit()
        self._warn_cant_post()
        if pick:
            account, item = pick
            self._log(f"Autopilot: “{item.name}” is due on {account.handle}.")
            self.post(item.id)
        elif not st.busy("check"):
            account = jobs.upload_check_due(st.store, st.accounts, st.settings)
            if account:
                self.check_posting(account, full=False, auto=True)

    # --- ready to upload (posting by hand) ----------------------------------------------

    def _ready_tick(self) -> None:
        """Reminders when a video's time comes (accounts without autopilot) and the morning summary."""
        st = self.state
        s = st.settings
        if s.remind_to_post:
            for a, item in ready.reminders(st.store, st.accounts, s):
                self._log(f"⏰ Time to post “{item.name}” on {a.handle}. It's at the top of the Ready page.")
                self._notify(f"Time to post on {a.handle}", item.name, always=True)
                st.save_soon()
        day = ready.summary_due(s, st.accounts)
        if day:
            s.last_summary_day = day
            st.save_settings()
            text = ready.summary(st.store, st.accounts)
            if text:
                self._log(f"Ready to post today: {text}.")
                self._notify("Ready to post today", text, always=True)

    def watch_tick(self, manual: bool = False) -> None:
        """Bring in new clips from the watched folders (on a worker thread), then score and queue them."""
        st = self.state
        s = st.settings
        folders = [f for f in s.watch_folders if f]
        if not folders or st.busy("watch"):
            return

        def found(items) -> None:
            if not items:
                if manual:
                    self._log("No new clips in your watched folders.")
                return
            got = ready.assign(st.store, st.accounts, items, s.watch_target)
            st.save_soon()
            st.library_changed.emit()
            where = ", ".join(f"{n} for {st.accounts.label_for(aid)}" for aid, n in got.items())
            msg = f"{len(items)} new clip{'s' * (len(items) != 1)} from your watched folders" + \
                  (f": {where}." if where else ", in the Library.")
            self._log(msg)
            self._notify("New clips ready", msg)
            self.library.score([i.id for i in items])
            if s.watch_ai and s.ai_api_key:
                for it in items:
                    account = st.account_for(it)
                    job = Job(f"AI caption for {it.name}",
                              lambda j, i=it.id, a=account: jobs.ai_item(st.store, i, s, j.log, account=a), kind="ai")
                    st.run(job, on_success=lambda _it: st.library_changed.emit())

        st.run(Job("Checking watched folders", lambda j: ready.import_watched(st.store, folders), kind="watch"),
               on_success=found, quiet=not manual)

    def _edit_in_queue(self, item_id: str) -> None:
        self.nav_buttons[2].click()
        self.queue.select_item(item_id)

    def _warn_cant_post(self) -> None:
        """Once per run, say so when autopilot is on for an account that isn't logged in."""
        st = self.state
        for a in st.accounts:
            if not a.autopilot or jobs.can_autopost(a, st.settings) or a.id in self._warned_cant_post:
                continue
            if any(i.status == "queued" and i.planned_at and i.planned_at <= time.time() for i in st.store.queue(a.id)):
                self._warned_cant_post.add(a.id)
                msg = f"Autopilot is on for {a.handle}, but it isn't logged in. Log in on the Account page."
                self._log(f"⚠ {msg}")
                self._notify("Autopilot can't post", msg, error=True)

    def login(self) -> None:
        st = self.state
        s = st.settings
        a = st.active
        if st.account_busy(a.id, ("login",)):
            return
        result: dict = {}

        def work(j: Job):
            if s.upload_method == "api":
                from ..tiktok.api import TikTokAPI

                api = TikTokAPI(s, a.tokens_path(), j.log)
                api.connect(cancelled=j.cancelled)
                name = api.profile().username
            else:
                from ..tiktok.browser import TikTokBrowser

                with TikTokBrowser(s, a.browser_dir(), j.log, headless=False, cancelled=j.cancelled) as b:
                    if not b.login():
                        raise RuntimeError("Login wasn't completed.")
                    a.logged_in = True
                    name = b.whoami()
            if name:
                if a.username and a.username.lower() != name.lower():
                    j.log(f"⚠ {a.label} was @{a.username}; it's now logged in as @{name}.")
                a.username = name
                j.log(f"Logged in as @{name}.")
                other = st.accounts.find_username(name, exclude=a.id)
                if other:
                    result["duplicate"] = other.label
            self._warned_cant_post.discard(a.id)
            jobs.refresh_account(st.store, s, a, j.log, j.cancelled)

        def finished(_=None):
            st.save_accounts()
            st.accounts_edited()
            if result.get("duplicate"):
                QMessageBox.warning(self, "Same TikTok account twice",
                                    f"@{a.username} is also added as “{result['duplicate']}”. Both entries would post "
                                    "to the same TikTok account. Remove one in Manage accounts, or log this one in "
                                    "to a different TikTok account.")

        job = Job(f"Logging {a.label} in to TikTok", work, kind="login")
        job.account_id = a.id
        st.run(job, pool="tiktok", on_success=finished, on_failure=finished)

    def logout(self) -> None:
        st = self.state
        s = st.settings
        a = st.active

        def work(j: Job):
            if s.upload_method == "api":
                from ..tiktok.api import TikTokAPI

                TikTokAPI(s, a.tokens_path(), j.log).disconnect()
            else:
                from ..tiktok.browser import TikTokBrowser

                with TikTokBrowser(s, a.browser_dir(), j.log, headless=True) as b:
                    b.logout()
                a.logged_in = False
            j.log(f"Logged {a.handle} out of TikTok.")

        job = Job(f"Logging {a.label} out", work, kind="login")
        job.account_id = a.id
        st.run(job, pool="tiktok", on_success=lambda _: (st.save_accounts(), st.account_changed.emit()))

    def refresh_account(self) -> None:
        st = self.state
        a = st.active
        if st.account_busy(a.id, ("account",)):
            return
        job = Job(f"Refreshing {a.label}'s TikTok stats",
                  lambda j: jobs.refresh_account(st.store, st.settings, a, j.log, j.cancelled), kind="account")
        job.account_id = a.id
        st.run(job, pool="tiktok", on_success=lambda _: (st.save_accounts(), st.account_changed.emit(),
                                                         st.library_changed.emit()))

    def check_posting(self, account: Account, full: bool, auto: bool = False) -> None:
        """Test TikTok Studio's upload page for `account` without posting (full: with a test clip)."""
        st = self.state
        if st.account_busy(account.id, ("post", "login", "account", "check")):
            return

        def work(j: Job):
            from .. import tiktok

            return tiktok.check_upload(account.apply(st.settings), account, j.log, j.cancelled, full=full)

        def done(results: list[tuple[str, str, str]]) -> None:
            verdict = "fail" if any(r[1] == "fail" for r in results) else \
                      "warn" if any(r[1] == "warn" for r in results) else "ok"
            lines = [f"{'✓' if r == 'ok' else '⚠' if r == 'warn' else '✖'} {step}{': ' + detail if detail else ''}"
                     for step, r, detail in results]
            # A quick check can't see the caption box or Post button, so it never replaces a full result.
            if full or verdict == "fail" or not account.upload_check_at or account.upload_check == "fail":
                account.upload_check, account.upload_check_detail = verdict, "\n".join(lines)
            account.upload_check_at = time.time()
            st.save_accounts()
            st.account_changed.emit()
            head = {"ok": f"Posting test for {account.handle} passed.",
                    "warn": f"Posting test for {account.handle} passed with a warning.",
                    "fail": f"Posting test for {account.handle} failed. Autopilot posts would fail the same way."}[verdict]
            self._log(("✓ " if verdict == "ok" else "⚠ ") + head + " " + "; ".join(lines))
            if verdict == "fail" and auto:
                self._notify("TikTok posting problem", head, error=True, always=True)
            if not auto:
                box = QMessageBox.information if verdict == "ok" else QMessageBox.warning
                box(self, "Posting test", head + "\n\n" + "\n".join(lines) +
                    ("\n\nNothing was posted. The test clip was put in the uploader and thrown away." if full else ""))

        def failed(msg: str) -> None:
            if msg == "Cancelled":
                return
            account.upload_check_at, account.upload_check = time.time(), "fail"
            account.upload_check_detail = msg
            st.save_accounts()
            st.account_changed.emit()
            if auto:
                self._notify("TikTok posting problem", f"Posting test for {account.handle} couldn't run: {msg}",
                             error=True, always=True)

        what = "Testing posting" if full else "Checking TikTok's upload page"
        job = Job(f"{what} for {account.label}", work, kind="check")
        job.account_id = account.id
        st.run(job, pool="tiktok", on_success=done, on_failure=failed, quiet=auto)

    def requeue(self, item_id: str) -> None:
        st = self.state
        it = st.store.get(item_id)
        if not it:
            return
        account = st.accounts.get(it.account) or st.active
        with st.store.lock:
            if it.status == "posted":
                it.status, it.posted_at, it.tiktok_id, it.tiktok_url = "new", None, "", ""
            if it.status == "failed":
                it.status, it.last_error = ("queued" if it.queue_pos is not None else "new"), ""
        if it.queue_pos is None:
            st.store.enqueue([item_id], account.id)
        st.save_soon()
        st.library_changed.emit()
        self._log(f"“{it.name}” is back in {account.handle}'s queue.")

    # --- accounts -------------------------------------------------------------------

    def _update_account_button(self) -> None:
        st = self.state
        a = st.active
        self.account_btn.setIcon(QIcon(account_badge(a.id, a.label, 22)))
        self.account_btn.setText(f" {a.label}  ")
        self.account_btn.setToolTip(f"{a.label}: {account_status(st, a)}\nClick to switch accounts (Ctrl+1…9).")
        self.setWindowTitle(f"{a.label} · {APP_NAME}" if len(st.accounts) > 1 else APP_NAME)

    def _fill_account_menu(self) -> None:
        st = self.state
        m = self.account_menu
        m.clear()
        group = QActionGroup(m)
        for n, a in enumerate(st.accounts, 1):
            text = f"{a.label}   ·   {account_status(st, a)}" + (f"\tCtrl+{n}" if n <= 9 else "")
            act = QAction(QIcon(account_badge(a.id, a.label, 22)), text, m)
            act.setCheckable(True)
            act.setChecked(a.id == st.active.id)
            act.triggered.connect(lambda _=False, i=a.id: st.switch_account(i))
            group.addAction(act)
            m.addAction(act)
        m.addSeparator()
        m.addAction("＋ Add account…", self.add_account)
        m.addAction("Manage accounts…", self.manage_accounts)

    def _switch_to_number(self, n: int) -> None:
        ids = self.state.accounts.ids()
        if n <= len(ids):
            self.state.switch_account(ids[n - 1])

    def _account_switched(self) -> None:
        a = self.state.active
        self._update_account_button()
        self._log(f"Showing {a.label}.")

    def add_account(self) -> Account | None:
        name, ok = QInputDialog.getText(self, "Add account",
                                        "A name for the new account (optional; its @username is used once you log in):",
                                        QLineEdit.Normal, "")
        if not ok:
            return None
        st = self.state
        a = st.add_account(name)
        st.switch_account(a.id)
        self.nav_buttons[3].click()
        how = ("Your browser opens TikTok's approval page: approve with the TikTok account you want to add."
               if st.settings.upload_method == "api" else
               "A new browser window opens with its own empty profile: log in there with the TikTok account "
               "you want to add. Your other accounts stay logged in.")
        if QMessageBox.question(self, "Add account", f"Log in to TikTok for {a.label} now?\n\n{how}") == QMessageBox.Yes:
            self.login()
        return a

    def manage_accounts(self, select: str = "") -> None:
        dlg = AccountsDialog(self.state, self, select=select if isinstance(select, str) else "")
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.add_requested.connect(lambda: (dlg.reload(a.id) if (a := self.add_account()) else None))
        dlg.exec()

    # --- settings -------------------------------------------------------------------

    def open_settings(self, tab: str = "") -> None:
        dlg = SettingsDialog(self.state.settings, self, on_check_updates=lambda: self.check_for_updates(manual=True),
                             accounts=[(a.id, a.label) for a in self.state.accounts],
                             tab=tab if isinstance(tab, str) else "")
        if dlg.exec():
            dlg.apply()
            self.state.save_settings()
            try:
                set_start_with_windows(self.state.settings.start_with_windows)
            except OSError as exc:
                self._log(f"Couldn't change the Windows startup entry: {exc}")
            with self.state.store.lock:
                for it in self.state.store.items.values():
                    jobs.recombine(it, self.state.settings)
            self.state.save_soon()
            self.state.library_changed.emit()
            self.state.account_changed.emit()
            self._log("Settings saved.")

    # --- tray / window ----------------------------------------------------------------

    def _make_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(theme.app_icon(), self)
        self.tray.setToolTip(f"{APP_NAME} {VERSION}")
        menu = QMenu()
        menu.addAction("Open", self._show_window)
        self.tray_autopilot = menu.addMenu("Autopilot")
        self.tray_autopilot.aboutToShow.connect(self._fill_tray_autopilot)
        menu.addAction("Check for updates", lambda: self.check_for_updates(manual=True))
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show_window()
                                    if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def _fill_tray_autopilot(self) -> None:
        st = self.state
        m = self.tray_autopilot
        m.clear()
        for a in st.accounts:
            act = QAction(QIcon(account_badge(a.id, a.label, 18)), a.label, m, checkable=True)
            act.setChecked(a.autopilot)
            act.toggled.connect(lambda on, i=a.id: st.set_autopilot(i, on))
            m.addAction(act)
        if len(st.accounts) > 1:
            m.addSeparator()
            m.addAction("On for every account", lambda: [st.set_autopilot(a.id, True) for a in st.accounts])
            m.addAction("Off for every account", lambda: [st.set_autopilot(a.id, False) for a in st.accounts])

    def _notify(self, title: str, msg: str, error: bool = False, always: bool = False) -> None:
        if self.tray and (always or not self.isActiveWindow()):
            self.tray.showMessage(title, msg, QSystemTrayIcon.Warning if error else QSystemTrayIcon.Information, 8000)

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        if not self._quitting and self.tray and self.state.settings.minimize_to_tray:
            e.ignore()
            self.hide()
            if not getattr(self, "_told_tray", False):
                self._told_tray = True
                self.tray.showMessage(APP_NAME, "Still running in the system tray"
                                      + (" so autopilot can keep posting." if any(a.autopilot for a in self.state.accounts)
                                         else ".")
                                      + " Right-click the icon to quit.", QSystemTrayIcon.Information, 5000)
            return
        if self.state.busy("post") and QMessageBox.question(
                self, "Quit", "A video is being posted right now. Quit anyway?") != QMessageBox.Yes:
            e.ignore()
            self._quitting = False
            return
        self.state.cancel_all()
        self.state.store.save()
        if self.tray:
            self.tray.hide()
        e.accept()
        QApplication.quit()

    def quit(self) -> None:
        self._quitting = True
        self.close()

    # --- drag & drop ------------------------------------------------------------------

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.is_dir() or p.suffix.lower() in VIDEO_EXTS]
        if paths:
            self.nav_buttons[0].click()
            self.library.add_paths(paths)

    def handle_message(self, msg: str) -> None:
        """Another launch of the app (e.g. a second double-click) asked us to show up, add files or update."""
        lines = msg.split("\n")
        command = lines[0].strip()
        if command == "update":
            self.check_for_updates(unattended=True)
        elif command != "tray":
            self._show_window()
        files = [Path(p) for p in lines[1:] if p]
        if files:
            self._show_window()
            self.library.add_paths(files)

    # --- updates ----------------------------------------------------------------------

    def _build_update_banner(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("updateBanner")
        bar.setStyleSheet("QFrame#updateBanner { background:#1E2A3A; border-bottom:1px solid #2F4A6B; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 16, 6)
        self.update_label = QLabel("")
        self.update_label.setTextFormat(Qt.RichText)
        self.update_progress = QProgressBar()
        self.update_progress.setFixedWidth(160)
        self.update_progress.hide()
        self.update_notes_btn = QPushButton("What's new")
        self.update_notes_btn.clicked.connect(lambda: self._show_notes(self.pending_release))
        self.update_install_btn = QPushButton("Install and restart")
        self.update_install_btn.setObjectName("primary")
        self.update_install_btn.clicked.connect(self._install_clicked)
        self.update_later_btn = QPushButton("Later")
        self.update_later_btn.clicked.connect(bar.hide)
        self.update_skip_btn = QPushButton("Skip this version")
        self.update_skip_btn.clicked.connect(self._skip_version)
        lay.addWidget(self.update_label, 1)
        for w in (self.update_progress, self.update_notes_btn, self.update_install_btn, self.update_later_btn,
                  self.update_skip_btn):
            lay.addWidget(w)
        bar.hide()
        return bar

    def _show_banner(self, rel: updater.Release, text: str = "") -> None:
        self.update_label.setText(text or f"✨ <b>{APP_NAME} {rel.version}</b> is available "
                                  f"<span style='color:{theme.MUTED}'>(you have {VERSION})</span>")
        self.update_notes_btn.setVisible(bool(rel.notes))
        self.update_banner.show()

    def check_for_updates(self, manual: bool = False, unattended: bool = False) -> None:
        st, s = self.state, self.state.settings
        if not (manual or unattended or s.auto_check_updates) or st.busy("update"):
            return

        def found(rel: updater.Release | None) -> None:
            if rel is None:
                self.pending_release = None
                if manual:
                    latest = updater.load_state().get("latest")
                    msg = (f"You're on the latest version ({VERSION})." if latest else
                           f"No releases have been published on github.com/{s.update_repo} yet.")
                    QMessageBox.information(self, "Updates", msg)
                return
            self.pending_release = rel
            self._log(f"Update available: {APP_NAME} {rel.version}.")
            if unattended:
                self.install_update(rel, unattended=True)
                return
            if manual or rel.version != s.skipped_version:
                self._show_banner(rel)
                if not self.isVisible():
                    self._notify("Update available", f"{APP_NAME} {rel.version} is ready to install.")
            if s.auto_install_updates and not manual:
                self.auto_install_timer.start()
                self._maybe_auto_install()

        def failed(msg: str) -> None:
            if manual:
                QMessageBox.warning(self, "Updates", f"Couldn't check for updates.\n\n{msg}")
            else:
                self._log(f"Update check failed: {msg}")

        st.run(Job("Checking for updates", lambda j: updater.check(s.update_repo), kind="update"),
               on_success=found, on_failure=failed, quiet=True)

    def _install_clicked(self) -> None:
        if self._staged:
            self._apply_update(*self._staged)
        elif self.pending_release:
            self.install_update(self.pending_release)

    def _skip_version(self) -> None:
        if self.pending_release:
            self.state.settings.skipped_version = self.pending_release.version
            self.state.save_settings()
        self.update_banner.hide()

    def _say(self, msg: str, unattended: bool, warn: bool = True) -> None:
        if unattended:
            self._log(msg)
        elif warn:
            QMessageBox.warning(self, "Updates", msg)
        else:
            QMessageBox.information(self, "Updates", msg)

    def install_update(self, rel: updater.Release, unattended: bool = False) -> None:
        st = self.state
        target = updater.install_dir()
        if target is None:
            self._say("You're running TikTok Manager from source, so it can't replace itself. "
                      "Opening the release page instead.", unattended, warn=False)
            if not unattended:
                webbrowser.open(rel.html_url or f"https://github.com/{st.settings.update_repo}/releases/latest")
            return
        try:
            updater.preflight(target)
        except updater.UpdateError as exc:
            self._say(str(exc), unattended)
            return
        if st.busy("post") or st.busy("login"):
            self._say("Wait for the current TikTok upload or login to finish, then install the update.",
                      unattended, warn=False)
            return
        if st.busy("update-download"):
            return

        def work(j: Job) -> Path:
            zip_path = updater.download(rel, lambda done, total: j.progress(f"{done}/{total}"), j.cancelled)
            j.progress("unpacking")
            return updater.stage(zip_path, rel.version, target)

        def progress(text: str) -> None:
            if "/" in text:
                done, total = (int(x) for x in text.split("/"))
                self.update_progress.setRange(0, max(1, total // 1024))
                self.update_progress.setValue(done // 1024)
                self.update_label.setText(f"Downloading {APP_NAME} {rel.version}… "
                                          f"{done / 1e6:.0f} / {total / 1e6:.0f} MB")
            else:
                self.update_progress.setRange(0, 0)
                self.update_label.setText(f"Preparing {APP_NAME} {rel.version}…")

        def ready(new_app: Path) -> None:
            self.update_progress.hide()
            self._staged = (new_app, rel)
            self.update_install_btn.setEnabled(True)
            self.update_install_btn.setText("Restart to update")
            self._show_banner(rel, f"✨ <b>{APP_NAME} {rel.version}</b> is downloaded and ready.")
            if unattended or QMessageBox.question(
                    self, "Update ready", f"{APP_NAME} {rel.version} is ready.\n\nRestart now to finish updating? "
                    "Your library, queue, settings and TikTok login are kept.") == QMessageBox.Yes:
                self._apply_update(new_app, rel, unattended)

        def failed(msg: str) -> None:
            self.update_progress.hide()
            self.update_install_btn.setEnabled(True)
            self._show_banner(rel)
            if msg != "Cancelled":
                self._say(f"The update couldn't be downloaded: {msg}", unattended)

        self._show_banner(rel, f"Downloading {APP_NAME} {rel.version}…")
        self.update_progress.setRange(0, 0)
        self.update_progress.show()
        self.update_install_btn.setEnabled(False)
        job = Job(f"Downloading update {rel.version}", work, kind="update-download")
        job.signals.progress.connect(progress)
        st.run(job, on_success=ready, on_failure=failed)

    def _apply_update(self, new_app: Path, rel: updater.Release, unattended: bool = False) -> None:
        st = self.state
        target = updater.install_dir()
        if target is None or not new_app.is_dir():
            self._staged = None
            return
        if st.busy("post") or st.busy("login"):
            self._say("Wait for the current TikTok upload or login to finish first.", unattended, warn=False)
            return
        st.store.save()
        st.save_settings()
        state = updater.load_state()
        state["installing"] = {"from": VERSION, "to": rel.version, "notes": rel.notes, "name": rel.name}
        updater.save_state(state)
        try:
            updater.launch_helper(new_app, target, rel.version, tray=not self.isVisible())
        except OSError as exc:
            self._say(f"Couldn't start the updater: {exc}", unattended)
            return
        self._log(f"Restarting to install {APP_NAME} {rel.version}…")
        self._quitting = True
        self.close()

    def _maybe_auto_install(self) -> None:
        """Install a found update by itself, but only when nobody is using the window and nothing is about to post."""
        st, s, rel = self.state, self.state.settings, self.pending_release
        if not (s.auto_install_updates and rel) or rel.version == updater.load_state().get("failed_version"):
            self.auto_install_timer.stop()
            return
        if self.isVisible() and not self.isMinimized():
            return
        if any(st.busy(k) for k in ("post", "login", "account", "update-download")):
            return
        soon = time.time() + AUTO_INSTALL_QUIET_MINUTES * 60
        autopilot = {a.id for a in st.accounts if a.autopilot}
        if any(i.account in autopilot and i.planned_at and i.planned_at <= soon
               for i in st.store.queue() if i.status == "queued"):
            return
        self.auto_install_timer.stop()
        self._log(f"Installing {APP_NAME} {rel.version} automatically.")
        if self._staged:
            self._apply_update(*self._staged, unattended=True)
        else:
            self.install_update(rel, unattended=True)

    def after_update(self, old_version: str) -> None:
        """First launch of a new version, started by the update helper."""
        state = updater.load_state()
        info = state.pop("installing", None) or {}
        state.pop("failed_version", None)
        updater.save_state(state)
        self._log(f"Updated {APP_NAME} from {old_version} to {VERSION}.")
        self._notify("Updated", f"{APP_NAME} is now version {VERSION}.")
        if self.isVisible() and info.get("notes"):
            QTimer.singleShot(800, lambda: self._show_notes_text(f"What's new in {VERSION}", info["notes"]))

    def after_failed_update(self, version: str) -> None:
        state = updater.load_state()
        state["failed_version"] = version  # stops automatic installs from retrying it in a loop
        state.pop("installing", None)
        updater.save_state(state)
        msg = (f"Updating to {version} didn't work, so {APP_NAME} {VERSION} was restored. "
               "Nothing in your library or settings changed.")
        self._log(msg)
        self._notify("Update didn't install", msg, error=True)
        if self.isVisible():
            tail = updater.log_tail()
            QMessageBox.warning(self, "Update rolled back", msg + (f"\n\nDetails:\n{tail}" if tail else ""))

    def _show_notes(self, rel: updater.Release | None) -> None:
        if rel:
            self._show_notes_text(f"What's new in {rel.version}", rel.notes or "No release notes.")

    def _show_notes_text(self, title: str, notes: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(560, 440)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(notes)
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.exec()
