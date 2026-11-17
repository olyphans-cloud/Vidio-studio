"""Split provider-aligned words into editable sentence/pause scenes."""

from __future__ import annotations

import re

END_PUNCTUATION = re.compile(r"[.!?…][\"'”’)]*$")


def segment_words(words: list[dict], pause_seconds: float = 0.7, max_scene_seconds: float = 8.0) -> list[dict]:
    """Build scene ranges only from actual provider word start/end timestamps."""
    valid = []
    last_start = -1.0
    for row in words:
        if not isinstance(row, dict):
            continue
        text = str(row.get("word", "")).strip()
        try:
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or start < 0 or end < start or start < last_start:
            continue
        valid.append({"word": text, "start": start, "end": end})
        last_start = start
    scenes = []
    group = []
    for word in valid:
        if group:
            pause = word["start"] - group[-1]["end"]
            elapsed = word["end"] - group[0]["start"]
            if pause >= pause_seconds or elapsed > max_scene_seconds or END_PUNCTUATION.search(group[-1]["word"]):
                scenes.append(_scene(group))
                group = []
        group.append(word)
    if group:
        scenes.append(_scene(group))
    return scenes


def _scene(group: list[dict]) -> dict:
    narration = " ".join(item["word"] for item in group)
    narration = re.sub(r"\s+([,.;:!?…])", r"\1", narration)
    return {
        "narration": narration,
        "start_seconds": group[0]["start"],
        "end_seconds": group[-1]["end"],
    }
