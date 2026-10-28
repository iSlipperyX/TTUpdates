"""Read the clips.json index ClipMonster writes next to its clips (titles, scores, hooks, quality check)."""

from __future__ import annotations

import json
import os
from pathlib import Path

REJECTED_DIR = "_rejected"  # ClipMonster's quality check moves failed clips here


def read_folder_index(folder: Path) -> dict[str, dict]:
    """Map clip file name -> what ClipMonster knows about it, for a ClipMonster output folder.

    Clips its quality check rejected are listed with "rejected": True so they can be left out."""
    index = folder / "clips.json"
    if not index.is_file():
        return {}
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    video = data.get("video") or {}
    auto = data.get("automation") or {}
    group = video.get("id") or video.get("url") or str(folder)
    out: dict[str, dict] = {}
    for clip in data.get("clips", []):
        file = clip.get("file")
        if not file:
            continue
        qc = clip.get("qc") or {}
        score = clip.get("score")
        rating = clip.get("rating")
        out[Path(file).name] = {
            "title": clip.get("title") or "",
            "score": float(score) if isinstance(score, (int, float)) else None,
            "reason": clip.get("reason") or "",
            "group": str(group),
            "source_title": video.get("title", ""),
            "channel": video.get("channel", ""),
            "url": clip.get("watch") or data.get("source") or video.get("url", ""),
            "hook": clip.get("hook") or "",
            "rating": int(rating) if isinstance(rating, (int, float)) else None,
            "permission": auto.get("permission", ""),
            # ClipMonster 2.3: the niche the clip was made for and the TikTok account that niche posts to
            "niche": str(auto.get("niche") or ""),
            "niche_id": str(auto.get("niche_id") or ""),
            "account": str(auto.get("account") or "").strip().lstrip("@").strip(),
            "license": video.get("license") or "",
            "warnings": list(qc.get("warnings") or []),
            "rejected": qc.get("ok") is False or file.replace("\\", "/").startswith(f"{REJECTED_DIR}/"),
        }
    return out


def needs_credit(meta: dict) -> bool:
    """Creative Commons clips must credit the creator. ClipMonster's picking rules say so in the
    permission; for videos added by hand, the video's licence says it."""
    return any("creative commons" in (meta.get(k) or "").lower() for k in ("permission", "license"))


def credit_line(channel: str) -> str:
    return f"🎥 {channel} on YouTube (CC BY)" if channel else ""


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "ClipMonster" / "settings.json"


def installed() -> bool:
    """ClipMonster has been run on this PC (it saves its settings on first start)."""
    return settings_path().is_file()


def ai_key() -> str:
    """The Anthropic API key saved in ClipMonster on this PC, if any, so one key serves both apps."""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    key = data.get("ai_api_key") if isinstance(data, dict) else ""
    return key.strip() if isinstance(key, str) and key.strip().startswith("sk-") else ""


def output_folders() -> list[Path]:
    """Where ClipMonster on this PC puts its clips (Automation's folder first), read from its settings."""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    output = Path(data.get("output_dir") or Path.home() / "Videos" / "ClipMonster").expanduser()
    auto = Path(data["auto_output_dir"]).expanduser() if data.get("auto_output_dir") else output / "Automation"
    return [auto, output]
