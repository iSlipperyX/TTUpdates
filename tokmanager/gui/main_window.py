"""Main window: navigation, activity log, tray icon, autopilot, and the TikTok actions."""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QStackedWidget, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget)

from .. import jobs, schedule, updater
from ..config import APP_NAME, VERSION
from ..media import VIDEO_EXTS
from . import theme
from .account import AccountPage
from .library import LibraryPage
from .queue_page import QueuePage
from .settings_dialog import SettingsDialog
from .state import AppState, Job

AUTOPILOT_CHECK_MS = 30_000
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
        hl.addSpacing(18)
        self.nav = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.library = LibraryPage(state)
        self.queue = QueuePage(state)
        self.account = AccountPage(state)
        self.nav_buttons: list[QPushButton] = []
        for i, (text, page) in enumerate((("Library", self.library), ("Queue", self.queue), ("Account", self.account))):
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
        self.account.login_requested.connect(self.login)
        self.account.logout_requested.connect(self.logout)
        self.account.refresh_requested.connect(self.refresh_account)
        self.account.requeue_requested.connect(self.requeue)

        self._make_tray()
        self.autopilot_timer = QTimer(self, interval=AUTOPILOT_CHECK_MS, timeout=self.autopilot_tick)
        self.autopilot_timer.start()
        # Keep "Today 15:00 (due)" style labels current.
        self.clock = QTimer(self, interval=60_000, timeout=self.queue.refresh)
        self.clock.start()
        QTimer.singleShot(3000, self.autopilot_tick)
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
        self._log(f"{APP_NAME} {VERSION} ready. {len(state.store.items)} videos in the library, "
                  f"{len(state.store.queue())} queued.")
        if start_hidden and self.tray:
            self.hide()
        else:
            self.show()

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
        jobs_ = [j for j in self.state.jobs if j.kind != "update"]  # background update checks stay silent
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
        if not item.exists():
            QMessageBox.warning(self, "Missing file", f"The video file is missing:\n{item.path}")
            return
        job = Job(f"Posting “{item.name}”", lambda j: jobs.post_item(st.store, item_id, st.settings, j.log,
                                                                     j.cancelled, j.progress), kind="post")
        job.item_id = item_id
        job.signals.progress.connect(lambda m: self.job_label.setText(m))

        def ok(it):
            self._log(f"✔ Posted “{it.name}” to TikTok.")
            self._notify("Posted to TikTok", it.name)
            st.library_changed.emit()

        def fail(msg):
            if "log in" in msg.lower() and st.settings.upload_method == "browser":
                st.settings.browser_logged_in = False
                st.save_settings()
                st.account_changed.emit()
            st.library_changed.emit()
            if msg != "Cancelled":
                self._notify("Couldn't post to TikTok", msg, error=True)

        self._log(f"Posting “{item.name}”…")
        st.run(job, pool="tiktok", on_success=ok, on_failure=fail)
        st.library_changed.emit()

    def autopilot_tick(self) -> None:
        if not self.state.settings.autopilot or self.state.busy("post") or self.state.busy("login"):
            return
        self.queue.replan()
        item = schedule.due(self.state.store.queue(), time.time())
        if item and item.exists():
            self._log(f"Autopilot: “{item.name}” is due.")
            self.post(item.id)

    def login(self) -> None:
        st = self.state
        s = st.settings

        def work(j: Job):
            if s.upload_method == "api":
                from ..tiktok.api import TikTokAPI

                TikTokAPI(s, j.log).connect(cancelled=j.cancelled)
            else:
                from ..tiktok.browser import TikTokBrowser

                with TikTokBrowser(s, j.log, headless=False, cancelled=j.cancelled) as b:
                    if not b.login():
                        raise RuntimeError("Login wasn't completed.")
                    s.browser_logged_in = True
                    name = b.whoami()
                    if name:
                        with st.store.lock:
                            st.store.profile.username = name
                        j.log(f"Logged in as @{name}.")
            jobs.refresh_account(st.store, s, j.log, j.cancelled)

        def finished(_=None):
            st.save_settings()
            st.account_changed.emit()

        st.run(Job("Logging in to TikTok", work, kind="login"), pool="tiktok", on_success=finished, on_failure=finished)

    def logout(self) -> None:
        st = self.state
        s = st.settings

        def work(j: Job):
            if s.upload_method == "api":
                from ..tiktok.api import TikTokAPI

                TikTokAPI(s, j.log).disconnect()
            else:
                from ..tiktok.browser import TikTokBrowser

                with TikTokBrowser(s, j.log, headless=True) as b:
                    b.logout()
                s.browser_logged_in = False
            j.log("Logged out of TikTok.")

        st.run(Job("Logging out", work, kind="login"), pool="tiktok",
               on_success=lambda _: (st.save_settings(), st.account_changed.emit()))

    def refresh_account(self) -> None:
        st = self.state
        if st.busy("account"):
            return
        st.run(Job("Refreshing TikTok stats", lambda j: jobs.refresh_account(st.store, st.settings, j.log, j.cancelled),
                   kind="account"), pool="tiktok",
               on_success=lambda _: (st.account_changed.emit(), st.library_changed.emit()))

    def requeue(self, item_id: str) -> None:
        st = self.state
        it = st.store.get(item_id)
        if not it:
            return
        with st.store.lock:
            if it.status == "posted":
                it.status, it.posted_at, it.tiktok_id, it.tiktok_url = "new", None, "", ""
            if it.status == "failed":
                it.status, it.last_error = ("queued" if it.queue_pos is not None else "new"), ""
        if it.queue_pos is None:
            st.store.enqueue([item_id])
        st.save_soon()
        st.library_changed.emit()
        self._log(f"“{it.name}” is back in the queue.")

    # --- settings -------------------------------------------------------------------

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.state.settings, self, on_check_updates=lambda: self.check_for_updates(manual=True))
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
        self.tray_autopilot = QAction("Autopilot", menu, checkable=True)
        self.tray_autopilot.setChecked(self.state.settings.autopilot)
        self.tray_autopilot.toggled.connect(self.queue.autopilot.setChecked)
        self.queue.autopilot.toggled.connect(self.tray_autopilot.setChecked)
        menu.addAction(self.tray_autopilot)
        menu.addAction("Check for updates", lambda: self.check_for_updates(manual=True))
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show_window()
                                    if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def _notify(self, title: str, msg: str, error: bool = False) -> None:
        if self.tray and not self.isActiveWindow():
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
                                      + (" so autopilot can keep posting." if self.state.settings.autopilot else ".")
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
        if s.autopilot and any(i.planned_at and i.planned_at <= soon for i in st.store.queue() if i.status == "queued"):
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
