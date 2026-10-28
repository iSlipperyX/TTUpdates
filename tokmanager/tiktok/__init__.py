"""One front door for posting, account refreshes and the official API connection.

Each account posts the way `Account.post_with` says (methods.py): through its own browser
profile, or through TikTok's official API. `s` is the account's own settings (Account.apply), so
captions use that account's default hashtags and privacy; `account` says whose browser profile
or API tokens to use."""

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
    """Post one queued item the account's way. Returns {'publish_id', 'tiktok_id', 'via'} where known."""
    from . import methods

    path = Path(item.path)
    if not path.is_file():
        raise FileNotFoundError(f"The video file is missing: {path}")
    caption = compose_caption(item, s)
    privacy = item.privacy or s.default_privacy
    if methods.uses_api(account):
        from .api import TikTokAPI

        ready, why = methods.can_post(s, account)
        if not ready:
            raise RuntimeError(why)
        drafts = account.post_with == methods.API_DRAFT
        status = TikTokAPI(s, account.tokens_path(), log).post(
            path, caption, privacy, s.allow_comments, s.allow_duet, s.allow_stitch, s.ai_generated_label,
            inbox=drafts, progress=progress, cancelled=cancelled, expected_user=account.username,
            audited=s.api_audited, duration=item.duration)
        ids = status.get("publicaly_available_post_id") or []
        return {"publish_id": status.get("publish_id", ""), "tiktok_id": str(ids[0]) if ids else "",
                "via": account.post_with}

    from .browser import TikTokBrowser

    with TikTokBrowser(s, account.browser_dir(), log, cancelled=cancelled) as b:
        b.upload(path, caption, privacy, s.allow_comments, s.allow_duet, s.allow_stitch,
                 s.ai_generated_label, progress=progress, expected_user=account.username)
    return {"via": methods.BROWSER}


def refresh_account(s: Settings, account: Account, log: Callable[[str], None],
                    cancelled: Callable[[], bool]) -> tuple[Profile, list[TikTokVideo]]:
    """The account's profile numbers and videos: from the official API when it's connected (no
    browser window), else (or when the API can't answer) from its browser login."""
    from . import methods

    username = account.username
    if methods.app_ready(s) and methods.api_connected(account):
        import requests

        from .api import ApiError, TikTokAPI

        try:
            api = TikTokAPI(s, account.tokens_path(), log)
            profile = api.profile()
            return profile, api.videos(username=profile.username)
        except (ApiError, requests.RequestException, OSError, ValueError) as exc:
            if not account.logged_in:
                raise
            log(f"The TikTok API couldn't read {account.handle}'s stats ({exc}); reading them through the browser.")

    from .browser import TikTokBrowser

    with TikTokBrowser(s, account.browser_dir(), log, cancelled=cancelled) as b:
        if not username:  # public profiles can be read logged out; finding "me" needs the login
            b.require_login()
        return b.fetch_account(username)


def connect_api(s: Settings, account: Account, log: Callable[[str], None],
                cancelled: Callable[[], bool]) -> str:
    """Connect the account to the official API: TikTok's approval page opens in the account's own
    browser profile (where it's already logged in to TikTok). Returns the approved @username."""
    from .api import TikTokAPI
    from .login import BrowserWindow

    api = TikTokAPI(s, account.tokens_path(), log)
    api.connect(cancelled=cancelled, open_page=lambda url: BrowserWindow.open(s, account.browser_dir(), url))
    return api.profile().username


def disconnect_api(s: Settings, account: Account, log: Callable[[str], None]) -> None:
    from .api import TikTokAPI

    TikTokAPI(s, account.tokens_path(), log).disconnect()


def check_api(s: Settings, account: Account, log: Callable[[str], None]) -> tuple[str, list[str]]:
    """Test the account's API connection for how it posts, without posting: ("ok" | "warn", lines)."""
    from . import methods
    from .api import TikTokAPI

    return TikTokAPI(s, account.tokens_path(), log).check(drafts=account.post_with == methods.API_DRAFT,
                                                         audited=s.api_audited)


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


def apply_profile(s: Settings, account: Account, log: Callable[[str], None],
                  cancelled: Callable[[], bool]) -> tuple[list[str], str]:
    """Put the account's photo, name and bio (profile.py) on TikTok through its Edit profile box.

    Uses the account's browser login however the account posts: TikTok's API can't edit
    profiles. Returns (what was changed, e.g. ["photo", "bio"], a note on anything TikTok wouldn't
    change yet, such as a name changed less than 7 days ago); raises if TikTok didn't take it.
    """
    from ..profile import photo_path
    from .browser import TikTokBrowser

    with TikTokBrowser(s, account.browser_dir(), log, cancelled=cancelled) as b:
        b.require_login()
        return b.set_profile(name=account.profile_name, bio=account.profile_bio, photo=photo_path(account),
                             expected_user=account.username)
