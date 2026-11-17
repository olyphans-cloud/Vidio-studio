"""MoneyPrinterTurbo adapter pinned to its documented v1 HTTP API shape.

The dashboard backend owns a local MoneyPrinterTurbo process. It configures a
provider from the installed upstream registry and submits ordinary video tasks;
it does not create a parallel LLM implementation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class MoneyPrinterTurboError(RuntimeError):
    pass


def _worker_env(mpt_root: Path, runtime_root: Path) -> dict[str, str]:
    """Pass runtime plumbing only; AUTO/Qwen credentials never enter MPT."""
    allowed = {"PATH", "HOME", "TMPDIR", "TEMP", "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update({"PYTHONPATH": str(mpt_root), "MPT_HOME": str(runtime_root)})
    return env


def _set_toml_key(text: str, section: str | None, key: str, value) -> str:
    """Set one key in a TOML table while preserving unrelated user settings."""
    lines = text.splitlines()
    table = "" if section is None else f"[{section}]"
    starts = [i for i, line in enumerate(lines) if line.strip() == table]
    if section is None:
        start, end = 0, next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    else:
        if not starts:
            lines.extend(("", table))
            start = len(lines) - 1
        else:
            start = starts[0]
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    encoded = json.dumps(value, ensure_ascii=False)
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start + (1 if section else 0), end):
        if pattern.match(lines[index]):
            lines[index] = f"{key} = {encoded}"
            break
    else:
        lines.insert(end, f"{key} = {encoded}")
    return "\n".join(lines).rstrip() + "\n"


def configure_upstream(root: str, secrets: dict[str, str], settings: dict) -> tuple[Path, int]:
    """Configure the local MPT video API without selecting its LLM provider."""
    mpt_root = Path(root).expanduser().resolve()
    if not (mpt_root / "main.py").is_file() or not (mpt_root / "app" / "config" / "config.py").is_file():
        raise MoneyPrinterTurboError("Folder MoneyPrinterTurbo tidak valid atau belum terpasang lengkap")
    config_path = mpt_root / "config.toml"
    if not config_path.exists():
        example = mpt_root / "config.example.toml"
        if not example.is_file():
            raise MoneyPrinterTurboError("config.example.toml MoneyPrinterTurbo tidak ditemukan")
        shutil.copy2(example, config_path)
    if not config_path.is_file() or config_path.is_symlink():
        raise MoneyPrinterTurboError("config.toml MoneyPrinterTurbo harus berupa file biasa")
    try:
        current_text = config_path.read_text(encoding="utf-8")
        parsed = tomllib.loads(current_text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MoneyPrinterTurboError("config.toml MoneyPrinterTurbo tidak dapat dibaca") from exc
    app = parsed.get("app")
    if not isinstance(app, dict):
        raise MoneyPrinterTurboError("Tabel [app] tidak ditemukan di config.toml MoneyPrinterTurbo")
    try:
        port = int(parsed.get("listen_port", 8080))
    except (TypeError, ValueError):
        port = 8080
    if not 1 <= port <= 65535:
        port = 8080

    updated = current_text
    updated = _set_toml_key(updated, None, "listen_host", "127.0.0.1")
    updated = _set_toml_key(updated, None, "listen_port", port)
    app_values = {
        "api_key": secrets["MPT_API_KEY"],
        "edge_tts_timeout": 90,
    }
    ddgs_adapter = False
    for source_file in (mpt_root / "app").rglob("*.py"):
        try:
            source_text = source_file.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "ddgs" in source_text and "video_source" in source_text:
            ddgs_adapter = True
            break
    if not ddgs_adapter:
        raise MoneyPrinterTurboError(
            "Instalasi MoneyPrinterTurbo ini tidak menyediakan adapter video DDGS yang dapat diverifikasi; "
            "AUTO dibatalkan dan tidak akan beralih ke Pexels atau provider lain"
        )
    app_values["video_source"] = "ddgs"
    for key, value in app_values.items():
        updated = _set_toml_key(updated, "app", key, value)
    try:
        # Reparse before replacing to ensure an additive edit never leaves MPT
        # with a syntactically invalid config.
        tomllib.loads(updated)
        temporary = config_path.with_name("config.toml.ovs-tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, config_path)
        os.chmod(config_path, 0o600)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MoneyPrinterTurboError("Pengaturan MoneyPrinterTurbo gagal diterapkan") from exc
    return config_path, port


def llm_catalog(root: str) -> dict:
    """Read provider registry metadata and configured defaults from installed MPT."""
    python = python_for(root)
    code = r'''import json
from app.config import config
from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, LLM_PROVIDER_REGISTRY
items=[]
for p in LLM_PROVIDER_REGISTRY:
 app=config.app
 endpoint=p.default_service_endpoint
 items.append({"id":p.provider_id,"label":p.default_label,"adapter":p.adapter,
  "requires_api_key":p.requires_api_key,"requires_model_name":p.requires_model_name,
  "requires_base_url":p.requires_base_url,"show_api_key":p.show_api_key,
  "show_base_url":p.show_base_url,"default_model":p.default_model,
  "model":p.resolve_model_name(app.get(p.config_key("model_name"),"")),
  "base_url":p.resolve_base_url(app.get(p.config_key("base_url"),"")),
  "configured":bool(app.get(p.config_key("api_key"),"")),
  "api_key_config_key":p.config_key("api_key"),
  "model_config_key":p.config_key("model_name"),
  "base_url_config_key":p.config_key("base_url"),
  "endpoints":[{"id":e.endpoint_id,"label":e.default_label,"base_url":e.base_url} for e in p.service_endpoints]})
print(json.dumps({"default_provider_id":DEFAULT_LLM_PROVIDER_ID,"active_provider_id":config.app.get("llm_provider",DEFAULT_LLM_PROVIDER_ID),"providers":items}))'''
    try:
        mpt_root = Path(root).expanduser().resolve()
        result = subprocess.run(
            [python, "-c", code], cwd=mpt_root, capture_output=True, text=True,
            timeout=15, check=False, env={**os.environ, "PYTHONPATH": str(mpt_root)},
        )
        if result.returncode:
            raise MoneyPrinterTurboError("Registry LLM MoneyPrinterTurbo tidak dapat dibaca; pastikan dependensi repository sudah terpasang")
        catalog = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(catalog.get("providers"), list) or not catalog["providers"]:
            raise ValueError("empty provider registry")
        return catalog
    except (OSError, subprocess.TimeoutExpired, IndexError, json.JSONDecodeError, ValueError):
        raise MoneyPrinterTurboError("Registry LLM MoneyPrinterTurbo tidak tersedia") from None


def qwen3_local_status() -> dict:
    """Inspect local Ollama/Qwen3 availability without downloading model files."""
    import platform
    import urllib.request
    import urllib.error

    ollama = shutil.which("ollama")
    mem_available = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    model_storage = Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama" / "models"))).expanduser()
    storage_probe = model_storage
    while not storage_probe.exists() and storage_probe != storage_probe.parent:
        storage_probe = storage_probe.parent
    disk_free = shutil.disk_usage(storage_probe).free
    result = {
        "installed": False, "ready": False, "status": "not_installed" if not ollama else "not_ready",
        "runtime": bool(ollama), "models": [], "architecture": platform.machine(),
        "cpu_count": os.cpu_count() or 1, "memory_available_bytes": mem_available,
        "disk_free_bytes": disk_free, "reason": "Ollama belum terpasang" if not ollama else "",
    }
    if not ollama:
        return result
    try:
        request = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(request, timeout=2) as response:
            catalog = json.loads(response.read(1_000_000).decode("utf-8"))
        models = [item for item in catalog.get("models", []) if str(item.get("name", "")).lower().startswith("qwen3")]
        result["installed"] = bool(models)
        result["models"] = [{"name": item.get("name"), "size_bytes": int(item.get("size", 0) or 0)} for item in models]
        result["status"] = "not_installed" if not models else "not_ready"
        if not models:
            result["reason"] = "Belum ada model Qwen3 terpasang di Ollama"
            return result
        arch_supported = platform.machine().lower() in {"aarch64", "arm64", "x86_64", "amd64"}
        for item in result["models"]:
            enough_memory = mem_available >= max(2 * 1024**3, int(item["size_bytes"] * 1.35))
            enough_cpu = result["cpu_count"] >= 4
            enough_disk = disk_free >= 2 * 1024**3
            item["ready"] = arch_supported and enough_memory and enough_cpu and enough_disk
            item["reason"] = "siap" if item["ready"] else (
                "arsitektur CPU tidak kompatibel" if not arch_supported else
                "CPU kurang dari 4 core" if not enough_cpu else
                "RAM tersedia tidak cukup" if not enough_memory else
                "ruang penyimpanan bebas kurang dari 2 GB"
            )
        if not any(item["ready"] for item in result["models"]):
            result["reason"] = "Model Qwen3 terpasang, tetapi tidak ada yang lolos pemeriksaan CPU, RAM, arsitektur, dan penyimpanan"
            return result
        result.update({"ready": True, "status": "ready", "reason": "Runtime lokal tersedia; pilih model yang lolos pemeriksaan sumber daya"})
        return result
    except urllib.error.HTTPError:
        result["reason"] = "Ollama berjalan tetapi katalog model lokal tidak dapat dibaca"
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        result["reason"] = "Ollama belum berjalan di localhost:11434"
    return result


def test_qwen3_local(model: str) -> None:
    """Run a short local-only inference; no cloud provider or fallback is used."""
    import urllib.request
    import urllib.error

    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps({"model": model, "prompt": "Balas hanya dengan kata SIAP.", "stream": False, "keep_alive": "1m"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read(1_000_000).decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise MoneyPrinterTurboError("Uji inferensi lokal Qwen3 gagal; tidak ada provider pengganti yang digunakan") from None
    if not str(data.get("response", "")).strip():
        raise MoneyPrinterTurboError("Qwen3 lokal tidak mengembalikan respons")


def python_for(root: str) -> str:
    mpt_root = Path(root).expanduser().resolve()
    candidates = [
        mpt_root / ".venv" / "bin" / "python", mpt_root / "venv" / "bin" / "python",
        mpt_root / ".venv" / "Scripts" / "python.exe",
        mpt_root.parent / "mpt-venv" / "bin" / "python",
    ]
    return str(next((path for path in candidates if path.is_file()), Path(sys.executable)))


def validate_internal_runtime(root: str) -> dict:
    """Verify the vendored source and isolated Python imports; do not start HTTP."""
    mpt_root = Path(root).expanduser().resolve()
    if not (mpt_root / "app" / "services" / "task.py").is_file() or not (mpt_root / "pyproject.toml").is_file():
        raise MoneyPrinterTurboError("MPT_ROOT tidak menunjuk ke source MPT yang memiliki pipeline internal")
    python = python_for(root)
    runtime_root = mpt_root.parent / "mpt-runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    env = _worker_env(mpt_root, runtime_root)
    code = "from app.services import task; from app.models.schema import TaskVideoRequest; from app import __version__; print(__version__)"
    result = subprocess.run([python, "-c", code], cwd=mpt_root, capture_output=True, text=True, timeout=90, check=False, env=env)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise MoneyPrinterTurboError("MPT internal pipeline/dependency check failed: " + (detail[-1][:240] if detail else "import failed"))
    runtime_config = runtime_root / "config.toml"
    try:
        if runtime_config.is_file() and not runtime_config.is_symlink():
            original = runtime_config.read_text(encoding="utf-8")
            updated = _set_toml_key(original, "app", "edge_tts_timeout", 90)
            tomllib.loads(updated)
            if updated != original:
                temporary = runtime_config.with_name("config.toml.ovs-tmp")
                temporary.write_text(updated, encoding="utf-8")
                os.chmod(temporary, runtime_config.stat().st_mode & 0o777)
                os.replace(temporary, runtime_config)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MoneyPrinterTurboError("Timeout Edge TTS pada konfigurasi runtime MPT gagal diterapkan") from exc
    return {"version": result.stdout.strip().splitlines()[-1], "python": python, "runtime_root": str(runtime_root), "worker_ready": True}


def run_internal_worker(
    root: str, task_id: str, params: dict, materials: list[Path],
    progress_callback=None, timeout: int = 14400,
) -> dict:
    """Run MPT's actual app.services.task.start in an isolated child process via JSON IPC."""
    mpt_root = Path(root).expanduser().resolve()
    runtime = validate_internal_runtime(root)
    runtime_root = Path(runtime["runtime_root"])
    ipc_dir = Path(__file__).resolve().parents[2] / "data" / "mpt-ipc"
    log_dir = Path(__file__).resolve().parents[2] / "data"
    ipc_dir.mkdir(parents=True, exist_ok=True)
    job_path = ipc_dir / f"{task_id}.job.json"
    status_path = ipc_dir / f"{task_id}.status.json"
    result_path = ipc_dir / f"{task_id}.result.json"
    log_path = log_dir / f"mpt-{task_id}.log"
    job = {
        "task_id": task_id, "mpt_root": str(mpt_root), "runtime_root": str(runtime_root),
        "status_path": str(status_path), "result_path": str(result_path),
        "params": params, "materials": [str(Path(item).resolve()) for item in materials],
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    env = _worker_env(mpt_root, runtime_root)
    worker_path = Path(__file__).resolve().with_name("mpt_worker.py")
    started = time.monotonic()
    try:
        with log_path.open("ab", buffering=0) as log:
            process = subprocess.Popen(
                [runtime["python"], str(worker_path), str(job_path), str(status_path)],
                cwd=mpt_root, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, env=env, start_new_session=True,
            )
            try:
                while process.poll() is None:
                    if time.monotonic() - started > timeout:
                        process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                        raise MoneyPrinterTurboError("MPT internal worker melewati batas waktu")
                    try:
                        snapshot = json.loads(status_path.read_text(encoding="utf-8"))
                        if progress_callback:
                            progress_callback(int(snapshot.get("progress", 0) or 0), snapshot)
                    except (OSError, ValueError, TypeError):
                        pass
                    time.sleep(0.5)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=8)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1200:] if log_path.exists() else ""
            raise MoneyPrinterTurboError("MPT internal worker tidak mengembalikan hasil. " + tail[-500:]) from None
        if process.returncode or not result.get("videos"):
            raise MoneyPrinterTurboError("MPT gagal pada tahap " + str(result.get("failed_stage") or "pipeline") + ": " + str(result.get("error") or "tidak ada video keluaran")[:400])
        checked_videos = []
        for item in result["videos"]:
            path = Path(item).resolve()
            if not path.is_relative_to(runtime_root.resolve()) or not path.is_file() or path.stat().st_size == 0:
                raise MoneyPrinterTurboError("MPT menghasilkan lokasi video yang tidak valid")
            checked_videos.append(path)
        result["video_paths"] = checked_videos
        result["log_path"] = str(log_path)
        result["version"] = runtime["version"]
        return result
    finally:
        job_path.unlink(missing_ok=True)


