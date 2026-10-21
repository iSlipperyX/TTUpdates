"""The work behind each button, free of any GUI code so it can run on worker threads."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import scoring
from .config import Settings, thumbs_dir
from .media import analyze, probe, thumbnail
from .store import Store, VideoItem

Log = Callable[[str], None]


def ai_part(ai: dict) -> float | None:
    if not ai or "appeal" not in ai:
        return None
    return round(ai["appeal"] * 0.75 + ai.get("hook", ai["appeal"]) * 0.25, 1)


def analyze_item(store: Store, item_id: str, s: Settings) -> VideoItem:
    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    path = Path(item.path)
    try:
        info = probe(path)
        a = analyze(path, info)
        sc = scoring.score(a, s, item.source_score, ai_part(item.ai))
        thumb = thumbs_dir() / f"{item.id}.jpg"
        try:
            thumbnail(path, thumb, min(1.0, info.duration / 3))
        except Exception:
            thumb = None
    except Exception as exc:
        with store.lock:
            item.analyze_error = str(exc) or exc.__class__.__name__
            item.analyzed_at = time.time()
        raise
    with store.lock:
        item.info = a.to_json()["info"]
        item.analysis = a.to_json()
        item.score = sc.to_json()
        item.analyze_error = ""
        item.analyzed_at = time.time()
        if thumb:
            item.thumb = str(thumb)
    return item


def ai_item(store: Store, item_id: str, s: Settings, log: Log | None = None) -> VideoItem:
    from .ai import rate

    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    if not item.score:
        analyze_item(store, item_id, s)
    result = rate(item, s, log)
    with store.lock:
        item.ai = result
        if not item.caption.strip():
            item.caption = result.get("caption", "")
        if not item.hashtags.strip():
            item.hashtags = " ".join("#" + h for h in result.get("hashtags", []))
        recombine(item, s)
    return item


def recombine(item: VideoItem, s: Settings) -> None:
    """Recompute the total after weights change or an AI rating arrives (no re-analysis)."""
    if not item.score:
        return
    parts = dict(item.score.get("parts", {}))
    ai = ai_part(item.ai)
    if ai is not None:
        parts["ai"] = ai
        item.score.setdefault("notes", {})["ai"] = "Claude's prediction of viewer appeal"
    if item.source_score is not None:
        parts["source"] = round(max(0.0, min(100.0, item.source_score)), 1)
    item.score["parts"] = parts
    item.score["total"] = scoring.combine(parts, s)


def post_item(store: Store, item_id: str, s: Settings, log: Log, cancelled: Callable[[], bool],
              progress: Callable[[str], None] | None = None) -> VideoItem:
    from . import tiktok

    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    with store.lock:
        item.status = "uploading"
        item.attempts += 1
        item.last_error = ""
    store.save()
    try:
        result = tiktok.post(item, s, log, cancelled, progress)
    except Exception as exc:
        with store.lock:
            item.status = "failed"
            item.last_error = str(exc) or exc.__class__.__name__
        store.save()
        raise
    with store.lock:
        item.status = "posted"
        item.posted_at = time.time()
        item.queue_pos = None
        item.pinned_at = None
        item.planned_at = None
        item.publish_id = result.get("publish_id", "")
        if result.get("tiktok_id"):
            item.tiktok_id = result["tiktok_id"]
            if store.profile.username:
                item.tiktok_url = f"https://www.tiktok.com/@{store.profile.username}/video/{item.tiktok_id}"
        store._renumber()
    store.save()
    return item


def refresh_account(store: Store, s: Settings, log: Log, cancelled: Callable[[], bool]) -> None:
    from . import tiktok

    profile, videos = tiktok.refresh_account(s, store.profile.username, log, cancelled)
    store.update_profile(profile)
    store.update_tiktok_videos(videos)
    store.save()
    log(f"Account refreshed: {profile.followers:,} followers, {len(videos)} videos read.")
