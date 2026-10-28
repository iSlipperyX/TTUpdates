"""Live activity: each account connected to TikTok's official API is read every minute or so.

A reading (`read`, on a worker thread) is the account's profile numbers and its newest videos,
from the Display API: plain HTTPS, no browser window. `Watcher.apply` (on the GUI thread) compares
it with the reading before and turns the difference into events for the Live page:

- a video's views, likes, comments and shares moving ("+120 views, +8 likes on …"),
- followers coming and going,
- a new video on the account, and a queued clip found among them: you posted it from your phone,
  so it's marked as posted on the Ready page by itself,
- a video taking off (far more views in the last 10 minutes than it usually gets),
- milestones (1K, 10K, 100K… views on a video; 100, 1K, 10K… followers).

The numbers also go into the library's stats, so the Account page and ClipMonster's results stay
current. Events are kept in live/<account id>.jsonl in the app's data folder (the last 2,000).

Limits that come from TikTok: the API only lists *public* videos ("Only me" posts don't show),
its numbers can trail the TikTok app by a few minutes, and it has no comment text, only counts.
Nothing here needs GUI code, so it can be tested and run on worker threads.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

from .accounts import Account
from .config import Settings, app_dir
from .store import INTERRUPTED, Profile, Store, TikTokVideo, VideoItem, _norm

# How often to read, as Settings.live_interval offers it: (seconds, words)
INTERVALS = [(30, "every 30 s"), (60, "every minute"), (120, "every 2 min"), (300, "every 5 min"),
             (600, "every 10 min")]
MIN_INTERVAL = 30
RECENT_VIDEOS = 40  # newest videos read every time (two pages of TikTok's video list)
FULL_EVERY = 30 * 60  # all of them (up to ALL_VIDEOS) this often, so older videos stay current too
ALL_VIDEOS = 200
HISTORY_SPACING = 30 * 60  # the library keeps one point per half hour of live readings (store._add_point)
MAX_BACKOFF = 15 * 60  # after errors, wait at most this long before trying again
AWAY_AFTER = 10 * 60  # a gap longer than this since the last reading is summed up as "while you were away"
FEED_VIDEOS = 6  # videos named in the feed per reading; the rest are summed up in one line
MAX_EVENTS = 2000
VIEW_MILESTONES = (1_000, 10_000, 100_000, 1_000_000, 10_000_000)
FOLLOWER_MILESTONES = (100, 1_000, 10_000, 100_000, 1_000_000)
HOT_WINDOW = 10 * 60  # "taking off": views gained in the last 10 minutes…
HOT_MIN_VIEWS = 300  # …at least this many…
HOT_FACTOR = 3.0  # …and this many times the video's usual 10-minute gain
HOT_QUIET = 3600  # said at most once an hour per video
SERIES_SECONDS = 3 * 3600  # per-video readings kept in memory (for "taking off" and the trend lines)
SEEN_WITHIN = 2 * 86400  # a queued clip is only matched to videos posted in the last two days

# Error codes after which reading stops until the account is connected again
STOP_CODES = ("not_connected", "invalid_grant", "access_token_invalid", "scope_not_authorized")


@dataclass
class Event:
    """One line of the Live page's activity feed."""

    at: float
    kind: str  # start | moved | more | followers | new | seen | hot | milestone | away | error
    text: str
    video_id: str = ""
    views: int = 0  # what changed (for "today" totals); counted only from readings after `since`
    likes: int = 0
    comments: int = 0
    shares: int = 0
    followers: int = 0
    since: float = 0.0  # when the reading this was compared with was taken
    alert: bool = False  # worth a tray notification

    @classmethod
    def from_json(cls, d: dict) -> "Event | None":
        known = {f.name for f in fields(cls)}
        try:
            return cls(**{k: v for k, v in d.items() if k in known})
        except TypeError:
            return None


@dataclass
class Reading:
    """What one look at the account found."""

    at: float
    profile: Profile
    videos: list[TikTokVideo]
    full: bool = False  # all its videos were read, not only the newest


