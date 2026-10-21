"""One front door for posting and account refreshes, whichever method is set in Settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..store import Profile, TikTokVideo, VideoItem

MAX_CAPTION = 2200


def normalize_hashtags(text: str) -> str:
    tags = []
    for raw in re.split(r"[\s,]+", text or ""):
        raw = raw.strip()
        if not raw:
            continue
        tag = "#" + re.sub(r"[^\w]", "", raw.lstrip("#"))
        if len(tag) > 1 and tag.lower() not in (t.lower() for t in tags):
            tags.append(tag)
    return " ".join(tags)


def compose_caption(item: VideoItem, s: Settings) -> str:
    caption = (item.caption or item.name).strip()
    tags = normalize_hashtags(f"{item.hashtags} {s.default_hashtags}")
    # Don't repeat tags already written into the caption itself.
    present = {t.lower() for t in re.findall(r"#\w+", caption)}
    tags = " ".join(t for t in tags.split() if t.lower() not in present)
    try:
        text = s.caption_template.format(caption=caption, hashtags=tags, title=item.name)
    except (KeyError, ValueError, IndexError):
        text = f"{caption} {tags}"
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:MAX_CAPTION]


def post(item: VideoItem, s: Settings, log: Callable[[str], None], cancelled: Callable[[], bool],
         progress: Callable[[str], None] | None = None) -> dict:
    """Post one queued item. Returns {'publish_id', 'tiktok_id'} where known."""
    path = Path(item.path)
    if not path.is_file():
        raise FileNotFoundError(f"The video file is missing: {path}")
    caption = compose_caption(item, s)
    privacy = item.privacy or s.default_privacy
    if s.upload_method == "api":
        from .api import TikTokAPI

        status = TikTokAPI(s, log).post(
            path, caption, privacy, s.allow_comments, s.allow_duet, s.allow_stitch, s.ai_generated_label,
            inbox=s.api_post_mode == "inbox", progress=progress, cancelled=cancelled)
        ids = status.get("publicaly_available_post_id") or []
        return {"publish_id": status.get("publish_id", ""), "tiktok_id": str(ids[0]) if ids else ""}

    from .browser import TikTokBrowser

    with TikTokBrowser(s, log, cancelled=cancelled) as b:
        b.upload(path, caption, privacy, s.allow_comments, s.allow_duet, s.allow_stitch,
                 s.ai_generated_label, progress=progress)
    return {}


def refresh_account(s: Settings, username: str, log: Callable[[str], None],
                    cancelled: Callable[[], bool]) -> tuple[Profile, list[TikTokVideo]]:
    if s.upload_method == "api":
        from .api import TikTokAPI

        api = TikTokAPI(s, log)
        profile = api.profile()
        return profile, api.videos(username=profile.username)

    from .browser import TikTokBrowser

    with TikTokBrowser(s, log, cancelled=cancelled) as b:
        if not username:  # public profiles can be read logged out; finding "me" needs the login
            b.require_login()
        return b.fetch_account(username)
