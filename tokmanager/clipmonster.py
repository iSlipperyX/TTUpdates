"""Read the clips.json index ClipMonster writes next to its clips (titles, scores, reasons)."""

from __future__ import annotations

import json
from pathlib import Path


def read_folder_index(folder: Path) -> dict[str, dict]:
    """Map clip file name -> {title, score, reason, group} for a ClipMonster output folder."""
    index = folder / "clips.json"
    if not index.is_file():
        return {}
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    video = data.get("video") or {}
    group = video.get("id") or video.get("url") or str(folder)
    out: dict[str, dict] = {}
    for clip in data.get("clips", []):
        name = clip.get("file")
        if not name:
            continue
        score = clip.get("score")
        out[name] = {
            "title": clip.get("title") or "",
            "score": float(score) if isinstance(score, (int, float)) else None,
            "reason": clip.get("reason") or "",
            "group": str(group),
            "source_title": video.get("title", ""),
            "channel": video.get("channel", ""),
        }
    return out
