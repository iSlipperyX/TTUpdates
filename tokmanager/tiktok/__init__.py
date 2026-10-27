"""One front door for posting and account refreshes, whichever method is set in Settings.

`s` is the account's own settings (Account.apply), so captions use that account's default hashtags
and privacy; `account` says whose browser profile or API tokens to use."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from ..accounts import Account
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
    if item.credit and item.credit not in caption:
        caption = f"{caption}\n{item.credit}"  # Creative Commons clips must say whose they are
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


def post(item: VideoItem, s: Settings, account: Account, log: Callable[[str], None], cancelled: Callable[[], bool],
         progress: Callable[[str], None] | None = None) -> dict:
    """Post one queued item. Returns {'publish_id', 'tiktok_id'} where known."""
    path = Path(item.path)
    if not path.is_file():
        raise FileNotFoundError(f"The video file is missing: {path}")
    caption = compose_caption(item, s)
    privacy = item.privacy or s.default_privacy
    if s.upload_method == "api":
        from .api import TikTokAPI

        status = TikTokAPI(s, account.tokens_path(), log).post(
            path, caption, privacy, s.allow_comments, s.allow_duet, s.allow_stitch, s.ai_generated_label,
            inbox=s.api_post_mode == "inbox", progress=progress, cancelled=cancelled, expected_user=account.username)
        ids = status.get("publicaly_available_post_id") or []
        return {"publish_id": status.get("publish_id", ""), "tiktok_id": str(ids[0]) if ids else ""}

    from .browser import TikTokBrowser

    with TikTokBrowser(s, account.browser_dir(), log, cancelled=cancelled) as b:
        b.upload(path, caption, privacy, s.allow_comments, s.allow_duet, s.allow_stitch,
                 s.ai_generated_label, progress=progress, expected_user=account.username)
    return {}


def refresh_account(s: Settings, account: Account, log: Callable[[str], None],
                    cancelled: Callable[[], bool]) -> tuple[Profile, list[TikTokVideo]]:
    username = account.username
    if s.upload_method == "api":
        from .api import TikTokAPI

        api = TikTokAPI(s, account.tokens_path(), log)
        profile = api.profile()
        return profile, api.videos(username=profile.username)

    from .browser import TikTokBrowser

    with TikTokBrowser(s, account.browser_dir(), log, cancelled=cancelled) as b:
        if not username:  # public profiles can be read logged out; finding "me" needs the login
            b.require_login()
        return b.fetch_account(username)


def check_upload(s: Settings, account: Account, log: Callable[[str], None], cancelled: Callable[[], bool],
                 full: bool = False) -> list[tuple[str, str, str]]:
    """Check that TikTok Studio's uploader still works for this account, without posting (browser method).

    The quick check opens the page; `full` also puts a 3 s black test clip in the uploader to find
    the caption box, privacy picker and Post button, then discards it.
    """
    from ..config import cache_dir
    from ..media import blank_clip
    from .browser import TikTokBrowser

    clip = blank_clip(cache_dir() / "upload-check.mp4") if full else None
    with TikTokBrowser(s, account.browser_dir(), log, cancelled=cancelled) as b:
        return b.check_upload_page(clip, expected_user=account.username)


def apply_profile(s: Settings, account: Account, log: Callable[[str], None], cancelled: Callable[[], bool]) -> list[str]:
    """Put the account's photo, name and bio (profile.py) on TikTok through its Edit profile box.

    Uses the account's browser login whichever posting method is set: TikTok's API can't edit
    profiles. Returns what was changed, e.g. ["photo", "bio"]; raises if TikTok didn't take it.
    """
    from ..profile import photo_path
    from .browser import TikTokBrowser

    with TikTokBrowser(s, account.browser_dir(), log, cancelled=cancelled) as b:
        b.require_login()
        return b.set_profile(name=account.profile_name, bio=account.profile_bio, photo=photo_path(account),
                             expected_user=account.username)
