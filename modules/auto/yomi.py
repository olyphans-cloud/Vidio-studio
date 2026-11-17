"""Thread-safe, bounded Groq API key rotation for the Qwen AUTO adapter."""

from __future__ import annotations

import os
import re
import threading
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone


KEY_ENV_NAMES = ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3")
_LOCK = threading.RLock()
_NEXT = 0
_SLOTS: dict[int, dict] = {}
_EVENT = threading.local()


def set_event_callback(callback):
    """Set this thread's safe event sink and return its previous callback."""
    previous = getattr(_EVENT, "callback", None)
    _EVENT.callback = callback
    return previous


def emit(message: str) -> None:
    callback = getattr(_EVENT, "callback", None)
    if callback:
        try:
            callback(message)
        except Exception:
            pass


def _keys() -> list[str]:
    return [os.environ.get(name, "").strip() for name in KEY_ENV_NAMES]


def configured() -> bool:
    return any(_keys())


def _sync_locked(keys: list[str]) -> None:
    now = time.monotonic()
    for index, key in enumerate(keys):
        slot = _SLOTS.get(index)
        if slot is None or slot["secret"] != key:
            _SLOTS[index] = {"secret": key, "status": "READY" if key else "DISABLED", "until": 0.0, "uses": 0, "limits": 0}
        elif slot["status"] == "RATE LIMITED" and slot["until"] <= now:
            slot["status"], slot["until"] = "READY", 0.0


def _available_keys() -> list[int]:
    global _NEXT
    keys = _keys()
    with _LOCK:
        _sync_locked(keys)
        count = len(keys)
        selected = []
        for offset in range(count):
            index = (_NEXT + offset) % count
            if keys[index] and _SLOTS[index]["status"] == "READY":
                selected.append(index)
        if selected:
            _NEXT = (selected[0] + 1) % count
        return selected


def next_key() -> tuple[int, str] | None:
    choices = _available_keys()
    if not choices:
        return None
    index = choices[0]
    return index + 1, _keys()[index]


def mark_success(number: int) -> None:
    with _LOCK:
        _sync_locked(_keys())
        slot = _SLOTS[number - 1]
        slot["status"], slot["until"] = "READY", 0.0
        slot["uses"] += 1


def _cooldown_seconds(headers) -> float:
    if headers:
        retry = headers.get("Retry-After")
        if retry:
            try:
                return max(1.0, min(86400.0, float(retry)))
            except (TypeError, ValueError):
                try:
                    when = parsedate_to_datetime(str(retry))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    return max(1.0, min(86400.0, (when - datetime.now(timezone.utc)).total_seconds()))
                except (TypeError, ValueError, OverflowError):
                    pass
        for name in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
            value = headers.get(name)
            if value:
                match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?\s*", str(value))
                if match:
                    amount = float(match.group(1))
                    unit = (match.group(2) or "s").lower()
                    amount *= {"ms": .001, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}.get(unit, 1)
                    return max(1.0, min(86400.0, amount))
    try:
        fallback = float(os.environ.get("GROQ_KEY_COOLDOWN_SECONDS", "60"))
    except (TypeError, ValueError):
        fallback = 60.0
    return max(1.0, min(3600.0, fallback))


def mark_rate_limited(number: int, headers=None) -> float:
    cooldown = _cooldown_seconds(headers)
    with _LOCK:
        _sync_locked(_keys())
        slot = _SLOTS[number - 1]
        slot["status"], slot["until"] = "RATE LIMITED", time.monotonic() + cooldown
        slot["limits"] += 1
    return cooldown


def mark_invalid(number: int) -> None:
    with _LOCK:
        _sync_locked(_keys())
        _SLOTS[number - 1]["status"] = "INVALID"


def availability_message() -> str:
    keys = _keys()
    with _LOCK:
        _sync_locked(keys)
        active = [(i, slot) for i, slot in enumerate(_SLOTS.values()) if keys[i]]
        if active and all(slot["status"] == "RATE LIMITED" for _, slot in active):
            emit("YOMI: Semua API Key terkena rate limit")
            return "YOMI: Semua API Key terkena rate limit. Siklus AUTO dihentikan; tunggu cooldown lalu coba lagi."
        if active and all(slot["status"] == "INVALID" for _, slot in active):
            emit("YOMI: Semua API Key ditolak (HTTP 401)")
            return "YOMI: Semua API Key ditolak (HTTP 401). Siklus AUTO dihentikan; periksa konfigurasi Groq."
        if not active:
            emit("YOMI: Tidak ada API Key Groq aktif")
            return "YOMI: Tidak ada API Key Groq aktif. Siklus AUTO dihentikan; atur minimal satu key."
        emit("YOMI: Tidak ada API Key yang tersedia")
        return "YOMI: Tidak ada API Key yang tersedia. Siklus AUTO dihentikan karena key lain ditolak atau terkena rate limit."