def start_service(root: str, port: int) -> subprocess.Popen | None:
    """Start MPT only after the user applies AUTO settings; never expose API publicly."""
    mpt_root = Path(root).expanduser().resolve()
    python = python_for(root)
    check = subprocess.run(
        [python, "-c", "import fastapi, uvicorn, edge_tts, toml"],
        cwd=mpt_root, capture_output=True, timeout=15, check=False,
    )
    if check.returncode:
        raise MoneyPrinterTurboError("Environment MoneyPrinterTurbo belum memiliki dependensi runtime (FastAPI, Uvicorn, Edge TTS, TOML)")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=2):
            return None
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    log_path = Path(__file__).resolve().parents[2] / "data" / "moneyprinterturbo.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sink = open(log_path, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            [python, "main.py"], cwd=mpt_root, stdin=subprocess.DEVNULL,
            stdout=sink, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, "PYTHONPATH": str(mpt_root)},
        )
    finally:
        sink.close()
    return process


def request_json(base_url: str, path: str, api_key: str, payload: dict | None = None, timeout: int = 30) -> dict:
    url = base_url.rstrip("/") + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "X-API-KEY": api_key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(4_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not include response content; provider or proxies can echo secrets.
        raise MoneyPrinterTurboError(f"MoneyPrinterTurbo menolak permintaan (HTTP {exc.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise MoneyPrinterTurboError("MoneyPrinterTurbo tidak dapat dijangkau atau mengembalikan respons tidak valid") from None
    if not isinstance(result, dict) or result.get("status", 200) not in (200, 201):
        raise MoneyPrinterTurboError("MoneyPrinterTurbo melaporkan permintaan gagal")
    return result.get("data", result)


def check_api(base_url: str, api_key: str) -> bool:
    request_json(base_url, "/api/v1/llm/scripts", api_key, {"video_subject": "uji koneksi", "video_language": "Indonesian", "paragraph_number": 1}, timeout=90)
    return True


def generate_script(base_url: str, api_key: str, topic: str, position: int, research: list[dict] | None = None) -> str:
    citations = "\n".join(
        f"- {item.get('title','')} | {item.get('description','')} | {item.get('url','')}"
        for item in (research or [])[:12]
    )
    prompt = f"Tulis naskah narasi video bahasa Indonesia. Ini video ke-{position}; pilih sudut pembahasan yang berbeda. Gunakan hanya klaim yang didukung referensi di bawah, jangan mengarang fakta. Jika sumber tidak cukup, jawab RISET_TIDAK_CUKUP. Sertakan struktur scene dengan arahan visual yang relevan. Naskah harus siap dibacakan.\nREFERENSI RISET:\n{citations}"
    data = request_json(base_url, "/api/v1/llm/scripts", api_key, {
        "video_subject": topic, "video_language": "Indonesian", "paragraph_number": 4,
        "video_script_prompt": prompt,
    }, timeout=180)
    script = str(data.get("video_script", "")).strip()
    if not script:
        raise MoneyPrinterTurboError("Provider LLM terpilih tidak menghasilkan naskah")
    return script


def generate_terms(base_url: str, api_key: str, topic: str, script: str) -> list[str]:
    data = request_json(base_url, "/api/v1/llm/terms", api_key, {
        "video_subject": topic, "video_script": script, "amount": 6,
        "match_materials_to_script": True,
    }, timeout=180)
    terms = data.get("video_terms", [])
    if isinstance(terms, str):
        try:
            terms = json.loads(terms)
        except json.JSONDecodeError:
            terms = [term.strip() for term in terms.splitlines() if term.strip()]
    if not isinstance(terms, list) or not terms:
        raise MoneyPrinterTurboError("Provider LLM tidak menghasilkan kata kunci footage")
    return [str(term).strip()[:200] for term in terms if str(term).strip()][:12]


def generate_metadata(base_url: str, api_key: str, topic: str, script: str) -> dict:
    data = request_json(base_url, "/api/v1/llm/social-metadata", api_key, {
        "video_subject": topic, "video_script": script,
        "language": "Indonesian", "platform": "youtube",
    }, timeout=180)
    title = str(data.get("title", "")).strip()
    if not title:
        raise MoneyPrinterTurboError("Provider LLM tidak menghasilkan judul video")
    return {"title": title[:180], "caption": str(data.get("caption", ""))[:3000], "hashtags": data.get("hashtags", [])}


def submit_video(base_url: str, api_key: str, topic: str, script: str, terms: list[str], voice: str, aspect: str, *, music_enabled: bool = True, subtitle_enabled: bool = True) -> str:
    payload = {
        "video_subject": topic, "video_script": script, "video_language": "Indonesian",
        "video_aspect": aspect, "video_fit_mode": "cover", "image_motion": "zoom",
        "video_count": 1, "video_source": "ddgs", "video_terms": terms, "match_materials_to_script": True,
        "video_concat_mode": "sequential", "video_transition_mode": "FadeIn",
        "voice_name": voice, "voice_rate": 1.0, "voice_volume": 1.0,
        "bgm_type": "random" if music_enabled else "none", "bgm_volume": 0.18, "subtitle_enabled": subtitle_enabled,
        "subtitle_position": "bottom", "subtitle_display_mode": "sentence",
        "font_size": 48 if aspect == "16:9" else 58, "n_threads": 2,
        "paragraph_number": 4,
    }
    data = request_json(base_url, "/api/v1/videos", api_key, payload, timeout=30)
    task_id = str((data.get("task_id") if isinstance(data, dict) else "") or "")
    if not task_id:
        raise MoneyPrinterTurboError("MoneyPrinterTurbo tidak mengembalikan ID pekerjaan")
    return task_id


def task_status(base_url: str, api_key: str, task_id: str) -> dict:
    return request_json(base_url, "/api/v1/tasks/" + urllib.parse.quote(task_id, safe=""), api_key, timeout=20)


def download_video(base_url: str, api_key: str, video_url: str, destination: Path) -> None:
    parsed = urllib.parse.urlsplit(video_url)
    base = urllib.parse.urlsplit(base_url)
    if parsed.scheme and (parsed.scheme != base.scheme or parsed.netloc != base.netloc):
        raise MoneyPrinterTurboError("URL hasil video tidak berasal dari server MoneyPrinterTurbo yang dikonfigurasi")
    path = parsed.path if parsed.scheme else video_url
    if not path.startswith(("/tasks/", "/download/", "/stream/")):
        raise MoneyPrinterTurboError("MoneyPrinterTurbo mengembalikan lokasi video yang tidak dikenal")
    headers = {"X-API-KEY": api_key}
    request = urllib.request.Request(base_url.rstrip("/") + path, headers=headers)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as out:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("video/") and "octet-stream" not in content_type:
                raise MoneyPrinterTurboError("Berkas hasil dari MoneyPrinterTurbo bukan video")
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 2 * 1024 * 1024 * 1024:
                    raise MoneyPrinterTurboError("Video hasil melebihi batas lokal 2 GB")
                out.write(chunk)
        if not total:
            raise MoneyPrinterTurboError("MoneyPrinterTurbo mengirim video kosong")
        os.replace(temporary, destination)
    except MoneyPrinterTurboError:
        temporary.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError):
        temporary.unlink(missing_ok=True)
        raise MoneyPrinterTurboError("Gagal mengunduh hasil video dari MoneyPrinterTurbo") from None


def edge_voices(root: str) -> list[dict]:
    python = python_for(root)
    code = "import asyncio,json,edge_tts; print(json.dumps(asyncio.run(edge_tts.list_voices())))"
    try:
        result = subprocess.run([python, "-c", code], cwd=Path(root).resolve(), capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise MoneyPrinterTurboError("Katalog Edge TTS belum dapat dibaca dari environment MoneyPrinterTurbo")
        voices = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, IndexError, json.JSONDecodeError):
        raise MoneyPrinterTurboError("Katalog suara Edge TTS tidak tersedia") from None
    return [
        {"id": voice.get("ShortName"), "gender": voice.get("Gender"), "locale": voice.get("Locale"), "name": voice.get("FriendlyName") or voice.get("ShortName")}
        for voice in voices if isinstance(voice, dict) and voice.get("Locale") == "id-ID" and voice.get("ShortName")
    ]


def preview_voice(root: str, voice_id: str, destination: Path) -> None:
    if not re.fullmatch(r"id-ID-[A-Za-z]+Neural", voice_id):
        raise MoneyPrinterTurboError("Pilih suara bahasa Indonesia dari katalog Edge TTS")
    python = python_for(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    code = "import asyncio,edge_tts,sys; asyncio.run(edge_tts.Communicate('Halo, ini contoh suara untuk pengaturan video otomatis.', sys.argv[1]).save(sys.argv[2]))"
    try:
        result = subprocess.run([python, "-c", code, voice_id, str(destination)], cwd=Path(root).resolve(), capture_output=True, timeout=30, check=False)
        if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
            raise MoneyPrinterTurboError("Preview Edge TTS gagal dibuat")
    except (OSError, subprocess.TimeoutExpired):
        destination.unlink(missing_ok=True)
        raise MoneyPrinterTurboError("Edge TTS tidak merespons untuk preview suara") from None
