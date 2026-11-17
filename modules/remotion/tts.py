"""Edge TTS and Cartesia synthesis for the shared Remotion timeline."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def synthesize_edge_tts(text: str, voice_id: str, output_wav: Path,
                        *, ffmpeg_bin: str, ffprobe_bin: str, speed: float = 1.0) -> dict:
    if not text.strip() or not voice_id:
        raise ValueError("Naskah dan suara AUTO wajib diisi")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    worker = Path(__file__).with_name("edge_tts_worker.py")
    with tempfile.TemporaryDirectory(prefix="ovs-edge-tts-") as temporary:
        temp = Path(temporary)
        audio_mp3, words_json = temp / "voice.mp3", temp / "words.json"
        job = temp / "job.json"
        job.write_text(json.dumps({"text": text, "voice": voice_id, "rate": speed, "audio": str(audio_mp3), "words": str(words_json)}, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run([sys.executable, str(worker), str(job)], capture_output=True, text=True, timeout=180, check=False)
        if result.returncode or not audio_mp3.is_file() or not audio_mp3.stat().st_size:
            raise RuntimeError("Edge TTS gagal membuat narasi audio")
        normalized = subprocess.run(
            [ffmpeg_bin, "-y", "-v", "error", "-i", str(audio_mp3), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(output_wav)],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if normalized.returncode or not output_wav.is_file() or output_wav.stat().st_size < 1000:
            raise RuntimeError("FFmpeg gagal menormalisasi audio Edge TTS")
        probed = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(output_wav)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        try:
            duration = float(probed.stdout.strip())
        except (ValueError, TypeError):
            duration = 0
        if probed.returncode or duration <= 0:
            raise RuntimeError("FFprobe gagal membaca durasi audio Edge TTS")
        try:
            words = json.loads(words_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            words = []
        return {"audio_path": output_wav, "duration_seconds": duration, "words": words}


def edge_voices() -> list[dict]:
    """Read Indonesian Edge TTS voices from the studio environment, without MPT."""
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Dependensi edge-tts belum dipasang di environment studio") from exc
    voices = asyncio.run(edge_tts.list_voices())
    return [
        {"id": row["ShortName"], "name": row.get("FriendlyName", row["ShortName"])}
        for row in voices
        if row.get("Locale", "").lower().startswith("id-")
    ]


def preview_edge_voice(voice_id: str, output_mp3: Path, text: str = "Halo, ini contoh suara untuk video.") -> None:
    if voice_id not in {row["id"] for row in edge_voices()}:
        raise ValueError("Suara Edge TTS tidak ditemukan pada katalog bahasa Indonesia")
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ovs-edge-preview-") as temporary:
        temp = Path(temporary)
        job = temp / "job.json"
        words = temp / "words.json"
        job.write_text(json.dumps({"text": text, "voice": voice_id, "audio": str(output_mp3), "words": str(words)}, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("edge_tts_worker.py")), str(job)], capture_output=True, text=True, timeout=90, check=False)
        if result.returncode or not output_mp3.is_file() or not output_mp3.stat().st_size:
            raise RuntimeError("Edge TTS gagal membuat preview suara")


def synthesize_cartesia(text: str, voice_id: str, output_wav: Path, *, language: str,
                        ffmpeg_bin: str, ffprobe_bin: str, speed: float = 1.08) -> dict:
    """Generate Cartesia speech and normalize it for the shared Remotion timeline."""
    from modules.cartesia.client import synthesize

    if not text.strip() or not voice_id:
        raise ValueError("Naskah dan Voice ID Cartesia AUTO wajib diisi")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    result = synthesize(text, voice_id, language, speed)
    with tempfile.TemporaryDirectory(prefix="ovs-cartesia-tts-") as temporary:
        source_wav = Path(temporary) / "cartesia.wav"
        source_wav.write_bytes(result["wav"])
        normalized = subprocess.run(
            [ffmpeg_bin, "-y", "-v", "error", "-i", str(source_wav), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(output_wav)],
            capture_output=True, text=True, timeout=180, check=False,
        )
    if normalized.returncode or not output_wav.is_file() or output_wav.stat().st_size < 1000:
        raise RuntimeError("FFmpeg gagal menormalisasi audio Cartesia")
    probed = subprocess.run(
        [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(output_wav)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        duration = float(probed.stdout.strip())
    except (ValueError, TypeError):
        duration = 0
    if probed.returncode or duration <= 0:
        raise RuntimeError("FFprobe gagal membaca durasi audio Cartesia")
    return {"audio_path": output_wav, "duration_seconds": duration, "words": result.get("words", [])}


def synthesize_auto_tts(text: str, settings: dict, output_wav: Path, *,
                        ffmpeg_bin: str, ffprobe_bin: str) -> dict:
    """Dispatch AUTO speech to its selected provider without silent fallback."""
    provider = settings.get("auto_tts_provider", "edge")
    if provider == "cartesia":
        return synthesize_cartesia(
            text, settings.get("cartesia_voice_id", ""), output_wav,
            language=settings.get("language", "id"),
            ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin,
        )
    if provider != "edge":
        raise ValueError("Provider TTS AUTO tidak dikenali")
    return synthesize_edge_tts(
        text, settings.get("auto_voice_id", ""), output_wav,
        ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin,
    )
