"""Shared app state: the store, settings, accounts, background job pools and change signals."""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .. import jobs as work
from ..accounts import Account, Accounts
from ..config import Settings, app_dir
from ..store import Store, VideoItem

log_file = logging.getLogger("tokmanager")


class JobSignals(QObject):
    log = Signal(str)
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    done = Signal()


class Job(QRunnable):
    """Runs fn(job) on a pool thread. fn can call job.log/job.progress and check job.cancelled()."""

    def __init__(self, label: str, fn: Callable[["Job"], Any], kind: str = "work"):
        super().__init__()
        self.setAutoDelete(False)
        self.label = label
        self.kind = kind
        self.fn = fn
        self.signals = JobSignals()
        self._cancel = False
        self.account_id = ""  # the TikTok account this job works for, if any

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def log(self, msg: str) -> None:
        self.signals.log.emit(msg)

    def progress(self, msg: str) -> None:
        self.signals.progress.emit(msg)

    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.fn(self))
        except Exception as exc:
            name = exc.__class__.__name__
            if name == "Cancelled" or self._cancel:
                self.signals.failed.emit("Cancelled")
            else:
                log_file.error("%s failed:\n%s", self.label, traceback.format_exc())
                self.signals.failed.emit(str(exc) or name)
        finally:
            self.signals.done.emit()


class AppState(QObject):
    library_changed = Signal()  # items added/removed/updated
    account_changed = Signal()  # login state / stats / account details changed
    account_switched = Signal()  # the window now shows a different account
    jobs_changed = Signal()
    message = Signal(str)  # log line for the activity panel

    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.store = Store()
        self.accounts = Accounts.load(self.settings)
        if self.store.adopt(self.accounts.ids(), self.accounts.items[0].id):
            self.store.save()
        for a in self.accounts:  # e.g. the account made from a 1.0 install: its @name was in the stats
            if not a.username and self.store.profile(a.id).username:
                a.username = self.store.profile(a.id).username
                self.save_accounts()
        # Analysis is CPU-bound ffmpeg work: two at a time keeps the PC usable.
        self.analysis_pool = QThreadPool(self)
        self.analysis_pool.setMaxThreadCount(2)
        # One browser/TikTok action at a time: they share a browser profile and a login.
        self.tiktok_pool = QThreadPool(self)
        self.tiktok_pool.setMaxThreadCount(1)
        self.jobs: list[Job] = []
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=self.store.save)

        handler = logging.FileHandler(app_dir() / "tokmanager.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log_file.addHandler(handler)
        log_file.setLevel(logging.INFO)

    def log(self, msg: str) -> None:
        log_file.info(msg)
        self.message.emit(msg)

    def save_soon(self) -> None:
        self._save_timer.start()

    def save_settings(self) -> None:
        self.settings.save()

    # --- accounts -------------------------------------------------------------------

    @property
    def active(self) -> Account:
        return self.accounts.active

    def save_accounts(self) -> None:
        self.accounts.save()

    def switch_account(self, account_id: str) -> None:
        if account_id == self.accounts.active_id or self.accounts.get(account_id) is None:
            return
        self.accounts.active_id = account_id
        self.save_accounts()
        self.account_switched.emit()

    def account_for(self, item: VideoItem | None) -> Account:
        """The account a video is queued for / was posted to, else the one being shown."""
        return (self.accounts.get(item.account) if item else None) or self.active

    def settings_for(self, account: Account | None = None) -> Settings:
        """App settings with an account's schedule and posting defaults (the shown account by default)."""
        return (account or self.active).apply(self.settings)

    def accounts_edited(self) -> None:
        """An account's name, defaults or order changed (saved already): refresh what shows them."""
        self.account_changed.emit()
        self.library_changed.emit()

    def add_account(self, name: str = "") -> Account:
        a = self.accounts.add(name, copy_schedule_from=self.active)
        self.save_accounts()
        self.log(f"Added an account{f' “{a.name}”' if a.name else ''}. Log in to it on the Account page.")
        self.accounts_edited()
        return a

    def remove_account(self, account_id: str) -> None:
        was_shown = account_id == self.accounts.active_id
        a = self.accounts.remove(account_id)
        if a is None:
            return
        self.store.forget_account(a.id)
        self.store.save()
        self.save_accounts()
        # A browser profile can be a few hundred MB; delete it without freezing the window.
        threading.Thread(target=Accounts.delete_login, args=(a,), daemon=True).start()
        self.log(f"Removed {a.label}.")
        if was_shown:
            self.account_switched.emit()
        self.accounts_edited()

    def set_autopilot(self, account_id: str, on: bool) -> None:
        a = self.accounts.get(account_id)
        if a is None or a.autopilot == on:
            return
        a.autopilot = on
        self.save_accounts()
        self.log(f"Autopilot on for {a.label}: its queued videos post at their scheduled times." if on
                 else f"Autopilot off for {a.label}.")
        self.account_changed.emit()

    def replan(self) -> None:
        work.replan(self.store, self.accounts, self.settings)

    def account_busy(self, account_id: str, kinds: tuple[str, ...] = ("post", "login", "account")) -> bool:
        return any(j.account_id == account_id and j.kind in kinds for j in self.jobs)

    def run(self, job: Job, pool: str = "analysis", on_success: Callable[[Any], None] | None = None,
            on_failure: Callable[[str], None] | None = None, quiet: bool = False) -> Job:
        job.signals.log.connect(self.log)
        if on_success:
            job.signals.succeeded.connect(on_success)

        def failed(msg: str) -> None:
            if not quiet:
                self.log(f"✖ {job.label}: {msg}")
            if on_failure:
                on_failure(msg)

        job.signals.failed.connect(failed)
        job.signals.done.connect(lambda: self._finished(job))
        self.jobs.append(job)
        self.jobs_changed.emit()
        (self.tiktok_pool if pool == "tiktok" else self.analysis_pool).start(job)
        return job

    def _finished(self, job: Job) -> None:
        if job in self.jobs:
            self.jobs.remove(job)
        self.jobs_changed.emit()
        self.save_soon()

    def busy(self, kind: str) -> bool:
        return any(j.kind == kind for j in self.jobs)

    def cancel_all(self, kind: str | None = None) -> None:
        for j in list(self.jobs):
            if kind is not None and j.kind != kind:
                continue
            j.cancel()
            # Jobs still waiting for a thread never run, so drop them here.
            if self.analysis_pool.tryTake(j) or self.tiktok_pool.tryTake(j):
                self.jobs.remove(j)
        self.jobs_changed.emit()
