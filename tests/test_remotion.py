import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from modules.remotion.scene_generator import infer_visual_type, split_narration, time_scenes
from modules.remotion import tts
from modules.remotion import render_manager
from backend.main import ROOT


class RemotionSceneTests(unittest.TestCase):
    def test_all_twenty_catalog_styles_are_available_in_ui_and_renderer(self):
        catalog = json.loads((ROOT / "modules/lemo/style_catalog.json").read_text())
        styles = catalog["styles"]
        self.assertEqual(len(styles), 20)
        slugs = {style["slug"] for style in styles}
        self.assertIn("whiteboard-explainer-no-hands", slugs)
        self.assertEqual(sum(style.get("origin") == "OLYPHANTS original" for style in styles), 10)
        ui = (ROOT / "frontend/js/app.js").read_text()
        renderer = (ROOT / "renderer/remotion/src/lemo-style-scene.tsx").read_text()
        for slug in slugs - {"watercolor"}:
            self.assertIn(f"'{slug}'", renderer)
        for slug in slugs:
            self.assertIn(f"value:'{slug}'", ui)

    def test_remotion_manifest_keeps_per_scene_visual_style(self):
        scenes = render_manager._frame_scenes([
            {"duration_seconds": 2, "visual_style": "watercolor"},
            {"duration_seconds": 2, "visual_style": "iso-infographic"},
        ], 30, 120)
        self.assertEqual([scene["visualStyle"] for scene in scenes], ["watercolor", "iso-infographic"])

    def test_split_narration_removes_visual_directions(self):
        result = split_narration(
            "Scene 1: Narasi: Kota tumbuh cepat. Visual: peta bergerak.\n"
            "Scene 2: Narasi: Penduduk memakai kereta. Visualisasi: grafik naik.", 2
        )
        self.assertEqual(len(result), 2)
        self.assertNotIn("Visual", " ".join(result))
        self.assertIn("Kota tumbuh cepat", result[0])

    def test_scene_timing_uses_word_alignment_and_fills_duration(self):
        segments = ["Kota tumbuh cepat", "Penduduk memakai kereta"]
        words = [{"word": str(i), "start": i * 0.5, "end": i * 0.5 + 0.3} for i in range(6)]
        timings = time_scenes(segments, words, 3.2)
        self.assertEqual(len(timings), 2)
        self.assertEqual(timings[0]["start_seconds"], 0)
        self.assertEqual(timings[-1]["end_seconds"], 3.2)
        self.assertAlmostEqual(sum(item["duration_seconds"] for item in timings), 3.2)

    def test_scene_type_inference_covers_map_chart_and_ui(self):
        self.assertEqual(infer_visual_type("peta wilayah"), "map")
        self.assertEqual(infer_visual_type("grafik data meningkat"), "chart")
        self.assertEqual(infer_visual_type("simulasi antarmuka aplikasi"), "ui")

    def test_auto_tts_dispatches_to_selected_provider(self):
        settings = {"auto_tts_provider": "cartesia", "cartesia_voice_id": "configured-voice", "language": "id"}
        expected = {"audio_path": Path("cartesia.wav"), "duration_seconds": 60.0, "words": []}
        with patch.object(tts, "synthesize_cartesia", return_value=expected) as cartesia, \
             patch.object(tts, "synthesize_edge_tts") as edge:
            result = tts.synthesize_auto_tts("narration", settings, Path("cartesia.wav"), ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe")
        self.assertEqual(result, expected)
        cartesia.assert_called_once()
        edge.assert_not_called()

        settings = {"auto_tts_provider": "edge", "auto_voice_id": "id-ID-GadisNeural", "auto_mpt_root": "/unused"}
        with patch.object(tts, "synthesize_edge_tts", return_value=expected) as edge, \
             patch.object(tts, "synthesize_cartesia") as cartesia:
            tts.synthesize_auto_tts("narration", settings, Path("edge.wav"), ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe")
        edge.assert_called_once()
        cartesia.assert_not_called()

    def test_python_to_remotion_manifest_contract_without_real_render(self):
        output_root = ROOT / "output"
        with tempfile.TemporaryDirectory(dir=output_root) as tmp:
            folder = Path(tmp)
            audio = folder / "audio.wav"
            audio.write_bytes(b"mock-audio")
            output = folder / "render.mp4"
            manifest = {
                "title": "Contract",
                "audio_path": str(audio),
                "duration_seconds": 2,
                "fps": 30,
                "width": 1080,
                "height": 1920,
                "visual_style": "pixel-rpg",
                "scenes": [
                    {"heading": "Peta", "narration": "Kota", "duration_seconds": 1,
                     "visual_type": "map", "visual_prompt": "Peta animasi", "illustrative_only": True},
                    {"heading": "Grafik", "narration": "Tumbuh", "duration_seconds": 1,
                     "visual_type": "chart", "visual_prompt": "Grafik animasi", "illustrative_only": True},
                ],
            }
            def fake_render(props_file, output_file, **kwargs):
                props = json.loads(props_file.read_text())
                self.assertEqual(props["audioFile"].split("/")[-1], "narration-60s.wav")
                self.assertEqual([scene["visualType"] for scene in props["scenes"]], ["map", "chart"])
                self.assertEqual(props["durationInFrames"], 1800)
                self.assertEqual(props["visualStyle"], "pixel-rpg")
                Path(output_file).write_bytes(b"mock-rendered-video")
                return {"status": "RENDERED", "path": Path(output_file)}
            def fake_normalize(source, destination, target_seconds):
                destination.write_bytes(b"normalized-audio-placeholder")
                return 2.0, target_seconds
            with patch.object(render_manager, "_normalize_audio_duration", side_effect=fake_normalize), \
                 patch.object(render_manager, "render", side_effect=fake_render), \
                 patch.object(render_manager, "quality_check", return_value={"status": "PASS"}):
                result = render_manager.render_composition(manifest, output)
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(output.is_file())

    def test_remotion_uses_selected_auto_video_duration(self):
        output_root = ROOT / "output"
        with tempfile.TemporaryDirectory(dir=output_root) as tmp:
            folder = Path(tmp)
            audio = folder / "audio.wav"
            audio.write_bytes(b"mock-audio")
            output = folder / "render-30s.mp4"
            manifest = {
                "title": "30-second AUTO video", "audio_path": str(audio), "duration_seconds": 2,
                "fps": 30, "width": 1080, "height": 1920, "visual_style": "pixel-rpg",
                "target_duration_seconds": 30,
                "scenes": [{"heading": "Scene", "narration": "Test", "duration_seconds": 2,
                            "visual_type": "illustration", "visual_mode": "generated", "visual_prompt": "pixel art"}],
            }
            def fake_render(props_file, output_file, **kwargs):
                props = json.loads(props_file.read_text())
                self.assertEqual(props["durationInFrames"], 900)
                self.assertEqual(props["audioFile"].split("/")[-1], "narration-30s.wav")
                Path(output_file).write_bytes(b"mock-rendered-video")
                return {"status": "RENDERED", "path": Path(output_file)}
            def fake_normalize(source, destination, target_seconds):
                destination.write_bytes(b"normalized-audio-placeholder")
                self.assertEqual(target_seconds, 30)
                return 2.0, target_seconds
            with patch.object(render_manager, "_normalize_audio_duration", side_effect=fake_normalize), \
                 patch.object(render_manager, "render", side_effect=fake_render), \
                 patch.object(render_manager, "quality_check", return_value={"status": "PASS"}):
                result = render_manager.render_composition(manifest, output)
            self.assertEqual(result["duration_seconds"], 30)
            self.assertEqual(result["status"], "PASS")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe required")
    def test_audio_normalizer_makes_exactly_one_minute(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "output") as tmp:
            source = Path(tmp) / "short.wav"
            destination = Path(tmp) / "minute.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(44100)
                wav.writeframes(b"\0\0" * 44100 * 2)
            original, normalized = render_manager._normalize_audio_duration(source, destination)
            self.assertAlmostEqual(original, 2.0, delta=0.03)
            self.assertAlmostEqual(normalized, 60.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
