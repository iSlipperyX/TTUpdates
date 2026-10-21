"""Posting plan: turn the queue order into post times, and helpers to reorder the queue."""

from __future__ import annotations

import datetime as dt
from typing import Iterator

from .config import Settings
from .store import VideoItem


def _hm(text: str, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = text.strip().split(":")
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except (ValueError, AttributeError):
        return fallback


def daily_slots(s: Settings) -> list[dt.time]:
    """`posts_per_day` times spread evenly from first_post_time to last_post_time."""
    n = max(1, int(s.posts_per_day))
    h0, m0 = _hm(s.first_post_time, (9, 0))
    h1, m1 = _hm(s.last_post_time, (21, 0))
    start, end = h0 * 60 + m0, h1 * 60 + m1
    if end < start:
        end = start
    if n == 1:
        mins = [start]
    else:
        step = (end - start) / (n - 1)
        mins = [round(start + i * step) for i in range(n)]
    return [dt.time(m // 60 % 24, m % 60) for m in sorted(set(mins))]


def _slot_stream(s: Settings, after: dt.datetime) -> Iterator[dt.datetime]:
    slots = daily_slots(s)
    day = after.date()
    while True:
        for t in slots:
            when = dt.datetime.combine(day, t)
            if when >= after:
                yield when
        day += dt.timedelta(days=1)


def plan(queue: list[VideoItem], s: Settings, now: float, last_post: float | None) -> dict[str, float]:
    """Post time for every queued item, in queue order.

    Pinned items keep their time. Others take the next free daily slot that is at least
    `min_gap_minutes` after the previous post and not within the gap of a pinned post.
    """
    gap = dt.timedelta(minutes=max(0, s.min_gap_minutes))
    now_dt = dt.datetime.fromtimestamp(now).replace(second=0, microsecond=0)
    pinned = sorted(dt.datetime.fromtimestamp(i.pinned_at) for i in queue if i.pinned_at)
    earliest = now_dt
    if last_post:
        earliest = max(earliest, dt.datetime.fromtimestamp(last_post) + gap)

    out: dict[str, float] = {}
    slots = _slot_stream(s, earliest)
    prev: dt.datetime | None = None
    for item in queue:
        if item.pinned_at:
            out[item.id] = item.pinned_at
            continue
        for when in slots:
            if prev and when < prev + gap:
                continue
            if any(abs((when - p).total_seconds()) < gap.total_seconds() for p in pinned):
                continue
            out[item.id] = when.timestamp()
            prev = when
            break
    return out


def due(queue: list[VideoItem], now: float) -> VideoItem | None:
    """The first queued item whose time has come (failed items are retried only if re-queued)."""
    for item in queue:
        if item.status == "queued" and item.planned_at and item.planned_at <= now:
            return item
    return None


def by_score(items: list[VideoItem]) -> list[VideoItem]:
    return sorted(items, key=lambda i: (i.total is None, -(i.total or 0)))


def spread(items: list[VideoItem]) -> list[VideoItem]:
    """Best-first, but never two clips from the same source video back to back when avoidable."""
    pool = by_score(items)
    out: list[VideoItem] = []
    while pool:
        last = out[-1].source_group if out else None
        pick = next((i for i in pool if not last or i.source_group != last), pool[0])
        pool.remove(pick)
        out.append(pick)
    return out


def fmt_when(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "—"
    when = dt.datetime.fromtimestamp(ts)
    today = dt.datetime.fromtimestamp(now).date() if now else dt.date.today()
    if when.date() == today:
        day = "Today"
    elif when.date() == today + dt.timedelta(days=1):
        day = "Tomorrow"
    elif when.date() == today - dt.timedelta(days=1):
        day = "Yesterday"
    else:
        day = when.strftime("%a %d %b")
    return f"{day} {when.strftime('%H:%M')}"
