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
            "warnings": list(qc.get("warnings") or []),
            "rejected": qc.get("ok") is False or file.replace("\\", "/").startswith(f"{REJECTED_DIR}/"),
        }
    return out


def needs_credit(meta: dict) -> bool:
    """Creative Commons clips must credit the creator (ClipMonster's picking rules say so in the permission)."""
    return "creative commons" in (meta.get("permission") or "").lower()


def credit_line(channel: str) -> str:
    return f"🎥 {channel} on YouTube (CC BY)" if channel else ""


def output_folders() -> list[Path]:
    """Where ClipMonster on this PC puts its clips (Automation's folder first), read from its settings."""
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    try:
        data = json.loads((Path(base) / "ClipMonster" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    output = Path(data.get("output_dir") or Path.home() / "Videos" / "ClipMonster").expanduser()
    auto = Path(data["auto_output_dir"]).expanduser() if data.get("auto_output_dir") else output / "Automation"
    return [auto, output]
