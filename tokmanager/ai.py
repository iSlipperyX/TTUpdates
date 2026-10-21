"""Optional AI rating: Claude looks at frames from the clip and predicts how well it will do,
then writes a caption and hashtags. Needs an Anthropic API key in Settings."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .config import Settings
from .media import sample_frames
from .store import VideoItem

FRAME_COUNT = 6

SCHEMA = {
    "type": "object",
    "properties": {
        "appeal": {"type": "integer", "description": "Predicted TikTok performance, 1-100"},
        "hook": {"type": "integer", "description": "Strength of the opening frame/first seconds, 1-100"},
        "verdict": {"type": "string", "description": "One or two sentences on why"},
        "caption": {"type": "string", "description": "Ready-to-post caption without hashtags, max 150 characters"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Hashtags without the # sign"},
        "ideas": {"type": "array", "items": {"type": "string"}, "description": "Up to 3 concrete edits that would raise performance"},
    },
    "required": ["appeal", "hook", "verdict", "caption", "hashtags", "ideas"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a TikTok growth strategist who has reviewed thousands of short-form videos. You get frames "
    "sampled across one video (the first frame is from the opening half-second) plus technical measurements. "
    "Judge it the way the For You feed does: would a stranger stop scrolling in the first second, and stay "
    "to the end? Be calibrated, not flattering: an average upload is about 50, a likely viral hit 85+. "
    "Write captions in a natural creator voice that add curiosity rather than describing the video, "
    "and pick a mix of broad and niche hashtags that fit the content."
)


def rate(item: VideoItem, s: Settings, log=None) -> dict:
    import anthropic

    if not s.ai_api_key:
        raise RuntimeError("Add your Anthropic API key in Settings → AI to use AI scoring.")
    frames = sample_frames(Path(item.path), item.duration, FRAME_COUNT)
    if not frames:
        raise RuntimeError("Couldn't read frames from the video.")

    score = item.score or {}
    facts = [
        f"File: {Path(item.path).name}",
        f"Title: {item.name}",
        f"Length: {item.duration:.1f}s, {item.info.get('width')}x{item.info.get('height')}",
        f"Technical score: {score.get('total', '?')}/100, parts: {json.dumps(score.get('parts', {}))}",
    ]
    if item.source_reason:
        facts.append(f"Why the clip finder picked it: {item.source_reason}")
    if s.ai_niche:
        facts.append(f"The creator's niche / audience: {s.ai_niche}")
    facts.append(f"Give {s.ai_hashtag_count} hashtags.")

    content: list[dict] = []
    for i, jpg in enumerate(frames):
        content.append({"type": "text", "text": "Opening frame:" if i == 0 else f"Frame {i + 1}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(jpg).decode("ascii")}})
    content.append({"type": "text", "text": "\n".join(facts)})

    client = anthropic.Anthropic(api_key=s.ai_api_key, max_retries=3)
    if log:
        log(f"Asking {s.ai_model} to rate “{item.name}”…")
    # `fallbacks: "default"` lets the API re-run a safety-declined request on its recommended fallback model.
    with client.beta.messages.stream(
        model=s.ai_model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to rate this video.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off; try again.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    data["appeal"] = max(1, min(100, int(data.get("appeal", 50))))
    data["hook"] = max(1, min(100, int(data.get("hook", 50))))
    data["hashtags"] = [h.lstrip("#").strip() for h in data.get("hashtags", []) if h.strip()]
    return data
