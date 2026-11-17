"""Registry of AUTO language model engines."""

from __future__ import annotations

from modules.auto import qwen


def _check_groq_qwen(settings: dict) -> tuple[str, str, str]:
    config = qwen.configuration()
    if not config["configured"]:
        return "blocked", "Atur minimal satu API Key Groq di environment backend/.env.", ""
    last_test = settings.get("auto_engine_last_test_at", "")
    if settings.get("auto_engine_last_test_success") and settings.get("auto_engine_last_test_id") == "groq_qwen":
        return "tested", "Tes koneksi Groq dengan model Qwen berhasil pada " + last_test, last_test
    return "registered", "YOMI siap menggunakan API Key Groq yang tersedia.", ""


ENGINE_REGISTRY = {
    "groq_qwen": {
        "id": "groq_qwen", "label": "YOMI ENGINE 1.0", "default": True,
        "adapter": "modules.auto.qwen", "provider": "Groq", "check": _check_groq_qwen,
    },
}


def registered_engines(settings: dict) -> dict:
    selected = settings.get("auto_engine_id") or "groq_qwen"
    entries = []
    config = qwen.configuration()
    for definition in ENGINE_REGISTRY.values():
        status, reason, last_test_at = definition["check"](settings)
        entries.append({
            **{key: value for key, value in definition.items() if key != "check"},
            "selected": definition["id"] == selected, "status": status,
            "reason": reason, "last_test_at": last_test_at,
            "model": config["model"], "configured": config["configured"],
        })
    return {"engines": entries, "selected_engine_id": selected}
