"""Legacy Hyperframes entry point and shared FFprobe output validation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def quality_check(output_file: Path, settings: dict, expected_duration: float,
                  ffprobe_bin: str, stage_callback=None) -> dict:
    if stage_callback:
        stage_callback("quality_control")
    fmt = settings.get("render_format", "mp4")
    try:
        probe = _run([ffprobe_bin, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output_file)], output_file.parent, timeout=45)
        if probe.returncode != 0:
            return {"status": "FAILED", "error": "FFprobe tidak dapat membaca video hasil render"}
        metadata = json.loads(probe.stdout)
        size_bytes = output_file.stat().st_size
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {"status": "FAILED", "error": "Pemeriksaan metadata video gagal"}
    streams = metadata.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    failures, warnings = [], []
    if size_bytes <= 1024: failures.append("Ukuran video terlalu kecil")
    if not video: failures.append("Video stream tidak ditemukan")
    if not audio: failures.append("Audio stream tidak ditemukan")
    if video:
        if (video.get("width"), video.get("height")) != (settings["render_width"], settings["render_height"]):
            failures.append("Resolusi tidak sesuai pengaturan")
        try:
            numerator, denominator = video.get("r_frame_rate", "0/1").split("/", 1)
            fps = float(numerator) / float(denominator)
            if abs(fps - settings["render_fps"]) > 0.05: failures.append("FPS tidak sesuai pengaturan")
        except (ValueError, ZeroDivisionError): warnings.append("FPS tidak dapat dibaca")
        expected_codec = "h264" if fmt == "mp4" else "vp9"
        if video.get("codec_name") != expected_codec: failures.append("Codec video tidak sesuai format yang diminta")
        try:
            actual = float(video.get("duration", metadata.get("format", {}).get("duration", 0)))
            if abs(actual - expected_duration) > max(0.15, expected_duration * 0.01): failures.append("Durasi video menyimpang dari audio sumber")
        except (TypeError, ValueError): warnings.append("Durasi video tidak dapat dibaca")
    if audio:
        expected_codec = "aac" if fmt == "mp4" else "opus"
        if audio.get("codec_name") != expected_codec: failures.append("Codec audio tidak sesuai format yang diminta")
        try:
            actual = float(audio.get("duration", metadata.get("format", {}).get("duration", 0)))
            if abs(actual - expected_duration) > max(0.15, expected_duration * 0.01): failures.append("Durasi audio menyimpang dari audio sumber")
        except (TypeError, ValueError): warnings.append("Durasi audio tidak dapat dibaca")
    return {
        "status": "FAILED" if failures else "WARNING" if warnings else "PASS",
        "checks": ["file_exists", "probe_readable", "audio_present", "video_present", "duration", "resolution", "fps", "codec", "size"],
        "failures": failures, "warnings": warnings, "duration_seconds": expected_duration,
        "size_bytes": size_bytes, "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": video.get("width") if video else None, "height": video.get("height") if video else None,
    }


def render_and_qc(composition_dir: Path, output_file: Path, settings: dict,
                  expected_duration: float, hyperframes_bin: str,
                  ffprobe_bin: str, stage_callback=None) -> dict:
    """Keep the old entry point available for explicit legacy callers."""
    command = [hyperframes_bin, "render", "--output", str(output_file), "--fps", str(settings["render_fps"]), "--format", settings.get("render_format", "mp4")]
    try:
        rendered = _run(command, composition_dir, timeout=1800)
    except subprocess.TimeoutExpired:
        return {"status": "FAILED", "error": "Render melewati batas waktu 30 menit"}
    except OSError:
        return {"status": "FAILED", "error": "Hyperframes gagal dijalankan"}
    if rendered.returncode != 0:
        return {"status": "FAILED", "error": f"Hyperframes berhenti dengan kode {rendered.returncode}"}
    if not output_file.is_file():
        return {"status": "FAILED", "error": "Hyperframes tidak menghasilkan file video"}
    return quality_check(output_file, settings, expected_duration, ffprobe_bin, stage_callback)
