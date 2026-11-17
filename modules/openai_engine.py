"""Single OpenAI Responses API service for ideation, research, and scripts."""

from __future__ import annotations

import json
import os
from typing import Any


class OpenAIServiceError(RuntimeError):
    """Safe user-facing error; never contains request credentials or raw SDK text."""


DEFAULT_MODEL = "gpt-6-luna"
MAX_RESEARCH_TOOL_CALLS = 16
WEB_SEARCH_USD_PER_CALL = 0.01

# Current documented standard token prices (USD per million tokens).
# Unknown model IDs must provide OPENAI_INPUT_PRICE_PER_MILLION and
# OPENAI_OUTPUT_PRICE_PER_MILLION so the daily budget can be enforced.
MODEL_PRICES = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-terra": (1.00, 5.00),
    "gpt-6.1-sol": (2.00, 10.00),
    "gpt-6-astra": (10.00, 50.00),
}

IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "research_summary": {"type": "string"},
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "concept": {"type": "string"},
                    "angle": {"type": "string"},
                    "hook": {"type": "string"},
                },
                "required": ["title", "concept", "angle", "hook"],
                "additionalProperties": False,
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["research_summary", "ideas", "sources"],
    "additionalProperties": False,
}

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "idea": {"type": "string"},
        "concept": {"type": "string"},
        "research_summary": {"type": "string"},
        "hook": {"type": "string"},
        "script": {"type": "string"},
        "description": {"type": "string"},
        "cta": {"type": "string"},
        "instructions": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "title", "idea", "concept", "research_summary", "hook", "script",
        "description", "cta", "instructions", "sources",
    ],
    "additionalProperties": False,
}


def model_name() -> str:
    return os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def price_per_million(model: str | None = None) -> tuple[float, float]:
    selected = model or model_name()
    if selected in MODEL_PRICES:
        return MODEL_PRICES[selected]
    try:
        input_price = float(os.environ["OPENAI_INPUT_PRICE_PER_MILLION"])
        output_price = float(os.environ["OPENAI_OUTPUT_PRICE_PER_MILLION"])
    except (KeyError, TypeError, ValueError):
        raise OpenAIServiceError(
            "Model OpenAI kustom belum punya estimasi biaya. Isi OPENAI_INPUT_PRICE_PER_MILLION dan OPENAI_OUTPUT_PRICE_PER_MILLION."
        ) from None
    if input_price < 0 or output_price < 0:
        raise OpenAIServiceError("Harga token OpenAI tidak valid")
    return input_price, output_price


def estimated_cost(input_tokens: int, output_tokens: int, search_calls: int, model: str | None = None) -> float:
    input_price, output_price = price_per_million(model)
    return (max(0, input_tokens) * input_price + max(0, output_tokens) * output_price) / 1_000_000 + max(0, search_calls) * WEB_SEARCH_USD_PER_CALL


def _client():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise OpenAIServiceError("OPENAI_API_KEY belum diisi di file .env")
    try:
        from openai import OpenAI
    except ImportError:
        raise OpenAIServiceError("SDK resmi OpenAI belum terpasang. Jalankan pip install -r requirements.txt") from None
    return OpenAI(api_key=key, max_retries=0, timeout=45.0)


def check_connection() -> dict:
    """Read-only authenticated API check; does not generate text or audio."""
    client = _client()
    try:
        client.models.list()
    except Exception as exc:
        raise _safe_api_error(exc) from None
    return {"connected": True, "model": model_name()}


def _safe_api_error(exc: Exception) -> OpenAIServiceError:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if name == "RateLimitError" or status == 429:
        return OpenAIServiceError("OpenAI membatasi permintaan. Coba kembali nanti; permintaan tidak diulang otomatis.")
    if name == "AuthenticationError" or status == 401:
        return OpenAIServiceError("OpenAI menolak API key. Periksa OPENAI_API_KEY di .env.")
    if name == "PermissionDeniedError" or status == 403:
        return OpenAIServiceError("API key tidak memiliki izin untuk model atau fitur yang diminta.")
    if name in {"APITimeoutError", "APIConnectionError"}:
        return OpenAIServiceError("Koneksi OpenAI gagal atau melewati batas waktu.")
    if status:
        return OpenAIServiceError(f"OpenAI mengembalikan HTTP {status}; periksa model dan konfigurasi API.")
    return OpenAIServiceError("Permintaan OpenAI gagal. Periksa koneksi dan konfigurasi, lalu coba sekali lagi.")


