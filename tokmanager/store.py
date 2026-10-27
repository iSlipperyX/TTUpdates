"""The video library, each account's upload queue and stats, and the post history, saved as one JSON file."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from .config import app_dir
from .media import VIDEO_EXTS

# Item status lifecycle: new -> queued -> uploading -> posted | failed
STATUSES = ("new", "queued", "uploading", "posted", "failed")


@dataclass
class VideoItem:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    path: str = ""
    title: str = ""
    added_at: float = field(default_factory=time.time)
    # Where the clip came from (ClipMonster folder / source video), used to spread similar clips apart
    source_group: str = ""
    source_score: float | None = None
    source_reason: str = ""
    source_channel: str = ""
    source_url: str = ""
    source_permission: str = ""  # why ClipMonster's picking rules allowed this video
    source_rating: int | None = None  # ClipMonster's AI "does it stand alone" rating, 1-10
    hook: str = ""  # ClipMonster's AI hook line (also the starting caption)
    warnings: list[str] = field(default_factory=list)  # ClipMonster's quality check notes
    credit: str = ""  # a line the caption must carry (Creative Commons credit)
    imported_by: str = ""  # "watch" when a watched folder brought it in
    reminded_at: float | None = None  # when the "time to post" reminder went out
    # Analysis + score
    info: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    score: dict = field(default_factory=dict)
    analyzed_at: float = 0.0
    analyze_error: str = ""
    thumb: str = ""
    ai: dict = field(default_factory=dict)  # score, hook, verdict, caption, hashtags, ideas
    # Post settings
    caption: str = ""
    hashtags: str = ""
    privacy: str = ""  # "" = use the default from settings
    # Queue
    status: str = "new"
    account: str = ""  # id of the account it's queued for / was posted to ("" while it's just in the library)
    queue_pos: int | None = None  # position in that account's queue
    pinned_at: float | None = None  # a fixed post time picked by the user (epoch seconds)
    planned_at: float | None = None  # computed by the scheduler
    # Result
    attempts: int = 0
    last_error: str = ""
    posted_at: float | None = None
    tiktok_id: str = ""
    tiktok_url: str = ""
    publish_id: str = ""

    @property
    def total(self) -> float | None:
        return self.score.get("total") if self.score else None

    @property
    def duration(self) -> float:
        return float(self.info.get("duration") or 0.0)

    @property
    def name(self) -> str:
        return self.title or Path(self.path).stem

    def exists(self) -> bool:
        return Path(self.path).is_file()


@dataclass
class TikTokVideo:
    """A video on the TikTok account, with a short history of its numbers."""

    id: str
    description: str = ""
    create_time: float = 0.0
    url: str = ""
    cover: str = ""
    duration: float = 0.0
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    history: list[list[float]] = field(default_factory=list)  # [fetched_at, views, likes, comments, shares]

    @property
    def engagement(self) -> float:
        return (self.likes + self.comments + self.shares + self.saves) / self.views * 100 if self.views else 0.0

    def views_change(self) -> int | None:
        if len(self.history) < 2:
            return None
        return int(self.history[-1][1] - self.history[-2][1])


@dataclass
class Profile:
    username: str = ""
    nickname: str = ""
    avatar: str = ""
    followers: int = 0
    following: int = 0
    likes: int = 0
    video_count: int = 0
    fetched_at: float = 0.0
    history: list[list[float]] = field(default_factory=list)  # [fetched_at, followers, likes, videos]


@dataclass
class AccountStats:
    """What was last read from one TikTok account: profile numbers and its videos."""

    profile: Profile = field(default_factory=Profile)
    tiktok_videos: dict[str, TikTokVideo] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"profile": asdict(self.profile), "tiktok_videos": [asdict(v) for v in self.tiktok_videos.values()]}

    @classmethod
    def from_json(cls, data: dict) -> "AccountStats":
        return cls(profile=_build(Profile, data.get("profile") or {}),
                   tiktok_videos={d["id"]: _build(TikTokVideo, d) for d in data.get("tiktok_videos", [])
                                  if d.get("id")})


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


class Store:
    """Thread-safe: scoring and upload workers update items from background threads."""

    def __init__(self, path: Path | None = None):
        self.path = path or (app_dir() / "library.json")
        self.lock = threading.RLock()
        self.items: dict[str, VideoItem] = {}
        self.stats: dict[str, AccountStats] = {}  # by account id
        self.ignored: set[str] = set()  # files removed from the library, which watched folders mustn't bring back
        self.load()

    # --- persistence ------------------------------------------------------------

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self.lock:
            self.items = {d["id"]: _build(VideoItem, d) for d in data.get("items", []) if d.get("id")}
            self.ignored = set(data.get("ignored") or [])
            accounts = data.get("accounts")
            if isinstance(accounts, dict):
                self.stats = {aid: AccountStats.from_json(d or {}) for aid, d in accounts.items()}
            elif data.get("profile") or data.get("tiktok_videos"):
                # Version 1 had one account; its stats wait under "" until adopt() gives them an owner.
                self.stats = {"": AccountStats.from_json(data)}
            # A crash mid-upload leaves items stuck; put them back in the queue.
            for it in self.items.values():
                if it.status == "uploading":
                    it.status = "queued" if it.queue_pos is not None else "failed"

    def save(self) -> None:
        with self.lock:
            data = {
                "version": 2,
                "items": [asdict(i) for i in self.items.values()],
                "accounts": {aid: st.to_json() for aid, st in self.stats.items()},
                "ignored": sorted(self.ignored),
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def adopt(self, account_ids: list[str], fallback: str) -> bool:
        """Give every queue entry, post and set of stats an account that exists.

        Entries from a single-account version (account "") go to `fallback`. Queued or failed
        videos of an account that no longer exists go back to the library. Posted ones keep
        their old account id so the history still says where they went. Returns True if
        anything changed.
        """
        known = set(account_ids)
        changed = False
        with self.lock:
            for it in self.items.values():
                if it.account in known:
                    continue
                if not it.account and (it.queue_pos is not None or it.status != "new" or it.attempts):
                    it.account = fallback
                    changed = True
                elif it.account and it.status != "posted":
                    self._unqueue(it)
                    changed = True
            legacy = self.stats.pop("", None)
            if legacy is not None:
                if fallback not in self.stats or not self.stats[fallback].profile.fetched_at:
                    self.stats[fallback] = legacy
                changed = True
            for aid in [a for a in self.stats if a not in known]:
                del self.stats[aid]
                changed = True
            if changed:
                self._renumber()
        return changed

    def forget_account(self, account_id: str) -> None:
        """An account was removed: its queue goes back to the library, its stats are dropped."""
        with self.lock:
            for it in self.items.values():
                if it.account == account_id and it.status != "posted":
                    self._unqueue(it)
            self.stats.pop(account_id, None)
            self._renumber()

    # --- library ----------------------------------------------------------------

    def add_paths(self, paths: list[Path], watched: bool = False, now: float | None = None,
                  settle: float = 60.0) -> list[VideoItem]:
        """Add video files (folders are searched recursively). Already-known files are skipped, and so
        are clips ClipMonster's quality check rejected.

        `watched` is a watched folder's automatic import: files removed from the library before stay
        out, and a file is only taken once it's finished, meaning ClipMonster has listed it in its
        clips.json, or (for other files) nothing has written to it for `settle` seconds. Adding by hand
        brings a removed file back.
        """
        from .clipmonster import REJECTED_DIR, credit_line, needs_credit, read_folder_index

        now = time.time() if now is None else now
        with self.lock:
            known = {_key(i.path) for i in self.items.values()}
            added: list[VideoItem] = []
            files: list[Path] = []
            for p in paths:
                p = Path(p)
                if p.is_dir():
                    files += sorted(f for f in p.rglob("*") if f.suffix.lower() in VIDEO_EXTS
                                    and ".downloads" not in f.parts and REJECTED_DIR not in f.parts
                                    and not f.name.startswith("."))
                elif p.suffix.lower() in VIDEO_EXTS:
                    files.append(p)
            index_cache: dict[Path, dict] = {}
            for f in files:
                key = _key(f)
                if key in known or (watched and key in self.ignored):
                    continue
                if f.parent not in index_cache:
                    index_cache[f.parent] = read_folder_index(f.parent)
                meta = index_cache[f.parent].get(f.name)
                if meta and meta.get("rejected"):
                    continue
                if watched and not meta:
                    try:
                        if now - f.stat().st_mtime < settle:
                            continue  # still being written
                    except OSError:
                        continue
                known.add(key)
                self.ignored.discard(key)
                item = VideoItem(path=str(f.resolve()), title=f.stem, imported_by="watch" if watched else "")
                if meta:
                    item.title = meta.get("title") or item.title
                    item.source_group = meta.get("group", "")
                    item.source_score = meta.get("score")
                    item.source_reason = meta.get("reason", "")
                    item.source_channel = meta.get("channel", "")
                    item.source_url = meta.get("url", "")
                    item.source_permission = meta.get("permission", "")
                    item.source_rating = meta.get("rating")
                    item.hook = meta.get("hook", "")
                    item.caption = item.hook
                    item.warnings = list(meta.get("warnings") or [])
                    if needs_credit(meta):
                        item.credit = credit_line(item.source_channel)
                else:
                    item.source_group = str(f.parent)
                self.items[item.id] = item
                added.append(item)
            return added

    def remove(self, ids: list[str]) -> None:
        with self.lock:
            for i in ids:
                it = self.items.pop(i, None)
                if it:
                    self.ignored.add(_key(it.path))
            self._renumber()

    def get(self, item_id: str) -> VideoItem | None:
        return self.items.get(item_id)

    def all(self) -> list[VideoItem]:
        with self.lock:
            return list(self.items.values())

    def copy_for(self, item_id: str, account: str) -> VideoItem | None:
        """Queue the same video for another account as a separate entry (its own status and stats)."""
        with self.lock:
            src = self.items.get(item_id)
            if src is None:
                return None
            fresh = VideoItem()
            copy = replace(src, id=fresh.id, added_at=fresh.added_at, info=deepcopy(src.info),
                           analysis=deepcopy(src.analysis), score=deepcopy(src.score), ai=deepcopy(src.ai),
                           status="new", account="", queue_pos=None, pinned_at=None, planned_at=None,
                           attempts=0, last_error="", posted_at=None, tiktok_id="", tiktok_url="", publish_id="",
                           warnings=list(src.warnings), reminded_at=None)
            self.items[copy.id] = copy
            self.enqueue([copy.id], account)
            return copy

    # --- queue ------------------------------------------------------------------

    def queue(self, account: str | None = None) -> list[VideoItem]:
        """One account's queue in order (every account's when `account` is None)."""
        with self.lock:
            q = [i for i in self.items.values() if i.queue_pos is not None
                 and i.status in ("queued", "uploading", "failed") and (account is None or i.account == account)]
            return sorted(q, key=lambda i: i.queue_pos)

    def enqueue(self, ids: list[str], account: str) -> None:
        """Add videos to the end of `account`'s queue (videos in another queue are left where they are)."""
        with self.lock:
            nxt = max((i.queue_pos for i in self.items.values()
                       if i.queue_pos is not None and i.account == account), default=-1) + 1
            for item_id in ids:
                it = self.items.get(item_id)
                if not it or it.status == "posted" or it.queue_pos is not None:
                    continue
                it.queue_pos, it.status, it.last_error, it.account = nxt, "queued", "", account
                nxt += 1
            self._renumber()

    def move_to(self, ids: list[str], account: str) -> None:
        """Move queued videos to the end of another account's queue."""
        with self.lock:
            movable = [i for i in ids if (it := self.items.get(i)) and it.queue_pos is not None
                       and it.status != "uploading" and it.account != account]
            for item_id in movable:
                it = self.items[item_id]
                it.queue_pos, it.planned_at, it.pinned_at, it.status = None, None, None, "new"
            self.enqueue(movable, account)

    def dequeue(self, ids: list[str]) -> None:
        with self.lock:
            for item_id in ids:
                it = self.items.get(item_id)
                if it and it.status != "uploading":
                    self._unqueue(it)
            self._renumber()

    def _unqueue(self, it: VideoItem) -> None:
        it.queue_pos, it.planned_at, it.pinned_at = None, None, None
        if it.status in ("queued", "failed", "uploading"):
            it.status, it.account = "new", ""
        elif it.status == "new":
            it.account = ""

    def set_order(self, ids: list[str]) -> None:
        with self.lock:
            for pos, item_id in enumerate(ids):
                if item_id in self.items:
                    self.items[item_id].queue_pos = pos
            self._renumber()

    def _renumber(self) -> None:
        """Positions 0, 1, 2… within each account's queue."""
        q = sorted((i for i in self.items.values() if i.queue_pos is not None), key=lambda i: i.queue_pos)
        nxt: dict[str, int] = {}
        for it in q:
            it.queue_pos = nxt.get(it.account, 0)
            nxt[it.account] = it.queue_pos + 1

    def posted(self, account: str | None = None) -> list[VideoItem]:
        with self.lock:
            return sorted((i for i in self.items.values() if i.status == "posted"
                           and (account is None or i.account == account)),
                          key=lambda i: i.posted_at or 0, reverse=True)

    def last_post_time(self, account: str | None = None) -> float | None:
        times = [i.posted_at for i in self.items.values()
                 if i.posted_at and (account is None or i.account == account)]
        return max(times) if times else None

    def posts_since(self, since: float, account: str | None = None) -> int:
        return sum(1 for i in self.items.values()
                   if i.posted_at and i.posted_at >= since and (account is None or i.account == account))

    # --- account ----------------------------------------------------------------

    def stats_for(self, account: str) -> AccountStats:
        with self.lock:
            if account not in self.stats:
                self.stats[account] = AccountStats()
            return self.stats[account]

    def profile(self, account: str) -> Profile:
        return self.stats_for(account).profile

    def videos(self, account: str) -> dict[str, TikTokVideo]:
        return self.stats_for(account).tiktok_videos

    def update_tiktok_videos(self, account: str, videos: list[TikTokVideo]) -> None:
        now = time.time()
        with self.lock:
            known = self.videos(account)
            for v in videos:
                old = known.get(v.id)
                if old:
                    v.history = old.history
                v.history = (v.history + [[now, v.views, v.likes, v.comments, v.shares]])[-60:]
                known[v.id] = v
            self.link_posts(account)

    def update_profile(self, account: str, p: Profile) -> None:
        with self.lock:
            st = self.stats_for(account)
            p.history = (st.profile.history + [[time.time(), p.followers, p.likes, p.video_count]])[-120:]
            p.username = p.username or st.profile.username
            st.profile = p

    def link_posts(self, account: str) -> None:
        """Match items this app posted to the account's videos (by id, else by caption and time)."""
        with self.lock:
            mine = [i for i in self.items.values() if i.account == account]
            videos = self.videos(account)
            taken = {i.tiktok_id for i in mine if i.tiktok_id}
            for it in mine:
                if it.status != "posted" or it.tiktok_id:
                    continue
                want = _norm(it.caption)[:60]
                best = None
                for v in videos.values():
                    if v.id in taken:
                        continue
                    close_in_time = it.posted_at and abs(v.create_time - it.posted_at) < 6 * 3600
                    same_text = want and _norm(v.description).startswith(want[:40])
                    if same_text and (close_in_time or not v.create_time):
                        best = v
                        break
                    if close_in_time and not want and best is None:
                        best = v
                if best:
                    it.tiktok_id, it.tiktok_url = best.id, best.url
                    taken.add(best.id)

    def video_for(self, item: VideoItem) -> TikTokVideo | None:
        if not item.tiktok_id or item.account not in self.stats:
            return None
        return self.stats[item.account].tiktok_videos.get(item.tiktok_id)


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())
