"""Cartesia SSE text-to-speech client using Python's standard library."""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
import wave

API_URL = "https://api.cartesia.ai/tts/sse"
API_VERSION = os.environ.get("CARTESIA_API_VERSION", "2026-08-14")
MODEL_ID = os.environ.get("CARTESIA_MODEL_ID", "sonic-3.6")
SAMPLE_RATE = 44100
MAX_AUDIO_BYTES = 200 * 1024 * 1024


class CartesiaError(RuntimeError):
    """A provider error safe to show without exposing request credentials."""


def _word_rows(event: dict) -> list[dict]:
    data = event.get("word_timestamps") or event.get("timestamps") or event
    if not isinstance(data, dict):
        return []
    words = data.get("words") or data.get("word") or []
    starts = data.get("start") or data.get("starts") or []
    ends = data.get("end") or data.get("ends") or []
    rows = []
    for word, start, end in zip(words, starts, ends):
        if isinstance(word, dict):
            text = word.get("text") or word.get("word") or ""
            start = word.get("start", start)
            end = word.get("end", end)
        else:
            text = str(word)
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if text and start >= 0 and end >= start:
            rows.append({"word": text, "start": start, "end": end})
    return rows


def parse_sse(lines) -> tuple[bytes, list[dict]]:
    """Collect PCM chunks and exact word timestamps from Cartesia SSE frames."""
    pcm = bytearray()
    words: list[dict] = []
    saw_done = False
    for raw_line in lines:
        line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw_data = line[5:].strip()
        if not raw_data or raw_data == "[DONE]":
            saw_done = True
            continue
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise CartesiaError("Cartesia mengembalikan event SSE yang tidak valid") from exc
        kind = event.get("type")
        if kind == "chunk":
            encoded = event.get("data")
            if encoded:
                try:
                    pcm.extend(base64.b64decode(encoded, validate=True))
                except (ValueError, base64.binascii.Error) as exc:
                    raise CartesiaError("Data audio Cartesia tidak valid") from exc
                if len(pcm) > MAX_AUDIO_BYTES:
                    raise CartesiaError("Ukuran audio melewati batas 200 MB")
        elif kind == "timestamps":
            words.extend(_word_rows(event))
        elif kind == "error":
            # Do not echo provider payloads or request headers into logs/UI.
            raise CartesiaError("Cartesia menolak permintaan TTS")
        elif kind == "done":
            saw_done = True
    if not saw_done:
        raise CartesiaError("Stream Cartesia berhenti sebelum event selesai")
    if not pcm:
        raise CartesiaError("Cartesia menyelesaikan permintaan tanpa data audio")
    return bytes(pcm), words


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap signed 16-bit little-endian mono PCM in a standard WAV container."""
    if not pcm or len(pcm) % 2:
        raise CartesiaError("Data audio PCM kosong atau memiliki ukuran yang tidak valid")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def synthesize(transcript: str, voice_id: str, language: str = "id", speed: float = 1.08) -> dict:
    api_key = os.environ.get("CARTESIA_API_KEY", "")
    if not api_key or not voice_id:
        raise CartesiaError("Cartesia API key atau Voice ID belum dikonfigurasi")
    if not transcript.strip():
        raise CartesiaError("Naskah tidak boleh kosong")
    if len(transcript) > 20000:
        raise CartesiaError("Naskah melebihi batas 20.000 karakter")
    if not 0.5 <= speed <= 2.0:
        raise CartesiaError("Kecepatan suara harus antara 0,5 dan 2,0")
    if abs(speed - 1.0) < 0.001 and language == "id":
        speed = 1.08

    body = json.dumps({
        "model_id": MODEL_ID,
        "transcript": transcript,
        "voice": voice_id,
        "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": SAMPLE_RATE},
        "language": language,
        "add_timestamps": True,
        "use_normalized_timestamps": True,
        "generation_config": {"speed": speed},
    }).encode("utf-8")
    request = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Cartesia-Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    })

    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                pcm, words = parse_sse(response)
            wav_bytes = pcm_to_wav(pcm)
            duration = len(pcm) / (SAMPLE_RATE * 2)
            return {
                "wav": wav_bytes,
                "duration_seconds": duration,
                "sample_rate": SAMPLE_RATE,
                "words": words,
                "alignment_available": bool(words),
            }
        except urllib.error.HTTPError as exc:
            last_error = CartesiaError(f"Cartesia gagal dengan HTTP {exc.code}")
            if exc.code not in {429, 500, 502, 503, 504} or attempt:
                raise last_error from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = CartesiaError("Koneksi ke Cartesia gagal atau melewati batas waktu")
            if attempt:
                raise last_error from None
        if attempt == 0:
            time.sleep(0.5)
    raise last_error or CartesiaError("Cartesia gagal menghasilkan audio")
