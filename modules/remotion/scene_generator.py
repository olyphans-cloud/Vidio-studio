"""Prepare narration-locked scenes and word-aligned visual timing."""

from __future__ import annotations

import re

from modules.auto import qwen


def clean_spoken_script(script: str) -> str:
    """Remove explicit visual direction labels before sending text to TTS."""
    kept = []
    for raw in script.splitlines():
        line = re.sub(r"^\s*(?:#{1,6}\s*)?(?:scene\s*\d+\s*[:.-]?\s*)", "", raw, flags=re.I).strip()
        if not line:
            continue
        line = re.split(r"\s+(?:visual|visualisasi|arahan visual|petunjuk visual)\s*:", line, maxsplit=1, flags=re.I)[0].strip()
        if re.match(r"^(?:visual|visualisasi|arahan visual|petunjuk visual)\s*:", line, re.I):
            continue
        line = re.sub(r"^narasi\s*:\s*", "", line, flags=re.I)
        kept.append(line)
    text = " ".join(kept)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def limit_narration_words(script: str, max_words: int) -> str:
    """Keep narration within its voice-time budget without cutting mid-sentence."""
    spoken = clean_spoken_script(script)
    if not spoken:
        return spoken
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", spoken) if part.strip()]
    result, count = [], 0
    for sentence in sentences:
        sentence_words = sentence.split()
        if count + len(sentence_words) > max_words:
            break
        result.append(sentence)
        count += len(sentence_words)
    if result:
        return " ".join(result)
    words = spoken.split()[:max_words]
    clipped = " ".join(words).rstrip(",;:")
    return clipped if clipped.endswith((".", "!", "?")) else clipped + "."


def split_narration(script: str, count: int = 4) -> list[str]:
    if not 1 <= count <= 8:
        raise ValueError("Jumlah scene harus antara 1 dan 8")
    spoken = clean_spoken_script(script)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", spoken) if part.strip()]
    if not spoken:
        raise ValueError("Naskah narasi kosong")
    if len(sentences) < count:
        words = spoken.split()
        count = min(count, max(1, len(words) // 3))
        base, extra = divmod(len(words), count)
        segments, cursor = [], 0
        for index in range(count):
            length = base + (1 if index < extra else 0)
            segments.append(" ".join(words[cursor:cursor + length]))
            cursor += length
        return segments
    segments, cursor = [], 0
    for index in range(count):
        remaining = count - index
        if remaining == 1:
            segments.append(" ".join(sentences[cursor:]))
            break
        words_left = sum(len(sentence.split()) for sentence in sentences[cursor:])
        target = words_left / remaining
        start, weight = cursor, 0
        while cursor < len(sentences) - (remaining - 1):
            next_weight = len(sentences[cursor].split())
            if cursor > start and weight + next_weight > target:
                break
            weight += next_weight
            cursor += 1
            if weight >= target:
                break
        segments.append(" ".join(sentences[start:cursor]))
    if len(segments) != count or any(not segment for segment in segments):
        raise ValueError("Naskah tidak dapat dibagi menjadi scene")
    return segments


def plan_scenes(topic: str, narration_segments: list[str]) -> list[dict]:
    return qwen.generate_visual_plan(topic, narration_segments)


def time_scenes(segments: list[str], word_alignment: list[dict], duration_seconds: float) -> list[dict]:
    """Align cuts to the spoken word boundaries; fall back to word-weighted timing."""
    if not segments or duration_seconds <= 0:
        raise ValueError("Scene membutuhkan narasi dan durasi audio positif")
    counts = [max(1, len(segment.split())) for segment in segments]
    boundaries = [0.0]
    if word_alignment and len(word_alignment) >= len(segments):
        total_words = sum(counts)
        accumulated = 0
        for count in counts[:-1]:
            accumulated += count
            word_index = min(len(word_alignment) - 1, max(1, round(accumulated * len(word_alignment) / total_words)))
            previous_end = float(word_alignment[word_index - 1].get("end", 0))
            next_start = float(word_alignment[word_index].get("start", previous_end))
            boundaries.append(min(duration_seconds, max(boundaries[-1], (previous_end + next_start) / 2)))
    else:
        total = sum(counts)
        cursor = 0
        for count in counts[:-1]:
            cursor += count
            boundaries.append(duration_seconds * cursor / total)
    boundaries.append(duration_seconds)
    result = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end <= start:
            raise ValueError("Timestamp TTS tidak menghasilkan durasi positif untuk setiap scene")
        result.append({"start_seconds": start, "end_seconds": end, "duration_seconds": end - start})
    return result


def infer_visual_type(description: str, narration: str = "") -> str:
    text = f"{description} {narration}".lower()
    if any(word in text for word in ("peta", "lokasi", "wilayah", "rute")):
        return "map"
    if any(word in text for word in ("grafik", "diagram", "data", "persen", "meningkat", "menurun")):
        return "chart"
    if any(word in text for word in ("aplikasi", "antarmuka", "dashboard", "layar")):
        return "ui"
    if any(word in text for word in ("angka", "judul", "kata", "tipografi")):
        return "kinetic"
    return "illustration"
