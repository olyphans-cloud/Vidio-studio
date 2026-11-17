"""Small runner for the Edge TTS dependency in the studio Python environment."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


async def synthesize(job: dict) -> None:
    import edge_tts

    communicator = edge_tts.Communicate(job["text"], job["voice"], rate=f"{round((float(job.get('rate', 1.0)) - 1) * 100):+d}%")
    audio = bytearray()
    words = []
    async for chunk in communicator.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            event = chunk.get("data", {})
            try:
                start = int(event["offset"]) / 10_000_000
                duration = int(event["duration"]) / 10_000_000
            except (KeyError, TypeError, ValueError):
                continue
            word = str(event.get("text") or "").strip()
            if word:
                words.append({"word": word, "start": start, "end": start + duration})
    if not audio:
        raise RuntimeError("Edge TTS returned no audio")
    Path(job["audio"]).write_bytes(audio)
    Path(job["words"]).write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    asyncio.run(synthesize(job))
