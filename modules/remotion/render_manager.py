"""Stage local assets, render one Remotion composition, then run shared FFprobe QC."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import uuid
from pathlib import Path

from backend.main import ROOT, read_settings
from modules.ffmpeg.renderer import quality_check
from modules.remotion.renderer import REMOTION_ROOT, render

TARGET_VIDEO_DURATION_SECONDS = 60.0


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=45, check=False,
    )
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError):
        duration = 0
    if result.returncode or duration <= 0:
        raise RuntimeError("FFprobe gagal membaca durasi audio Remotion")
    return duration


def _normalize_audio_duration(source: Path, destination: Path, target_seconds: float = TARGET_VIDEO_DURATION_SECONDS) -> tuple[float, float]:
    """Time-stretch slightly if overlong, or pad speech ending so it plays at natural human tempo."""
    duration = _probe_duration(source)
    raw_tempo = duration / target_seconds
    if raw_tempo > 1.0:
        tempo = min(1.20, raw_tempo)
    elif raw_tempo >= 0.96:
        tempo = raw_tempo
    else:
        # Keep speech at natural human speaking speed (1.0x); never drag or slow down voice
        tempo = 1.0

    factors = []
    if abs(tempo - 1.0) > 0.01:
        temp_tempo = tempo
        while temp_tempo > 2.0:
            factors.append(2.0)
            temp_tempo /= 2.0
        while temp_tempo < 0.5:
            factors.append(0.5)
            temp_tempo /= 0.5
        factors.append(temp_tempo)

    filters = [f"atempo={factor:.8f}" for factor in factors] if factors else []
    stretched_duration = duration / (math.prod(factors) if factors else 1.0)
    if stretched_duration < target_seconds:
        filters.append(f"apad=pad_dur={target_seconds - stretched_duration:.6f}")
    filters.extend([f"atrim=duration={target_seconds:.6f}", "asetpts=PTS-STARTPTS"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-y", "-v", "error", "-i", str(source), "-af", ",".join(filters),
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", "-t", f"{target_seconds:.6f}", str(destination)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if result.returncode or not destination.is_file() or destination.stat().st_size < 1000:
        raise RuntimeError("FFmpeg gagal menyesuaikan audio ke durasi video satu menit")
    actual_duration = _probe_duration(destination)
    if abs(actual_duration - target_seconds) > 0.15:
        raise RuntimeError("Audio Remotion tidak berhasil disetel ke durasi satu menit")
    return duration, actual_duration


def _inside_output(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to((ROOT / "output").resolve()):
        raise ValueError("Remotion hanya dapat menerima asset lokal di folder output proyek")
    return resolved


def _frame_scenes(scenes: list[dict], fps: int, total_frames: int) -> list[dict]:
    if not scenes:
        raise ValueError("Komposisi Remotion memerlukan minimal satu scene")
    rendered = []
    for item in scenes:
        row = dict(item)
        frames = max(1, round(float(row.pop("duration_seconds", 0)) * fps))
        row["durationInFrames"] = frames
        row["visualType"] = row.pop("visual_type", "illustration")
        row["visualMode"] = row.pop("visual_mode", "generated")
        row["visualPrompt"] = row.pop("visual_prompt", "")
        row["visualStyle"] = row.pop("visual_style", "")
        row["illustrativeOnly"] = bool(row.pop("illustrative_only", False))
        row["mediaFiles"] = []
        row.pop("media_paths", None)
        row.pop("start_seconds", None)
        row.pop("end_seconds", None)
        rendered.append(row)
    delta = total_frames - sum(row["durationInFrames"] for row in rendered)
    rendered[-1]["durationInFrames"] = max(1, rendered[-1]["durationInFrames"] + delta)
    for index, row in enumerate(rendered):
        row["transition"] = row.get("transition", "fade")
        if index and row["transition"] != "cut" and min(row["durationInFrames"], rendered[index - 1]["durationInFrames"]) >= 24:
            rendered[index - 1]["durationInFrames"] += 12
        else:
            row["transition"] = "cut"
    return rendered


def render_composition(manifest: dict, output_file: Path, *, stage_callback=None, progress_callback=None, timeout: int = 1800) -> dict:
    """Render an audio-led, 9:16 MP4 composition from generated scenes and optional local media."""
    output_file = output_file.resolve()
    if not output_file.is_relative_to((ROOT / "output").resolve()):
        raise ValueError("Output Remotion harus disimpan di folder output proyek")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    audio_source = _inside_output(Path(manifest["audio_path"]))
    source_duration = float(manifest["duration_seconds"])
    fps = int(manifest.get("fps", 30))
    width, height = int(manifest.get("width", 1080)), int(manifest.get("height", 1920))
    if source_duration <= 0 or fps != 30 or (width, height) not in {(1080, 1920), (1920, 1080)}:
        raise ValueError("Remotion output wajib 1080×1920 atau 1920×1080 pada 30 fps")
    duration = float(manifest.get("target_duration_seconds", TARGET_VIDEO_DURATION_SECONDS))
    if not 1 <= duration <= TARGET_VIDEO_DURATION_SECONDS:
        raise ValueError("Durasi render Remotion harus antara 1 dan 60 detik")
    total_frames = max(1, math.ceil(duration * fps))
    scale = duration / source_duration if source_duration > duration else 1.0
    scaled_source_scenes = []
    for item in manifest["scenes"]:
        row = dict(item)
        if "duration_seconds" in row:
            row["duration_seconds"] = float(row["duration_seconds"]) * scale
        scaled_source_scenes.append(row)
    scenes = _frame_scenes(scaled_source_scenes, fps, total_frames)
    run_id = uuid.uuid4().hex
    stage_dir = REMOTION_ROOT / "public" / ".runs" / run_id
    stage_dir.mkdir(parents=True, exist_ok=False)
    try:
        staged_audio = stage_dir / f"narration-{int(duration)}s.wav"
        _normalize_audio_duration(audio_source, staged_audio, duration)
        static_prefix = f".runs/{run_id}"
        for scene, original in zip(scenes, manifest["scenes"]):
            media_paths = [Path(path) for path in original.get("media_paths", [])]
            durations = [float(value) for value in original.get("media_durations", [])]
            if not media_paths:
                continue
            weights = durations if len(durations) == len(media_paths) and sum(durations) > 0 else [1.0] * len(media_paths)
            frame_weights = [max(1, round(scene["durationInFrames"] * weight / sum(weights))) for weight in weights]
            frame_weights[-1] = max(1, scene["durationInFrames"] - sum(frame_weights[:-1]))
            media_entries = []
            for media_index, media_path in enumerate(media_paths):
                source = _inside_output(media_path)
                suffix = source.suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm"}:
                    continue
                staged = stage_dir / f"scene-{len(media_entries)}-{media_index}{suffix}"
                shutil.copy2(source, staged)
                media_entries.append({"file": f"{static_prefix}/{staged.name}", "durationInFrames": frame_weights[media_index]})
            scene["mediaFiles"] = media_entries
        props = {
            "title": str(manifest.get("title") or "Olyphants Video")[:180],
            "audioFile": f"{static_prefix}/{staged_audio.name}", "scenes": scenes,
            "fps": fps, "durationInFrames": total_frames, "width": width, "height": height,
            "visualStyle": manifest.get("visual_style", "remotion"),
        }
        props_file = stage_dir / "props.json"
        props_file.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
        rendered = render(props_file, output_file, timeout=timeout, stage_callback=stage_callback, progress_callback=progress_callback)
        settings = read_settings()
        settings.update({"render_width": width, "render_height": height, "render_fps": fps, "render_format": "mp4"})
        qc = quality_check(output_file, settings, duration, shutil.which("ffprobe") or "ffprobe", stage_callback)
        return {**rendered, "status": qc["status"], "qc": qc, "duration_seconds": duration}
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
