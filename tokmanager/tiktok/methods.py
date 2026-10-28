"""Whether an account is ready to post: every account posts through TikTok Studio in its own
browser profile, logged in once from the Account page."""

from __future__ import annotations

from ..accounts import Account
from ..config import Settings


def can_post(s: Settings, a: Account) -> tuple[bool, str]:
    """(ready, why not), in words for the Queue page and warnings."""
    return (True, "") if a.logged_in else (False, f"{a.label} isn't logged in. Log in on the Account page.")


def short_status(s: Settings, a: Account) -> str:
    """'logged in' / 'not logged in' for account lists."""
    return "logged in" if a.logged_in else "not logged in"
