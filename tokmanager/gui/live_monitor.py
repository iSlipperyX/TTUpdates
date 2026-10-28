"""Runs the live view: reads each watched account on a timer (live.py) and passes on what changed."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from .. import live
from ..accounts import Account
from .state import AppState, Job

TICK_MS = 5_000
SHARE_EVERY = 30 * 60  # how often live readings pass on to ClipMonster's results.json how its clips do


@dataclass
class Failure:
    """A reading that didn't work (returned rather than raised, so the error code isn't lost)."""

    message: str
    code: str = ""


@dataclass
class Status:
    state: str  # login | off | starting | live | reading | waiting | retrying | stopped
    text: str


class LiveMonitor(QObject):
    updated = Signal(str)  # account id: a reading came in (or failed); its feed and numbers changed
    alert = Signal(str, str)  # title, text: worth a tray notification

    def __init__(self, state: AppState, start: bool = True):
        super().__init__(state)
        self.state = state
        self.watchers: dict[str, live.Watcher] = {}
        self.shared_at = 0.0
        self.timer = QTimer(self, interval=TICK_MS, timeout=self.tick)
        if start:
            self.timer.start()
            QTimer.singleShot(1500, self, self.tick)

    # --- which accounts ---------------------------------------------------------------

    def watcher(self, account_id: str) -> live.Watcher:
        w = self.watchers.get(account_id)
        if w is None:
            w = self.watchers[account_id] = live.Watcher(account_id)
        return w

    def can_watch(self, a: Account) -> bool:
        return a.logged_in or bool(a.username)  # a public profile can be read, but the login makes it reliable

    BROWSER_JOBS = ("login", "post", "account", "check", "profile")

    def watching(self, a: Account) -> bool:
        return a.live and self.can_watch(a)

    def reading(self, account_id: str) -> bool:
        """A reading of this account is queued or running."""
        return any(j.kind == "live" and j.account_id == account_id for j in self.state.jobs)

    def _reading_started(self, account_id: str) -> bool:
        return any(j.kind == "live" and j.account_id == account_id and j.started for j in self.state.jobs)

    def status(self, a: Account, now: float | None = None) -> Status:
        now = time.time() if now is None else now
        if not self.can_watch(a):
            return Status("login", f"Log {a.label} in to TikTok to watch it live.")
        if not a.live:
            return Status("off", "Not watching. Turn on Watch live to see views, likes and followers as they come in.")
        w = self.watcher(a.id)
        if w.stopped:
            return Status("stopped", f"Stopped: {w.stopped}")
        if self._reading_started(a.id):
            return Status("reading", "Reading TikTok…")
        if self.reading(a.id) or (self.state.account_busy(a.id, self.BROWSER_JOBS) and now >= w.next_at):
            return Status("waiting", "Waiting for the browser (a post or other TikTok job is running)…")
        if not w.last_ok:
            if w.error:
                return Status("retrying", f"{w.error} Trying again in {live.ago(w.next_at - now)}.")
            return Status("starting", "Starting…")
        when = f"Read {live.ago(now - w.last_ok)} ago"
        if w.error:
            return Status("retrying", f"{when}. The last try failed: {w.error} Again in {live.ago(w.next_at - now)}.")
        return Status("live", f"{when} · next in {live.ago(max(0, w.next_at - now))}")

    # --- reading ------------------------------------------------------------------

    def tick(self) -> None:
        now = time.time()
        known = set(self.state.accounts.ids())
        for aid in [a for a in self.watchers if a not in known]:  # removed accounts
            del self.watchers[aid]
        if any(j.kind == "live" for j in self.state.jobs):
            return  # one reading in the browser queue at a time, so a post that comes due waits for one at most
        for a in self.state.accounts:
            w = self.watcher(a.id)
            if not a.live or w.stopped or now < w.next_at or self.reading(a.id):
                continue
            if not self.can_watch(a) or self.state.account_busy(a.id, self.BROWSER_JOBS):
                continue  # its browser profile is in use: read after that job
            self._read(a, w, now)
            return

    def check_now(self, account_id: str) -> None:
        a = self.state.accounts.get(account_id)
        if a is None:
            return
        w = self.watcher(a.id)
        w.resume()
        if self.watching(a) and not self.reading(a.id) and not self.state.account_busy(a.id, self.BROWSER_JOBS):
            self._read(a, w, time.time())
        self.updated.emit(a.id)

    def resume(self, account_id: str) -> None:
        """The account was logged in again, or watching turned back on: read it soon."""
        self.watcher(account_id).resume()
        QTimer.singleShot(200, self, self.tick)

    def set_watching(self, account_id: str, on: bool) -> None:
        a = self.state.accounts.get(account_id)
        if a is None or a.live == on:
            return
        a.live = on
        self.state.save_accounts()
        self.state.log(f"Watching {a.label} live." if on else f"Stopped watching {a.label} live.")
        if on:
            self.resume(a.id)
        self.updated.emit(a.id)

    def _read(self, a: Account, w: live.Watcher, now: float) -> None:
        st = self.state
        s = st.settings
        full = w.want_full(now)
        w.schedule(s, now)  # the next one is planned from the start, so a slow answer doesn't push it back

        def work(j: Job):
            if j.cancelled() or st.accounts.get(a.id) is None:  # removed while it waited
                return Failure("Cancelled.", "cancelled")
            try:
                return live.read(s, a, full=full, log=lambda _m: None, cancelled=j.cancelled)
            except live.LiveError as exc:
                return Failure(str(exc), exc.code)
            except OSError as exc:  # the browser profile folder
                return Failure(f"{exc}.", "other")

        def done(result) -> None:
            account = st.accounts.get(a.id)
            if account is None:  # removed while reading
                return
            if isinstance(result, Failure):
                if result.code == "cancelled":  # the app is closing
                    return
                events = w.failed(s, result.message, result.code, time.time())
                if result.code == "logged_out" and account.logged_in:
                    self._logged_out(account)
                if w.failures == 1 or w.stopped:
                    st.log(f"Live view of {account.handle}: {result.message}")
                for e in events:
                    if e.alert and s.live_alerts:
                        self.alert.emit(f"Live view of {account.handle} stopped", result.message)
            else:
                if result.logged_in is False and account.logged_in:  # read as a visitor: the login ran out
                    self._logged_out(account)
                had_name = account.username
                events = w.apply(st.store, account, s, result)
                if account.username != had_name:
                    st.save_accounts()
                st.save_soon()
                st.stats_changed.emit(account.id)
                if any(e.kind == "seen" for e in events):
                    for e in events:
                        if e.kind == "seen":
                            st.log(e.text)
                    st.library_changed.emit()
                if s.live_alerts:
                    self._notify(account, [e for e in events if e.alert])
                self._share(any(e.kind == "seen" for e in events))
                if w.next_at < time.time():  # it waited or took longer than the interval: plan from now
                    w.schedule(s, time.time())
            self.updated.emit(account.id)

        def crashed(msg: str) -> None:
            account = st.accounts.get(a.id)
            if account is not None:
                w.failed(s, msg, "", time.time())
                self.updated.emit(account.id)

        job = Job(f"Reading {a.handle} live", work, kind="live")
        job.account_id = a.id
        # The browser pool: one TikTok window at a time per PC, so a reading never fights a post for the profile
        st.run(job, pool="tiktok", on_success=done, on_failure=crashed, quiet=True)
        self.updated.emit(a.id)


    def _logged_out(self, a: Account) -> None:
        a.logged_in = False
        self.state.save_accounts()
        self.state.log(f"{a.label}'s TikTok login has run out. Log in again on the Account page"
                       + (" (the Live page keeps reading its public profile meanwhile)." if a.username else "."))
        self.state.account_changed.emit()

    def _notify(self, a: Account, events: list[live.Event]) -> None:
        """One notification per reading: after a night away, ten milestones are one message, not ten."""
        if len(events) == 1:
            self.alert.emit(_title(events[0], a), events[0].text)
        elif events:
            more = len(events) - 2
            self.alert.emit(f"{len(events)} updates on {a.handle}",
                            "\n".join(e.text for e in events[:2]) + (f"\n…and {more} more on the Live page." if more > 0
                                                                      else ""))

    def _share(self, now_please: bool) -> None:
        """Keep ClipMonster's results.json current without a Refresh (at most every half hour)."""
        st = self.state
        if not st.settings.share_results or (not now_please and time.time() - self.shared_at < SHARE_EVERY):
            return
        self.shared_at = time.time()
        from .. import results

        try:
            results.share(st.store)
        except OSError as exc:
            st.log(f"Couldn't share results with ClipMonster: {exc}")


def _title(e: live.Event, a: Account) -> str:
    return {"seen": f"Posted on {a.handle}", "hot": f"Taking off on {a.handle}",
            "milestone": f"Milestone on {a.handle}"}.get(e.kind, f"{a.handle} live")
