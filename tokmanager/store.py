"""The video library, upload queue and post history, saved as one JSON file."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
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
    queue_pos: int | None = None
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


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


class Store:
    """Thread-safe: scoring and upload workers update items from background threads."""

    def __init__(self, path: Path | None = None):
        self.path = path or (app_dir() / "library.json")
        self.lock = threading.RLock()
        self.items: dict[str, VideoItem] = {}
        self.profile = Profile()
        self.tiktok_videos: dict[str, TikTokVideo] = {}
        self.load()

    # --- persistence ------------------------------------------------------------

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self.lock:
            self.items = {d["id"]: _build(VideoItem, d) for d in data.get("items", []) if d.get("id")}
            self.profile = _build(Profile, data.get("profile") or {})
            self.tiktok_videos = {d["id"]: _build(TikTokVideo, d) for d in data.get("tiktok_videos", [])
                                  if d.get("id")}
            # A crash mid-upload leaves items stuck; put them back in the queue.
            for it in self.items.values():
                if it.status == "uploading":
                    it.status = "queued" if it.queue_pos is not None else "failed"

    def save(self) -> None:
        with self.lock:
            data = {
                "version": 1,
                "items": [asdict(i) for i in self.items.values()],
                "profile": asdict(self.profile),
                "tiktok_videos": [asdict(v) for v in self.tiktok_videos.values()],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    # --- library ----------------------------------------------------------------

    def add_paths(self, paths: list[Path]) -> list[VideoItem]:
        """Add video files (folders are searched recursively). Already-known files are skipped."""
        from .clipmonster import read_folder_index

        with self.lock:
            known = {os.path.normcase(os.path.abspath(i.path)) for i in self.items.values()}
            added: list[VideoItem] = []
            files: list[Path] = []
            for p in paths:
                p = Path(p)
                if p.is_dir():
                    files += sorted(f for f in p.rglob("*") if f.suffix.lower() in VIDEO_EXTS
                                    and ".downloads" not in f.parts and not f.name.startswith("."))
                elif p.suffix.lower() in VIDEO_EXTS:
                    files.append(p)
            index_cache: dict[Path, dict] = {}
            for f in files:
                key = os.path.normcase(os.path.abspath(f))
                if key in known:
                    continue
                known.add(key)
                item = VideoItem(path=str(f.resolve()), title=f.stem)
                if f.parent not in index_cache:
                    index_cache[f.parent] = read_folder_index(f.parent)
                meta = index_cache[f.parent].get(f.name)
                if meta:
                    item.title = meta.get("title") or item.title
                    item.source_group = meta.get("group", "")
                    item.source_score = meta.get("score")
                    item.source_reason = meta.get("reason", "")
                else:
                    item.source_group = str(f.parent)
                self.items[item.id] = item
                added.append(item)
            return added

    def remove(self, ids: list[str]) -> None:
        with self.lock:
            for i in ids:
                self.items.pop(i, None)
            self._renumber()

    def get(self, item_id: str) -> VideoItem | None:
        return self.items.get(item_id)

    def all(self) -> list[VideoItem]:
        with self.lock:
            return list(self.items.values())

    # --- queue ------------------------------------------------------------------

    def queue(self) -> list[VideoItem]:
        with self.lock:
            q = [i for i in self.items.values() if i.queue_pos is not None and i.status in ("queued", "uploading", "failed")]
            return sorted(q, key=lambda i: i.queue_pos)

    def enqueue(self, ids: list[str]) -> None:
        with self.lock:
            nxt = max((i.queue_pos for i in self.items.values() if i.queue_pos is not None), default=-1) + 1
            for item_id in ids:
                it = self.items.get(item_id)
                if not it or it.status == "posted" or it.queue_pos is not None:
                    continue
                it.queue_pos, it.status, it.last_error = nxt, "queued", ""
                nxt += 1
            self._renumber()

    def dequeue(self, ids: list[str]) -> None:
        with self.lock:
            for item_id in ids:
                it = self.items.get(item_id)
                if it and it.status != "uploading":
                    it.queue_pos, it.planned_at, it.pinned_at = None, None, None
                    if it.status in ("queued", "failed"):
                        it.status = "new"
            self._renumber()

    def set_order(self, ids: list[str]) -> None:
        with self.lock:
            for pos, item_id in enumerate(ids):
                if item_id in self.items:
                    self.items[item_id].queue_pos = pos
            self._renumber()

    def _renumber(self) -> None:
        q = sorted((i for i in self.items.values() if i.queue_pos is not None), key=lambda i: i.queue_pos)
        for pos, it in enumerate(q):
            it.queue_pos = pos

    def posted(self) -> list[VideoItem]:
        with self.lock:
            return sorted((i for i in self.items.values() if i.status == "posted"),
                          key=lambda i: i.posted_at or 0, reverse=True)

    def last_post_time(self) -> float | None:
        times = [i.posted_at for i in self.items.values() if i.posted_at]
        return max(times) if times else None

    def posts_since(self, since: float) -> int:
        return sum(1 for i in self.items.values() if i.posted_at and i.posted_at >= since)

    # --- account ----------------------------------------------------------------

    def update_tiktok_videos(self, videos: list[TikTokVideo]) -> None:
        now = time.time()
        with self.lock:
            for v in videos:
                old = self.tiktok_videos.get(v.id)
                if old:
                    v.history = old.history
                v.history = (v.history + [[now, v.views, v.likes, v.comments, v.shares]])[-60:]
                self.tiktok_videos[v.id] = v
            self.link_posts()

    def update_profile(self, p: Profile) -> None:
        with self.lock:
            p.history = (self.profile.history + [[time.time(), p.followers, p.likes, p.video_count]])[-120:]
            p.username = p.username or self.profile.username
            self.profile = p

    def link_posts(self) -> None:
        """Match items this app posted to videos on the account (by id, else by caption and time)."""
        with self.lock:
            taken = {i.tiktok_id for i in self.items.values() if i.tiktok_id}
            for it in self.items.values():
                if it.status != "posted" or it.tiktok_id:
                    continue
                want = _norm(it.caption)[:60]
                best = None
                for v in self.tiktok_videos.values():
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
        return self.tiktok_videos.get(item.tiktok_id) if item.tiktok_id else None


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())
