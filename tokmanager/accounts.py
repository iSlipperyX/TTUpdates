"""TikTok accounts: each has its own login, queue, schedule, posting defaults and stats.

The library of videos is shared. A video belongs to an account once it's queued for it
(`VideoItem.account`), and the account's stats live in the library file under its id.

Version 1.0 had one account, with its schedule and posting defaults in settings.json, its
browser login in `%LOCALAPPDATA%\\TikTokManager\\browser-profile` and its API tokens in
`tiktok_tokens.json`. The first start of a multi-account version turns that into the first
account and leaves those files where they are, so the login is kept (and an older version
still finds them after a rollback).
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from .config import Settings, app_dir, cache_dir

LEGACY_BROWSER_PROFILE = "browser-profile"
LEGACY_TOKENS = "tiktok_tokens.json"

# Settings fields that each account has its own copy of. Account.apply() lays them over the
# app-wide Settings, so the scheduler, caption builder and AI prompts don't need to know about accounts.
PER_ACCOUNT = {
    "posts_per_day": "posts_per_day",
    "first_post_time": "first_post_time",
    "last_post_time": "last_post_time",
    "min_gap_minutes": "min_gap_minutes",
    "autopilot": "autopilot",
    "logged_in": "browser_logged_in",
    "default_privacy": "default_privacy",
    "default_hashtags": "default_hashtags",
    "niche": "ai_niche",
}


@dataclass
class Account:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""  # optional label; "@username" is shown when empty
    username: str = ""  # TikTok @handle, filled in after logging in
    created_at: float = field(default_factory=time.time)
    # Where this account's login lives (relative to the app's local / roaming data folders).
    browser_profile: str = ""
    tokens_file: str = ""
    logged_in: bool = False  # browser method: TikTok's login cookies were seen in its profile
    # Schedule
    autopilot: bool = False
    posts_per_day: int = 3
    first_post_time: str = "09:00"
    last_post_time: str = "21:00"
    min_gap_minutes: int = 120
    # Posting defaults
    default_privacy: str = "public"
    default_hashtags: str = "#fyp"
    niche: str = ""  # audience / topic, used by AI captions and ratings

    def __post_init__(self) -> None:
        self.browser_profile = self.browser_profile or f"browser-profiles/{self.id}"
        self.tokens_file = self.tokens_file or f"tokens/{self.id}.json"

    @property
    def label(self) -> str:
        if self.name.strip():
            return self.name.strip()
        return f"@{self.username}" if self.username else "New account"

    @property
    def handle(self) -> str:
        """'@name' when known, else the label; used in log lines and notifications."""
        return f"@{self.username}" if self.username else self.label

    def browser_dir(self) -> Path:
        path = cache_dir() / self.browser_profile
        path.mkdir(parents=True, exist_ok=True)
        return path

    def tokens_path(self) -> Path:
        path = app_dir() / self.tokens_file
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def login_paths(self) -> tuple[Path, Path]:
        """The browser profile folder and API tokens file (without creating them)."""
        return cache_dir() / self.browser_profile, app_dir() / self.tokens_file

    def apply(self, s: Settings) -> Settings:
        """A copy of the app settings with this account's schedule and posting defaults."""
        return replace(s, **{target: getattr(self, src) for src, target in PER_ACCOUNT.items()})


class Accounts:
    """The account list (accounts.json) and which account the window is showing."""

    def __init__(self, path: Path | None = None):
        self.path = path or (app_dir() / "accounts.json")
        self.items: list[Account] = []
        self.active_id = ""
        self.migrated = False  # True on the first start after upgrading from a single-account version

    # --- persistence ------------------------------------------------------------

    @classmethod
    def load(cls, settings: Settings, path: Path | None = None) -> "Accounts":
        acc = cls(path)
        try:
            data = json.loads(acc.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            known = {f.name for f in fields(Account)}
            acc.items = [Account(**{k: v for k, v in d.items() if k in known})
                         for d in data.get("accounts", []) if isinstance(d, dict) and d.get("id")]
            acc.active_id = data.get("active", "")
        if not acc.items:
            acc.items = [acc._from_settings(settings)]
            acc.migrated = data is None
            acc.save()
        if acc.get(acc.active_id) is None:
            acc.active_id = acc.items[0].id
        return acc

    @staticmethod
    def _from_settings(s: Settings) -> Account:
        """The first account, made from a 1.0 install's settings and login (or fresh defaults)."""
        a = Account(**{src: getattr(s, target) for src, target in PER_ACCOUNT.items()})
        if (cache_dir() / LEGACY_BROWSER_PROFILE).is_dir():
            a.browser_profile = LEGACY_BROWSER_PROFILE
        if (app_dir() / LEGACY_TOKENS).is_file():
            a.tokens_file = LEGACY_TOKENS
        return a

    def save(self) -> None:
        data = {"version": 1, "active": self.active_id, "accounts": [asdict(a) for a in self.items]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- access -----------------------------------------------------------------

    def __iter__(self):
        return iter(list(self.items))

    def __len__(self) -> int:
        return len(self.items)

    def ids(self) -> list[str]:
        return [a.id for a in self.items]

    def get(self, account_id: str | None) -> Account | None:
        return next((a for a in self.items if a.id == account_id), None)

    @property
    def active(self) -> Account:
        return self.get(self.active_id) or self.items[0]

    def label_for(self, account_id: str) -> str:
        a = self.get(account_id)
        return a.label if a else "removed account"

    def find_username(self, username: str, exclude: str = "") -> Account | None:
        name = username.lstrip("@").strip().lower()
        if not name:
            return None
        return next((a for a in self.items if a.id != exclude and a.username.lower() == name), None)

    # --- changes ----------------------------------------------------------------

    def add(self, name: str = "", copy_schedule_from: Account | None = None) -> Account:
        """A new account with its own (empty) login. It copies the schedule of `copy_schedule_from`,
        but autopilot starts off so nothing posts before you've logged in and looked at its queue."""
        a = Account(name=name.strip())
        if copy_schedule_from:
            for f in ("posts_per_day", "first_post_time", "last_post_time", "min_gap_minutes", "default_privacy"):
                setattr(a, f, getattr(copy_schedule_from, f))
        self.items.append(a)
        return a

    def remove(self, account_id: str) -> Account | None:
        """Take an account off the list (the last one can't be removed). Its login files are left for
        delete_login() so the caller can delete them off the GUI thread."""
        a = self.get(account_id)
        if a is None or len(self.items) <= 1:
            return None
        self.items.remove(a)
        if self.active_id == account_id:
            self.active_id = self.items[0].id
        return a

    @staticmethod
    def delete_login(a: Account) -> None:
        """Delete a removed account's browser profile and API tokens from this PC."""
        profile, tokens = a.login_paths()
        shutil.rmtree(profile, ignore_errors=True)
        try:
            tokens.unlink()
        except OSError:
            pass

    def move(self, account_id: str, delta: int) -> None:
        a = self.get(account_id)
        if a is None:
            return
        i = self.items.index(a)
        j = max(0, min(len(self.items) - 1, i + delta))
        self.items.insert(j, self.items.pop(i))
