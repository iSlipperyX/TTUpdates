"""User settings and where the app keeps its files."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import safefile
from .vault import protect, unprotect

APP_NAME = "TikTok Manager"
APP_ID = "TikTokManager"
VERSION = "1.6.0"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    """Thumbnails and the browser profile live in the local (non-roaming) profile."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbs_dir() -> Path:
    path = cache_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def updates_dir() -> Path:
    path = cache_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Weights:
    """How much each part of the score counts. Only relative sizes matter."""

    hook: float = 0.25
    format: float = 0.15
    duration: float = 0.15
    pacing: float = 0.15
    audio: float = 0.15
    visual: float = 0.10
    source: float = 0.20  # ClipMonster's own clip score, when the video came from it
    ai: float = 0.35  # the AI's rating, when AI scoring is on


@dataclass
class Settings:
    # Scoring
    weights: Weights = field(default_factory=Weights)
    ideal_min_len: float = 21.0
    ideal_max_len: float = 45.0
    target_lufs: float = -14.0
    # AI (optional, ai.py): Claude or Gemini, each with its own key and model
    ai_provider: str = "claude"  # claude | gemini
    ai_fallback: bool = True  # the chosen AI fails or has no key: ask the other one, if it has a key
    ai_api_key: str = ""  # Anthropic's, for Claude
    ai_model: str = "claude-opus-5-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    ai_niche: str = ""  # e.g. "podcast clips about startups", helps captions and scoring
    ai_hashtag_count: int = 4
    # Posting. default_privacy, default_hashtags, ai_niche, the schedule, autopilot and browser_logged_in
    # are per account (accounts.Account); the values here only seed the first account when upgrading
    # from 1.0, and Account.apply() overrides them on the copy each account's work runs with.
    browser_channel: str = "msedge"  # msedge | chrome
    show_browser: bool = True
    default_privacy: str = "public"  # public | friends | private
    allow_comments: bool = True
    allow_duet: bool = True
    allow_stitch: bool = True
    ai_generated_label: bool = False
    caption_template: str = "{caption} {hashtags}"
    default_hashtags: str = "#fyp"
    # Schedule
    posts_per_day: int = 3
    first_post_time: str = "09:00"
    last_post_time: str = "21:00"
    min_gap_minutes: int = 120
    autopilot: bool = False
    browser_logged_in: bool = False
    # Ready to upload (ready.py): clips arrive from watched folders, you post them yourself
    watch_folders: list[str] = field(default_factory=list)  # e.g. ClipMonster's Automation folder
    watch_target: str = "library"  # library | spread (shared between accounts) | an account id
    setup_done: bool = False  # first-run setup finished (or skipped); existing installs count as done
    clipmonster_offered: str = ""  # the ClipMonster folder the app last offered to watch ("" = never asked)
    watch_ai: bool = False  # AI caption each imported clip (one AI call per clip)
    watch_hashtags: bool = True  # AI hashtags for each imported clip (a quick text-only call, needs a key)
    remind_to_post: bool = True  # notify when a queued video's time comes (accounts with autopilot off)
    morning_summary: bool = True  # one notification a day: what's ready for each account
    ready_folder: str = ""  # "" = Videos\TikTok Ready; where "Copy to a folder" puts clips + caption files
    phone_folder: str = ""  # phone hand-off (phone.py): today's clips kept here, e.g. OneDrive\TikTok Ready; "" = off
    last_summary_day: str = ""
    share_results: bool = True  # results.py: tell ClipMonster how its clips did (results.json)
    # Live view (live.py): logged-in accounts' profile pages are read this often (seconds), in the background
    live_interval: int = 300
    live_show_browser: bool = False  # show the window while reading (if TikTok keeps asking for a captcha)
    live_alerts: bool = True  # tray notifications: a video taking off, milestones, a queued clip seen on TikTok
    # Window
    minimize_to_tray: bool = True
    start_with_windows: bool = False
    last_folder: str = ""
    # Updates (GitHub Releases)
    update_repo: str = "iSlipperyX/TTUpdates"
    auto_check_updates: bool = True
    auto_install_updates: bool = False  # install by itself when idle (handy with autopilot in the tray)
    skipped_version: str = ""

    @classmethod
    def path(cls) -> Path:
        return app_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        data = safefile.load(cls.path(), "your settings")
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known and k != "weights"})
        if "setup_done" not in data:  # saved by a version before first-run setup: not a new install
            s.setup_done = True
        if "api_client_key" in data and s.live_interval < 120:  # 1.5.0 read its API every minute or faster
            s.live_interval = 300
        if isinstance(s.watch_folders, str):  # hand-edited, or a single folder written as text
            s.watch_folders = [s.watch_folders]
        s.watch_folders = [str(f) for f in s.watch_folders or [] if f]
        for name in SECRET_FIELDS:
            setattr(s, name, unprotect(getattr(s, name)))
        w = data.get("weights") or {}
        wknown = {f.name for f in fields(Weights)}
        s.weights = Weights(**{k: float(v) for k, v in w.items() if k in wknown})
        return s

    def save(self) -> None:
        data = asdict(self)
        for name in SECRET_FIELDS:
            data[name] = protect(data[name])
        safefile.save(self.path(), data)


SECRET_FIELDS = ("ai_api_key", "gemini_api_key")