def _sources_from_response(response: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    try:
        data = response.model_dump()
    except Exception:
        data = {}
    for item in data.get("output", []) if isinstance(data, dict) else []:
        if item.get("type") != "web_search_call":
            continue
        action = item.get("action") or {}
        for source in action.get("sources", []) if isinstance(action, dict) else []:
            url = str(source.get("url", "")).strip()
            title = str(source.get("title", "")).strip()
            if url.startswith(("https://", "http://")) and url not in {row["url"] for row in found}:
                found.append({"title": title[:300] or url, "url": url[:2000]})
    return found[:20]


def _request_json(kind: str, instructions: str, prompt: str, schema: dict, research: bool,
                  max_output_tokens: int) -> dict:
    client = _client()
    tools = [{"type": "web_search", "search_context_size": "low"}] if research else []
    kwargs = {
        "model": model_name(),
        "instructions": instructions,
        "input": prompt,
        "max_output_tokens": max(128, min(int(max_output_tokens), 8192)),
        "text": {"format": {"type": "json_schema", "name": kind, "strict": True, "schema": schema}},
    }
    if research:
        kwargs.update({
            "tools": tools,
            "tool_choice": "required",
            "max_tool_calls": MAX_RESEARCH_TOOL_CALLS,
            "include": ["web_search_call.action.sources"],
        })
    try:
        response = client.responses.create(**kwargs)
    except Exception as exc:
        raise _safe_api_error(exc) from None
    usage = getattr(response, "usage", None)
    metrics = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "web_search_calls": sum(
            1 for item in getattr(response, "output", [])
            if getattr(item, "type", "") == "web_search_call"
        ),
        "model": getattr(response, "model", model_name()),
        "request_id": str(getattr(response, "_request_id", "") or "")[:120],
    }
    if getattr(response, "status", "completed") == "incomplete":
        error = OpenAIServiceError("Jawaban OpenAI terpotong oleh batas output. Naikkan batas token atau pendekkan instruksi.")
        error.usage = metrics
        raise error
    try:
        result = json.loads(response.output_text)
    except (TypeError, ValueError):
        raise OpenAIServiceError("OpenAI tidak mengembalikan JSON terstruktur yang valid") from None
    if not isinstance(result, dict):
        raise OpenAIServiceError("Format jawaban OpenAI tidak valid")
    result["_usage"] = metrics
    if research:
        sources = _sources_from_response(response)
        # Only report URLs supplied by the actual hosted search tool.
        result["sources"] = sources
    return result


def generate_ideas(topic: str, category: str, language: str, tone: str, max_output_tokens: int) -> dict:
    prompt = (
        f"Buat 3 ide video orisinal berbahasa {language}. Topik masukan: {topic or 'pilih tren yang relevan'}. "
        f"Kategori: {category or 'umum'}. Gaya: {tone or 'jelas dan menarik'}. "
        "Lakukan satu pencarian web ringkas. Ringkasan maksimal dua kalimat dan setiap ide singkat. "
        "Sertakan maksimal tiga sumber yang benar-benar dikembalikan alat. Jangan mengarang fakta atau sumber. "
        "Ide harus layak untuk video pendek dan berbeda satu sama lain."
    )
    return _request_json(
        "content_ideas",
        "Anda adalah editor riset video berbahasa Indonesia. Jawab hanya dalam skema JSON yang diberikan.",
        prompt, IDEAS_SCHEMA, True, max_output_tokens,
    )


def research_topic(topic: str, category: str, language: str, max_output_tokens: int) -> dict:
    prompt = (
        f"Riset topik video: {topic}. Kategori: {category or 'umum'}. Bahasa: {language}. "
        "Lakukan satu pencarian web ringkas untuk memeriksa fakta terbaru, lalu berikan 3 sudut ide video ringkas. "
        "Jangan mengarang fakta atau sumber; kumpulkan sumber yang dikembalikan alat pencarian."
    )
    return _request_json(
        "topic_research", "Anda adalah peneliti konten. Bedakan fakta, konteks, dan ide. Jawab hanya dalam JSON.",
        prompt, IDEAS_SCHEMA, True, max_output_tokens,
    )


def write_script(topic: str, title: str, duration_seconds: int, language: str, tone: str,
                 research: bool, max_output_tokens: int, selected_idea: str = "") -> dict:
    prompt = (
        f"Tulis konsep dan naskah video untuk topik: {topic}. Ide terpilih: {selected_idea or 'pilih sudut yang paling kuat'}. "
        f"Judul awal: {title or 'buat judul yang menarik'}. Target durasi: {duration_seconds} detik. "
        f"Bahasa: {language}. Tone: {tone or 'jelas, akurat, menarik'}. "
        "Buat satu hook pembuka yang kuat, naskah narasi sesuai durasi, deskripsi, CTA, dan arahan visual yang tidak dibacakan. "
        "Naskah harus wajar dibacakan, tidak memuat klaim yang tidak didukung. Jika riset aktif, rangkum fakta hanya dari sumber hasil pencarian."
    )
    return _request_json(
        "video_script",
        "Anda adalah penulis naskah video. Ikuti durasi dan bahasa. Hindari klaim berlebihan dan jawab hanya JSON sesuai skema.",
        prompt, DRAFT_SCHEMA, research, max_output_tokens,
    )

