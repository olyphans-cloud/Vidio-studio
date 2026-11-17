import urllib.error
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import main as backend
from backend.main import validate_settings
from modules.auto import qwen


class AutoSettingsTests(unittest.TestCase):
    @staticmethod
    def _json_response(text="SIAP"):
        import json
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, _limit):
                return json.dumps({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 3}}).encode()
        return Response()

    def test_yomi_single_key_and_round_robin_three_keys(self):
        import os
        from modules.auto import qwen, yomi
        keys = {"GROQ_API_KEY": "test-yomi-one", "GROQ_API_KEY_2": "", "GROQ_API_KEY_3": ""}
        seen = []
        def response(request, timeout):
            seen.append(request.get_header("Authorization"))
            return self._json_response()
        with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", response):
            qwen.chat("one")
            self.assertEqual(seen, ["Bearer test-yomi-one"])
        seen.clear()
        keys.update({"GROQ_API_KEY_2": "test-yomi-two", "GROQ_API_KEY_3": "test-yomi-three"})
        with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", response):
            for _ in range(6):
                qwen.chat("round robin")
        self.assertEqual(seen, ["Bearer test-yomi-one", "Bearer test-yomi-two", "Bearer test-yomi-three"] * 2)

    def test_yomi_429_fails_over_and_records_safe_events(self):
        import os
        from modules.auto import qwen, yomi
        events, seen = [], []
        def response(request, timeout):
            seen.append(request.get_header("Authorization"))
            if len(seen) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "private", {"Retry-After": "30"}, None)
            return self._json_response()
        keys = {"GROQ_API_KEY": "test-secret-yomi-one", "GROQ_API_KEY_2": "test-secret-yomi-two", "GROQ_API_KEY_3": ""}
        with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", response):
            yomi.set_event_callback(events.append)
            try:
                self.assertEqual(qwen.chat("fail over")[0], "SIAP")
            finally:
                yomi.set_event_callback(None)
        self.assertEqual(seen, ["Bearer test-secret-yomi-one", "Bearer test-secret-yomi-two"])
        self.assertIn("YOMI: API Key 1 terkena rate limit", events)
        self.assertIn("YOMI: Beralih ke API Key 2", events)
        self.assertIn("YOMI: API Key 2 berhasil", events)
        self.assertNotIn("test-secret", " ".join(events))

    def test_yomi_all_keys_rate_limited_stops_after_one_attempt_per_key(self):
        import os
        from modules.auto import qwen, yomi
        events, calls = [], []
        def limited(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 429, "private", {"Retry-After": "120"}, None)
        keys = {"GROQ_API_KEY": "test-limit-one", "GROQ_API_KEY_2": "test-limit-two", "GROQ_API_KEY_3": "test-limit-three"}
        with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", limited):
            yomi.set_event_callback(events.append)
            try:
                with self.assertRaisesRegex(qwen.GroqError, "Semua API Key terkena rate limit") as raised:
                    qwen.chat("limit")
            finally:
                yomi.set_event_callback(None)
        self.assertEqual(len(calls), 3)
        self.assertIn("YOMI: Semua API Key terkena rate limit", events)
        self.assertNotIn("test-limit", str(raised.exception) + " ".join(events))

    def test_yomi_cooldown_expires_and_key_can_be_reused(self):
        import os
        from modules.auto import yomi
        keys = {"GROQ_API_KEY": "test-cooldown-key", "GROQ_API_KEY_2": "", "GROQ_API_KEY_3": ""}
        with patch.dict(os.environ, keys), patch.object(yomi.time, "monotonic", return_value=100.0), patch.object(yomi, "_NEXT", 0):
            yomi.mark_rate_limited(1, {"Retry-After": "2"})
            self.assertIsNone(yomi.next_key())
        with patch.dict(os.environ, keys), patch.object(yomi.time, "monotonic", return_value=103.0):
            self.assertEqual(yomi.next_key(), (1, "test-cooldown-key"))

    def test_yomi_401_rotates_but_403_and_bad_requests_do_not(self):
        import os
        from modules.auto import qwen, yomi
        keys = {"GROQ_API_KEY": "test-invalid-one", "GROQ_API_KEY_2": "test-valid-two", "GROQ_API_KEY_3": ""}
        calls = []
        def first_invalid(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, "private", {}, None)
            return self._json_response()
        with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", first_invalid):
            self.assertEqual(qwen.chat("auth fallback")[0], "SIAP")
        self.assertEqual(len(calls), 2)
        for status in (403, 400, 404, 422):
            calls.clear()
            def fail(request, timeout):
                calls.append(request)
                raise urllib.error.HTTPError(request.full_url, status, "private", {}, None)
            with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", fail):
                with self.assertRaises(qwen.GroqError):
                    qwen.chat("no rotation")
            self.assertEqual(len(calls), 1)

    def test_yomi_timeout_has_no_retry_and_parallel_requests_are_bounded(self):
        import os
        from concurrent.futures import ThreadPoolExecutor
        from modules.auto import qwen, yomi
        keys = {"GROQ_API_KEY": "test-parallel-one", "GROQ_API_KEY_2": "test-parallel-two", "GROQ_API_KEY_3": "test-parallel-three"}
        calls = []
        lock = __import__("threading").Lock()
        def response(request, timeout):
            with lock:
                calls.append(request.get_header("Authorization"))
            return self._json_response()
        with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", response):
            with ThreadPoolExecutor(max_workers=12) as pool:
                self.assertEqual(len(list(pool.map(lambda _: qwen.chat("parallel"), range(12)))), 12)
        self.assertEqual(len(calls), 12)
        self.assertEqual(set(calls), {"Bearer test-parallel-one", "Bearer test-parallel-two", "Bearer test-parallel-three"})
        timeout_calls = []
        def timeout(request, timeout):
            timeout_calls.append(request)
            raise TimeoutError()
        with patch.dict(os.environ, keys), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", timeout):
            with self.assertRaisesRegex(qwen.GroqError, "melewati batas waktu"):
                qwen.chat("timeout")
        self.assertEqual(len(timeout_calls), 1)

    def test_auto_progress_stores_yomi_events_without_changing_percent(self):
        from modules.auto import yomi
        now = backend.now_iso()
        with tempfile.TemporaryDirectory(dir=backend.ROOT / "data") as temp_dir:
            test_db = Path(temp_dir) / "yomi-progress.sqlite3"
            with patch.object(backend, "DB_PATH", test_db):
                backend.initialize_db()
                with backend.connect_db() as db:
                    db.execute("INSERT INTO auto_cycles(id,production_date,topic,engine_id,target_unique,format_mode,status,created_at,started_at) VALUES('cycle','2026-11-12','test','groq_qwen',1,'portrait','running',?,?)", (now, now))
                    db.execute("INSERT INTO auto_queue(id,cycle_id,position,topic,status,progress,created_at,updated_at) VALUES('job','cycle',1,'test','processing',17,?,?)", (now, now))
                backend.auto_llm_call("script", lambda: yomi.emit("YOMI: API Key 2 berhasil"), cycle_id="cycle", item_id="job")
                with backend.connect_db() as db:
                    row = db.execute("SELECT progress,progress_detail FROM auto_queue WHERE id='job'").fetchone()
                self.assertEqual(row["progress"], 17)
                self.assertEqual(row["progress_detail"], "YOMI: API Key 2 berhasil")

    def test_auto_stops_cycle_and_cancels_jobs_when_all_mock_keys_are_limited(self):
        import os
        from modules.auto import qwen, yomi
        now = backend.now_iso()
        def limited(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "private response", {"Retry-After": "90"}, None)
        env = {"GROQ_API_KEY": "test-auto-stop-one", "GROQ_API_KEY_2": "test-auto-stop-two", "GROQ_API_KEY_3": "test-auto-stop-three"}
        with tempfile.TemporaryDirectory(dir=backend.ROOT / "data") as temp_dir:
            test_db = Path(temp_dir) / "yomi-stop.sqlite3"
            with patch.object(backend, "DB_PATH", test_db), patch.dict(os.environ, env), patch.object(yomi, "_NEXT", 0), patch.object(qwen.urllib.request, "urlopen", limited):
                backend.initialize_db()
                with backend.connect_db() as db:
                    db.execute("INSERT INTO auto_cycles(id,production_date,topic,engine_id,target_unique,format_mode,status,created_at,started_at) VALUES('cycle','2026-11-12','test','groq_qwen',2,'portrait','running',?,?)", (now, now))
                    db.execute("INSERT INTO auto_queue(id,cycle_id,position,topic,status,created_at,updated_at) VALUES('job1','cycle',1,'test','processing',?,?)", (now, now))
                    db.execute("INSERT INTO auto_queue(id,cycle_id,position,topic,status,created_at,updated_at) VALUES('job2','cycle',2,'test','queued',?,?)", (now, now))
                with self.assertRaisesRegex(qwen.GroqError, "Semua API Key terkena rate limit") as raised:
                    backend.auto_llm_call("script", lambda: qwen.chat("mock"), cycle_id="cycle", item_id="job1")
                backend._handle_auto_item_failure({"id": "job1", "cycle_id": "cycle"}, raised.exception)
                with backend.connect_db() as db:
                    cycle = db.execute("SELECT status,error FROM auto_cycles WHERE id='cycle'").fetchone()
                    jobs = db.execute("SELECT status,error,progress_detail FROM auto_queue ORDER BY position").fetchall()
                self.assertEqual(cycle["status"], "error")
                self.assertIn("Semua API Key terkena rate limit", cycle["error"])
                self.assertEqual([job["status"] for job in jobs], ["failed", "cancelled"])
                self.assertIn("YOMI: Semua API Key terkena rate limit", jobs[0]["progress_detail"])
                self.assertIn("YOMI: Siklus AUTO dihentikan", jobs[0]["progress_detail"])
                self.assertIn("Semua API Key terkena rate limit", jobs[1]["error"])

    def test_groq_chat_uses_qwen_model_endpoint_headers_and_limits(self):
        import json
        import os
        from modules.auto import qwen

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, _limit):
                return json.dumps({"choices": [{"message": {"content": "SIAP"}}], "usage": {"total_tokens": 7}}).encode()

        captured = {}
        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["auth"] = request.get_header("Authorization")
            captured["user_agent"] = request.get_header("User-agent")
            return Response()

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-secret-key", "GROQ_MODEL": "qwen/qwen3.8-27b", "GROQ_BASE_URL": "https://example.test/openai/v1", "GROQ_MAX_OUTPUT_TOKENS": "333"}), patch.object(qwen.urllib.request, "urlopen", fake_urlopen):
            content, usage = qwen.chat("test")
        self.assertEqual(content, "SIAP")
        self.assertEqual(usage["total_tokens"], 7)
        self.assertEqual(captured["url"], "https://example.test/openai/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "qwen/qwen3.8-27b")
        self.assertEqual(captured["body"]["max_tokens"], 333)
        self.assertEqual(captured["auth"], "Bearer test-secret-key")
        self.assertEqual(captured["user_agent"], "OLYPHANTS-VIDEO-STUDIO/1.0")

    def test_groq_missing_credentials_fails_without_request(self):
        import os
        from modules.auto import qwen
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "GROQ_API_KEY_2": "", "GROQ_API_KEY_3": "", "GROQ_BASE_URL": "https://api.groq.com/openai/v1"}):
            with self.assertRaisesRegex(qwen.GroqError, "Tidak ada API Key"):
                qwen.chat("test")

    def test_qwen_json_output_accepts_markdown_fences(self):
        from modules.auto import qwen
        self.assertEqual(qwen._decode_json_response('```json\n{"title":"Judul"}\n```', "{", "}"), {"title": "Judul"})
        self.assertEqual(qwen._decode_json_response('Berikut hasilnya: ["kata satu"]', "[", "]"), ["kata satu"])

    def test_groq_http_errors_are_safe_and_not_retried(self):
        import os
        from modules.auto import qwen

        for status, expected in ((401, "Semua API Key ditolak"), (403, "Akses Groq"), (429, "Semua API Key terkena rate limit"), (404, "model")):
            calls = []
            def fail_once(request, timeout):
                calls.append(request)
                raise urllib.error.HTTPError(request.full_url, status, "private response", {}, None)
            secret = f"test-secret-key-{status}"
            with patch.dict(os.environ, {"GROQ_API_KEY": secret, "GROQ_API_KEY_2": "", "GROQ_API_KEY_3": "", "GROQ_BASE_URL": "https://api.groq.com/openai/v1"}), patch.object(qwen.urllib.request, "urlopen", fail_once):
                with self.assertRaisesRegex(qwen.GroqError, expected) as raised:
                    qwen.chat("test")
            self.assertEqual(len(calls), 1)
            self.assertNotIn(secret, str(raised.exception))

    def test_auto_tts_provider_accepts_only_edge_or_cartesia(self):
        self.assertEqual(validate_settings({"auto_tts_provider": "cartesia"})["auto_tts_provider"], "cartesia")
        self.assertEqual(validate_settings({"auto_tts_provider": "edge"})["auto_tts_provider"], "edge")
        with self.assertRaisesRegex(ValueError, "auto_tts_provider"):
            validate_settings({"auto_tts_provider": "other"})

    def test_target_bounds_and_formats(self):
        self.assertEqual(validate_settings({"auto_daily_target": 1})["auto_daily_target"], 1)
        self.assertEqual(validate_settings({"auto_daily_target": 24})["auto_daily_target"], 24)
        for target in (0, 25):
            with self.assertRaises(ValueError):
                validate_settings({"auto_daily_target": target})
        for mode in ("landscape", "portrait", "both"):
            self.assertEqual(validate_settings({"auto_video_format": mode})["auto_video_format"], mode)

    def test_auto_video_duration_presets(self):
        for duration in (15, 30, 45, 60):
            self.assertEqual(validate_settings({"auto_video_duration_seconds": duration})["auto_video_duration_seconds"], duration)
        for duration in (0, 14, 61, "long"):
            with self.assertRaisesRegex(ValueError, "Durasi AUTO"):
                validate_settings({"auto_video_duration_seconds": duration})

    def test_completed_test_mode_video_does_not_consume_daily_production_target(self):
        from zoneinfo import ZoneInfo
        today = backend.datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat()
        now = backend.now_iso()
        with tempfile.TemporaryDirectory(dir=backend.ROOT / "data") as temp_dir:
            test_db = Path(temp_dir) / "test-mode-target.sqlite3"
            with patch.object(backend, "DB_PATH", test_db):
                backend.initialize_db()
                backend.save_auto_settings({"auto_daily_target": 1, "auto_video_format": "portrait"})
                with backend.connect_db() as db:
                    db.execute("INSERT INTO auto_cycles(id,production_date,topic,engine_id,target_unique,format_mode,status,is_additional,test_mode,created_at,started_at,finished_at) VALUES('test-cycle',?,'test','groq_qwen',1,'portrait','complete',1,1,?,?,?)", (today, now, now, now))
                    db.execute("INSERT INTO auto_queue(id,cycle_id,position,topic,status,outputs_json,created_at,updated_at,finished_at) VALUES('test-job','test-cycle',1,'test','complete','[]',?,?,?)", (now, now, now))
                with patch.object(backend, "start_auto_worker"):
                    cycle = backend._new_auto_cycle("production topic", "")
                self.assertEqual(cycle["target_unique"], 1)

    def test_lemo_source_styles_are_allowlisted_and_subtitles_default_off(self):
        self.assertFalse(backend.DEFAULT_SETTINGS["auto_subtitle_enabled"])
        for style in ("kino-montage", "dark-documentary", "cel-anime-80s", "midcentury-toon", "brick-toy", "swiss-motion", "iso-infographic", "pixel-rpg", "watercolor", "isometric-picture-book"):
            self.assertEqual(validate_settings({"auto_visual_style": style})["auto_visual_style"], style)
            self.assertEqual(validate_settings({"visual_style": style})["visual_style"], style)
        with self.assertRaisesRegex(ValueError, "Gaya visual harus"):
            validate_settings({"auto_visual_style": "cinematic"})

    def test_qwen_visual_plan_receives_selected_lemo_style(self):
        import json
        result = [{"heading": "Gagasan utama", "visual_type": "illustration", "visual_mode": "generated", "visual_prompt": "pixel art", "illustrative_only": True, "transition": "cut"}]
        with patch.object(qwen, "chat", return_value=(json.dumps(result), {})) as mocked:
            qwen.generate_visual_plan("topik uji", ["narasi uji"], "pixel-rpg", "16-bit Pixel RPG")
        self.assertIn("GAYA VISUAL: 16-bit Pixel RPG (pixel-rpg)", mocked.call_args.args[0])

    def test_auto_visual_plan_selects_a_coherent_mix_of_scene_styles(self):
        import json
        styles = {"watercolor": "Watercolor Brush", "iso-infographic": "Isometric Infographic", "dark-documentary": "Radar Sweep Documentary"}
        segments = ["Kanal ini dibangun melalui kerja besar.", "Proses penggalian membentuk rute air.", "Kapal kemudian melewati jalur tersebut."]
        plan = [{"heading": "Kanal", "visual_type": "illustration", "visual_mode": "generated", "visual_prompt": "kanal", "visual_style": "watercolor"} for _ in segments]
        with patch.object(qwen, "chat", return_value=(json.dumps(plan), {})) as mocked:
            result = qwen.generate_visual_plan("Kanal Panama", segments, "watercolor", "Watercolor Brush", style_options=styles)
        self.assertIn('"visual_style"', mocked.call_args.args[0])
        self.assertIn("iso-infographic", mocked.call_args.args[0])
        self.assertEqual(len(result), len(segments))
        self.assertGreaterEqual(len({item["visual_style"] for item in result}), 2)
        self.assertLessEqual(len({item["visual_style"] for item in result}), 3)
        self.assertTrue(all(item["visual_style"] in styles for item in result))

    def test_auto_visual_plan_has_no_fixed_anchor_when_qwen_selects_styles(self):
        import json
        styles = {"watercolor": "Watercolor Brush", "iso-infographic": "Isometric Infographic"}
        response = [{"heading": "Bagian", "visual_type": "illustration", "visual_mode": "generated", "visual_prompt": "ilustrasi", "visual_style": "watercolor"}]
        with patch.object(qwen, "chat", return_value=(json.dumps(response), {})) as mocked:
            qwen.generate_visual_plan("topik", ["narasi"], "", "auto", style_options=styles)
        prompt = mocked.call_args.args[0]
        self.assertIn("Tidak ada gaya acuan tetap", prompt)
        self.assertNotIn("boleh menjadi gaya utama", prompt)

    def test_qwen_can_write_cautious_topic_script_without_ddgs(self):
        with patch.object(qwen, "chat", return_value=("Naskah edukasi singkat.", {})) as mocked:
            script = qwen.generate_script("Pembangunan Terusan Panama", 1)
        self.assertEqual(script, "Naskah edukasi singkat.")
        prompt = mocked.call_args.args[0]
        self.assertIn("pengetahuan umum yang mapan", prompt)
        self.assertIn("jangan mengarang kutipan, angka, tanggal", prompt.lower())

    def test_auto_rejects_unknown_and_qwen_settings(self):
        with self.assertRaises(ValueError):
            validate_settings({"auto_qwen_model": "qwen3:local"})
        self.assertEqual(validate_settings({"auto_visual_style": "auto"})["auto_visual_style"], "auto")

    def test_auto_visual_style_choices_are_validated_as_a_nonempty_catalog_subset(self):
        from backend.main import LEMO_STYLE_OPTIONS, validate_settings

        chosen = ["whiteboard-explainer-no-hands", "ukiyo-e"]
        self.assertEqual(validate_settings({"auto_visual_styles": chosen})["auto_visual_styles"], chosen)
        with self.assertRaises(ValueError):
            validate_settings({"auto_visual_styles": []})
        with self.assertRaises(ValueError):
            validate_settings({"auto_visual_styles": ["unknown-style"]})
        self.assertEqual(len(LEMO_STYLE_OPTIONS), 20)

    def test_ai_engine_registry_exposes_groq_provider_with_qwen_model(self):
        from modules.auto.engines import ENGINE_REGISTRY, registered_engines
        self.assertEqual(list(ENGINE_REGISTRY), ["groq_qwen"])
        self.assertEqual(ENGINE_REGISTRY["groq_qwen"]["label"], "YOMI ENGINE 1.0")
        with patch.dict("os.environ", {"GROQ_API_KEY": "", "GROQ_API_KEY_2": "", "GROQ_API_KEY_3": "", "GROQ_BASE_URL": "https://api.groq.com/openai/v1"}):
            result = registered_engines({"auto_engine_id": "groq_qwen"})
        self.assertEqual([engine["id"] for engine in result["engines"]], ["groq_qwen"])
        self.assertEqual(result["selected_engine_id"], "groq_qwen")
        self.assertEqual(result["engines"][0]["provider"], "Groq")
        self.assertEqual(result["engines"][0]["model"], "qwen/qwen3.8-27b")
        self.assertEqual(result["engines"][0]["status"], "blocked")

    def test_engine_selection_is_backend_allowlisted(self):
        self.assertEqual(validate_settings({"auto_engine_id": "groq_qwen"})["auto_engine_id"], "groq_qwen")
        with self.assertRaises(ValueError):
            validate_settings({"auto_engine_id": "qwen"})

    def test_engine_selection_persists_in_auto_settings(self):
        with tempfile.TemporaryDirectory(dir=backend.ROOT / "data") as temp_dir:
            test_db = Path(temp_dir) / "engine-settings.sqlite3"
            with patch.object(backend, "DB_PATH", test_db):
                backend.initialize_db()
                backend.save_auto_settings({"auto_engine_id": "groq_qwen"})
                self.assertEqual(backend.read_settings()["auto_engine_id"], "groq_qwen")

    def test_dashboard_has_only_backend_registered_engine_ui(self):
        frontend = (backend.ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/auto/engines", frontend)
        self.assertIn("AI ENGINE", frontend)
        self.assertIn("YOMI ENGINE 1.0", frontend)
        self.assertIn("item.progress_detail", frontend)
        self.assertNotIn("YOMI Monitoring", frontend)
        self.assertIn("/auto/groq-test", frontend)
        self.assertNotIn("Alibaba Cloud Model Studio", frontend)
        self.assertNotIn("MoneyPrinterTurbo dan LLM bawaan", frontend)
        self.assertNotIn("name=\"auto_llm_provider\"", frontend)
        self.assertNotIn("name=\"auto_visual_style\"", frontend)
        self.assertIn("Pilih satu atau beberapa", frontend)
        self.assertIn("Qwen memilih gaya yang paling cocok dari pilihanmu", frontend)

    def test_manual_mpt_worker_still_requires_supplied_script(self):
        source = (backend.ROOT / "modules" / "auto" / "mpt_worker.py").read_text(encoding="utf-8")
        self.assertIn("A supplied script is required; MPT LLM fallback is disabled", source)
        backend_source = (backend.ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn("from modules.cartesia.client import CartesiaError, synthesize", backend_source)
        self.assertIn('"cartesia_tts"', backend_source)

    def test_groq_failure_cancels_remaining_auto_jobs(self):
        from modules.auto.qwen import GroqError
        now = backend.now_iso()
        with tempfile.TemporaryDirectory(dir=backend.ROOT / "data") as temp_dir:
            test_db = Path(temp_dir) / "groq-failure.sqlite3"
            with patch.object(backend, "DB_PATH", test_db):
                backend.initialize_db()
                with backend.connect_db() as db:
                    db.execute("INSERT INTO auto_cycles(id,production_date,topic,engine_id,target_unique,format_mode,status,created_at,started_at) VALUES('cycle','2026-11-12','test','groq_qwen',2,'portrait','running',?,?)", (now, now))
                    db.execute("INSERT INTO auto_queue(id,cycle_id,position,topic,status,created_at,updated_at) VALUES('job1','cycle',1,'test','processing',?,?)", (now, now))
                    db.execute("INSERT INTO auto_queue(id,cycle_id,position,topic,status,created_at,updated_at) VALUES('job2','cycle',2,'test','queued',?,?)", (now, now))
                backend._handle_auto_item_failure({"id": "job1", "cycle_id": "cycle"}, GroqError("Groq rate limit"))
                with backend.connect_db() as db:
                    cycle = db.execute("SELECT status FROM auto_cycles WHERE id='cycle'").fetchone()
                    statuses = [row["status"] for row in db.execute("SELECT status FROM auto_queue WHERE cycle_id='cycle' ORDER BY position")]
                    stopped_detail = db.execute("SELECT progress_detail FROM auto_queue WHERE id='job1'").fetchone()["progress_detail"]
                self.assertEqual(cycle["status"], "error")
                self.assertEqual(statuses, ["failed", "cancelled"])
                self.assertIn("YOMI: Siklus AUTO dihentikan", stopped_detail)

    def test_auto_llm_daily_request_cap_stops_before_provider_call(self):
        import os
        now = backend.now_iso()
        called = []
        with tempfile.TemporaryDirectory(dir=backend.ROOT / "data") as temp_dir:
            test_db = Path(temp_dir) / "groq-request-cap.sqlite3"
            with patch.object(backend, "DB_PATH", test_db), patch.dict(os.environ, {"GROQ_DAILY_REQUEST_LIMIT": "1"}):
                backend.initialize_db()
                with backend.connect_db() as db:
                    db.execute("INSERT INTO auto_llm_usage(id,provider,model,stage,status,created_at) VALUES('prior','groq','qwen/qwen3.8-27b','prior','success',?)", (now,))
                from modules.auto.qwen import GroqError
                with self.assertRaisesRegex(GroqError, "Batas GROQ_DAILY_REQUEST_LIMIT"):
                    backend.auto_llm_call("script", lambda: called.append(True))
                self.assertEqual(called, [])

    def test_ddgs_research_deduplicates_and_requires_text_sources(self):
        from modules.auto import research

        class DDGS:
            def __init__(self, **kwargs):
                pass

            def text(self, query, max_results):
                return [
                    {"title": "A", "body": "desc", "href": "https://a.test"},
                    {"title": "A duplicate", "href": "https://a.test"},
                    {"title": "B", "href": "https://b.test"},
                ]

            def images(self, query, max_results):
                return [{"title": "image", "image": "https://cdn.test/a.jpg", "url": "https://source.test"}]

            def videos(self, query, max_results):
                return [{"title": "video", "content": "https://video.test"}]

        with patch.object(research, "DDGS", DDGS):
            result = research.search("tema", validate=False)
        self.assertEqual(len(result["text"]), 2)
        self.assertEqual(len(result["images"]), 1)
        self.assertEqual(len(result["videos"]), 1)
        self.assertEqual(result["images"][0]["media_url"], "https://cdn.test/a.jpg")

    def test_url_validation_rejects_private_and_non_http_urls(self):
        from modules.auto.research import validate_url
        self.assertEqual(validate_url("file:///etc/passwd")["status"], "unsafe_or_invalid")
        self.assertEqual(validate_url("http://127.0.0.1/admin")["status"], "unsafe_or_invalid")

    def test_url_validation_reports_network_failure(self):
        from modules.auto import research

        class Opener:
            def open(self, request, timeout):
                raise urllib.error.URLError("offline")

        with patch.object(research, "_public_http_url", return_value=(True, "")), patch.object(research.urllib.request, "build_opener", return_value=Opener()):
            result = research.validate_url("https://example.com/article")
        self.assertEqual(result["status"], "unreachable")
        self.assertEqual(result["reason"], "network_error")

    def test_image_result_requires_image_content_type(self):
        from modules.auto import research
        with patch.object(research, "validate_url", return_value={"status": "valid", "reason": "", "http_status": 200, "content_type": "text/html"}):
            result = research._normalize_results("images", [{"url": "https://example.com", "image": "https://example.com/not-really-image"}], validate=True)
        self.assertEqual(result[0]["media_url_status"], "invalid_media_type")

    def test_ddgs_failure_returns_no_fabricated_results(self):
        from modules.auto import research

        class BrokenDDGS:
            def __init__(self, **kwargs):
                pass

            def text(self, query, **kwargs):
                raise RuntimeError("network unavailable")

        with patch.object(research, "DDGS", BrokenDDGS):
            result = research.search_kind("real topic", "text", validate=False)
        self.assertEqual(result["results"], [])
        self.assertIn("DDGS text search failed", result["error"])

    def test_research_query_uses_script_terms(self):
        from modules.auto.research import _make_query
        query = _make_query("Tema utama", "Arsitektur kolonial menggunakan batu kapur lokal", ["bangunan bersejarah", "detail batu kapur"])
        self.assertIn("Tema utama", query)
        self.assertIn("arsitektur", query)
        self.assertIn("kolonial", query)
        self.assertIn("bangunan bersejarah", query)
        self.assertIn("detail batu kapur", query)

    def test_ddgs_search_receives_groq_visual_keywords(self):
        from modules.auto import research
        received = []
        def fake_search(topic, kind, script, limit, **kwargs):
            received.append(kwargs.get("keywords"))
            return {"results": [], "error": ""}
        with patch.object(research, "search_kind", fake_search):
            result = research.search("Tema", script="Naskah", keywords=["sawah hijau", "petani panen"], validate=False)
        self.assertEqual(received, [["sawah hijau", "petani panen"]] * 3)
        self.assertIn("sawah hijau", result["query"])


if __name__ == "__main__":
    unittest.main()
