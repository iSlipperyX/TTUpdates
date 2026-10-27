"""The work behind each button, free of any GUI code so it can run on worker threads."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import schedule, scoring
from .accounts import Account, Accounts
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


def ai_item(store: Store, item_id: str, s: Settings, log: Log | None = None,
            account: Account | None = None) -> VideoItem:
    """Claude rates the video and writes a caption, for `account`'s niche when one is given."""
    from .ai import rate

    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    if not item.score:
        analyze_item(store, item_id, s)
    result = rate(item, account.apply(s) if account else s, log)
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


def post_item(store: Store, item_id: str, s: Settings, account: Account, log: Log, cancelled: Callable[[], bool],
              progress: Callable[[str], None] | None = None) -> VideoItem:
    """Post a queued video to the account whose queue it's in. `s` is the app-wide settings."""
    from . import tiktok

    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    if item.account != account.id:
        raise RuntimeError(f"“{item.name}” is queued for a different account.")
    with store.lock:
        item.status = "uploading"
        item.attempts += 1
        item.last_error = ""
        item.upload_started_at = time.time()
    store.save()
    try:
        result = tiktok.post(item, account.apply(s), account, log, cancelled, progress)
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
            if account.username:
                item.tiktok_url = f"https://www.tiktok.com/@{account.username}/video/{item.tiktok_id}"
        store._renumber()
    store.save()
    return item


def refresh_account(store: Store, s: Settings, account: Account, log: Log, cancelled: Callable[[], bool]) -> None:
    from . import tiktok

    profile, videos = tiktok.refresh_account(account.apply(s), account, log, cancelled)
    if profile.username and not account.username:
        account.username = profile.username
    store.update_profile(account.id, profile)
    store.update_tiktok_videos(account.id, videos)
    store.save()
    log(f"{account.handle} refreshed: {profile.followers:,} followers, {len(videos)} videos read.")
    if s.share_results:
        from . import results

        try:
            n = results.share(store)
        except OSError as exc:
            log(f"Couldn't share results with ClipMonster: {exc}")
        else:
            if n > 0:
                log(f"Shared how {n} clip{'s' if n != 1 else ''} did with ClipMonster.")


# --- scheduling across accounts ------------------------------------------------------


def replan(store: Store, accounts: Accounts | list[Account], s: Settings, now: float | None = None) -> None:
    """Work out every queued video's post time, using its own account's schedule and last post."""
    now = time.time() if now is None else now
    for a in accounts:
        q = store.queue(a.id)
        planned = schedule.plan([i for i in q if i.status == "queued"], a.apply(s), now, store.last_post_time(a.id))
        with store.lock:
            for it in q:
                it.planned_at = planned.get(it.id)


def can_autopost(account: Account, s: Settings) -> bool:
    """Autopilot is on for the account and it has a login to post with (browser method)."""
    return account.autopilot and (s.upload_method != "browser" or account.logged_in)


def autopilot_pick(store: Store, accounts: Accounts | list[Account], s: Settings,
                   now: float | None = None, log: Log | None = None) -> tuple[Account, VideoItem] | None:
    """The video autopilot should post now: the most overdue one among accounts with autopilot on.

    Due videos whose file has gone missing are marked failed (so they don't hold up the rest of
    that account's queue) instead of being returned.
    """
    now = time.time() if now is None else now
    replan(store, accounts, s, now)
    best: tuple[Account, VideoItem] | None = None
    for a in accounts:
        if not can_autopost(a, s):
            continue
        while (item := schedule.due(store.queue(a.id), now)) is not None and not item.exists():
            with store.lock:
                item.status, item.last_error = "failed", f"The video file is missing: {item.path}"
            if log:
                log(f"✖ {a.handle}: skipped “{item.name}”, its file is missing.")
        if item and (best is None or (item.planned_at or 0) < (best[1].planned_at or 0)):
            best = (a, item)
    return best


UPLOAD_CHECK_EVERY = 20 * 3600  # the quick upload-page check runs about once a day per autopilot account
UPLOAD_CHECK_QUIET = 30 * 60  # and never this close before a post is due


def upload_check_due(store: Store, accounts: Accounts | list[Account], s: Settings,
                     now: float | None = None) -> Account | None:
    """An autopilot account whose TikTok upload page is due its daily check (browser method only)."""
    now = time.time() if now is None else now
    if s.upload_method != "browser":
        return None
    for a in accounts:
        if not can_autopost(a, s) or now - a.upload_check_at < UPLOAD_CHECK_EVERY:
            continue
        nxt = next_post(store, a.id)
        if nxt and nxt.planned_at and nxt.planned_at - now < UPLOAD_CHECK_QUIET:
            continue
        return a
    return None


def next_post(store: Store, account_id: str | None = None) -> VideoItem | None:
    """The next queued video by planned time (for one account, or any)."""
    q = [i for i in store.queue(account_id) if i.status == "queued" and i.planned_at]
    return min(q, key=lambda i: i.planned_at) if q else None
