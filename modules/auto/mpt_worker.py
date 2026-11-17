"""IPC worker that runs MoneyPrinterTurbo's Python task pipeline directly."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def run(job_path: Path) -> int:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    mpt_root = Path(job["mpt_root"]).resolve()
    runtime_root = Path(job["runtime_root"]).resolve()
    status_path = Path(job["status_path"]).resolve()
    result_path = Path(job["result_path"]).resolve()
    if not (mpt_root / "app" / "services" / "task.py").is_file():
        raise RuntimeError("MPT source does not contain app.services.task")
    if not os.environ.get("MPT_HOME"):
        os.environ["MPT_HOME"] = str(runtime_root)
    sys.path.insert(0, str(mpt_root))

    from app.models import const
    from app.models.schema import MaterialInfo, TaskVideoRequest
    from app.services import state as task_state
    from app.services import task as task_service

    task_id = str(job["task_id"])
    local_dir = runtime_root / "storage" / "local_videos"
    local_dir.mkdir(parents=True, exist_ok=True)
    material_rows = []
    for index, source in enumerate(job["materials"], 1):
        src = Path(source).resolve(strict=True)
        if not src.is_file():
            raise RuntimeError("MPT material input is not a regular file")
        extension = src.suffix.lower()
        if extension not in {".mp4", ".mov", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}:
            raise RuntimeError("MPT material type is not allowed")
        name = f"{task_id}-{index}{extension}"
        shutil.copy2(src, local_dir / name)
        material_rows.append(MaterialInfo(provider="local", url=name))

    payload = dict(job["params"])
    payload.update({"video_source": "local", "video_materials": material_rows})
    if not str(payload.get("video_script", "")).strip():
        raise RuntimeError("A supplied script is required; MPT LLM fallback is disabled")
    params = TaskVideoRequest.model_validate(payload)
    original_update = task_state.state.update_task

    def update_task(current_id, state=const.TASK_STATE_PROCESSING, progress=0, **kwargs):
        result = original_update(current_id, state=state, progress=progress, **kwargs)
        snapshot = task_state.state.get_task(current_id) or {}
        _write_json(status_path, {
            "task_id": current_id,
            "state": snapshot.get("state", state),
            "progress": snapshot.get("progress", progress),
            "failed_stage": snapshot.get("failed_stage"),
            "error": snapshot.get("error"),
        })
        return result

    task_state.state.update_task = update_task
    try:
        task_service.start(task_id, params, stop_at="video")
    finally:
        task_state.state.update_task = original_update

    snapshot = task_state.state.get_task(task_id) or {}
    complete_state = getattr(const, "TASK_STATE_COMPLETE", 2)
    videos = snapshot.get("videos") or []
    result = {
        "task_id": task_id,
        "state": snapshot.get("state"),
        "progress": snapshot.get("progress", 0),
        "failed_stage": snapshot.get("failed_stage"),
        "error": snapshot.get("error"),
        "videos": [str(Path(item).resolve()) for item in videos],
        "audio_file": snapshot.get("audio_file", ""),
        "subtitle_path": snapshot.get("subtitle_path", ""),
        "materials": snapshot.get("materials", []),
        "warnings": snapshot.get("warnings", []),
    }
    _write_json(result_path, result)
    _write_json(status_path, result)
    return 0 if snapshot.get("state") == complete_state and videos else 2


if __name__ == "__main__":
    try:
        raise SystemExit(run(Path(sys.argv[1]).resolve()))
    except Exception as exc:
        if len(sys.argv) > 2:
            try:
                _write_json(Path(sys.argv[2]).resolve(), {"state": "error", "progress": 0, "error": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass
        print(f"MPT worker failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
