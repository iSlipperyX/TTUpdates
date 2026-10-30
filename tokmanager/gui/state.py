"""Shared app state: the store, settings, accounts, background job pools and change signals."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .. import jobs as work
from .. import niches, ready
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
        self.started = False  # it left the pool's queue and is running
        self.ended = threading.Event()  # set when fn has returned (before `done` is emitted)
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
        self.started = True
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
            self.ended.set()
            self.signals.done.emit()


class AppState(QObject):
    library_changed = Signal()  # items added/removed/updated
    account_changed = Signal()  # login state / stats / account details changed
    account_switched = Signal()  # the window now shows a different account
    jobs_changed = Signal()
    message = Signal(str)  # log line for the activity panel
    phone_synced = Signal()  # the phone folder was updated (or couldn't be): phone_result / phone_error
    stats_changed = Signal(str)  # an account's TikTok numbers were read (account id), e.g. by the live view
    niche_switched = Signal()  # the pages now show another niche (niche_filter)

    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.store = Store()
        self.accounts = Accounts.load(self.settings)
        # The last look at the watched folders (ready.watch_report), for the Ready page
        self.watch_reports: list = []
        self.watch_checked_at = 0.0
        # The last update of the phone folder (phone.sync) and why the last try failed, if it did
        self.phone_result = None
        self.phone_error = ""
        # The last look at Google Drive (drive_sync.Result), for the Ready page and Settings
        self.drive_result = None
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
        # Google Drive downloads (drive_sync.py) can take minutes: a thread of their own, so scoring never waits.
        self.drive_pool = QThreadPool(self)
        self.drive_pool.setMaxThreadCount(1)
        self.jobs: list[Job] = []
        # The niche index (niches()), and whether something it's made from changed since
        self._niche_index: niches.Index | None = None
        self._niche_sig: tuple = ()
        self._niche_dirty, self._niche_checked = True, 0.0
        for sig in (self.library_changed, self.account_changed, self.account_switched):
            sig.connect(self._niches_changed)
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=self.store.save)

        # 2 MB per file, 3 old ones kept: about a month of normal use, and never more than 8 MB.
        handler = RotatingFileHandler(app_dir() / "tokmanager.log", maxBytes=2_000_000, backupCount=3,
                                      encoding="utf-8")
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

    # --- niches ---------------------------------------------------------------------

    def niches(self) -> niches.Index:
        """Which niche each video is in (niches.py), worked out again when the library, the accounts,
        the watched folders or ClipMonster's niches change."""
        # Asked for every row a page draws, so it's only looked at again after a change was signalled,
        # or a couple of seconds on (for changes made without a signal, or in ClipMonster).
        now = time.monotonic()
        if self._niche_index is not None and not self._niche_dirty and now - self._niche_checked < 2:
            return self._niche_index
        cm = niches.clipmonster_info()
        with self.store.lock:
            roots = ready.watch_roots(self.settings)
            sig = (cm.stamp, tuple(roots), tuple((a.id, a.username) for a in self.accounts),
                   tuple((i.id, i.path, i.niche, i.niche_id, i.niche_set, i.account, i.source_channel)
                         for i in self.store.items.values()))
            if self._niche_index is None or self._niche_sig != sig:
                self._niche_index = niches.Index(self.store.items.values(), self.accounts,
                                                 roots, cm)
                self._niche_sig = sig
        self._niche_dirty, self._niche_checked = False, now
        return self._niche_index

    def _niches_changed(self) -> None:
        self._niche_dirty = True

    @property
    def niche_filter(self) -> str:
        """The niche the pages show: niches.ALL, niches.NONE or a niche key (one that's gone shows all)."""
        want = self.settings.niche_filter
        return want if self.niches().valid(want) else niches.ALL

    def set_niche_filter(self, key: str) -> None:
        if key == self.niche_filter:
            return
        self.settings.niche_filter = key
        self.save_settings()
        self.niche_switched.emit()

    def in_niche(self, items: list[VideoItem]) -> list[VideoItem]:
        """The videos the chosen niche shows (all of them when no niche is chosen)."""
        want = self.niche_filter
        return items if want == niches.ALL else self.niches().keep(items, want)

    def niche_label(self) -> str:
        """'Gaming' when a niche is chosen, 'No niche' for videos in none, "" for all."""
        want = self.niche_filter
        return "" if want == niches.ALL else "No niche" if want == niches.NONE else self.niches().name(want)

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
        from ..live import EventLog

        EventLog.remove(EventLog.path_for(a.id))  # its live activity feed
        # A browser profile can be a few hundred MB; delete it without freezing the window. A live
        # reading that's queued or running is cancelled first: Edge holds the profile's files while it
        # runs, and a reading that started after the delete would make the folder again.
        running = []
        for j in [j for j in self.jobs if j.account_id == a.id and j.kind == "live"]:
            j.cancel()
            if self.tiktok_pool.tryTake(j):  # still queued: it never opens the profile
                self.jobs.remove(j)
                self.jobs_changed.emit()
            else:
                running.append(j)

        def delete() -> None:
            for j in running:
                j.ended.wait(120)
            Accounts.delete_login(a)

        threading.Thread(target=delete, daemon=True).start()
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
        {"tiktok": self.tiktok_pool, "drive": self.drive_pool}.get(pool, self.analysis_pool).start(job)
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
            if self.analysis_pool.tryTake(j) or self.tiktok_pool.tryTake(j) or self.drive_pool.tryTake(j):
                self.jobs.remove(j)
        self.jobs_changed.emit()