def read(s: Settings, account: Account, full: bool = False, log: Callable[[str], None] = lambda _m: None,
         now: Callable[[], float] = time.time) -> Reading:
    """Read the account's profile and videos from the official API (worker thread)."""
    from .tiktok.api import TikTokAPI

    api = TikTokAPI(s, account.tokens_path(), log)
    api.wait_on_rate_limit = False  # Watcher.failed backs off instead of holding this thread
    profile = api.profile()
    videos = api.videos(max_videos=ALL_VIDEOS if full else RECENT_VIDEOS, username=profile.username)
    return Reading(at=now(), profile=profile, videos=videos, full=full)


def fmt(n: int) -> str:
    n = int(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}{suffix}".replace(".0", "") if abs(v) < 100 else f"{v:.0f}{suffix}"
    return str(n)


def changes(views: int = 0, likes: int = 0, comments: int = 0, shares: int = 0, followers: int = 0) -> str:
    """'+120 views, +8 likes, −1 follower'."""
    bits = []
    for n, word in ((views, "view"), (likes, "like"), (comments, "comment"), (shares, "share"),
                    (followers, "follower")):
        if n:
            bits.append(f"{'+' if n > 0 else '−'}{fmt(abs(n))} {word}{'s' if abs(n) != 1 else ''}")
    return ", ".join(bits)


def video_name(v: TikTokVideo, item: VideoItem | None = None, limit: int = 48) -> str:
    """What to call a TikTok video in the feed: the clip's hook when it came from this app, else its caption."""
    text = " ".join(((item.headline if item else "") or v.description or "").split()) or "a video with no caption"
    return f"“{text[:limit - 1].rstrip()}…”" if len(text) > limit else f"“{text}”"


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    if seconds < 2 * 86400:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} days"


