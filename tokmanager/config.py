"""User settings and where the app keeps its files."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .vault import protect, unprotect

APP_NAME = "TikTok Manager"
APP_ID = "TikTokManager"
VERSION = "1.0.0"


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


def browser_profile_dir() -> Path:
    path = cache_dir() / "browser-profile"
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
    ai: float = 0.35  # Claude's rating, when AI scoring is on


@dataclass
class Settings:
    # Scoring
    weights: Weights = field(default_factory=Weights)
    ideal_min_len: float = 21.0
    ideal_max_len: float = 45.0
    target_lufs: float = -14.0
    # AI (optional)
    ai_enabled: bool = False
    ai_api_key: str = ""
    ai_model: str = "claude-opus-5-5"
    ai_niche: str = ""  # e.g. "podcast clips about startups", helps captions and scoring
    ai_hashtag_count: int = 4
    # Posting
    upload_method: str = "browser"  # "browser" (your logged-in TikTok in Edge/Chrome) or "api"
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
    retry_failed: bool = True
    # Official TikTok API (optional, needs your own developer app)
    api_client_key: str = ""
    api_client_secret: str = ""
    api_redirect_port: int = 53682
    api_post_mode: str = "direct"  # direct (publishes) | inbox (sends a draft to the TikTok app)
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
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known and k != "weights"})
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
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path())


SECRET_FIELDS = ("ai_api_key", "api_client_secret")
