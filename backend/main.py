"""Local-first REST API and static dashboard server (Python standard library only)."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from email import policy
from email.parser import BytesParser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LEMO_STYLE_CATALOG_PATH = ROOT / "modules" / "lemo" / "style_catalog.json"
LEMO_STYLE_CATALOG = json.loads(LEMO_STYLE_CATALOG_PATH.read_text(encoding="utf-8"))
LEMO_STYLE_OPTIONS = {item["slug"]: item for item in LEMO_STYLE_CATALOG["styles"]}
LEMO_STYLE_ALIASES = {"lemo-watercolor": "watercolor"}
SUPPORTED_VISUAL_STYLES = {"remotion", *LEMO_STYLE_OPTIONS, *LEMO_STYLE_ALIASES}


def canonical_visual_style(value: str) -> str:
    return LEMO_STYLE_ALIASES.get(value, value)


def lemo_style_name(value: str) -> str:
    canonical = canonical_visual_style(value)
    return str(LEMO_STYLE_OPTIONS.get(canonical, {}).get("name") or canonical)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_env_file() -> None:
    """Read simple KEY=VALUE entries without logging secrets or overriding shell env."""
    env_file = ROOT / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        os.environ.setdefault(key, value)


load_env_file()
DB_PATH = Path(os.environ.get("OVS_DB_PATH", ROOT / "data" / "studio.sqlite3"))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH
if not DB_PATH.resolve().is_relative_to(ROOT.resolve()):
    raise SystemExit("OVS_DB_PATH must stay inside the project directory")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize_db() -> None:
    with connect_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                research_references TEXT NOT NULL DEFAULT '',
                script TEXT NOT NULL DEFAULT '',
                instructions TEXT NOT NULL DEFAULT '',
                script_approved INTEGER NOT NULL DEFAULT 0,
                ai_metadata_json TEXT NOT NULL DEFAULT '{}',
                visual_style TEXT NOT NULL DEFAULT 'watercolor',
                status TEXT NOT NULL DEFAULT 'Draft',
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenes (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                narration TEXT NOT NULL DEFAULT '',
                start_seconds REAL NOT NULL DEFAULT 0,
                end_seconds REAL NOT NULL DEFAULT 1,
                duration_seconds REAL NOT NULL DEFAULT 0,
                transition TEXT NOT NULL DEFAULT 'cut',
                visual_description TEXT NOT NULL DEFAULT '',
                animation TEXT NOT NULL DEFAULT '',
                text_overlay TEXT NOT NULL DEFAULT '',
                background TEXT NOT NULL DEFAULT '',
                revision_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS scenes_project_position ON scenes(project_id, position);
            CREATE TABLE IF NOT EXISTS media_assets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                scene_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),
                mime_type TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(scene_id) REFERENCES scenes(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS media_scene_position ON media_assets(scene_id, position);
            CREATE TABLE IF NOT EXISTS audio_assets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                sample_rate INTEGER NOT NULL,
                alignment_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS audio_project_created ON audio_assets(project_id, created_at);
            CREATE TABLE IF NOT EXISTS compositions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                qc_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                artifact_path TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ai_usage (
                id TEXT PRIMARY KEY,
                request_type TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                web_search_calls INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd REAL NOT NULL DEFAULT 0,
                reserved_cost_usd REAL NOT NULL DEFAULT 0,
                request_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ai_usage_created ON ai_usage(created_at);
            CREATE TABLE IF NOT EXISTS automation_queue (
                id TEXT PRIMARY KEY,
                schedule_key TEXT UNIQUE,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                project_id TEXT,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS automation_queue_status ON automation_queue(status, created_at);
            CREATE TABLE IF NOT EXISTS auto_cycles (
                id TEXT PRIMARY KEY,
                production_date TEXT NOT NULL,
                topic TEXT NOT NULL,
                engine_id TEXT NOT NULL DEFAULT 'moneyprinterturbo',
                target_unique INTEGER NOT NULL,
                format_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                is_additional INTEGER NOT NULL DEFAULT 0,
                test_mode INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS auto_cycles_date_status ON auto_cycles(production_date,status);
            CREATE TABLE IF NOT EXISTS auto_queue (
                id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                topic TEXT NOT NULL,
                video_script TEXT NOT NULL DEFAULT '',
                video_terms TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                progress_detail TEXT NOT NULL DEFAULT '',
                mpt_task_ids TEXT NOT NULL DEFAULT '{}',
                outputs_json TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(cycle_id) REFERENCES auto_cycles(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS auto_queue_cycle_status ON auto_queue(cycle_id,status,position);
            CREATE TABLE IF NOT EXISTS auto_llm_usage (
                id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS auto_llm_usage_created ON auto_llm_usage(created_at,status);
        """)
        # Additive migrations keep databases created by earlier versions intact.
        project_columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
        if "research_references" not in project_columns:
            db.execute("ALTER TABLE projects ADD COLUMN research_references TEXT NOT NULL DEFAULT ''")
        if "script_approved" not in project_columns:
            db.execute("ALTER TABLE projects ADD COLUMN script_approved INTEGER NOT NULL DEFAULT 0")
        if "ai_metadata_json" not in project_columns:
            db.execute("ALTER TABLE projects ADD COLUMN ai_metadata_json TEXT NOT NULL DEFAULT '{}'")
        if "visual_style" not in project_columns:
            db.execute("ALTER TABLE projects ADD COLUMN visual_style TEXT NOT NULL DEFAULT 'remotion'")
        llm_usage_columns = {row["name"] for row in db.execute("PRAGMA table_info(auto_llm_usage)")}
        if "prompt_tokens" not in llm_usage_columns:
            db.execute("ALTER TABLE auto_llm_usage ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0")
        if "completion_tokens" not in llm_usage_columns:
            db.execute("ALTER TABLE auto_llm_usage ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0")
        cycle_columns = {row["name"] for row in db.execute("PRAGMA table_info(auto_cycles)")}
        if "test_mode" not in cycle_columns:
            db.execute("ALTER TABLE auto_cycles ADD COLUMN test_mode INTEGER NOT NULL DEFAULT 0")
        scene_columns = {row["name"] for row in db.execute("PRAGMA table_info(scenes)")}
        if "duration_seconds" not in scene_columns:
            db.execute("ALTER TABLE scenes ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 0")
        if "transition" not in scene_columns:
            db.execute("ALTER TABLE scenes ADD COLUMN transition TEXT NOT NULL DEFAULT 'cut'")
        auto_columns = {row["name"] for row in db.execute("PRAGMA table_info(auto_queue)")}
        if "progress" not in auto_columns:
            db.execute("ALTER TABLE auto_queue ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
        if "progress_detail" not in auto_columns:
            db.execute("ALTER TABLE auto_queue ADD COLUMN progress_detail TEXT NOT NULL DEFAULT ''")
        if "video_terms" not in auto_columns:
            db.execute("ALTER TABLE auto_queue ADD COLUMN video_terms TEXT NOT NULL DEFAULT '[]'")
        if "metadata_json" not in auto_columns:
            db.execute("ALTER TABLE auto_queue ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        cycle_columns = {row["name"] for row in db.execute("PRAGMA table_info(auto_cycles)")}
        if "engine_id" not in cycle_columns:
            db.execute("ALTER TABLE auto_cycles ADD COLUMN engine_id TEXT NOT NULL DEFAULT 'moneyprinterturbo'")


DEFAULT_SETTINGS = {
    "daily_target": 5,
    "production_time": "09:00",
    "timezone": "Asia/Jakarta",
    "category": "",
    "language": "id",
    "template": "",
    "platforms": [],
    "approval_required": True,
    "render_width": 1080,
    "render_height": 1920,
    "render_fps": 30,
    "render_format": "mp4",
    "cartesia_voice_id": "",
    "auto_daily_target": 5,
    "auto_schedule_enabled": False,
    "auto_production_time": "09:00",
    "auto_timezone": "Asia/Jakarta",
    "auto_schedule_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "auto_video_format": "portrait",
    "auto_video_duration_seconds": 60,
    "auto_tts_provider": "edge",
    "auto_voice_id": "id-ID-GadisNeural",
    "auto_engine_id": "groq_qwen",
    "auto_llm_provider": "groq",
    "auto_llm_model": "qwen/qwen3.8-27b",
    "auto_llm_base_url": "",
    "auto_mpt_root": str(ROOT / "vendor" / "dependencies" / "MoneyPrinterTurbo"),
    "auto_mpt_port": 8080,
    "auto_topic": "",
    "auto_music_enabled": True,
    "auto_subtitle_enabled": False,
    "auto_visual_style": "auto",
    "auto_visual_styles": list(LEMO_STYLE_OPTIONS),
    "visual_style": "watercolor",
}


def read_settings() -> dict:
    with connect_db() as db:
        rows = db.execute("SELECT key,value FROM settings WHERE key LIKE 'config:%'").fetchall()
    result = dict(DEFAULT_SETTINGS)
    for row in rows:
        try:
            result[row["key"][7:]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue
    result.pop("auto_ai_choice", None)
    if result.get("auto_engine_id") in {"moneyprinterturbo", "qwen_api"}:
        result["auto_engine_id"] = "groq_qwen"
    result["auto_llm_provider"] = "groq"
    result["auto_llm_model"] = os.environ.get("GROQ_MODEL") or result.get("auto_llm_model") or "qwen/qwen3.8-27b"
    vendored_mpt = ROOT / "vendor" / "dependencies" / "MoneyPrinterTurbo"
    result["auto_mpt_root"] = os.environ.get("MPT_ROOT") or os.environ.get("MONEYPRINTERTURBO_ROOT") or result.get("auto_mpt_root") or (str(vendored_mpt) if vendored_mpt.is_dir() else "")
    if not result["cartesia_voice_id"]:
        result["cartesia_voice_id"] = os.environ.get("CARTESIA_VOICE_ID", "")
    return result


AUTO_SECRET_KEYS = ("AUTO_LLM_API_KEY", "MPT_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
AUTO_APPLY_LOCK = threading.Lock()
AUTO_WORKER_THREAD: threading.Thread | None = None
AUTO_SCHEDULER_THREAD: threading.Thread | None = None
AUTO_PROCESS: subprocess.Popen | None = None
AUTO_SHUTDOWN = threading.Event()


def write_auto_secrets(values: dict[str, str]) -> None:
    """Store AUTO secrets in the protected local .env without returning them."""
    env_file = ROOT / ".env"
    current = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    replacements = {key: value for key, value in values.items() if value}
    lines = current.splitlines()
    seen = set()
    output = []
    for line in lines:
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in replacements:
            key = match.group(1)
            output.append(f"{key}={json.dumps(replacements[key])}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in replacements.items():
        if key not in seen:
            output.append(f"{key}={json.dumps(value)}")
        os.environ[key] = value
    temporary = env_file.with_suffix(".env.tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, env_file)
    os.chmod(env_file, 0o600)


def auto_secrets_configured() -> dict:
    return {key.lower(): bool(os.environ.get(key, "").strip()) for key in AUTO_SECRET_KEYS}


def validate_scene(payload: dict, existing: dict | None = None) -> dict:
    current = existing or {}
    values = {
        "narration": str(payload.get("narration", current.get("narration", "")))[:10000],
        "visual_description": str(payload.get("visual_description", current.get("visual_description", "")))[:4000],
        "animation": str(payload.get("animation", current.get("animation", "")))[:1000],
        "text_overlay": str(payload.get("text_overlay", current.get("text_overlay", "")))[:2000],
        "background": str(payload.get("background", current.get("background", "")))[:1000],
        "revision_notes": str(payload.get("revision_notes", current.get("revision_notes", "")))[:4000],
        "transition": str(payload.get("transition", current.get("transition", "cut"))),
    }
    if values["transition"] not in {"cut", "fade"}:
        raise ValueError("transition must be cut or fade")
    try:
        values["duration_seconds"] = float(payload.get("duration_seconds", current.get("duration_seconds", 0)))
    except (ValueError, TypeError):
        raise ValueError("duration_seconds must be a number")
    if values["duration_seconds"] < 0 or values["duration_seconds"] > 3600:
        raise ValueError("duration_seconds must be between 0 and 3600")
    try:
        values["start_seconds"] = float(payload.get("start_seconds", current.get("start_seconds", 0)))
        values["end_seconds"] = float(payload.get("end_seconds", current.get("end_seconds", 1)))
    except (ValueError, TypeError):
        raise ValueError("scene timestamps must be numbers")
    if values["start_seconds"] < 0 or values["end_seconds"] <= values["start_seconds"]:
        raise ValueError("scene end_seconds must be greater than start_seconds >= 0")
    return values


def split_script_into_scenes(script: str, count: int) -> list[str]:
    """Split a saved script into contiguous, roughly even narration chunks."""
    if not 2 <= count <= 5:
        raise ValueError("Jumlah scene harus 2 sampai 5")
    units = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", script.strip()) if part.strip()]
    if len(units) < count:
        words = script.split()
        if len(words) < count * 3:
            raise ValueError("Naskah terlalu pendek untuk dibagi menjadi scene yang memadai")
        base, extra = divmod(len(words), count)
        chunks, cursor = [], 0
        for index in range(count):
            size = base + (1 if index < extra else 0)
            chunks.append(" ".join(words[cursor:cursor + size]))
            cursor += size
        return chunks
    groups, cursor = [], 0
    for index in range(count):
        remaining_groups = count - index
        if remaining_groups == 1:
            groups.append(" ".join(units[cursor:]))
            break
        remaining_words = sum(len(unit.split()) for unit in units[cursor:])
        target = remaining_words / remaining_groups
        start, weight = cursor, 0
        while cursor < len(units) - (remaining_groups - 1):
            next_weight = len(units[cursor].split())
            if cursor > start and weight + next_weight > target:
                break
            weight += next_weight
            cursor += 1
            if weight >= target:
                break
        groups.append(" ".join(units[start:cursor]))
    if len(groups) != count or any(not group.strip() for group in groups):
        raise ValueError("Naskah tidak dapat dibagi dengan aman menjadi scene")
    return groups


def generate_remotion_scenes(project_id: str, count: int, replace_existing: bool) -> dict:
    """Split a saved narration and create Remotion instructions without requiring media."""
    from modules.remotion.scene_generator import split_narration
    from modules.auto.qwen import generate_visual_plan

    if not 2 <= count <= 5:
        raise ValueError("Jumlah scene harus 2 sampai 5")
    with connect_db() as db:
        project = db.execute("SELECT id,title,topic,script,status,visual_style FROM projects WHERE id=?", (project_id,)).fetchone()
        existing = db.execute("SELECT id FROM scenes WHERE project_id=? ORDER BY position", (project_id,)).fetchall()
    if not project:
        raise LookupError("Project not found")
    if project["status"] in {"Producing", "Processing"}:
        raise RuntimeError("Scene tidak dapat dibuat saat produksi sedang berjalan")
    if not project["topic"].strip() or not project["script"].strip():
        raise ValueError("Simpan topik dan naskah sebelum membuat scene otomatis")
    if existing and not replace_existing:
        raise RuntimeError("Scene manual sudah ada; konfirmasi penggantian atau hapus scene terlebih dahulu")
    segments = split_narration(project["script"], count)
    style = project["visual_style"]
    plans = generate_visual_plan(project["topic"], segments, canonical_visual_style(style), lemo_style_name(style))
    timestamp = now_iso()
    created_scene_ids = []
    with connect_db() as db:
        current = db.execute("SELECT topic,script,status FROM projects WHERE id=?", (project_id,)).fetchone()
        current_ids = [row["id"] for row in db.execute("SELECT id FROM scenes WHERE project_id=? ORDER BY position", (project_id,)).fetchall()]
        if not current or current["topic"] != project["topic"] or current["script"] != project["script"] or current["status"] in {"Producing", "Processing"}:
            raise RuntimeError("Draft berubah saat YOMI membuat visual; simpan ulang lalu coba lagi")
        if current_ids != [row["id"] for row in existing]:
            raise RuntimeError("Scene proyek berubah saat YOMI membuat visual; muat ulang lalu coba lagi")
        for index, plan in enumerate(plans):
            scene_id = str(uuid.uuid4())
            scene = validate_scene({
                "narration": segments[index], "visual_description": plan["visual_prompt"],
                "animation": plan["visual_type"], "text_overlay": plan["heading"],
                "duration_seconds": 0, "transition": plan["transition"], "start_seconds": 0, "end_seconds": 1,
            })
            position = len(current_ids) + index
            db.execute(
                "INSERT INTO scenes(id,project_id,position,narration,start_seconds,end_seconds,duration_seconds,transition,visual_description,animation,text_overlay,background,revision_notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (scene_id, project_id, position, scene["narration"], scene["start_seconds"], scene["end_seconds"], scene["duration_seconds"], scene["transition"], scene["visual_description"], scene["animation"], scene["text_overlay"], scene["background"], scene["revision_notes"], timestamp, timestamp),
            )
            created_scene_ids.append(scene_id)
        old_media_paths = []
        if existing:
            old_ids = [row["id"] for row in existing]
            placeholders = ",".join("?" for _ in old_ids)
            old_media_paths = [ROOT / row["relative_path"] for row in db.execute(
                f"SELECT relative_path FROM media_assets WHERE project_id=? AND scene_id IN ({placeholders})",
                (project_id, *old_ids),
            ).fetchall()]
            db.execute(f"DELETE FROM scenes WHERE project_id=? AND id IN ({placeholders})", (project_id, *old_ids))
            for position, scene_id in enumerate(created_scene_ids):
                db.execute("UPDATE scenes SET position=? WHERE id=? AND project_id=?", (position, scene_id, project_id))
        db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (timestamp, project_id))
        db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "scenes_generated_remotion", f"{count} scene animasi dibuat dari naskah dengan YOMI dan Remotion", timestamp))
    output_root = (ROOT / "output").resolve()
    for old_path in old_media_paths:
        resolved = old_path.resolve()
        if resolved.is_relative_to(output_root) and resolved.is_file():
            resolved.unlink()
    return {
        "scene_count": len(created_scene_ids), "media_count": 0,
        "generated_count": len(created_scene_ids), "scene_ids": created_scene_ids,
        "visual_types": [plan["visual_type"] for plan in plans],
    }

def validate_settings(payload: dict) -> dict:
    allowed = set(DEFAULT_SETTINGS)
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("unsupported settings: " + ", ".join(sorted(unknown)))
    result = {}
    if "daily_target" in payload:
        try:
            target = int(payload["daily_target"])
        except (TypeError, ValueError):
            raise ValueError("daily_target must be an integer from 1 to 24")
        if not 1 <= target <= 24:
            raise ValueError("daily_target must be between 1 and 24")
        result["daily_target"] = target
    if "production_time" in payload:
        value = str(payload["production_time"])
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("production_time must use HH:MM")
        result["production_time"] = value
    for key in ("timezone", "category", "language", "template", "cartesia_voice_id"):
        if key in payload:
            value = str(payload[key]).strip()
            if key == "timezone":
                from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
                try:
                    ZoneInfo(value)
                except (ZoneInfoNotFoundError, ValueError):
                    raise ValueError("timezone must be a recognized IANA time zone")
            result[key] = value[:120]
    if "platforms" in payload:
        items = payload["platforms"]
        supported = {"youtube", "tiktok", "instagram", "facebook"}
        if not isinstance(items, list) or any(not isinstance(item, str) or item not in supported for item in items):
            raise ValueError("platforms must be a list of supported platform IDs")
        result["platforms"] = list(dict.fromkeys(items))
    if "approval_required" in payload:
        if not isinstance(payload["approval_required"], bool):
            raise ValueError("approval_required must be a boolean")
        result["approval_required"] = payload["approval_required"]
    for key, lower, upper in (("render_width", 144, 3840), ("render_height", 144, 3840), ("render_fps", 1, 120)):
        if key in payload:
            try:
                number = int(payload[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be between {lower} and {upper}")
            if not lower <= number <= upper:
                raise ValueError(f"{key} must be between {lower} and {upper}")
            result[key] = number
    if "render_format" in payload:
        value = str(payload["render_format"]).lower()
        if value not in {"mp4", "webm"}:
            raise ValueError("render_format must be mp4 or webm")
        result["render_format"] = value
    if "auto_daily_target" in payload:
        try:
            target = int(payload["auto_daily_target"])
        except (TypeError, ValueError):
            raise ValueError("auto_daily_target must be between 1 and 24")
        if not 1 <= target <= 24:
            raise ValueError("auto_daily_target must be between 1 and 24")
        result["auto_daily_target"] = target
    for key in ("auto_schedule_enabled", "auto_music_enabled", "auto_subtitle_enabled"):
        if key in payload:
            if not isinstance(payload[key], bool):
                raise ValueError(f"{key} must be a boolean")
            result[key] = payload[key]
    if "auto_production_time" in payload:
        value = str(payload["auto_production_time"])
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("auto_production_time must use HH:MM")
        result["auto_production_time"] = value
    if "auto_timezone" in payload:
        value = str(payload["auto_timezone"]).strip()
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("auto_timezone must be a recognized IANA time zone")
        result["auto_timezone"] = value[:80]
    if "auto_schedule_days" in payload:
        days = payload["auto_schedule_days"]
        allowed_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        if not isinstance(days, list) or not days or any(day not in allowed_days for day in days):
            raise ValueError("auto_schedule_days must contain valid weekdays")
        result["auto_schedule_days"] = list(dict.fromkeys(days))
    if "auto_video_format" in payload:
        value = str(payload["auto_video_format"])
        if value not in {"landscape", "portrait", "both"}:
            raise ValueError("auto_video_format must be landscape, portrait, or both")
        result["auto_video_format"] = value
    if "auto_video_duration_seconds" in payload:
        try:
            duration = int(payload["auto_video_duration_seconds"])
        except (TypeError, ValueError):
            raise ValueError("Durasi AUTO harus 15, 30, 45, atau 60 detik")
        if duration not in {15, 30, 45, 60}:
            raise ValueError("Durasi AUTO harus 15, 30, 45, atau 60 detik")
        result["auto_video_duration_seconds"] = duration
    if "auto_tts_provider" in payload:
        value = str(payload["auto_tts_provider"]).strip().lower()
        if value not in {"edge", "cartesia"}:
            raise ValueError("auto_tts_provider must be edge or cartesia")
        result["auto_tts_provider"] = value
    if "auto_visual_style" in payload:
        value = str(payload["auto_visual_style"]).strip()
        if value != "auto" and value not in LEMO_STYLE_OPTIONS and value not in LEMO_STYLE_ALIASES:
            raise ValueError("Gaya visual harus dipilih dari katalog Lemo yang tersedia")
        result["auto_visual_style"] = value
    if "auto_visual_styles" in payload:
        values = payload["auto_visual_styles"]
        if not isinstance(values, list) or not values or any(not isinstance(value, str) or value not in LEMO_STYLE_OPTIONS for value in values):
            raise ValueError("Pilih minimal satu gaya visual dari katalog")
        result["auto_visual_styles"] = list(dict.fromkeys(values))
    if "visual_style" in payload:
        value = str(payload["visual_style"]).strip()
        if value not in SUPPORTED_VISUAL_STYLES:
            raise ValueError("Gaya visual tidak tersedia")
        result["visual_style"] = value
    if "auto_voice_id" in payload:
        value = str(payload["auto_voice_id"]).strip()
        if not re.fullmatch(r"id-ID-[A-Za-z]+Neural", value):
            raise ValueError("auto_voice_id must be selected from the Indonesian Edge TTS catalog")
        result["auto_voice_id"] = value
    if "auto_engine_id" in payload:
        from modules.auto.engines import ENGINE_REGISTRY
        value = str(payload["auto_engine_id"]).strip()
        if value not in ENGINE_REGISTRY:
            raise ValueError("AI ENGINE belum terdaftar atau tidak tersedia")
        result["auto_engine_id"] = value
    for key, limit in (("auto_llm_provider", 120), ("auto_llm_model", 200), ("auto_llm_base_url", 500)):
        if key in payload:
            value = str(payload[key]).strip()
            if len(value) > limit or (key == "auto_llm_base_url" and value and not value.startswith(("https://", "http://"))):
                raise ValueError(f"{key} is invalid")
            result[key] = value
    if "auto_mpt_root" in payload:
        value = str(payload["auto_mpt_root"]).strip()
        if len(value) > 500 or "\x00" in value:
            raise ValueError("auto_mpt_root is invalid")
        result["auto_mpt_root"] = value
    if "auto_topic" in payload:
        value = str(payload["auto_topic"]).strip()
        if len(value) > 1000:
            raise ValueError("auto_topic must be at most 1000 characters")
        result["auto_topic"] = value
    return result


def capabilities() -> dict:
    env = os.environ
    node_bin = shutil.which("node")
    node_version = "unavailable"
    node_major = 0
    if node_bin:
        try:
            result = subprocess.run([node_bin, "--version"], capture_output=True, text=True, timeout=3, check=False)
            node_version = result.stdout.strip() if result.returncode == 0 else "unknown"
            match = re.match(r"v?(\d+)", node_version)
            node_major = int(match.group(1)) if match else 0
        except (OSError, subprocess.TimeoutExpired):
            node_version = "unknown"
    remotion_ready = (ROOT / "renderer" / "remotion" / "node_modules" / "@remotion" / "renderer").is_dir()
    hyperframes_bin = shutil.which("hyperframes")
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    blockers = []
    if not remotion_ready:
        blockers.append("Dependensi Remotion belum dipasang")
    if not node_bin:
        blockers.append("Node.js tidak ditemukan")
    elif node_major < 22:
        blockers.append(f"Remotion memerlukan Node.js 22+ (tersedia {node_version})")
    if not ffmpeg_bin:
        blockers.append("FFmpeg tidak ditemukan")
    if not ffprobe_bin:
        blockers.append("FFprobe tidak ditemukan")
    return {
        "python": {"available": True, "version": __import__("platform").python_version()},
        "node": {"available": bool(node_bin), "version": node_version, "compatible": bool(node_bin and node_major >= 22)},
        "ffmpeg": {"available": bool(ffmpeg_bin)},
        "ffprobe": {"available": bool(ffprobe_bin)},
        "remotion": {"available": remotion_ready, "compatible": bool(remotion_ready and node_bin and node_major >= 22)},
        "hyperframes": {"available": bool(hyperframes_bin), "compatible": bool(hyperframes_bin and node_bin and node_major >= 22)},
        "render": {"ready": not blockers, "blockers": blockers},
        "cartesia": {"configured": bool(env.get("CARTESIA_API_KEY") and read_settings()["cartesia_voice_id"])},
        "telegram": {"configured": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"))},
        "publishing": {"status": "not_configured"},
    }


def cartesia_connection_check() -> bool:
    """Verify Cartesia key and configured voice with a read-only request."""
    import urllib.error
    import urllib.request

    api_key = os.environ.get("CARTESIA_API_KEY", "").strip()
    voice_id = read_settings()["cartesia_voice_id"]
    if not api_key or not voice_id:
        return False
    api_version = os.environ.get("CARTESIA_API_VERSION", "2026-08-14")
    request = urllib.request.Request(
        "https://api.cartesia.ai/voices/" + quote(voice_id, safe=""),
        headers={"Authorization": "Bearer " + api_key, "Cartesia-Version": api_version},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False


def save_auto_settings(updates: dict) -> None:
    if not updates:
        return
    timestamp = now_iso()
    with connect_db() as db:
        for key, value in updates.items():
            db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                ("config:" + key, json.dumps(value, ensure_ascii=False), timestamp),
            )


def store_auto_error(message: str) -> None:
    save_auto_settings({"auto_last_error": str(message)[:500], "auto_runtime_state": "error"})


def record_auto_llm(stage: str, status: str, *, cycle_id: str = "", item_id: str = "", error: str = "", usage: dict | None = None) -> None:
    settings = read_settings()
    provider = settings.get("auto_llm_provider", "") or "unselected"
    model = settings.get("auto_llm_model", "")
    with connect_db() as db:
        db.execute(
            "INSERT INTO auto_llm_usage(id,cycle_id,item_id,provider,model,stage,status,error,prompt_tokens,completion_tokens,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), cycle_id, item_id, provider or "unconfigured", model or "unconfigured", stage[:80], status, error[:240], int((usage or {}).get("prompt_tokens", 0) or 0), int((usage or {}).get("completion_tokens", 0) or 0), now_iso()),
        )


def check_auto_llm_daily_limit() -> None:
    from zoneinfo import ZoneInfo
    from modules.auto.qwen import GroqError
    try:
        limit = int(os.environ.get("GROQ_DAILY_REQUEST_LIMIT", "80"))
    except ValueError:
        raise GroqError("GROQ_DAILY_REQUEST_LIMIT harus berupa bilangan bulat") from None
    if not 1 <= limit <= 10000:
        raise GroqError("GROQ_DAILY_REQUEST_LIMIT harus antara 1 dan 10000")
    local_zone = ZoneInfo("Asia/Jakarta")
    local_midnight = datetime.now(local_zone).replace(hour=0, minute=0, second=0, microsecond=0)
    utc_start = local_midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as db:
        used = db.execute("SELECT COUNT(*) FROM auto_llm_usage WHERE created_at>=?", (utc_start,)).fetchone()[0]
    if used >= limit:
        raise GroqError(f"Batas GROQ_DAILY_REQUEST_LIMIT ({limit}) tercapai; siklus dihentikan tanpa request baru")


def auto_llm_call(stage: str, callback, *, cycle_id: str = "", item_id: str = ""):
    from modules.auto import yomi
    previous_event = None
    if item_id:
        def record_yomi_event(message: str) -> None:
            with connect_db() as db:
                row = db.execute("SELECT progress_detail FROM auto_queue WHERE id=?", (item_id,)).fetchone()
                if not row:
                    return
                events = [entry for entry in row["progress_detail"].splitlines() if entry]
                events.append(message[:180])
                db.execute("UPDATE auto_queue SET progress_detail=?,updated_at=? WHERE id=?", ("\n".join(events[-20:]), now_iso(), item_id))
        previous_event = yomi.set_event_callback(record_yomi_event)
    try:
        check_auto_llm_daily_limit()
        result = callback()
    except Exception as exc:
        from modules.auto.qwen import last_usage
        record_auto_llm(stage, "failed", cycle_id=cycle_id, item_id=item_id, error=str(exc), usage=last_usage())
        raise
    finally:
        if item_id:
            yomi.set_event_callback(previous_event)
    from modules.auto.qwen import last_usage
    record_auto_llm(stage, "success", cycle_id=cycle_id, item_id=item_id, usage=last_usage())
    return result


def _auto_video_metadata(topic: str, script: str, *, cycle_id: str, item_id: str) -> dict:
    """Generate optional publishing metadata, with a factual title fallback.

    A malformed structured response must not discard an otherwise valid video
    script. Provider and quota failures still propagate and stop the cycle.
    """
    from modules.auto.qwen import GroqError, generate_metadata

    try:
        return auto_llm_call(
            "metadata", lambda: generate_metadata(topic, script),
            cycle_id=cycle_id, item_id=item_id,
        )
    except GroqError as exc:
        if str(exc) not in {
            "Model Qwen melalui Groq menghasilkan JSON terstruktur tidak valid",
            "Model Qwen melalui Groq tidak menghasilkan judul video",
        }:
            raise
        return {
            "title": topic[:180], "description": "", "caption": "", "hashtags": [],
            "metadata_fallback": "Judul memakai topik pengguna karena format metadata dari model tidak valid.",
        }


def _auto_formats(format_mode: str) -> list[tuple[str, str, int, int]]:
    formats = {
        "landscape": [("landscape", "16:9", 1920, 1080)],
        "portrait": [("portrait", "9:16", 1080, 1920)],
        "both": [("landscape", "16:9", 1920, 1080), ("portrait", "9:16", 1080, 1920)],
    }
    return formats[format_mode]


def _auto_mpt():
    from modules.auto import mpt
    settings = read_settings()
    root = settings.get("auto_mpt_root", "")
    if not root:
        raise mpt.MoneyPrinterTurboError("Path MoneyPrinterTurbo belum dipilih di AUTO → Pengaturan")
    port = int(settings.get("auto_mpt_port", 8080))
    return mpt, root, f"http://127.0.0.1:{port}", os.environ.get("MPT_API_KEY", "").strip()


def _prepare_auto_tts() -> list[dict]:
    settings = read_settings()
    if settings.get("auto_engine_id", "groq_qwen") != "groq_qwen":
        raise ValueError("AI ENGINE yang dipilih tidak terdaftar")
    if settings.get("auto_tts_provider", "edge") == "cartesia":
        if not os.environ.get("CARTESIA_API_KEY") or not settings.get("cartesia_voice_id"):
            raise ValueError("Cartesia AUTO memerlukan API key dan Voice ID di Settings")
        return []
    from modules.remotion.tts import edge_voices
    voices = edge_voices()
    voice_id = settings.get("auto_voice_id", "")
    if not voices:
        raise ValueError("Katalog Edge TTS tidak menyediakan suara bahasa Indonesia")
    if voice_id not in {voice["id"] for voice in voices}:
        raise ValueError("Suara pilihan tidak tersedia dalam katalog Edge TTS saat ini")
    return voices

def _new_auto_cycle(topic: str, first_script: str, additional: bool = False) -> dict:
    settings = read_settings()
    target = int(settings["auto_daily_target"])
    format_mode = settings["auto_video_format"]
    local_today = datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Jakarta")).date().isoformat()
    cycle_id, timestamp = str(uuid.uuid4()), now_iso()
    with connect_db() as db:
        running = db.execute("SELECT id FROM auto_cycles WHERE status='running' LIMIT 1").fetchone()
        if running:
            raise ValueError("AUTO sedang menjalankan siklus lain")
        already_done = db.execute(
            "SELECT COUNT(*) FROM auto_queue q JOIN auto_cycles c ON c.id=q.cycle_id WHERE c.production_date=? AND c.test_mode=0 AND q.status='complete'",
            (local_today,),
        ).fetchone()[0]
        target = max(0, target - int(already_done))
        if target == 0:
            raise ValueError("Target harian Asia/Jakarta sudah tercapai")
        previous = db.execute(
            "SELECT id,status FROM auto_cycles WHERE production_date=? AND is_additional=0 ORDER BY created_at DESC LIMIT 1",
            (local_today,),
        ).fetchone()
        if previous and previous["status"] == "complete" and not additional:
            raise ValueError("Target siklus reguler hari ini sudah tercapai; tandai siklus baru sebagai tambahan")
        db.execute(
            "INSERT INTO auto_cycles(id,production_date,topic,engine_id,target_unique,format_mode,status,is_additional,created_at,started_at) "
            "VALUES(?,?,?,?,?,?,'running',?,?,?)",
            (cycle_id, local_today, topic, settings.get("auto_engine_id", "groq_qwen"), target, format_mode, int(additional), timestamp, timestamp),
        )
        for index in range(1, target + 1):
            item_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO auto_queue(id,cycle_id,position,topic,video_script,status,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?)",
                (item_id, cycle_id, index, topic, first_script if index == 1 else "", timestamp, timestamp),
            )
        db.execute(
            "INSERT INTO events(kind,message,created_at) VALUES(?,?,?)",
            ("auto_cycle_started", f"Siklus AUTO dimulai: target {target} video unik, format {format_mode}", timestamp),
        )
    save_auto_settings({"auto_topic": topic, "auto_last_error": ""})
    save_auto_settings({"auto_runtime_state": "running"})
    if not additional and settings.get("auto_schedule_enabled"):
        try:
            from zoneinfo import ZoneInfo
            local_date = datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat()
            save_auto_settings({"auto_scheduled_date": local_date})
        except Exception:
            pass
    start_auto_worker()
    return {"id": cycle_id, "date": local_today, "engine_id": settings.get("auto_engine_id", "groq_qwen"), "target_unique": target, "format_mode": format_mode, "status": "running", "additional": additional}


def apply_auto(payload: dict) -> dict:
    """Save AUTO configuration, verify the selected engine, and begin a cycle."""
    if not AUTO_APPLY_LOCK.acquire(blocking=False):
        raise ValueError("Konfigurasi AUTO sedang diterapkan")
    try:
        with connect_db() as db:
            running = db.execute("SELECT id FROM auto_cycles WHERE status='running' LIMIT 1").fetchone()
        if running:
            raise ValueError("Siklus AUTO masih berjalan; tunggu hingga selesai sebelum menerapkan siklus berikutnya")
        supplied_settings = payload.get("settings", {})
        if not isinstance(supplied_settings, dict):
            raise ValueError("settings must be an object")
        updates = validate_settings(supplied_settings)
        updates.setdefault("auto_engine_id", read_settings().get("auto_engine_id", "groq_qwen"))
        allowed_auto = {key for key in DEFAULT_SETTINGS if key.startswith("auto_")}
        if set(updates) - allowed_auto:
            raise ValueError("AUTO hanya dapat mengubah pengaturan AUTO")
        selected_tts = updates.get("auto_tts_provider", read_settings().get("auto_tts_provider", "edge"))
        if selected_tts == "cartesia" and not (os.environ.get("CARTESIA_API_KEY") and read_settings().get("cartesia_voice_id")):
            raise ValueError("Sebelum memilih Cartesia di AUTO, isi API key dan Voice ID di Settings")
        topic = str(payload.get("topic", updates.get("auto_topic", read_settings().get("auto_topic", "")))).strip()
        if not topic or len(topic) > 1000:
            raise ValueError("Masukkan topik video terlebih dahulu di BUAT VIDEO")
        if payload.get("additional"):
            local_date = datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Jakarta")).date().isoformat()
            with connect_db() as db:
                completed_regular = db.execute(
                    "SELECT 1 FROM auto_cycles WHERE production_date=? AND is_additional=0 AND status='complete' LIMIT 1",
                    (local_date,),
                ).fetchone()
            if not completed_regular:
                raise ValueError("Siklus tambahan hanya tersedia setelah target reguler hari ini tercapai")
        updates["auto_topic"] = topic
        # Any edit invalidates the last successful preflight until the exact
        # current selection has been checked again. This also gates scheduler.
        updates["auto_llm_last_test_success"] = False
        updates["auto_engine_last_test_id"] = updates["auto_engine_id"]
        updates["auto_engine_last_test_success"] = False
        save_auto_settings(updates)

        incoming = {}
        for key in AUTO_SECRET_KEYS:
            value = payload.get(key.lower(), "")
            if value:
                value = str(value).strip()
                if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{8,512}", value):
                    raise ValueError(f"Kredensial {key} tidak valid")
                incoming[key] = value
        if incoming:
            write_auto_secrets(incoming)
        voices = _prepare_auto_tts()
        active = read_settings()
        engine_id = active["auto_engine_id"]
        from modules.auto import qwen
        if not qwen.configuration()["configured"]:
            raise ValueError("Atur minimal satu API Key Groq di environment backend/.env sebelum memulai AUTO")
        cycle = _new_auto_cycle(topic, "", bool(payload.get("additional", False)))
        return {
            "cycle": cycle, "engine_id": engine_id, "engine_label": "YOMI ENGINE 1.0",
            "voice_count": len(voices), "state": "running", "model": qwen.configuration()["model"],
        }
    except Exception as exc:
        store_auto_error(str(exc))
        raise
    finally:
        AUTO_APPLY_LOCK.release()


def _validate_auto_video(path: Path, aspect: str, expected_width: int, expected_height: int) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("FFprobe diperlukan untuk memeriksa hasil AUTO")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError(f"Quality Control {aspect} gagal membaca video")
    try:
        data = json.loads(result.stdout)
        video = next(stream for stream in data["streams"] if stream.get("codec_type") == "video")
        has_audio = any(stream.get("codec_type") == "audio" for stream in data["streams"])
        duration = float(data["format"].get("duration", 0))
    except (ValueError, KeyError, StopIteration, TypeError):
        raise ValueError(f"Quality Control {aspect} menemukan metadata yang tidak valid") from None
    if (video.get("width"), video.get("height")) != (expected_width, expected_height):
        raise ValueError(f"Quality Control {aspect} gagal: resolusi bukan {expected_width}x{expected_height}")
    if not has_audio or duration <= 1:
        raise ValueError(f"Quality Control {aspect} gagal: audio atau durasi video tidak valid")
    return {"width": expected_width, "height": expected_height, "duration_seconds": round(duration, 2), "has_audio": True, "status": "PASS"}


def _auto_poll_task(base_url: str, api_key: str, task_id: str, item_id: str, aspect: str) -> dict:
    from modules.auto.mpt import MoneyPrinterTurboError
    started = time.monotonic()
    while not AUTO_SHUTDOWN.is_set():
        if time.monotonic() - started > 4 * 60 * 60:
            raise MoneyPrinterTurboError("Produksi MoneyPrinterTurbo melewati batas 4 jam")
        data = _mpt_task_status(base_url, api_key, task_id)
        try:
            state = int(data.get("state", 4))
            progress = max(0, min(100, int(data.get("progress", 0))))
        except (TypeError, ValueError):
            state, progress = 4, 0
        with connect_db() as db:
            db.execute("UPDATE auto_queue SET progress=?,updated_at=? WHERE id=?", (progress, now_iso(), item_id))
        if state == 1:
            return data
        if state == -1:
            raise MoneyPrinterTurboError("MoneyPrinterTurbo gagal pada tahap " + str(data.get("failed_stage") or "produksi"))
        time.sleep(3)
    raise MoneyPrinterTurboError("Worker AUTO dihentikan")


def _mpt_task_status(base_url: str, api_key: str, task_id: str) -> dict:
    from modules.auto.mpt import task_status
    return task_status(base_url, api_key, task_id)


def _finish_auto_cycle(cycle_id: str) -> None:
    settings_update = None
    with connect_db() as db:
        cycle = db.execute("SELECT * FROM auto_cycles WHERE id=?", (cycle_id,)).fetchone()
        if not cycle or cycle["status"] != "running":
            return
        rows = db.execute("SELECT status FROM auto_queue WHERE cycle_id=?", (cycle_id,)).fetchall()
        succeeded = sum(row["status"] == "complete" for row in rows)
        remaining = sum(row["status"] in {"queued", "processing"} for row in rows)
        failed = sum(row["status"] == "failed" for row in rows)
        if succeeded >= cycle["target_unique"]:
            db.execute("UPDATE auto_cycles SET status='complete',finished_at=? WHERE id=?", (now_iso(), cycle_id))
            db.execute("UPDATE auto_queue SET status='cancelled',finished_at=?,updated_at=? WHERE cycle_id=? AND status='queued'", (now_iso(), now_iso(), cycle_id))
            settings_update = {"auto_runtime_state": "complete", "auto_last_error": ""}
        elif remaining == 0:
            error = f"Target tidak tercapai: {succeeded}/{cycle['target_unique']} video unik berhasil; {failed} gagal"
            db.execute("UPDATE auto_cycles SET status='error',error=?,finished_at=? WHERE id=?", (error, now_iso(), cycle_id))
            db.execute("INSERT INTO events(kind,message,created_at) VALUES('auto_cycle_error',?,?)", (error, now_iso()))
            settings_update = {"auto_runtime_state": "error", "auto_last_error": error}
    # Persist settings after the cycle transaction commits; save_auto_settings
    # opens its own connection and nesting it here can deadlock SQLite.
    if settings_update:
        save_auto_settings(settings_update)


def _run_auto_item(item: dict) -> None:
    from modules.auto import mpt
    item_id, cycle_id = item["id"], item["cycle_id"]
    with connect_db() as db:
        cycle = db.execute("SELECT * FROM auto_cycles WHERE id=?", (cycle_id,)).fetchone()
    if not cycle or cycle["status"] != "running":
        return
    settings = read_settings()
    if cycle["engine_id"] != settings.get("auto_engine_id", "groq_qwen"):
        raise mpt.MoneyPrinterTurboError("AI ENGINE siklus berubah; job diblokir agar tidak berpindah engine diam-diam")
    root = settings["auto_mpt_root"]
    port = int(settings.get("auto_mpt_port", 8080))
    base_url, api_key = f"http://127.0.0.1:{port}", os.environ.get("MPT_API_KEY", "").strip()
    with connect_db() as db:
        db.execute("UPDATE auto_queue SET status='processing',started_at=COALESCE(NULLIF(started_at,''),?),updated_at=? WHERE id=?", (now_iso(), now_iso(), item_id))
        current = db.execute("SELECT * FROM auto_queue WHERE id=?", (item_id,)).fetchone()
    script = current["video_script"]
    terms = json.loads(current["video_terms"] or "[]")
    metadata = json.loads(current["metadata_json"] or "{}")
    if not script:
        from modules.auto.research import search as ddgs_search
        research = ddgs_search(current["topic"])
        sources = [item for item in research["text"] if item["url_validation"]["status"] == "valid"]
        if len(sources) < 2:
            raise mpt.MoneyPrinterTurboError("DDGS tidak memberikan minimal dua URL riset teks yang tervalidasi")
        from modules.auto import qwen
        script = auto_llm_call("script", lambda: qwen.generate_script(current["topic"], current["position"], sources), cycle_id=cycle_id, item_id=item_id)
        if "RISET_TIDAK_CUKUP" in script:
            raise mpt.MoneyPrinterTurboError("LLM menilai bahan riset DDGS tidak cukup")
        with connect_db() as db:
            db.execute("UPDATE auto_queue SET video_script=?,updated_at=? WHERE id=?", (script, now_iso(), item_id))
    if not terms:
        from modules.auto import qwen
        terms = auto_llm_call("footage_terms", lambda: qwen.generate_terms(current["topic"], script), cycle_id=cycle_id, item_id=item_id)
        with connect_db() as db:
            db.execute("UPDATE auto_queue SET video_terms=?,updated_at=? WHERE id=?", (json.dumps(terms, ensure_ascii=False), now_iso(), item_id))
    # Search again with the actual narration keywords so visual references are
    # relevant to this scene's content, not only to the broad topic.
    from modules.auto.research import search as ddgs_search
    research = ddgs_search(current["topic"], script=script, keywords=terms)
    research["text"] = [item for item in research["text"] if item["url_validation"]["status"] == "valid"]
    research["images"] = [item for item in research["images"] if item["url_validation"]["status"] == "valid" and item["media_url_status"] == "valid"]
    research["videos"] = [item for item in research["videos"] if item["url_validation"]["status"] == "valid"]
    if len(research["text"]) < 2:
        raise mpt.MoneyPrinterTurboError("DDGS tidak memberikan minimal dua referensi tervalidasi untuk isi naskah")
    if not metadata.get("title"):
        metadata.update(_auto_video_metadata(current["topic"], script, cycle_id=cycle_id, item_id=item_id))
    metadata["research"] = research
    with connect_db() as db:
        db.execute("UPDATE auto_queue SET metadata_json=?,updated_at=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), now_iso(), item_id))
    try:
        task_ids = json.loads(current["mpt_task_ids"] or "{}")
    except json.JSONDecodeError:
        task_ids = {}
    try:
        outputs = json.loads(current["outputs_json"] or "[]")
    except json.JSONDecodeError:
        outputs = []
    completed_formats = {entry.get("format") for entry in outputs}
    format_specs = _auto_formats(cycle["format_mode"])
    directory = ROOT / "output" / "auto" / cycle_id
    for format_name, aspect, width, height in format_specs:
        if format_name in completed_formats:
            continue
        if cycle["status"] != "running":
            return
        task_id = task_ids.get(format_name)
        if not task_id:
            task_id = mpt.submit_video(
                base_url, api_key, current["topic"], script, terms, settings["auto_voice_id"], aspect,
                music_enabled=settings["auto_music_enabled"], subtitle_enabled=settings["auto_subtitle_enabled"],
            )
            task_ids[format_name] = task_id
            with connect_db() as db:
                db.execute("UPDATE auto_queue SET mpt_task_ids=?,updated_at=? WHERE id=?", (json.dumps(task_ids), now_iso(), item_id))
        result = _auto_poll_task(base_url, api_key, task_id, item_id, format_name)
        video_list = result.get("videos") or result.get("combined_videos") or []
        if not video_list:
            raise mpt.MoneyPrinterTurboError(f"MoneyPrinterTurbo tidak menghasilkan berkas {format_name}")
        filename = f"{item_id}-{format_name}.mp4"
        target = directory / filename
        mpt.download_video(base_url, api_key, str(video_list[0]), target)
        qc = _validate_auto_video(target, aspect, width, height)
        outputs.append({"format": format_name, "path": target.relative_to(ROOT).as_posix(), "qc": qc})
        with connect_db() as db:
            db.execute("UPDATE auto_queue SET outputs_json=?,progress=?,updated_at=? WHERE id=?", (json.dumps(outputs, ensure_ascii=False), 100, now_iso(), item_id))
    with connect_db() as db:
        db.execute("UPDATE auto_queue SET status='complete',progress=100,outputs_json=?,finished_at=?,updated_at=? WHERE id=?", (json.dumps(outputs, ensure_ascii=False), now_iso(), now_iso(), item_id))
    _finish_auto_cycle(cycle_id)


def _run_auto_item_internal(item: dict) -> None:
    """YOMI scripts and plans scenes; selected TTS supplies audio; Remotion renders the final MP4."""
    from modules.auto import qwen
    from modules.remotion.scene_generator import clean_spoken_script, limit_narration_words, split_narration, time_scenes
    from modules.remotion.render_manager import render_composition
    from modules.remotion.tts import synthesize_auto_tts

    item_id, cycle_id = item["id"], item["cycle_id"]
    with connect_db() as db:
        cycle = db.execute("SELECT * FROM auto_cycles WHERE id=?", (cycle_id,)).fetchone()
    if not cycle or cycle["status"] != "running":
        return
    settings = read_settings()
    if cycle["engine_id"] != settings.get("auto_engine_id", "groq_qwen"):
        raise RuntimeError("AI ENGINE siklus berubah; job diblokir")
    with connect_db() as db:
        db.execute("UPDATE auto_queue SET status='processing',started_at=COALESCE(NULLIF(started_at,''),?),updated_at=? WHERE id=?", (now_iso(), now_iso(), item_id))
        current = db.execute("SELECT * FROM auto_queue WHERE id=?", (item_id,)).fetchone()
    script = current["video_script"]
    metadata = json.loads(current["metadata_json"] or "{}")
    test_mode = bool(metadata.get("test_mode"))
    selected_styles = settings.get("auto_visual_styles") or list(LEMO_STYLE_OPTIONS)
    selected_styles = [style for style in selected_styles if style in LEMO_STYLE_OPTIONS] or list(LEMO_STYLE_OPTIONS)
    selected_visual_style = selected_styles[0]
    if test_mode and not script:
        raise ValueError("TEST MODE memerlukan naskah uji")
    if not test_mode and not script:
        target_duration = int(settings.get("auto_video_duration_seconds", 60))
        script = auto_llm_call("script", lambda: qwen.generate_script(current["topic"], current["position"], target_duration_seconds=target_duration), cycle_id=cycle_id, item_id=item_id)
        if "PERLU_SUMBER" in script:
            raise RuntimeError("Qwen memerlukan topik yang lebih spesifik atau sumber dari pengguna")
    if not test_mode:
        target_words = max(24, round(int(settings.get("auto_video_duration_seconds", 60)) * 1.6))
        script = limit_narration_words(script, target_words)
        with connect_db() as db:
            db.execute("UPDATE auto_queue SET video_script=?,updated_at=? WHERE id=?", (script, now_iso(), item_id))
    spoken_script = clean_spoken_script(script)
    segments = split_narration(spoken_script, 4)
    if test_mode and metadata.get("test_llm"):
        plans = auto_llm_call("visual_plan", lambda: qwen.generate_visual_plan(current["topic"], segments, selected_visual_style, lemo_style_name(selected_visual_style)), cycle_id=cycle_id, item_id=item_id)
    elif test_mode:
        visual_types = ("kinetic", "chart", "illustration", "ui")
        plans = [{
            "narration": text, "heading": ["GAGASAN UTAMA", "DATA BERGERAK", "ILUSTRASI", "PENUTUP"][index % 4],
            "visual_type": visual_types[index % len(visual_types)], "visual_mode": "generated",
            "visual_prompt": "Lukisan animasi yang menggambarkan " + text[:250], "illustrative_only": True,
            "transition": "fade",
            "visual_style": selected_styles[index % len(selected_styles)],
        } for index, text in enumerate(segments)]
    else:
        style_options = {slug: lemo_style_name(slug) for slug in selected_styles}
        plans = auto_llm_call("visual_plan", lambda: qwen.generate_visual_plan(
            current["topic"], segments, "", "auto",
            style_options=style_options,
        ), cycle_id=cycle_id, item_id=item_id)
    ffmpeg_bin, ffprobe_bin = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        raise RuntimeError("FFmpeg dan FFprobe diperlukan untuk audio serta QC Remotion")
    directory = ROOT / "output" / "auto" / cycle_id
    audio_path = directory / "audio" / f"{item_id}.wav"
    tts_settings = {**settings, "auto_tts_provider": "edge"} if test_mode else settings
    audio = synthesize_auto_tts(
        spoken_script, tts_settings, audio_path,
        ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin,
    )
    timings = time_scenes(segments, audio["words"], audio["duration_seconds"])
    render_scenes = []
    for index, (plan, timing) in enumerate(zip(plans, timings)):
        # AUTO can produce a complete illustrated film without an external search service.
        # User-provided footage remains a MANUAL workflow feature.
        media_paths = []
        media_durations = []
        visual_mode = "generated"
        render_scenes.append({
            **plan, **timing, "visual_mode": visual_mode,
            "illustrative_only": True,
            "media_paths": [str(path) for path in media_paths], "media_durations": media_durations,
        })
    metadata["scene_visuals"] = [{"heading": scene["heading"], "visual_type": scene["visual_type"], "visual_mode": scene["visual_mode"], "visual_style": scene.get("visual_style", selected_visual_style), "illustrative_only": scene["illustrative_only"]} for scene in render_scenes]
    with connect_db() as db:
        db.execute("UPDATE auto_queue SET metadata_json=?,updated_at=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), now_iso(), item_id))
    outputs = json.loads(current["outputs_json"] or "[]")
    completed_formats = {entry.get("format") for entry in outputs}
    format_specs = _auto_formats(cycle["format_mode"])
    for format_index, (format_name, aspect, width, height) in enumerate(format_specs):
        if format_name in completed_formats:
            continue
        if cycle["status"] != "running":
            return
        manifest = {
            "title": metadata.get("title") or current["topic"], "audio_path": str(audio["audio_path"]),
            "duration_seconds": audio["duration_seconds"], "fps": 30,
            "width": width, "height": height, "format": "mp4", "scenes": render_scenes,
            "visual_style": selected_visual_style,
            "target_duration_seconds": 12 if test_mode else int(settings.get("auto_video_duration_seconds", 60)),
        }
        def update_progress(value):
            overall = 25 + round((format_index + max(0.0, min(1.0, float(value))) ) * 70 / len(format_specs))
            with connect_db() as db:
                db.execute("UPDATE auto_queue SET progress=?,updated_at=? WHERE id=?", (min(95, overall), now_iso(), item_id))
        target = directory / f"{item_id}-{format_name}.mp4"
        result = render_composition(manifest, target, progress_callback=update_progress)
        qc = _validate_auto_video(target, aspect, width, height)
        outputs.append({"format": format_name, "path": target.relative_to(ROOT).as_posix(), "qc": qc, "renderer": "Remotion"})
        with connect_db() as db:
            db.execute("UPDATE auto_queue SET outputs_json=?,progress=?,updated_at=? WHERE id=?", (json.dumps(outputs, ensure_ascii=False), 98, now_iso(), item_id))
    with connect_db() as db:
        db.execute("UPDATE auto_queue SET status='complete',progress=100,outputs_json=?,finished_at=?,updated_at=? WHERE id=?", (json.dumps(outputs, ensure_ascii=False), now_iso(), now_iso(), item_id))
    _finish_auto_cycle(cycle_id)

def _handle_auto_item_failure(item: dict, exc: Exception) -> None:
    message = str(exc)[:500]
    with connect_db() as db:
        db.execute("UPDATE auto_queue SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?", (message, now_iso(), now_iso(), item["id"]))
    from modules.auto.qwen import GroqError
    if isinstance(exc, GroqError):
        with connect_db() as db:
            row = db.execute("SELECT progress_detail FROM auto_queue WHERE id=?", (item["id"],)).fetchone()
            if row:
                detail = [entry for entry in row["progress_detail"].splitlines() if entry]
                detail.append("YOMI: Siklus AUTO dihentikan")
                db.execute("UPDATE auto_queue SET progress_detail=? WHERE id=?", ("\n".join(detail[-20:]), item["id"]))
        with connect_db() as db:
            db.execute("UPDATE auto_queue SET status='cancelled',error=?,finished_at=?,updated_at=? WHERE cycle_id=? AND status='queued'", ("Dibatalkan: " + message[:300], now_iso(), now_iso(), item["cycle_id"]))
            db.execute("UPDATE auto_cycles SET status='error',error=?,finished_at=? WHERE id=? AND status='running'", (message, now_iso(), item["cycle_id"]))
            db.execute("INSERT INTO events(kind,message,created_at) VALUES('auto_cycle_error',?,?)", ("Siklus dihentikan setelah kegagalan Groq: " + message, now_iso()))
        save_auto_settings({"auto_runtime_state": "error", "auto_last_error": message})
    else:
        _finish_auto_cycle(item["cycle_id"])


def _auto_worker_loop() -> None:
    while not AUTO_SHUTDOWN.is_set():
        with connect_db() as db:
            active = db.execute("SELECT id FROM auto_cycles WHERE status='running' ORDER BY created_at LIMIT 1").fetchone()
            item = db.execute("SELECT * FROM auto_queue WHERE cycle_id=? AND status IN ('queued','processing') ORDER BY position LIMIT 1", (active["id"],)).fetchone() if active else None
        if not active:
            time.sleep(1)
            continue
        if not item:
            _finish_auto_cycle(active["id"])
            time.sleep(1)
            continue
        try:
            _run_auto_item_internal(dict(item))
        except Exception as exc:
            _handle_auto_item_failure(dict(item), exc)


def start_auto_worker() -> None:
    global AUTO_WORKER_THREAD
    if AUTO_WORKER_THREAD and AUTO_WORKER_THREAD.is_alive():
        return
    AUTO_SHUTDOWN.clear()
    AUTO_WORKER_THREAD = threading.Thread(target=_auto_worker_loop, name="ovs-auto-worker", daemon=True)
    AUTO_WORKER_THREAD.start()


def _auto_schedule_loop() -> None:
    day_names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    while not AUTO_SHUTDOWN.wait(20):
        settings = read_settings()
        if (not settings.get("auto_schedule_enabled") or not settings.get("auto_topic")
                or not settings.get("auto_llm_last_test_success")):
            continue
        try:
            from zoneinfo import ZoneInfo
            local = datetime.now(ZoneInfo("Asia/Jakarta"))
        except Exception:
            continue
        if local.strftime("%H:%M") < settings["auto_production_time"] or day_names[local.weekday()] not in settings["auto_schedule_days"]:
            continue
        run_date = local.date().isoformat()
        if settings.get("auto_scheduled_date") == run_date:
            continue
        with connect_db() as db:
            existing_cycle = db.execute("SELECT id FROM auto_cycles WHERE production_date=? AND is_additional=0 LIMIT 1", (run_date,)).fetchone()
            active_cycle = db.execute("SELECT id FROM auto_cycles WHERE status='running' LIMIT 1").fetchone()
        if existing_cycle or active_cycle:
            save_auto_settings({"auto_scheduled_date": run_date})
            continue
        if not AUTO_APPLY_LOCK.acquire(blocking=False):
            continue
        try:
            save_auto_settings({"auto_scheduled_date": run_date})
            _prepare_auto_tts()
            _new_auto_cycle(settings["auto_topic"], "", False)
        except Exception as exc:
            store_auto_error(str(exc))
        finally:
            AUTO_APPLY_LOCK.release()


def start_auto_scheduler() -> None:
    global AUTO_SCHEDULER_THREAD
    if AUTO_SCHEDULER_THREAD and AUTO_SCHEDULER_THREAD.is_alive():
        return
    AUTO_SCHEDULER_THREAD = threading.Thread(target=_auto_schedule_loop, name="ovs-auto-scheduler", daemon=True)
    AUTO_SCHEDULER_THREAD.start()


def recover_auto_runtime() -> None:
    """Resume persisted AUTO queue work after the dashboard process restarts."""
    with connect_db() as db:
        active = db.execute("SELECT 1 FROM auto_cycles WHERE status='running' LIMIT 1").fetchone()
    if active:
        start_auto_worker()
    start_auto_scheduler()


def auto_dashboard() -> dict:
    settings = read_settings()
    jakarta_today = datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Jakarta")).date().isoformat()
    with connect_db() as db:
        cycles = [dict(row) for row in db.execute("SELECT * FROM auto_cycles ORDER BY created_at DESC LIMIT 30").fetchall()]
        cycle = next((row for row in cycles if row["status"] == "running"), cycles[0] if cycles else None)
        queue_rows = [dict(row) for row in db.execute("SELECT * FROM auto_queue WHERE cycle_id=? ORDER BY position", (cycle["id"],)).fetchall()] if cycle else []
        llm_aggregate = db.execute("SELECT COUNT(*) AS total,SUM(status='success') AS successful,SUM(status='failed') AS failed,SUM(prompt_tokens) AS prompt_tokens,SUM(completion_tokens) AS completion_tokens FROM auto_llm_usage").fetchone()
        daily_rows = db.execute(
            "SELECT q.status,q.outputs_json FROM auto_queue q JOIN auto_cycles c ON c.id=q.cycle_id WHERE c.production_date=? AND c.test_mode=0",
            (jakarta_today,),
        ).fetchall()
    daily_done = sum(row["status"] == "complete" for row in daily_rows)
    daily_failed = sum(row["status"] == "failed" for row in daily_rows)
    daily_files = sum(len(json.loads(row["outputs_json"] or "[]")) for row in daily_rows)
    unique_done = sum(row["status"] == "complete" for row in queue_rows) if cycle else 0
    unique_failed = sum(row["status"] == "failed" for row in queue_rows) if cycle else 0
    final_files = 0
    safe_queue = []
    for row in queue_rows:
        try:
            outputs = json.loads(row["outputs_json"] or "[]")
        except json.JSONDecodeError:
            outputs = []
        final_files += len(outputs)
        safe_queue.append({
            "id": row["id"], "position": row["position"], "topic": row["topic"],
            "status": row["status"], "progress": row["progress"], "error": row["error"],
            "progress_detail": row["progress_detail"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "outputs": [{**entry, "url": f"/api/auto/videos/{row['id']}/{entry['format']}"} for entry in outputs],
        })
    safe_cycles = []
    for entry in cycles:
        with connect_db() as db:
            stats = db.execute("SELECT status,outputs_json FROM auto_queue WHERE cycle_id=?", (entry["id"],)).fetchall()
        safe_cycles.append({
            "id": entry["id"], "date": entry["production_date"], "topic": entry["topic"],
            "engine_id": entry.get("engine_id", "groq_qwen"),
            "test_mode": bool(entry.get("test_mode", 0)),
            "target_unique": entry["target_unique"], "format_mode": entry["format_mode"],
            "status": entry["status"], "additional": bool(entry["is_additional"]),
            "successful_unique": sum(row["status"] == "complete" for row in stats),
            "failed_unique": sum(row["status"] == "failed" for row in stats),
            "final_files": sum(len(json.loads(row["outputs_json"] or "[]")) for row in stats),
            "error": entry["error"], "started_at": entry["started_at"], "finished_at": entry["finished_at"],
        })
    cycle = ({**cycle, "successful_unique": unique_done, "failed_unique": unique_failed,
              "final_files": final_files, "queue": safe_queue} if cycle else None)
    engine_tested = bool(
        settings.get("auto_engine_last_test_success")
        and settings.get("auto_engine_last_test_id") == settings.get("auto_engine_id", "groq_qwen")
    )
    from modules.auto.qwen import configuration as qwen_configuration
    qwen_config = qwen_configuration()
    provider = "groq"
    model = qwen_config["model"]
    llm_configured = bool(settings.get("auto_llm_last_test_success") and settings.get("auto_llm_last_test_provider") == provider)
    llm_registry = {
        "available": qwen_config["configured"], "providers": [],
        "active_provider": provider, "active_model": model,
        "configured": llm_configured,
        "last_test_at": settings.get("auto_llm_last_test_at", ""),
    }
    qwen = {"installed": True, "ready": llm_configured, "status": "tested" if llm_configured else ("configured" if qwen_config["configured"] else "blocked"), "provider": "Groq", "model": model, "reason": "" if qwen_config["configured"] else "Atur minimal satu API Key Groq"}
    return {
        "status": automation_status(), "cycle": cycle, "history": safe_cycles,
        "settings": {
            key: settings[key] for key in DEFAULT_SETTINGS
            if key.startswith("auto_") and key not in {"auto_llm_provider", "auto_llm_model", "auto_llm_base_url", "auto_mpt_root", "auto_mpt_port"}
        },
        "secrets_configured": {**auto_secrets_configured(), "groq_api_key": qwen_config["configured"]},
        "llm_registry": llm_registry, "qwen": qwen,
        "llm_usage": {"requests": llm_aggregate["total"] or 0, "successful": llm_aggregate["successful"] or 0, "failed": llm_aggregate["failed"] or 0, "prompt_tokens": llm_aggregate["prompt_tokens"] or 0, "completion_tokens": llm_aggregate["completion_tokens"] or 0, "cost_estimate": "unavailable_without_region_and_model_pricing"},
        "daily": {"date": jakarta_today, "target": settings["auto_daily_target"], "successful": daily_done, "failed": daily_failed, "final_files": daily_files, "target_reached": daily_done >= settings["auto_daily_target"]},
        "services": {
            "moneyprinterturbo": "not_used",
            "lemo": f"{len(LEMO_STYLE_OPTIONS)}-styles",
            "cartesia_tts": bool(os.environ.get("CARTESIA_API_KEY") and settings.get("cartesia_voice_id")),
            "tts_provider": settings.get("auto_tts_provider", "edge"),
            "mpt_version": settings.get("auto_mpt_version", "versi belum diperiksa"),
            "worker": "running" if AUTO_WORKER_THREAD and AUTO_WORKER_THREAD.is_alive() else "stopped",
            "queue": "active" if cycle and cycle["status"] == "running" else "empty",
            "ddgs": "available" if __import__("importlib.util", fromlist=["find_spec"]).find_spec("ddgs") else "not_installed",
            "llm": "tested" if llm_configured else "not_tested",
            "ai_engine": "tested" if engine_tested else ("blocked" if not qwen_config["configured"] else "not_tested"),
            "rendering": "active" if any(row["status"] == "processing" for row in queue_rows) else "idle",
            "telegram_approval": "not_implemented",
        },
        "last_error": settings.get("auto_last_error", ""),
    }


def automation_status() -> dict:
    with connect_db() as db:
        active = db.execute("SELECT * FROM auto_cycles WHERE status='running' ORDER BY created_at DESC LIMIT 1").fetchone()
        latest = db.execute("SELECT * FROM auto_cycles ORDER BY created_at DESC LIMIT 1").fetchone()
    cycle = dict(active or latest) if (active or latest) else None
    runtime_state = read_settings().get("auto_runtime_state", "stopped")
    if active:
        state = "running"
    elif runtime_state == "error":
        state = "error"
    elif latest and latest["status"] == "complete":
        state = "complete"
    elif latest and latest["status"] == "error":
        state = "error"
    else:
        state = "stopped"
    return {
        "enabled": state == "running", "state": state,
        "label": {"running": "BERJALAN", "complete": "SELESAI", "error": "ERROR", "stopped": "BERHENTI"}[state],
        "cycle": cycle,
    }


def overview() -> dict:
    with connect_db() as db:
        rows = db.execute("SELECT status, COUNT(*) AS count FROM projects GROUP BY status").fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        recent = db.execute(
            "SELECT p.id, p.title, p.status, p.updated_at, "
            "(SELECT j.stage FROM jobs j WHERE j.project_id=p.id AND j.status='Processing' "
            "ORDER BY j.started_at DESC LIMIT 1) AS active_stage "
            "FROM projects p ORDER BY p.updated_at DESC LIMIT 8"
        ).fetchall()
        completed_videos = db.execute(
            "SELECT p.id AS project_id, p.title, p.topic, j.id AS job_id, j.status, "
            "j.artifact_path, j.finished_at, j.qc_json FROM projects p JOIN jobs j ON j.id=("
            "SELECT j2.id FROM jobs j2 WHERE j2.project_id=p.id "
            "AND j2.status IN ('QC Passed','QC Warning') AND j2.artifact_path<>'' "
            "ORDER BY j2.finished_at DESC LIMIT 1) "
            "ORDER BY j.finished_at DESC LIMIT 12"
        ).fetchall()
        events = db.execute(
            "SELECT kind, message, created_at FROM events ORDER BY id DESC LIMIT 8"
        ).fetchall()
        mode = db.execute("SELECT value FROM settings WHERE key='production_mode'").fetchone()
    config = read_settings()
    caps = capabilities()
    auto = automation_status()
    remotion_demo_path = (ROOT / "output" / "remotion-test" / "auto-politik-terkini.mp4").resolve()
    remotion_demo_available = remotion_demo_path.is_relative_to((ROOT / "output").resolve()) and remotion_demo_path.is_file()
    return {
        "engine": "ready" if caps["render"]["ready"] and caps["cartesia"]["configured"] else "blocked",
        "backend": "online",
        "production_mode": "manual",
        "auto_enabled": auto["enabled"],
        "auto_state": auto["state"],
        "auto_label": auto["label"],
        "daily_target": config["daily_target"],
        "counts": {
            "finished": counts.get("QC Passed", 0) + counts.get("QC Warning", 0),
            "processing": counts.get("Processing", 0) + counts.get("Producing", 0),
            "waiting_approval": counts.get("Waiting Approval", 0),
            "published": counts.get("Published", 0),
        },
        "pipeline": [
            {"name": name, "status": "blocked" if unavailable else "not_started"}
            for name, unavailable in [
                ("MANUAL INPUT", False), ("CARTESIA", not caps["cartesia"]["configured"]),
                ("AUDIO PROCESSING", not caps["ffmpeg"]["available"]),
                ("SCENE + AUDIO SYNC", False), ("REMOTION RENDER", not caps["remotion"]["available"]),
                ("QUALITY CONTROL", not (caps["ffprobe"]["available"] and caps["ffmpeg"]["available"])),
            ]
        ],
        "capabilities": caps,
        "recent_projects": [dict(row) for row in recent],
        "completed_videos": [
            {**dict(row), "qc": json.loads(row["qc_json"] or "{}"), "qc_json": None}
            for row in completed_videos
        ],
        "remotion_demo_available": remotion_demo_available,
        "recent_activity": [dict(row) for row in events],
        "generated_at": now_iso(),
    }


def clear_render_cache() -> dict:
    """Remove only disposable Hyperframes/FFmpeg working folders under output."""
    with connect_db() as db:
        active_job = db.execute("SELECT 1 FROM jobs WHERE status='Processing' LIMIT 1").fetchone()
        active_cycle = db.execute("SELECT 1 FROM auto_cycles WHERE status='running' LIMIT 1").fetchone()
    if active_job or active_cycle:
        raise RuntimeError("Cache tidak dapat dibersihkan saat produksi video sedang berjalan")
    output_root = (ROOT / "output").resolve()
    if not output_root.is_dir():
        return {"removed_items": 0, "freed_bytes": 0}
    candidates = set()
    for pattern in ("work-*", ".hf-transaction-*", ".hyperframes"):
        candidates.update(output_root.rglob(pattern))
    removed_items = freed_bytes = 0
    for candidate in sorted(candidates, key=lambda path: len(path.parts)):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(output_root):
            continue
        for root, _, files in os.walk(resolved, followlinks=False):
            for filename in files:
                try:
                    freed_bytes += (Path(root) / filename).lstat().st_size
                except OSError:
                    pass
        shutil.rmtree(resolved)
        removed_items += 1
    return {"removed_items": removed_items, "freed_bytes": freed_bytes}


def create_composition_artifact(project_id: str, title: str, scenes: list[dict], audio: dict, visual_style: str = "") -> dict:
    """Backward-compatible composition endpoint, now producing a Remotion manifest."""
    return create_project_composition(project_id, audio, visual_style)

def create_audio_asset(project_id: str, speed: float = 1.08) -> dict:
    """Create the project's voice asset with Cartesia after manual production starts."""
    from modules.cartesia.client import CartesiaError, synthesize

    with connect_db() as db:
        project = db.execute("SELECT id,script,script_approved FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        raise LookupError("Project not found")
    if not project["script_approved"]:
        raise PermissionError("Setujui naskah sebelum produksi audio dimulai")
    if not project["script"].strip():
        raise ValueError("Tambahkan naskah sebelum membuat audio")
    voice_id = read_settings()["cartesia_voice_id"]
    if not os.environ.get("CARTESIA_API_KEY") or not voice_id:
        raise PermissionError("Cartesia API key dan Voice ID belum dikonfigurasi")
    result = synthesize(project["script"], voice_id, read_settings()["language"], speed)
    output_root = ROOT / "output"
    output_root.mkdir(parents=True, exist_ok=True)
    resolved_output = output_root.resolve()
    output_dir = output_root / project_id
    if not resolved_output.is_relative_to(ROOT.resolve()) or not output_dir.resolve().is_relative_to(resolved_output):
        raise ValueError("Output path must stay inside project output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_id, timestamp = str(uuid.uuid4()), now_iso()
    file_path = output_dir / f"cartesia-{asset_id}.wav"
    with file_path.open("xb") as audio_file:
        audio_file.write(result["wav"])
    relative_path = file_path.relative_to(ROOT).as_posix()
    alignment = result["words"]
    with connect_db() as db:
        db.execute(
            "INSERT INTO audio_assets(id,project_id,relative_path,duration_seconds,sample_rate,alignment_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (asset_id, project_id, relative_path, result["duration_seconds"], result["sample_rate"], json.dumps(alignment, ensure_ascii=False), timestamp),
        )
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
        db.execute(
            "INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)",
            (project_id, "audio_generated", "Cartesia menghasilkan audio dengan " + (str(len(alignment)) + " timestamp kata" if alignment else "tanpa timestamp kata"), timestamp),
        )
    return {"id": asset_id, "duration_seconds": result["duration_seconds"],
            "alignment_available": result["alignment_available"], "word_count": len(alignment),
            "file_url": f"/api/projects/{project_id}/audio/file/{asset_id}"}


MEDIA_LIMIT_BYTES = 100 * 1024 * 1024
MEDIA_FORMATS = {
    ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"), ".webp": ("image", "image/webp"), ".gif": ("image", "image/gif"),
    ".mp4": ("video", "video/mp4"), ".mov": ("video", "video/quicktime"), ".webm": ("video", "video/webm"),
}


def identify_media(filename: str, declared_type: str, data: bytes) -> tuple[str, str, str]:
    suffix = Path(filename).suffix.lower()
    detected = MEDIA_FORMATS.get(suffix)
    if not detected:
        raise ValueError("Format media harus JPG, PNG, WEBP, GIF, MP4, MOV, atau WEBM")
    media_type, canonical_mime = detected
    valid = (
        suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff")
        or suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")
        or suffix == ".webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        or suffix == ".gif" and data.startswith((b"GIF87a", b"GIF89a"))
        or suffix in {".mp4", ".mov"} and len(data) >= 12 and data[4:8] == b"ftyp"
        or suffix == ".webm" and data.startswith(b"\x1a\x45\xdf\xa3")
    )
    if not valid:
        raise ValueError("Isi file tidak cocok dengan format media yang dipilih")
    if declared_type and declared_type not in {canonical_mime, "application/octet-stream"}:
        raise ValueError("MIME type tidak cocok dengan format file")
    return media_type, canonical_mime, suffix


def store_media(project_id: str, scene_id: str, filename: str, mime_type: str, data: bytes,
                duration_seconds: float = 0) -> dict:
    if not data or len(data) > MEDIA_LIMIT_BYTES:
        raise ValueError("Ukuran media harus 1 byte sampai 100 MB")
    media_type, mime_type, suffix = identify_media(filename, mime_type, data)
    if not 0 <= duration_seconds <= 3600:
        raise ValueError("Durasi media harus antara 0 sampai 3600 detik")
    with connect_db() as db:
        scene = db.execute("SELECT id FROM scenes WHERE id=? AND project_id=?", (scene_id, project_id)).fetchone()
        if not scene:
            raise LookupError("Scene proyek tidak ditemukan")
        position = db.execute("SELECT COALESCE(MAX(position),-1)+1 AS next FROM media_assets WHERE scene_id=?", (scene_id,)).fetchone()["next"]
    asset_id, timestamp = str(uuid.uuid4()), now_iso()
    output_root = ROOT / "output"
    project_dir = output_root / project_id
    media_dir = project_dir / "media"
    if not media_dir.resolve().is_relative_to(output_root.resolve()):
        raise ValueError("Lokasi media tidak valid")
    media_dir.mkdir(parents=True, exist_ok=True)
    media_path = media_dir / f"{asset_id}{suffix}"
    with media_path.open("xb") as output_file:
        output_file.write(data)
    relative_path = media_path.relative_to(ROOT).as_posix()
    if media_type == "video" and duration_seconds == 0:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            try:
                probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)], capture_output=True, text=True, timeout=15, check=True)
                duration_seconds = max(0.0, min(3600.0, float(probe.stdout.strip())))
            except (OSError, ValueError, subprocess.SubprocessError):
                duration_seconds = 0
    with connect_db() as db:
        db.execute("INSERT INTO media_assets(id,project_id,scene_id,filename,media_type,mime_type,relative_path,size_bytes,position,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (asset_id, project_id, scene_id, Path(filename).name[:255], media_type, mime_type, relative_path, len(data), position, duration_seconds, timestamp))
        db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (timestamp, project_id))
    return {"id": asset_id, "project_id": project_id, "scene_id": scene_id, "filename": Path(filename).name[:255],
            "media_type": media_type, "mime_type": mime_type, "size_bytes": len(data), "position": position,
            "duration_seconds": duration_seconds, "file_url": f"/api/projects/{project_id}/media/{asset_id}"}


def project_workspace(project_id: str) -> dict | None:
    with connect_db() as db:
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return None
        scenes = db.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY position", (project_id,)).fetchall()
        media = db.execute("SELECT id,project_id,scene_id,filename,media_type,mime_type,size_bytes,position,duration_seconds,created_at FROM media_assets WHERE project_id=? ORDER BY scene_id,position", (project_id,)).fetchall()
    media_by_scene = {}
    for item in media:
        row = dict(item)
        row["file_url"] = f"/api/projects/{project_id}/media/{row['id']}"
        media_by_scene.setdefault(row["scene_id"], []).append(row)
    result_scenes = []
    for scene in scenes:
        row = dict(scene)
        row["media"] = media_by_scene.get(row["id"], [])
        result_scenes.append(row)
    return {"project": dict(project), "scenes": result_scenes}


def create_project_composition(project_id: str, audio: dict, visual_style: str = "") -> dict:
    """Write a Remotion manifest from the approved narration and optional scene media."""
    from modules.remotion.scene_generator import infer_visual_type, time_scenes
    from modules.auto.qwen import generate_visual_plan

    config = read_settings()
    if visual_style and visual_style not in SUPPORTED_VISUAL_STYLES:
        raise ValueError("Gaya visual tidak tersedia")
    workspace = project_workspace(project_id)
    if not workspace or not workspace["scenes"]:
        raise ValueError("Tambahkan scene sebelum produksi")
    visual_style = visual_style or workspace["project"].get("visual_style", "watercolor")
    output_root = ROOT / "output"
    project_output = output_root / project_id
    audio_path = (ROOT / audio["relative_path"]).resolve()
    if not audio_path.is_relative_to(output_root.resolve()) or not audio_path.is_file():
        raise ValueError("Audio produksi tidak tersedia")
    source_scenes = workspace["scenes"]
    segments = [str(scene["narration"]).strip() for scene in source_scenes]
    need_plan = any(not (scene.get("visual_description") or scene.get("animation") or scene.get("text_overlay")) for scene in source_scenes)
    plans = generate_visual_plan(workspace["project"]["topic"], segments, canonical_visual_style(visual_style), lemo_style_name(visual_style)) if need_plan else [{} for _ in source_scenes]
    try:
        alignment = json.loads(audio.get("alignment_json", "[]"))
    except (TypeError, json.JSONDecodeError):
        alignment = []
    timings = time_scenes(segments, alignment, float(audio["duration_seconds"]))
    render_scenes = []
    for index, (scene, plan, timing) in enumerate(zip(source_scenes, plans, timings)):
        media = scene.get("media") or []
        media_paths = []
        media_durations = []
        for item in media:
            row = None
            with connect_db() as db:
                row = db.execute("SELECT relative_path FROM media_assets WHERE id=? AND project_id=?", (item["id"], project_id)).fetchone()
            if not row:
                continue
            source = (ROOT / row["relative_path"]).resolve()
            if not source.is_relative_to(project_output.resolve()) or not source.is_file():
                continue
            media_paths.append(str(source))
            media_durations.append(float(item.get("duration_seconds") or 0))
        requested_mode = plan.get("visual_mode", "generated")
        visual_mode = requested_mode if media_paths else "generated"
        if media_paths and requested_mode == "generated":
            visual_mode = "hybrid"
        visual_type = scene.get("animation") if scene.get("animation") in {"kinetic", "explainer", "infographic", "chart", "map", "illustration", "ui", "media", "hybrid"} else infer_visual_type(scene.get("visual_description", ""), scene["narration"])
        render_scenes.append({
            "narration": segments[index], "heading": str(scene.get("text_overlay") or plan.get("heading") or f"Scene {index + 1}")[:100],
            "visual_type": visual_type, "visual_mode": visual_mode,
            "visual_prompt": str(scene.get("visual_description") or plan.get("visual_prompt") or "Animasi ilustratif sesuai narasi")[:1000],
            "illustrative_only": bool(plan.get("illustrative_only", not bool(media_paths))),
            "transition": "fade" if scene.get("transition") == "fade" else "cut",
            **timing, "media_paths": media_paths, "media_durations": media_durations,
        })
        with connect_db() as db:
            db.execute("UPDATE scenes SET start_seconds=?,end_seconds=?,updated_at=? WHERE id=?", (timing["start_seconds"], timing["end_seconds"], now_iso(), scene["id"]))
    composition_id = str(uuid.uuid4())
    composition_dir = project_output / f"composition-{composition_id}"
    if not composition_dir.resolve().is_relative_to(output_root.resolve()):
        raise ValueError("Composition path must stay inside project output directory")
    composition_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "title": workspace["project"]["title"], "audio_path": str(audio_path),
        "duration_seconds": float(audio["duration_seconds"]), "fps": 30,
        "width": int(config.get("render_width", 1080)), "height": int(config.get("render_height", 1920)),
        "format": config.get("render_format", "mp4"), "scenes": render_scenes,
        "visual_style": visual_style or config.get("visual_style", "lemo-watercolor"),
    }
    manifest_path = composition_dir / "composition.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return {"id": composition_id, "relative_path": manifest_path.relative_to(ROOT).as_posix(), "duration_seconds": audio["duration_seconds"]}

def process_audio(input_path: Path, output_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg tidak tersedia untuk memproses audio")
    result = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(input_path), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(output_path)], capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size < 1000:
        raise RuntimeError("FFmpeg gagal memproses audio Cartesia")


def update_manual_job_stage(job_id: str, stage: str) -> None:
    with connect_db() as db:
        db.execute("UPDATE jobs SET stage=? WHERE id=? AND status='Processing'", (stage, job_id))


def start_manual_production(project_id: str, speed: float, visual_style: str = "") -> dict:
    if not 0.5 <= speed <= 2.0:
        raise ValueError("Kecepatan suara harus antara 0,5 dan 2,0")
    if visual_style and visual_style not in SUPPORTED_VISUAL_STYLES:
        raise ValueError("Gaya visual tidak tersedia")
    workspace = project_workspace(project_id)
    if not workspace:
        raise LookupError("Project not found")
    project, scenes = workspace["project"], workspace["scenes"]
    visual_style = visual_style or project.get("visual_style", "remotion")
    if visual_style not in SUPPORTED_VISUAL_STYLES:
        raise ValueError("Gaya visual tidak tersedia")
    if not 2 <= len(scenes) <= 5:
        raise ValueError("Produksi memerlukan 2 sampai 5 scene")
    if not project["topic"].strip() or not project["script"].strip():
        raise ValueError("Isi topik dan naskah lengkap terlebih dahulu")
    if len(project["script"]) > 20000:
        raise ValueError("Naskah melebihi batas Cartesia 20.000 karakter")
    if not scenes:
        raise ValueError("Tambahkan setidaknya satu scene")
    for index, scene in enumerate(scenes, 1):
        if not scene["narration"].strip():
            raise ValueError(f"Isi narasi untuk scene {index}")
    caps = capabilities()
    if not caps["cartesia"]["configured"]:
        raise PermissionError("Cartesia API key dan Voice ID belum dikonfigurasi")
    if not caps["render"]["ready"]:
        raise RuntimeError("Toolchain render belum siap: " + "; ".join(caps["render"]["blockers"]))
    with connect_db() as db:
        active_job = db.execute("SELECT id FROM jobs WHERE project_id=? AND status='Processing' LIMIT 1", (project_id,)).fetchone()
    if active_job:
        raise ValueError("Produksi video untuk proyek ini sedang berjalan")
    timestamp = now_iso()
    job_id = str(uuid.uuid4())
    with connect_db() as db:
        db.execute("UPDATE projects SET script_approved=1,status='Producing',updated_at=? WHERE id=?", (timestamp, project_id))
        db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "manual_production_started", "Pengguna memulai pipeline produksi manual", timestamp))
        db.execute("INSERT INTO jobs(id,project_id,stage,status,started_at) VALUES(?,?,?,'Processing',?)", (job_id, project_id, "cartesia_tts", timestamp))
    try:
        audio_result = create_audio_asset(project_id, speed)
        update_manual_job_stage(job_id, "audio_ffmpeg")
        with connect_db() as db:
            audio = db.execute("SELECT * FROM audio_assets WHERE id=?", (audio_result["id"],)).fetchone()
        audio = dict(audio)
        input_path = (ROOT / audio["relative_path"]).resolve()
        output_path = input_path.with_name(input_path.stem + "-processed.wav")
        process_audio(input_path, output_path)
        relative_audio = output_path.relative_to(ROOT).as_posix()
        audio["relative_path"] = relative_audio
        with connect_db() as db:
            db.execute("UPDATE audio_assets SET relative_path=?,sample_rate=48000 WHERE id=?", (relative_audio, audio["id"]))
        update_manual_job_stage(job_id, "scene_media_sync")
        composition = create_project_composition(project_id, audio, visual_style)
        with connect_db() as db:
            db.execute("INSERT INTO compositions(id,project_id,relative_path,created_at) VALUES(?,?,?,?)", (composition["id"], project_id, composition["relative_path"], now_iso()))
        render_output = ROOT / "output" / project_id / f"final-{job_id}.{read_settings()['render_format']}"
        update_manual_job_stage(job_id, "remotion_render")
        result = modules_render(
            composition["relative_path"], render_output, audio["duration_seconds"],
            stage_callback=lambda stage: update_manual_job_stage(job_id, stage),
        )
        finished = now_iso()
        job_status = {"PASS": "QC Passed", "WARNING": "QC Warning", "FAILED": "QC Failed"}.get(result["status"], "Failed")
        artifact = render_output.relative_to(ROOT).as_posix() if render_output.is_file() else ""
        with connect_db() as db:
            db.execute("UPDATE jobs SET status=?,qc_json=?,error=?,artifact_path=?,finished_at=? WHERE id=?", (job_status, json.dumps(result, ensure_ascii=False), result.get("error", ""), artifact, finished, job_id))
            db.execute("UPDATE projects SET status=?,updated_at=? WHERE id=?", (job_status, finished, project_id))
            db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "manual_production_finished", "Render dan QC selesai dengan status " + result["status"], finished))
        return {"job_id": job_id, "status": result["status"], "project_status": job_status, "qc": result,
                "artifact_path": artifact, "video_url": f"/api/projects/{project_id}/video/{job_id}" if artifact else "",
                "audio_url": audio_result["file_url"], "composition": composition["relative_path"]}
    except Exception as exc:
        with connect_db() as db:
            db.execute("UPDATE projects SET status='Failed',updated_at=? WHERE id=?", (now_iso(), project_id))
            db.execute("UPDATE jobs SET status='Failed',error=?,finished_at=? WHERE id=? AND status='Processing'", (str(exc)[:400], now_iso(), job_id))
            db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "manual_production_failed", str(exc)[:400], now_iso()))
        raise