class EventLog:
    """The account's feed, kept as one JSON line per event."""

    def __init__(self, account_id: str, folder: Path | None = None):
        self.path = (folder or app_dir() / "live") / f"{account_id}.jsonl"
        self.events: list[Event] = []
        self.load()

    def load(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):
            lines = []
        out = []
        for line in lines[-MAX_EVENTS:]:
            try:
                e = Event.from_json(json.loads(line))
            except ValueError:  # a line cut short by a crash
                continue
            if e:
                out.append(e)
        self.events = out
        if len(lines) > MAX_EVENTS * 1.5:
            self._rewrite()

    def add(self, events: list[Event]) -> None:
        if not events:
            return
        self.events = (self.events + events)[-MAX_EVENTS:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
        except OSError:
            pass  # the feed still shows them; only the copy on disk is behind

    def _rewrite(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in self.events),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def clear(self) -> None:
        self.events = []
        try:
            self.path.unlink()
        except OSError:
            pass


def day_start(now: float) -> float:
    d = dt.datetime.fromtimestamp(now)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def today(events: list[Event], now: float | None = None) -> dict[str, int]:
    """What the feed saw change since local midnight: views, likes, comments, shares, followers.

    A reading compared with one from before midnight (after the app was closed overnight, say)
    isn't counted, because part of that change happened yesterday.
    """
    now = time.time() if now is None else now
    start = day_start(now)
    out = {"views": 0, "likes": 0, "comments": 0, "shares": 0, "followers": 0}
    for e in events:
        if e.at >= start and e.since >= start:
            for k in out:
                out[k] += getattr(e, k)
    return out


@dataclass
class VideoLive:
    """The live view's own record of one video: recent readings, for trends and "taking off"."""

    series: deque = field(default_factory=lambda: deque())  # (at, views)
    hot_at: float = 0.0

    def add(self, at: float, views: int) -> None:
        self.series.append((at, views))
        while self.series and at - self.series[0][0] > SERIES_SECONDS:
            self.series.popleft()

    def gain(self, seconds: float, at: float | None = None) -> int | None:
        """Views gained in the last `seconds` (None until there are readings that far back)."""
        if len(self.series) < 2:
            return None
        at = self.series[-1][0] if at is None else at
        older = [v for t, v in self.series if at - t >= seconds * 0.8]
        if not older:
            return None
        return int(self.series[-1][1] - older[-1])

    def usual_gain(self, seconds: float) -> float | None:
        """The median gain per `seconds` over the readings kept, leaving out the last window."""
        pts = list(self.series)
        if len(pts) < 4 or pts[-1][0] - pts[0][0] < seconds * 3:
            return None
        gains = []
        j = 0
        for i, (t, v) in enumerate(pts):
            while j < i and t - pts[j][0] > seconds:
                j += 1
            if t - pts[j][0] >= seconds * 0.8 and pts[-1][0] - t >= seconds:
                gains.append(v - pts[j][1])
        return float(statistics.median(gains)) if gains else None


class Watcher:
    """Turns successive readings of one account into feed events and keeps its stats current."""

    def __init__(self, account_id: str, log_folder: Path | None = None):
        self.account_id = account_id
        self.log = EventLog(account_id, log_folder)
        self.prev_at = 0.0
        self.prev: dict[str, tuple[int, int, int, int]] = {}  # video id -> views, likes, comments, shares
        self.prev_followers: int | None = None
        self.videos: dict[str, VideoLive] = {}
        self.last_full = 0.0
        self.last_ok = 0.0
        self.failures = 0
        self.stopped = ""  # why reading stopped until the account is connected again
        self.error = ""
        self.next_at = 0.0

    # --- scheduling ---------------------------------------------------------------

    def want_full(self, now: float) -> bool:
        return now - self.last_full >= FULL_EVERY

    def schedule(self, s: Settings, now: float) -> None:
        self.next_at = now + max(MIN_INTERVAL, int(s.live_interval or 60))

    def failed(self, s: Settings, message: str, code: str, now: float) -> list[Event]:
        """A reading didn't work: wait longer each time, or stop when the connection is gone."""
        self.failures += 1
        self.error = message
        events = []
        if code in STOP_CODES:
            self.stopped = message
            events.append(Event(at=now, kind="error", text=f"Stopped watching: {message}", alert=True))
        else:
            base = max(MIN_INTERVAL, int(s.live_interval or 60))
            wait = min(MAX_BACKOFF, base * 2 ** min(self.failures, 6))
            if code == "rate_limit_exceeded":
                wait = max(wait, 120)
            self.next_at = now + wait
            if self.failures == 1 or self.failures % 10 == 0:  # don't fill the feed while TikTok is down
                events.append(Event(at=now, kind="error",
                                    text=f"Couldn't read TikTok: {message} Trying again in {ago(wait)}."))
        self.log.add(events)
        return events

    def resume(self) -> None:
        """The account was connected again (or watching was turned back on)."""
        self.stopped = self.error = ""
        self.failures = 0
        self.next_at = 0.0

    # --- a reading ------------------------------------------------------------------

    def _seed(self, store: Store) -> None:
        """Before the first reading of this run: compare with the stats the library already has."""
        with store.lock:
            vids = store.videos(self.account_id)
            p = store.profile(self.account_id)
            self.prev = {v.id: (v.views, v.likes, v.comments, v.shares) for v in vids.values()}
            last = max((v.history[-1][0] for v in vids.values() if v.history), default=0.0)
            self.prev_at = max(last, p.fetched_at)
            self.prev_followers = p.followers if p.fetched_at else None

    def apply(self, store: Store, account: Account, s: Settings, r: Reading) -> list[Event]:
        """Take a reading in: events for the feed (already added to its log), stats saved to the store."""
        from .tiktok import compose_caption

        first_of_run = not self.last_ok
        if first_of_run:
            self._seed(store)
        never_read = not self.prev and self.prev_followers is None
        since = self.prev_at
        gap = r.at - since if since else 0.0
        away = bool(since) and gap > AWAY_AFTER
        self.failures, self.error, self.stopped = 0, "", ""
        self.last_ok = r.at
        if r.full:
            self.last_full = r.at
        self.schedule(s, r.at)

        with store.lock:
            items = {i.tiktok_id: i for i in store.items.values() if i.account == account.id and i.tiktok_id}
        events: list[Event] = []
        if never_read:
            events.append(Event(at=r.at, kind="start", text=(
                f"Watching {account.handle} live: {fmt(r.profile.followers)} followers, "
                f"{len(r.videos)} videos read. Changes show up here from the next reading.")))

        # Videos that weren't on the account before: posted just now (by phone, or elsewhere)
        seen_events: list[Event] = []
        new_ids = [] if never_read else [v.id for v in r.videos if v.id not in self.prev and self._is_new(v, since, r)]
        for v in r.videos:
            if v.id not in new_ids and not (never_read and r.at - v.create_time < SEEN_WITHIN):
                continue
            it = match_queued(store, account, v, s, r.at, compose_caption)
            if it is not None:
                from .ready import mark_posted

                if mark_posted(store, it.id, now=v.create_time or r.at, via="seen"):
                    with store.lock:
                        it.tiktok_id, it.tiktok_url = v.id, v.url or it.tiktok_url
                    items[v.id] = it
                    seen_events.append(Event(at=r.at, kind="seen", video_id=v.id, alert=True, since=since, text=(
                        f"✔ {video_name(v, it)} is on TikTok: marked as posted (it was waiting on the Ready page).")))
            if v.id in new_ids:
                when = f", posted {ago(r.at - v.create_time)} ago" if v.create_time else ""
                events.append(Event(at=r.at, kind="new", video_id=v.id, since=since,
                                    views=v.views if not away else 0,
                                    text=f"New video on {account.handle}: {video_name(v, items.get(v.id))}{when}."))
        events += seen_events

        # How each known video moved
        moved: list[tuple[int, TikTokVideo, tuple[int, int, int, int]]] = []
        for v in r.videos:
            live = self.videos.setdefault(v.id, VideoLive())
            live.add(r.at, v.views)
            before = self.prev.get(v.id)
            if before is None:
                continue
            d = (v.views - before[0], v.likes - before[1], v.comments - before[2], v.shares - before[3])
            if any(d):
                moved.append((d[0], v, d))
            for m in VIEW_MILESTONES:
                if before[0] < m <= v.views:
                    events.append(Event(at=r.at, kind="milestone", video_id=v.id, alert=True, since=since,
                                        text=f"🏆 {video_name(v, items.get(v.id))} passed {fmt(m)} views."))
            hot = self._taking_off(v, live, r.at)
            if hot:
                events.append(Event(at=r.at, kind="hot", video_id=v.id, alert=True, since=since, text=hot))

        moved.sort(key=lambda m: (abs(m[2][0]), sum(abs(x) for x in m[2][1:])), reverse=True)
        if away and moved:
            total = [sum(m[2][k] for m in moved) for k in range(4)]
            events.append(Event(at=r.at, kind="away", since=since, views=total[0], likes=total[1],
                                comments=total[2], shares=total[3], text=(
                                    f"Since {ago(gap)} ago: {changes(*total)} across {len(moved)} "
                                    f"video{'s' if len(moved) != 1 else ''}.")))
        elif moved:
            for _, v, d in moved[:FEED_VIDEOS]:
                events.append(Event(at=r.at, kind="moved", video_id=v.id, since=since, views=d[0], likes=d[1],
                                    comments=d[2], shares=d[3],
                                    text=f"{changes(*d)} on {video_name(v, items.get(v.id))}"))
            rest = moved[FEED_VIDEOS:]
            if rest:
                total = [sum(m[2][k] for m in rest) for k in range(4)]
                events.append(Event(at=r.at, kind="more", since=since, views=total[0], likes=total[1],
                                    comments=total[2], shares=total[3],
                                    text=f"{changes(*total)} across {len(rest)} more videos"))

        # Followers
        f_now = r.profile.followers
        if self.prev_followers is not None and f_now != self.prev_followers:
            d = f_now - self.prev_followers
            events.append(Event(at=r.at, kind="followers", followers=d, since=since,
                                text=f"{changes(followers=d)} ({fmt(f_now)} now)"
                                + (f" since {ago(gap)} ago" if away else "")))
            for m in FOLLOWER_MILESTONES:
                if self.prev_followers < m <= f_now:
                    events.append(Event(at=r.at, kind="milestone", alert=True, since=since,
                                        text=f"🏆 {account.handle} reached {fmt(m)} followers."))

        # Into the library, and remember this reading for the next one
        store.update_profile(account.id, r.profile, now=r.at, spacing=HISTORY_SPACING)
        store.update_tiktok_videos(account.id, r.videos, now=r.at, spacing=HISTORY_SPACING)
        if r.profile.username and not account.username:
            account.username = r.profile.username
        for v in r.videos:
            self.prev[v.id] = (v.views, v.likes, v.comments, v.shares)
        self.prev_followers = f_now
        self.prev_at = r.at
        self.log.add(events)
        return events

    @staticmethod
    def _is_new(v: TikTokVideo, since: float, r: Reading) -> bool:
        """A video the last reading didn't have that really is new, not just one it didn't reach
        (an older video first seen in a full reading)."""
        if not v.create_time:
            return not r.full
        return v.create_time >= (since or r.at) - 3600

    def _taking_off(self, v: TikTokVideo, live: VideoLive, now: float) -> str:
        gain = live.gain(HOT_WINDOW, now)
        if gain is None or gain < HOT_MIN_VIEWS or now - live.hot_at < HOT_QUIET:
            return ""
        usual = live.usual_gain(HOT_WINDOW)
        if usual is None or gain < HOT_FACTOR * max(usual, 1.0):
            return ""
        live.hot_at = now
        return (f"🔥 {video_name(v)} is taking off: +{fmt(gain)} views in the last 10 minutes, "
                f"about {gain / max(usual, 1.0):.0f}× its usual pace.")

    # --- for the Live page --------------------------------------------------------------

    def trend(self, video_id: str) -> list[tuple[float, int]]:
        live = self.videos.get(video_id)
        return list(live.series) if live else []

    def last_hour(self, video_id: str) -> int | None:
        live = self.videos.get(video_id)
        return live.gain(3600) if live else None


def match_queued(store: Store, account: Account, v: TikTokVideo, s: Settings, now: float,
                 compose: Callable[[VideoItem, Settings], str]) -> VideoItem | None:
    """The clip waiting in the account's queue that this new TikTok video is, if exactly one fits.

    It's the same clip when the video's caption is what the Ready page gave you to paste (its
    caption with hashtags), or starts with the same 40 characters, and it went up after the clip
    arrived and within the last two days. Two clips that fit equally well match neither, so
    nothing is marked posted by mistake.
    """
    desc = _norm(v.description)
    if len(desc) < 8 or (v.create_time and now - v.create_time > SEEN_WITHIN):
        return None
    settings = account.apply(s)
    exact, prefix = [], []
    with store.lock:
        queue = [i for i in store.queue(account.id)
                 if i.status in ("queued", "failed") and not i.tiktok_id
                 and not (i.status == "failed" and i.last_error == INTERRUPTED)]  # link_posts handles those
    for it in queue:
        if v.create_time and v.create_time < it.added_at - 600:
            continue
        texts = {t for t in (_norm(compose(it, settings)), _norm(it.caption)) if len(t) >= 8}
        if desc in texts:
            exact.append(it)
        elif any(desc.startswith(t[:40]) or (len(desc) >= 20 and t.startswith(desc[:40]))
                 for t in texts if len(t) >= 20):
            prefix.append(it)
    if len(exact) == 1:
        return exact[0]
    if not exact and len(prefix) == 1:
        return prefix[0]
    return None
