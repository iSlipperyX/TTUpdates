"""How each account posts, and whether it's ready to.

Every account has a browser profile (its TikTok login, used for posting, stats, the upload-page
test and profile changes) and can also be connected to TikTok's official API with your own
developer app. `Account.post_with` picks which one posts:

- "browser": TikTok Studio's upload page in the account's own browser profile (the default).
- "api": the Content Posting API publishes the video. Until TikTok audits your developer app,
  those posts are "Only me", and TikTok keeps them private even after the audit.
- "api_draft": the API sends the video to the TikTok app's inbox as a draft; you add sounds or
  edits and post it from your phone, with any privacy. This works before the audit too.

An API connection also helps a browser account: stats come from the API (no browser window), and
the browser is only used when the API can't answer.
"""

from __future__ import annotations

import time

from ..accounts import Account
from ..config import Settings

BROWSER, API, API_DRAFT = "browser", "api", "api_draft"

# value, short name, one-line explanation (Manage accounts shows these)
METHODS = [
    (BROWSER, "Browser (recommended)",
     "Posts through TikTok's website in this account's own browser profile, like you would by hand."),
    (API, "Official TikTok API: publish",
     "TikTok's Content Posting API publishes the video. Needs your developer app (Settings → TikTok API)."),
    (API_DRAFT, "Official TikTok API: draft to the TikTok app",
     "The API sends the video to the TikTok app's inbox; you finish posting on your phone."),
]

EXPIRY_WARN_DAYS = 14  # say so on the Account page when the API login is about to run out


def name(method: str) -> str:
    return next((n for v, n, _ in METHODS if v == method), METHODS[0][1])


def explain(method: str) -> str:
    return next((e for v, _, e in METHODS if v == method), METHODS[0][2])


def uses_api(a: Account) -> bool:
    return a.post_with in (API, API_DRAFT)


def app_ready(s: Settings) -> bool:
    """A developer app's client key and secret are filled in (Settings → TikTok API)."""
    return bool(s.api_client_key.strip() and s.api_client_secret.strip())


def api_connected(a: Account) -> bool:
    from .api import Tokens

    path = a.login_paths()[1]
    return path.is_file() and Tokens.load(path).usable


def granted_scopes(a: Account) -> set[str]:
    from .api import Tokens

    path = a.login_paths()[1]
    return set(Tokens.load(path).scope.split(",")) if path.is_file() else set()


def api_days_left(a: Account) -> float | None:
    """Days until the API login runs out (TikTok's refresh token lasts a year), None if not connected."""
    from .api import Tokens

    path = a.login_paths()[1]
    if not path.is_file():
        return None
    t = Tokens.load(path)
    return (t.refresh_expires_at - time.time()) / 86400 if t.usable else None


def can_post(s: Settings, a: Account) -> tuple[bool, str]:
    """(ready, why not) for the account's posting method, in words for the Queue page and warnings."""
    if not uses_api(a):
        return (True, "") if a.logged_in else (False, f"{a.label} isn't logged in. Log in on the Account page.")
    if not app_ready(s):
        return False, (f"{a.label} posts with the TikTok API, but no developer app is set up. Add it in "
                       "Settings → TikTok API, or switch the account to Browser in Manage accounts.")
    if not api_connected(a):
        return False, f"{a.label} posts with the TikTok API, but it isn't connected. Press Connect API on the Account page."
    need = "video.upload" if a.post_with == API_DRAFT else "video.publish"
    if need not in granted_scopes(a):
        return False, (f"{a.label} was connected to the TikTok API only for watching it. Press Connect API on the "
                       "Account page again to allow posting (your developer app needs the Content Posting API).")
    if a.api_username and a.username and a.api_username.lstrip("@").lower() != a.username.lstrip("@").lower():
        return False, (f"{a.label}'s TikTok API connection is for @{a.api_username.lstrip('@')}, not @{a.username}. "
                       "Press Connect API on the Account page again, logged in as the right account.")
    return True, ""


def short_status(s: Settings, a: Account) -> str:
    """'logged in' / 'API connected' etc. for account lists."""
    if uses_api(a):
        return "API connected" if api_connected(a) else "API not connected"
    text = "logged in" if a.logged_in else "not logged in"
    if app_ready(s) and api_connected(a):
        text += " · API connected"
    return text