def modules_render(composition_relative: str, output_path: Path, duration: float, stage_callback=None) -> dict:
    from modules.remotion.render_manager import render_composition
    composition_path = (ROOT / composition_relative).resolve()
    output_root = ROOT / "output"
    if not composition_path.is_relative_to(output_root.resolve()) or not composition_path.is_file():
        raise ValueError("Composition file missing or outside output directory")
    try:
        manifest = json.loads(composition_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("Remotion composition manifest tidak valid") from None
    if abs(float(manifest.get("duration_seconds", 0)) - float(duration)) > 0.05:
        raise ValueError("Durasi Remotion composition tidak cocok dengan audio")
    return render_composition(manifest, output_path, stage_callback=stage_callback)


class Handler(BaseHTTPRequestHandler):
    server_version = "OlyphantsStudio/0.1"

    def log_message(self, fmt, *args):
        # Never echo request bodies, query strings, or credentials into logs.
        super().log_message(fmt.split("?")[0], *args)

    def send_json(self, code: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size > 1_000_000:
            raise ValueError("Request body exceeds 1 MB")
        parsed = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    def do_HEAD(self):
        path = urlparse(self.path).path
        relative = unquote(path.lstrip("/")) or "index.html"
        candidate = (FRONTEND / relative).resolve()
        if not candidate.is_relative_to(FRONTEND.resolve()) or not candidate.is_file():
            candidate = FRONTEND / "index.html"
        try:
            body = candidate.read_bytes()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(candidate.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/remotion/demo":
            video_path = (ROOT / "output" / "remotion-test" / "auto-politik-terkini.mp4").resolve()
            if not video_path.is_relative_to((ROOT / "output").resolve()) or not video_path.is_file():
                return self.send_json(404, {"error": "Video uji Remotion tidak ditemukan"})
            file_size = video_path.stat().st_size
            byte_range = self.headers.get("Range", "")
            start, end, status_code = 0, max(0, file_size - 1), 200
            if byte_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range.strip())
                if not match or file_size == 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                first, last = match.groups()
                if not first:
                    start = max(0, file_size - int(last or "0"))
                    end = file_size - 1
                else:
                    start = int(first)
                    end = min(int(last), file_size - 1) if last else file_size - 1
                if start >= file_size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status_code = 206
            content_length = max(0, end - start + 1)
            self.send_response(status_code)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(content_length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Accept-Ranges", "bytes")
            if status_code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            if urlparse(self.path).query == "download=1":
                self.send_header("Content-Disposition", 'attachment; filename="olyphants-remotion-test.mp4"')
            self.end_headers()
            with video_path.open("rb") as video_file:
                video_file.seek(start)
                remaining = content_length
                while remaining:
                    chunk = video_file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        if path == "/api/health":
            return self.send_json(200, {"status": "ok", "time": now_iso()})
        if path == "/api/auto":
            return self.send_json(200, auto_dashboard())
        if path == "/api/auto/engines":
            from modules.auto.engines import registered_engines
            return self.send_json(200, registered_engines(read_settings()))
        if path in {"/api/auto/groq-status", "/api/auto/qwen-status"}:
            from modules.auto.qwen import configuration
            return self.send_json(200, configuration())
        if path == "/api/auto/llm-providers":
            root = read_settings().get("auto_mpt_root", "")
            if not root:
                return self.send_json(503, {"error": "Pilih folder MoneyPrinterTurbo dahulu"})
            try:
                from modules.auto.mpt import llm_catalog
                catalog = llm_catalog(root)
                return self.send_json(200, {"providers": catalog["providers"], "default_provider_id": catalog["default_provider_id"]})
            except Exception as exc:
                return self.send_json(503, {"error": str(exc)[:300]})
        if path == "/api/auto/voices":
            try:
                from modules.remotion.tts import edge_voices
                return self.send_json(200, {"voices": edge_voices(), "provider": "edge-tts", "requires_mpt": False})
            except Exception as exc:
                return self.send_json(503, {"error": str(exc)[:300]})
        preview_match = re.fullmatch(r"/api/auto/voice-previews/([0-9a-f-]{36})\.mp3", path)
        if preview_match:
            preview = (ROOT / "output" / "auto-previews" / (preview_match.group(1) + ".mp3")).resolve()
            if not preview.is_relative_to((ROOT / "output" / "auto-previews").resolve()) or not preview.is_file():
                return self.send_json(404, {"error": "Preview suara tidak ditemukan"})
            content = preview.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return
        video_match = re.fullmatch(r"/api/auto/videos/([0-9a-f-]{36})/(landscape|portrait)", path)
        if video_match:
            item_id, format_name = video_match.groups()
            with connect_db() as db:
                row = db.execute("SELECT outputs_json FROM auto_queue WHERE id=?", (item_id,)).fetchone()
            if not row:
                return self.send_json(404, {"error": "Video AUTO tidak ditemukan"})
            entries = json.loads(row["outputs_json"] or "[]")
            entry = next((value for value in entries if value.get("format") == format_name), None)
            output_root = (ROOT / "output" / "auto").resolve()
            video_path = (ROOT / entry["path"]).resolve() if entry else output_root / "missing"
            if not video_path.is_relative_to(output_root) or not video_path.is_file():
                return self.send_json(404, {"error": "File video final tidak ditemukan"})
            file_size = video_path.stat().st_size
            byte_range = self.headers.get("Range", "")
            start, end, status_code = 0, max(0, file_size - 1), 200
            if byte_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range.strip())
                if not match or file_size == 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                first, last = match.groups()
                if not first:
                    suffix = int(last or "0")
                    if suffix <= 0:
                        start = file_size
                    else:
                        start = max(0, file_size - suffix)
                    end = file_size - 1
                else:
                    start = int(first)
                    end = min(int(last), file_size - 1) if last else file_size - 1
                if start >= file_size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status_code = 206
            content_length = max(0, end - start + 1)
            self.send_response(status_code)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(content_length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Accept-Ranges", "bytes")
            if status_code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            with video_path.open("rb") as video_file:
                video_file.seek(start)
                remaining = content_length
                while remaining:
                    chunk = video_file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        if path == "/api/status":
            return self.send_json(200, overview())
        if path in {"/api/ai/status", "/api/ai/usage"}:
            return self.send_json(200, {"enabled": False, "reason": "AI content generation is disabled by the manual production architecture."})
        if path == "/api/automation":
            return self.send_json(200, automation_status())
        if path == "/api/projects":
            with connect_db() as db:
                rows = db.execute(
                    "SELECT p.*, (SELECT j.stage FROM jobs j WHERE j.project_id=p.id AND j.status='Processing' "
                    "ORDER BY j.started_at DESC LIMIT 1) AS active_stage, "
                    "(SELECT j.id FROM jobs j WHERE j.project_id=p.id AND j.status IN ('QC Passed','QC Warning') "
                    "AND j.artifact_path<>'' ORDER BY j.finished_at DESC LIMIT 1) AS final_job_id "
                    "FROM projects p ORDER BY p.updated_at DESC"
                ).fetchall()
            return self.send_json(200, {"projects": [dict(row) for row in rows]})
        if path == "/api/settings":
            return self.send_json(200, {"settings": read_settings(), "secrets_configured": {
                "cartesia": capabilities()["cartesia"]["configured"],
                "telegram": capabilities()["telegram"]["configured"],
            }})
        if path == "/api/apps":
            caps = capabilities()
            return self.send_json(200, {"apps": {
                "youtube": {"status": "not_configured", "auth": "not_authenticated"},
                "tiktok": {"status": "not_configured", "auth": "not_authenticated"},
                "instagram": {"status": "not_configured", "auth": "not_authenticated"},
                "facebook": {"status": "not_configured", "auth": "not_authenticated"},
                "cartesia": {"status": "configured" if caps["cartesia"]["configured"] else "not_configured"},
                "telegram": {"status": "configured" if caps["telegram"]["configured"] else "not_configured"},
            }})
        if path.startswith("/api/projects/"):
            parts = path.strip("/").split("/")
            project_id = parts[2] if len(parts) > 2 else ""
            with connect_db() as db:
                row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                events = db.execute("SELECT kind, message, created_at FROM events WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
            if not row:
                return self.send_json(404, {"error": "Project not found"})
            if len(parts) == 4 and parts[3] == "workspace":
                return self.send_json(200, project_workspace(project_id))
            if len(parts) == 5 and parts[3] == "media":
                with connect_db() as db:
                    asset = db.execute("SELECT relative_path,mime_type FROM media_assets WHERE id=? AND project_id=?", (parts[4], project_id)).fetchone()
                if not asset:
                    return self.send_json(404, {"error": "Media not found"})
                output_root = ROOT / "output"
                media_path = (ROOT / asset["relative_path"]).resolve()
                if not media_path.is_relative_to(output_root.resolve()) or not media_path.is_file():
                    return self.send_json(404, {"error": "Media file missing"})
                content = media_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", asset["mime_type"])
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(content)
                return
            if len(parts) == 5 and parts[3] == "video":
                with connect_db() as db:
                    job = db.execute("SELECT j.artifact_path,p.title FROM jobs j JOIN projects p ON p.id=j.project_id WHERE j.id=? AND j.project_id=?", (parts[4], project_id)).fetchone()
                if not job or not job["artifact_path"]:
                    return self.send_json(404, {"error": "Video output not found"})
                output_root = ROOT / "output"
                video_path = (ROOT / job["artifact_path"]).resolve()
                if not video_path.is_relative_to(output_root.resolve()) or not video_path.is_file():
                    return self.send_json(404, {"error": "Video file missing"})
                content = video_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(video_path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                if urlparse(self.path).query == "download=1":
                    filename = re.sub(r"[^\w.-]+", "-", job["title"], flags=re.UNICODE).strip(".-")[:100] or "video"
                    filename += video_path.suffix
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename, safe=""))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(content)
                return
            if len(parts) == 4 and parts[3] == "scenes":
                with connect_db() as db:
                    scenes = db.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY position", (project_id,)).fetchall()
                return self.send_json(200, {"scenes": [dict(scene) for scene in scenes]})
            if len(parts) == 4 and parts[3] == "audio":
                with connect_db() as db:
                    audio = db.execute("SELECT * FROM audio_assets WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
                if not audio:
                    return self.send_json(200, {"audio": None})
                result = dict(audio)
                result["alignment"] = json.loads(result.pop("alignment_json"))
                result["file_url"] = f"/api/projects/{project_id}/audio/file/{result['id']}"
                return self.send_json(200, {"audio": result})
            if len(parts) == 4 and parts[3] == "composition":
                with connect_db() as db:
                    composition = db.execute("SELECT * FROM compositions WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
                return self.send_json(200, {"composition": dict(composition) if composition else None})
            if len(parts) == 4 and parts[3] == "render":
                with connect_db() as db:
                    jobs = db.execute("SELECT id,stage,status,error,artifact_path,started_at,finished_at,qc_json FROM jobs WHERE project_id=? ORDER BY started_at DESC LIMIT 20", (project_id,)).fetchall()
                return self.send_json(200, {"jobs": [{**dict(job), "qc": json.loads(job["qc_json"])} for job in jobs], "capability": capabilities()["render"]})
            if len(parts) == 6 and parts[3:5] == ["audio", "file"]:
                asset_id = parts[5]
                with connect_db() as db:
                    audio = db.execute("SELECT relative_path FROM audio_assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone()
                if not audio:
                    return self.send_json(404, {"error": "Audio not found"})
                output_root = ROOT / "output"
                resolved_output = output_root.resolve()
                audio_path = (ROOT / audio["relative_path"]).resolve()
                if not resolved_output.is_relative_to(ROOT.resolve()) or not audio_path.is_relative_to(resolved_output) or not audio_path.is_file():
                    return self.send_json(404, {"error": "Audio file not found"})
                content = audio_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            if len(parts) > 3:
                return self.send_json(404, {"error": "Endpoint not found"})
            return self.send_json(200, {"project": dict(row), "history": [dict(event) for event in events]})
        return self.serve_frontend(path)

    def serve_frontend(self, path: str):
        relative = unquote(path.lstrip("/")) or "index.html"
        candidate = (FRONTEND / relative).resolve()
        if not candidate.is_relative_to(FRONTEND.resolve()) or not candidate.is_file():
            candidate = FRONTEND / "index.html"
        try:
            body = candidate.read_bytes()
        except OSError:
            return self.send_json(404, {"error": "Not found"})
        mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(candidate.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/maintenance/clear-cache":
            try:
                return self.send_json(200, clear_render_cache())
            except RuntimeError as exc:
                return self.send_json(409, {"error": str(exc)})
            except OSError as exc:
                return self.send_json(500, {"error": f"Gagal membersihkan cache render: {exc.strerror or 'I/O error'}"})
        if path.startswith("/api/ai/"):
            return self.send_json(410, {"error": "AI ideation, research, and script generation are disabled; enter materials manually in New Video."})
        upload_route = re.fullmatch(r"/api/projects/([^/]+)/scenes/([^/]+)/media", path)
        if upload_route:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MEDIA_LIMIT_BYTES + 1_000_000:
                return self.send_json(413, {"error": "Upload must be smaller than 100 MB"})
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("multipart/form-data"):
                return self.send_json(415, {"error": "Use multipart/form-data for media uploads"})
            raw = self.rfile.read(size)
            message = BytesParser(policy=policy.default).parsebytes(
                b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("ascii", "replace") + b"\r\n\r\n" + raw
            )
            if not message.is_multipart():
                return self.send_json(400, {"error": "Malformed multipart upload"})
            file_data, filename, mime_type, duration = None, "", "", 0.0
            try:
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if name == "file" and part.get_filename():
                        file_data = part.get_payload(decode=True)
                        filename = part.get_filename()
                        mime_type = part.get_content_type()
                    elif name == "duration_seconds":
                        duration = float((part.get_payload(decode=True) or b"0").decode("utf-8"))
                if file_data is None:
                    return self.send_json(400, {"error": "Upload file is required"})
                asset = store_media(upload_route.group(1), upload_route.group(2), filename, mime_type, file_data, duration)
                return self.send_json(201, asset)
            except LookupError as exc:
                return self.send_json(404, {"error": str(exc)})
            except (ValueError, UnicodeDecodeError) as exc:
                return self.send_json(400, {"error": str(exc)})
            except OSError:
                return self.send_json(500, {"error": "Could not store media file"})
        try:
            payload = self.read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        if path == "/api/auto/topic":
            topic = str(payload.get("topic", "")).strip()
            if not topic or len(topic) > 1000:
                return self.send_json(400, {"error": "Topik wajib diisi dan maksimal 1000 karakter"})
            save_auto_settings({"auto_topic": topic})
            return self.send_json(200, {"saved": True})
        if path == "/api/auto/research":
            topic = str(payload.get("topic", read_settings().get("auto_topic", ""))).strip()
            if not topic or len(topic) > 1000:
                return self.send_json(400, {"error": "Tema riset wajib diisi dan maksimal 1000 karakter"})
            try:
                from modules.auto.research import search
                return self.send_json(200, {"research": search(topic, script=str(payload.get("script", ""))[:12000])})
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                return self.send_json(503, {"error": str(exc)[:300]})
        if path == "/api/auto/mpt-root":
            root = str(payload.get("root", "")).strip()
            try:
                updates = validate_settings({"auto_mpt_root": root})
                if not root:
                    return self.send_json(400, {"error": "Folder MoneyPrinterTurbo wajib diisi"})
                from modules.auto.mpt import edge_voices
                voices = edge_voices(root)
                if not voices:
                    return self.send_json(503, {"error": "Katalog Edge TTS tidak memiliki suara Indonesia"})
                save_auto_settings(updates)
                return self.send_json(200, {"providers": [], "provider_count": 0, "voices": voices, "voice_count": len(voices)})
            except Exception as exc:
                return self.send_json(400, {"error": str(exc)[:350]})
        if path == "/api/auto/apply":
            try:
                result = apply_auto(payload)
                return self.send_json(200, result)
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                return self.send_json(503, {"error": str(exc)[:500], "state": automation_status()})
        if path in {"/api/auto/groq-test", "/api/auto/qwen-test"}:
            try:
                from modules.auto.qwen import test_connection
                result = auto_llm_call("connection_test", test_connection)
                timestamp = now_iso()
                save_auto_settings({
                    "auto_engine_last_test_success": True,
                    "auto_engine_last_test_id": "groq_qwen",
                    "auto_engine_last_test_at": timestamp,
                    "auto_llm_last_test_success": True,
                    "auto_llm_last_test_provider": "groq",
                    "auto_llm_last_test_model": result["model"],
                    "auto_llm_last_test_at": timestamp,
                })
                return self.send_json(200, {"ok": True, "provider": "Groq", "model": result["model"], "usage": result.get("usage", {})})
            except Exception as exc:
                save_auto_settings({"auto_engine_last_test_success": False, "auto_llm_last_test_success": False})
                return self.send_json(503, {"ok": False, "error": str(exc)[:300]})
        if path == "/api/auto/test-production":
            try:
                from modules.auto.qwen import configuration
                if not configuration()["configured"]:
                    return self.send_json(503, {"accepted": False, "error": "Atur API key Groq sebelum menguji Lemo"})
                with connect_db() as db:
                    active = db.execute("SELECT id FROM auto_cycles WHERE status='running' LIMIT 1").fetchone()
                if active:
                    return self.send_json(409, {"error": "Selesaikan siklus AUTO aktif sebelum menjalankan TEST MODE"})
                cycle_id, item_id, timestamp = str(uuid.uuid4()), str(uuid.uuid4()), now_iso()
                test_topic = "[UJI LEMO] Watercolor Brush"
                test_script = "Ini video uji integrasi gaya Watercolor Brush dari Lemo. Sapuan warna transparan membentuk lanskap di atas kertas bertekstur. Setiap elemen muncul bertahap, ditemani narasi dan catatan visual. Remotion menyusun adegan, sementara FFmpeg memproses audio dan video."
                metadata = {"title": "Uji Lemo — Watercolor Brush", "description": "Render uji gaya Lemo tanpa MoneyPrinterTurbo.", "test_mode": True, "test_llm": True, "visual_style": "watercolor"}
                with connect_db() as db:
                    db.execute("INSERT INTO auto_cycles(id,production_date,topic,engine_id,target_unique,format_mode,status,is_additional,test_mode,created_at,started_at) VALUES(?,?,?,?,1,'portrait','running',1,1,?,?)", (cycle_id, datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Jakarta")).date().isoformat(), test_topic, "groq_qwen", timestamp, timestamp))
                    db.execute("INSERT INTO auto_queue(id,cycle_id,position,topic,video_script,video_terms,metadata_json,status,created_at,updated_at) VALUES(?,?,1,?,?,?,?,'queued',?,?)", (item_id, cycle_id, test_topic, test_script, json.dumps(["local test fixture"]), json.dumps(metadata, ensure_ascii=False), timestamp, timestamp))
                    db.execute("INSERT INTO events(kind,message,created_at) VALUES('auto_test_started',?,?)", ("Uji produksi Lemo Watercolor Brush dimulai tanpa MoneyPrinterTurbo", timestamp))
                save_auto_settings({"auto_runtime_state": "running", "auto_last_error": ""})
                start_auto_worker()
                return self.send_json(202, {"accepted": True, "test_mode": True, "cycle_id": cycle_id, "item_id": item_id, "visual_style": "watercolor", "message": "Uji Lemo berjalan tanpa MoneyPrinterTurbo; lihat antrean AUTO"})
            except Exception as exc:
                return self.send_json(503, {"accepted": False, "test_mode": True, "error": str(exc)[:500]})
        if path == "/api/auto/voice-preview":
            voice_id = str(payload.get("voice_id", ""))
            try:
                from modules.remotion.tts import preview_edge_voice
                preview_id = str(uuid.uuid4())
                target = ROOT / "output" / "auto-previews" / (preview_id + ".mp3")
                preview_edge_voice(voice_id, target)
                return self.send_json(200, {"url": f"/api/auto/voice-previews/{preview_id}.mp3"})
            except Exception as exc:
                return self.send_json(503, {"error": str(exc)[:300]})
        if path == "/api/projects":
            title = str(payload.get("title", "")).strip()[:160]
            if not title:
                return self.send_json(400, {"error": "title is required"})
            visual_style = str(payload.get("visual_style", "watercolor"))
            if visual_style not in SUPPORTED_VISUAL_STYLES:
                return self.send_json(400, {"error": "Gaya visual tidak tersedia"})
            project_id, timestamp = str(uuid.uuid4()), now_iso()
            with connect_db() as db:
                db.execute(
                    "INSERT INTO projects(id,title,topic,research_references,script,instructions,visual_style,script_approved,ai_metadata_json,status,source,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,0,'{}','Draft','manual',?,?)",
                    (project_id, title, str(payload.get("topic", ""))[:1000], str(payload.get("research_references", ""))[:50000],
                     str(payload.get("script", ""))[:100000], str(payload.get("instructions", ""))[:100000], visual_style, timestamp, timestamp),
                )
                db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "project_created", "Proyek manual disimpan sebagai draft", timestamp))
            return self.send_json(201, {"id": project_id, "status": "Draft", "created_at": timestamp})
        if path == "/api/production/mode" or path.startswith("/api/automation/queue"):
            return self.send_json(410, {"error": "AUTO mandiri ditunda; pipeline produksi berjalan dari New Video."})
        if path == "/api/projects/manual-production":
            return self.send_json(404, {"error": "Project ID is required in the route"})
        if path == "/api/settings":
            try:
                updates = validate_settings(payload)
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
            timestamp = now_iso()
            with connect_db() as db:
                for key, value in updates.items():
                    db.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                               ("config:" + key, json.dumps(value, ensure_ascii=False), timestamp))
            return self.send_json(200, {"settings": read_settings(), "saved": sorted(updates)})
        if path.startswith("/api/projects/"):
            parts = path.strip("/").split("/")
            project_id = parts[2] if len(parts) > 2 else ""
            if len(parts) == 4 and parts[3] == "produce":
                try:
                    result = start_manual_production(project_id, float(payload.get("speed", 1.0)), str(payload.get("visual_style", "")))
                except LookupError as exc:
                    return self.send_json(404, {"error": str(exc)})
                except (ValueError, PermissionError) as exc:
                    return self.send_json(409 if isinstance(exc, PermissionError) else 400, {"error": str(exc)})
                except Exception as exc:
                    return self.send_json(502, {"error": str(exc)[:400]})
                return self.send_json(200, result)
            if len(parts) == 4 and parts[3] == "approve-script":
                with connect_db() as db:
                    project = db.execute("SELECT id,script FROM projects WHERE id=?", (project_id,)).fetchone()
                    if not project:
                        return self.send_json(404, {"error": "Project not found"})
                    if not project["script"].strip():
                        return self.send_json(409, {"error": "Naskah kosong tidak dapat disetujui"})
                    db.execute("UPDATE projects SET script_approved=1,status='Draft',updated_at=? WHERE id=?", (now_iso(), project_id))
                    db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "script_approved", "Naskah disetujui pengguna", now_iso()))
                return self.send_json(200, {"id": project_id, "script_approved": True})
            if len(parts) == 4 and parts[3] == "scenes":
                with connect_db() as db:
                    exists = db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
                    scene_count = db.execute("SELECT COUNT(*) AS count FROM scenes WHERE project_id=?", (project_id,)).fetchone()["count"]
                    position = db.execute("SELECT COALESCE(MAX(position),-1)+1 AS next FROM scenes WHERE project_id=?", (project_id,)).fetchone()["next"]
                if not exists:
                    return self.send_json(404, {"error": "Project not found"})
                if scene_count >= 5:
                    return self.send_json(409, {"error": "Maksimal 5 scene per video"})
                try:
                    scene = validate_scene(payload)
                except ValueError as exc:
                    return self.send_json(400, {"error": str(exc)})
                scene_id, timestamp = str(uuid.uuid4()), now_iso()
                with connect_db() as db:
                    db.execute("INSERT INTO scenes(id,project_id,position,narration,start_seconds,end_seconds,duration_seconds,transition,visual_description,animation,text_overlay,background,revision_notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (scene_id, project_id, position, scene["narration"], scene["start_seconds"], scene["end_seconds"], scene["duration_seconds"], scene["transition"], scene["visual_description"], scene["animation"], scene["text_overlay"], scene["background"], scene["revision_notes"], timestamp, timestamp))
                    db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (timestamp, project_id))
                    db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "scene_created", "Scene ditambahkan", timestamp))
                return self.send_json(201, {"id": scene_id, **scene, "position": position})
            if len(parts) == 5 and parts[3:5] == ["scenes", "auto"]:
                try:
                    count = int(payload.get("count", 3))
                    replace_existing = payload.get("replace_existing") is True
                    result = generate_remotion_scenes(project_id, count, replace_existing)
                    return self.send_json(201, result)
                except LookupError as exc:
                    return self.send_json(404, {"error": str(exc)})
                except ValueError as exc:
                    return self.send_json(400, {"error": str(exc)})
                except RuntimeError as exc:
                    return self.send_json(409, {"error": str(exc)})
                except Exception as exc:
                    return self.send_json(502, {"error": str(exc)[:400]})
            if len(parts) == 4 and parts[3] == "audio":
                try:
                    speed = float(payload.get("speed", 1.0))
                    result = create_audio_asset(project_id, speed)
                except LookupError as exc:
                    return self.send_json(404, {"error": str(exc)})
                except (ValueError, PermissionError) as exc:
                    return self.send_json(409 if isinstance(exc, PermissionError) else 400, {"error": str(exc)})
                except Exception as exc:
                    from modules.cartesia.client import CartesiaError
                    if isinstance(exc, CartesiaError):
                        return self.send_json(502, {"error": str(exc)})
                    return self.send_json(500, {"error": "Cartesia gagal membuat audio"})
                return self.send_json(201, result)
            if len(parts) == 5 and parts[3:5] == ["scenes", "from-audio"]:
                return self.send_json(410, {"error": "Scene dibuat dan diurutkan manual melalui New Video."})
            if len(parts) == 4 and parts[3] == "composition":
                with connect_db() as db:
                    project = db.execute("SELECT id,title FROM projects WHERE id=?", (project_id,)).fetchone()
                    scenes = [dict(row) for row in db.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY position", (project_id,)).fetchall()]
                    audio = db.execute("SELECT * FROM audio_assets WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
                if not project:
                    return self.send_json(404, {"error": "Project not found"})
                if not scenes or not audio:
                    return self.send_json(409, {"error": "Scene dan audio diperlukan untuk membuat komposisi"})
                try:
                    artifact = create_composition_artifact(project_id, project["title"], scenes, dict(audio), str(payload.get("visual_style", "")))
                except (OSError, ValueError) as exc:
                    return self.send_json(500, {"error": str(exc)})
                timestamp = now_iso()
                with connect_db() as db:
                    db.execute("INSERT INTO compositions(id,project_id,relative_path,created_at) VALUES(?,?,?,?)", (artifact["id"], project_id, artifact["relative_path"], timestamp))
                    db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "composition_built", "Remotion composition manifest dibuat; render belum dijalankan", timestamp))
                return self.send_json(201, {"composition": artifact["relative_path"], "renderer": "not_run"})
            if len(parts) == 4 and parts[3] == "render":
                caps = capabilities()
                if not caps["render"]["ready"]:
                    return self.send_json(409, {"error": "BLOCKED BY TOOLCHAIN", "blockers": caps["render"]["blockers"]})
                with connect_db() as db:
                    project = db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
                    composition = db.execute("SELECT * FROM compositions WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
                    audio = db.execute("SELECT * FROM audio_assets WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
                    latest_scene = db.execute("SELECT MAX(updated_at) AS updated_at FROM scenes WHERE project_id=?", (project_id,)).fetchone()["updated_at"]
                    active = db.execute("SELECT id FROM jobs WHERE project_id=? AND status='Processing' LIMIT 1", (project_id,)).fetchone()
                if not project:
                    return self.send_json(404, {"error": "Project not found"})
                if not composition or not audio:
                    return self.send_json(409, {"error": "Audio dan Remotion composition diperlukan sebelum render"})
                if active:
                    return self.send_json(409, {"error": "Render job sedang berjalan", "job_id": active["id"]})
                if (latest_scene and latest_scene > composition["created_at"]) or audio["created_at"] > composition["created_at"]:
                    return self.send_json(409, {"error": "Composition lama. Buat ulang composition setelah perubahan scene atau audio."})
                output_root = ROOT / "output"
                resolved_output = output_root.resolve()
                composition_path = (ROOT / composition["relative_path"]).resolve()
                project_output = output_root / project_id
                if not resolved_output.is_relative_to(ROOT.resolve()) or not composition_path.is_relative_to(resolved_output) or not composition_path.is_file():
                    return self.send_json(409, {"error": "File composition tidak tersedia di folder output proyek"})
                if not project_output.resolve().is_relative_to(resolved_output):
                    return self.send_json(400, {"error": "Output path must stay inside project output directory"})
                config = read_settings()
                job_id, timestamp = str(uuid.uuid4()), now_iso()
                output_path = project_output / f"render-{job_id}.{config['render_format']}"
                with connect_db() as db:
                    db.execute("INSERT INTO jobs(id,project_id,stage,status,started_at) VALUES(?,?,?,'Processing',?)", (job_id, project_id, "remotion_render", timestamp))
                    db.execute("UPDATE projects SET status='Processing',updated_at=? WHERE id=?", (timestamp, project_id))
                result = modules_render(composition["relative_path"], output_path, audio["duration_seconds"])
                finished = now_iso()
                job_status = {"PASS": "QC Passed", "WARNING": "QC Warning", "FAILED": "QC Failed"}.get(result["status"], "Failed")
                artifact = output_path.relative_to(ROOT).as_posix() if output_path.is_file() else ""
                with connect_db() as db:
                    db.execute("UPDATE jobs SET status=?,qc_json=?,error=?,artifact_path=?,finished_at=? WHERE id=?",
                               (job_status, json.dumps(result, ensure_ascii=False), result.get("error", ""), artifact, finished, job_id))
                    db.execute("UPDATE projects SET status=?,updated_at=? WHERE id=?", (job_status, finished, project_id))
                    db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "render_finished", "Render selesai dengan status " + result["status"], finished))
                return self.send_json(200 if result["status"] == "PASS" else 422, {"job_id": job_id, "status": result["status"], "qc": result, "artifact_path": artifact})
        return self.send_json(404, {"error": "Endpoint not found"})

    def do_PUT(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        if path.startswith("/api/projects/"):
            parts = path.strip("/").split("/")
            project_id = parts[2] if len(parts) > 2 else ""
            if len(parts) == 3:
                with connect_db() as db:
                    existing = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                if not existing:
                    return self.send_json(404, {"error": "Project not found"})
                title = str(payload.get("title", existing["title"])).strip()[:160]
                topic = str(payload.get("topic", existing["topic"]))[:1000]
                research_references = str(payload.get("research_references", existing["research_references"]))[:50000]
                script = str(payload.get("script", existing["script"]))[:100000]
                instructions = str(payload.get("instructions", existing["instructions"]))[:100000]
                visual_style = str(payload.get("visual_style", existing["visual_style"]))
                if visual_style not in SUPPORTED_VISUAL_STYLES:
                    return self.send_json(400, {"error": "Gaya visual tidak tersedia"})
                metadata = payload.get("ai_metadata")
                if metadata is None:
                    metadata_json = existing["ai_metadata_json"]
                elif isinstance(metadata, dict):
                    metadata_json = json.dumps(metadata, ensure_ascii=False)
                else:
                    return self.send_json(400, {"error": "ai_metadata must be an object"})
                if not title:
                    return self.send_json(400, {"error": "title is required"})
                changed_script = script != existing["script"]
                timestamp = now_iso()
                with connect_db() as db:
                    db.execute(
                        "UPDATE projects SET title=?,topic=?,research_references=?,script=?,instructions=?,visual_style=?,ai_metadata_json=?,"
                        "script_approved=CASE WHEN ? THEN 0 ELSE script_approved END,"
                        "status=CASE WHEN ? THEN 'Draft' ELSE status END,updated_at=? WHERE id=?",
                        (title, topic, research_references, script, instructions, visual_style, metadata_json, int(changed_script), int(changed_script), timestamp, project_id),
                    )
                    if changed_script:
                        db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "script_revised", "Naskah berubah dan memerlukan persetujuan ulang", timestamp))
                return self.send_json(200, {"id": project_id, "script_approved": bool(existing["script_approved"] and not changed_script), "updated_at": timestamp})
            if len(parts) == 5 and parts[3] == "scenes" and parts[4] == "order":
                order = payload.get("scene_ids")
                if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
                    return self.send_json(400, {"error": "scene_ids must be a list of IDs"})
                with connect_db() as db:
                    current = [row["id"] for row in db.execute("SELECT id FROM scenes WHERE project_id=? ORDER BY position", (parts[2],)).fetchall()]
                    if not db.execute("SELECT 1 FROM projects WHERE id=?", (parts[2],)).fetchone():
                        return self.send_json(404, {"error": "Project not found"})
                    if len(order) != len(current) or set(order) != set(current):
                        return self.send_json(400, {"error": "scene_ids must contain every scene exactly once"})
                    for position, scene_id in enumerate(order):
                        db.execute("UPDATE scenes SET position=?, updated_at=? WHERE id=?", (position, now_iso(), scene_id))
                    db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (now_iso(), parts[2]))
                return self.send_json(200, {"scene_ids": order})
            if len(parts) == 7 and parts[3] == "scenes" and parts[5:7] == ["media", "order"]:
                scene_id = parts[4]
                order = payload.get("media_ids")
                if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
                    return self.send_json(400, {"error": "media_ids must be a list of media IDs"})
                with connect_db() as db:
                    current = [row["id"] for row in db.execute("SELECT id FROM media_assets WHERE project_id=? AND scene_id=? ORDER BY position", (project_id, scene_id)).fetchall()]
                    if len(order) != len(current) or set(order) != set(current):
                        return self.send_json(400, {"error": "media_ids must contain every scene media exactly once"})
                    for position, media_id in enumerate(order):
                        db.execute("UPDATE media_assets SET position=? WHERE id=? AND project_id=? AND scene_id=?", (position, media_id, project_id, scene_id))
                    db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (now_iso(), project_id))
                return self.send_json(200, {"media_ids": order})
            if len(parts) == 5 and parts[3] == "media":
                try:
                    duration = float(payload.get("duration_seconds", 0))
                except (TypeError, ValueError):
                    return self.send_json(400, {"error": "duration_seconds must be a number"})
                if not 0 <= duration <= 3600:
                    return self.send_json(400, {"error": "duration_seconds must be between 0 and 3600"})
                with connect_db() as db:
                    updated = db.execute("UPDATE media_assets SET duration_seconds=? WHERE id=? AND project_id=?", (duration, parts[4], project_id)).rowcount
                    if updated:
                        db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (now_iso(), project_id))
                if not updated:
                    return self.send_json(404, {"error": "Media not found"})
                return self.send_json(200, {"id": parts[4], "duration_seconds": duration})
            if len(parts) == 5 and parts[3] == "scenes":
                project_id, scene_id = parts[2], parts[4]
                with connect_db() as db:
                    row = db.execute("SELECT * FROM scenes WHERE id=? AND project_id=?", (scene_id, project_id)).fetchone()
                if not row:
                    return self.send_json(404, {"error": "Scene not found"})
                try:
                    scene = validate_scene(payload, dict(row))
                except ValueError as exc:
                    return self.send_json(400, {"error": str(exc)})
                timestamp = now_iso()
                with connect_db() as db:
                    db.execute("UPDATE scenes SET narration=?,start_seconds=?,end_seconds=?,duration_seconds=?,transition=?,visual_description=?,animation=?,text_overlay=?,background=?,revision_notes=?,updated_at=? WHERE id=? AND project_id=?",
                               (scene["narration"], scene["start_seconds"], scene["end_seconds"], scene["duration_seconds"], scene["transition"], scene["visual_description"], scene["animation"], scene["text_overlay"], scene["background"], scene["revision_notes"], timestamp, scene_id, project_id))
                    db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (timestamp, project_id))
                    db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "scene_updated", "Scene diperbarui", timestamp))
                return self.send_json(200, {"id": scene_id, **scene, "updated_at": timestamp})
        return self.send_json(404, {"error": "Endpoint not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path == "/api/remotion/demo":
            demo_path = (ROOT / "output" / "remotion-test" / "auto-politik-terkini.mp4").resolve()
            if not demo_path.is_relative_to((ROOT / "output").resolve()) or not demo_path.is_file():
                return self.send_json(404, {"error": "Video contoh Remotion tidak ditemukan"})
            demo_path.unlink()
            return self.send_json(200, {"deleted": "remotion-demo"})
        auto_video = re.fullmatch(r"/api/auto/videos/([0-9a-f-]{36})/(landscape|portrait)", path)
        if auto_video:
            item_id, format_name = auto_video.groups()
            with connect_db() as db:
                row = db.execute("SELECT q.outputs_json,q.status,q.cycle_id FROM auto_queue q WHERE q.id=?", (item_id,)).fetchone()
                active = db.execute("SELECT 1 FROM auto_cycles c WHERE c.id=? AND c.status='running'", (row["cycle_id"],)).fetchone() if row else None
            if not row:
                return self.send_json(404, {"error": "Video AUTO tidak ditemukan"})
            if active or row["status"] == "processing":
                return self.send_json(409, {"error": "Video tidak dapat dihapus selama siklus produksinya berjalan"})
            entries = json.loads(row["outputs_json"] or "[]")
            entry = next((item for item in entries if item.get("format") == format_name), None)
            if not entry:
                return self.send_json(404, {"error": "Video final tidak ditemukan atau sudah dihapus"})
            output_root = (ROOT / "output" / "auto").resolve()
            video_path = (ROOT / entry.get("path", "")).resolve()
            if not video_path.is_relative_to(output_root) or not video_path.is_file():
                return self.send_json(404, {"error": "File video final tidak ditemukan"})
            try:
                video_path.unlink()
                remaining = [item for item in entries if item is not entry]
                with connect_db() as db:
                    db.execute("UPDATE auto_queue SET outputs_json=?,updated_at=? WHERE id=?", (json.dumps(remaining, ensure_ascii=False), now_iso(), item_id))
                    db.execute("INSERT INTO events(kind,message,created_at) VALUES('auto_video_deleted',?,?)", (f"Video AUTO {format_name} dihapus; hasil siklus lainnya tetap disimpan", now_iso()))
                return self.send_json(200, {"deleted": item_id, "format": format_name})
            except OSError as exc:
                return self.send_json(500, {"error": f"Gagal menghapus file video: {exc.strerror or 'I/O error'}"})
        auto_history = re.fullmatch(r"/api/auto/history/([0-9a-f-]{36})", path)
        if auto_history:
            cycle_id = auto_history.group(1)
            with connect_db() as db:
                cycle = db.execute("SELECT id,status FROM auto_cycles WHERE id=?", (cycle_id,)).fetchone()
                if not cycle:
                    return self.send_json(404, {"error": "Riwayat siklus tidak ditemukan"})
                if cycle["status"] == "running":
                    return self.send_json(409, {"error": "Riwayat siklus tidak dapat dihapus saat produksi berjalan"})
                items = db.execute("SELECT id FROM auto_queue WHERE cycle_id=?", (cycle_id,)).fetchall()
            auto_root = (ROOT / "output" / "auto").resolve()
            data_root = (ROOT / "data").resolve()
            mpt_root = Path(read_settings().get("auto_mpt_root", "")).expanduser().resolve()
            runtime_root = (mpt_root.parent / "mpt-runtime").resolve()
            auto_paths = [(auto_root / cycle_id).resolve()]
            auto_paths.extend((auto_root / "research-assets" / row["id"]).resolve() for row in items)
            runtime_paths, data_paths = [], []
            for row in items:
                for format_name in ("landscape", "portrait"):
                    task_id = f"ovs-{row['id']}-{format_name}"
                    runtime_paths.append((runtime_root / "storage" / "tasks" / task_id).resolve())
                    local_media = runtime_root / "storage" / "local_videos"
                    if local_media.is_dir():
                        runtime_paths.extend(path.resolve() for path in local_media.glob(f"{task_id}-*"))
                    data_paths.extend((data_root / "mpt-ipc" / f"{task_id}.{suffix}.json").resolve()
                                      for suffix in ("job", "status", "result"))
                    data_paths.append((data_root / f"mpt-{task_id}.log").resolve())
            paths = auto_paths + runtime_paths + data_paths
            if (any(not target.is_relative_to(auto_root) for target in auto_paths)
                    or any(not target.is_relative_to(runtime_root) for target in runtime_paths)
                    or any(not target.is_relative_to(data_root) for target in data_paths)):
                return self.send_json(400, {"error": "Lokasi output siklus tidak valid"})
            try:
                for target in paths:
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.is_file():
                        target.unlink()
                with connect_db() as db:
                    db.execute("DELETE FROM auto_llm_usage WHERE cycle_id=?", (cycle_id,))
                    db.execute("DELETE FROM auto_cycles WHERE id=?", (cycle_id,))
                return self.send_json(200, {"deleted": cycle_id})
            except OSError as exc:
                return self.send_json(500, {"error": f"Gagal menghapus hasil siklus: {exc.strerror or 'I/O error'}"})
        if path.startswith("/api/projects/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                project_id = parts[2]
                with connect_db() as db:
                    project = db.execute("SELECT id,status FROM projects WHERE id=?", (project_id,)).fetchone()
                    active_job = db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='Processing' LIMIT 1", (project_id,)).fetchone()
                if not project:
                    return self.send_json(404, {"error": "Project not found"})
                if active_job or project["status"] in {"Producing", "Processing"}:
                    return self.send_json(409, {"error": "Proyek tidak dapat dihapus saat produksi berjalan"})
                output_root = (ROOT / "output").resolve()
                project_output = (output_root / project_id).resolve()
                if not project_output.is_relative_to(output_root):
                    return self.send_json(400, {"error": "Lokasi output proyek tidak valid"})
                try:
                    with connect_db() as db:
                        db.execute("DELETE FROM events WHERE project_id=?", (project_id,))
                        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
                    if project_output.is_dir():
                        shutil.rmtree(project_output)
                    return self.send_json(200, {"deleted": project_id})
                except OSError as exc:
                    return self.send_json(500, {"error": f"Gagal menghapus file proyek: {exc.strerror or 'I/O error'}"})
            if len(parts) == 5 and parts[3] == "video":
                project_id, job_id = parts[2], parts[4]
                with connect_db() as db:
                    job = db.execute("SELECT id,status,artifact_path FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
                    active = db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='Processing' LIMIT 1", (project_id,)).fetchone()
                if not job or job["status"] not in {"QC Passed", "QC Warning"} or not job["artifact_path"]:
                    return self.send_json(404, {"error": "Video final tidak ditemukan atau sudah dihapus"})
                if active:
                    return self.send_json(409, {"error": "Video tidak dapat dihapus selama produksi proyek sedang berjalan"})
                output_root = (ROOT / "output").resolve()
                video_path = (ROOT / job["artifact_path"]).resolve()
                if not video_path.is_relative_to(output_root) or not video_path.is_file():
                    return self.send_json(404, {"error": "File video final tidak ditemukan"})
                video_path.unlink()
                timestamp = now_iso()
                with connect_db() as db:
                    db.execute("UPDATE jobs SET status='Deleted',artifact_path='',error='Video final dihapus pengguna' WHERE id=? AND project_id=?", (job_id, project_id))
                    previous = db.execute("SELECT status FROM jobs WHERE project_id=? AND status IN ('QC Passed','QC Warning') AND artifact_path<>'' ORDER BY finished_at DESC LIMIT 1", (project_id,)).fetchone()
                    db.execute("UPDATE projects SET status=?,updated_at=? WHERE id=?", (previous["status"] if previous else "Draft", timestamp, project_id))
                    db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "final_video_deleted", "Video final dihapus; naskah dan aset proyek dipertahankan", timestamp))
                return self.send_json(200, {"deleted": job_id, "project_id": project_id})
            if len(parts) == 5 and parts[3] == "media":
                project_id, media_id = parts[2], parts[4]
                with connect_db() as db:
                    asset = db.execute("SELECT relative_path FROM media_assets WHERE id=? AND project_id=?", (media_id, project_id)).fetchone()
                    if not asset:
                        return self.send_json(404, {"error": "Media not found"})
                    db.execute("DELETE FROM media_assets WHERE id=? AND project_id=?", (media_id, project_id))
                    db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (now_iso(), project_id))
                output_root = ROOT / "output"
                media_path = (ROOT / asset["relative_path"]).resolve()
                if media_path.is_relative_to(output_root.resolve()) and media_path.is_file():
                    media_path.unlink()
                return self.send_json(200, {"deleted": media_id})
            if len(parts) == 5 and parts[3] == "scenes":
                project_id, scene_id = parts[2], parts[4]
                with connect_db() as db:
                    scene_count = db.execute("SELECT COUNT(*) AS count FROM scenes WHERE project_id=?", (project_id,)).fetchone()["count"]
                    if scene_count <= 2:
                        return self.send_json(409, {"error": "Video harus memiliki minimal 2 scene"})
                    media_paths = [row["relative_path"] for row in db.execute("SELECT relative_path FROM media_assets WHERE project_id=? AND scene_id=?", (project_id, scene_id)).fetchall()]
                    result = db.execute("DELETE FROM scenes WHERE id=? AND project_id=?", (scene_id, project_id))
                    if not result.rowcount:
                        return self.send_json(404, {"error": "Scene not found"})
                    db.execute("UPDATE projects SET status='Draft',updated_at=? WHERE id=?", (now_iso(), project_id))
                    db.execute("INSERT INTO events(project_id,kind,message,created_at) VALUES(?,?,?,?)", (project_id, "scene_deleted", "Scene dihapus", now_iso()))
                output_root = ROOT / "output"
                for relative_path in media_paths:
                    media_path = (ROOT / relative_path).resolve()
                    if media_path.is_relative_to(output_root.resolve()) and media_path.is_file():
                        media_path.unlink()
                return self.send_json(200, {"deleted": scene_id})
        return self.send_json(404, {"error": "Endpoint not found"})


def main() -> None:
    initialize_db()
    host = os.environ.get("OVS_HOST", "127.0.0.1")
    port = int(os.environ.get("OVS_PORT", "8765"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing non-loopback bind: configure authentication before network access")
    recover_auto_runtime()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"OLYPHANTS VIDEO STUDIO: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        AUTO_SHUTDOWN.set()
        server.server_close()


if __name__ == "__main__":
    main()
