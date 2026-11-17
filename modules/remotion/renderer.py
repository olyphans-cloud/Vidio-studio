"""Python-to-Node bridge for Remotion's local render CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from backend.main import ROOT

REMOTION_ROOT = ROOT / "renderer" / "remotion"


def render(props_file: Path, output_file: Path, *, timeout: int = 1800, stage_callback=None, progress_callback=None) -> dict:
    node = shutil.which("node")
    browser = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    render_script = REMOTION_ROOT / "render.mjs"
    if not node or not (REMOTION_ROOT / "node_modules" / "@remotion" / "renderer").is_dir():
        raise RuntimeError("Dependensi Remotion belum dipasang di renderer/remotion")
    if not browser:
        raise RuntimeError("Browser Chrome/Chromium tidak tersedia untuk Remotion")
    if stage_callback:
        stage_callback("remotion_render")
    environment = os.environ.copy()
    environment["OLYPHANTS_CHROME_PATH"] = browser
    with tempfile.TemporaryDirectory(prefix="remotion-bundle-", dir=REMOTION_ROOT.parent) as bundle_dir:
        job_file = props_file.with_name("render-job.json")
        progress_file = props_file.with_name("render-progress.json")
        progress_file.unlink(missing_ok=True)
        log_file = props_file.with_name("render-node.log")
        job_file.write_text(json.dumps({"propsPath": str(props_file), "outputPath": str(output_file), "bundlePath": str(Path(bundle_dir) / "bundle"), "progressPath": str(progress_file)}), encoding="utf-8")
        command = [node, str(render_script), str(job_file)]
        try:
            with log_file.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command, cwd=REMOTION_ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, text=True)
                started = time.monotonic()
                while process.poll() is None:
                    if time.monotonic() - started > timeout:
                        process.terminate()
                        try: process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=3)
                        raise RuntimeError("Remotion melewati batas waktu render")
                    if progress_callback and progress_file.is_file():
                        try: progress_callback(float(json.loads(progress_file.read_text(encoding="utf-8")).get("progress", 0)) / 100)
                        except (OSError, ValueError, TypeError, json.JSONDecodeError): pass
                    time.sleep(.35)
                if process.returncode:
                    detail = log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                    raise RuntimeError("Remotion gagal merender video" + (f": {' '.join(detail[-40:])[-4000:]}" if detail else ""))
        except subprocess.TimeoutExpired:
            raise RuntimeError("Remotion melewati batas waktu render") from None
        except OSError:
            raise RuntimeError("Remotion gagal dijalankan oleh Node.js") from None
        finally:
            progress_file.unlink(missing_ok=True)
            job_file.unlink(missing_ok=True)
            log_file.unlink(missing_ok=True)
    if not output_file.is_file() or output_file.stat().st_size <= 1024:
        raise RuntimeError("Remotion tidak menghasilkan MP4 yang valid")
    return {"status": "RENDERED", "path": output_file}
