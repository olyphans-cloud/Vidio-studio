# FULL REPOSITORY AUDIT — OLYPHANTS VIDEO STUDIO

**Repository:** https://github.com/olyphans-cloud/Vidio-studio.git  
**Branch diaudit:** `import/olyphants-video-studio-20261117`  
**Commit diaudit:** `e345ce804cf5dbab00d8872f16ddb06c3958a194`  
**Commit main (tidak diubah):** `a4daaff6ff60f5b2a32893d1c4747b097c8790da`  
**Tanggal audit:** 2026-11-17  
**Sifat audit:** READ-ONLY terhadap source; laporan ini adalah satu-satunya file baru.

---

## 0. Ringkasan Eksekutif

- Branch remote tersedia dan commit remote **identik** dengan commit lokal (`e345ce8`).
- Seluruh **46 file** yang diimpor terverifikasi ada di remote; perbandingan isi direktori lokal vs clone remote tidak menemukan perbedaan.
- Branch `main` tidak berubah (`a4daaff`).
- Tidak ditemukan API key, token, password, private key, atau credential URL di working tree maupun riwayat commit.
- **46 unit test** tersedia. Pada clone bersih, 2 test error karena database belum diinisialisasi; setelah `initialize_db()`, seluruh 46 test lulus.
- Temuan utama: repository ini **tidak menyertakan `vendor/` (MoneyPrinterTurbo)**, padahal README menyatakan source MPT tersedia untuk kompatibilitas; beberapa pengaturan UI tidak sesuai dengan implementasi renderer; dan belum ada autentikasi/CSRF pada API lokal.
- Render Remotion nyata dan pengujian HTTP end-to-end **belum dijalankan** dalam audit ini.

---

## 1. Verifikasi GitHub

| Pemeriksaan | Hasil | Bukti |
|---|---|---|
| Branch remote tersedia | Ya | `refs/heads/import/olyphants-video-studio-20261117` |
| Commit remote = commit lokal | Ya | Keduanya `e345ce804cf5dbab00d8872f16ddb06c3958a194` |
| Jumlah file remote | 46 | `git ls-files \| wc -l` |
| Ukuran source remote | ±740 KB | `du -sh --exclude=.git` |
| Perbandingan daftar file | Identik | `diff` daftar `git ls-tree -r --name-only` |
| Perbandingan isi file | Identik | `diff -r --exclude=.git` |
| Branch main | Tidak berubah | `a4daaff6ff60f5b2a32893d1c4747b097c8790da` |
| File sensitif di remote | Tidak ada | Tidak ada `.env`, `*.sqlite3`, `*.db`, `*.log`, `*.pem`, `*.key`, `node_modules`, `.venv`, `vendor`, `data`, `output` |
| Scan rahasia working tree | Bersih | Pola `sk-`, `gsk_`, `ghp_`, `github_pat_`, `xox*`, `AKIA`, private-key header, credential URL |
| Scan rahasia riwayat commit | Bersih | `git log -p --all` terhadap pola yang sama |

**Kesimpulan verifikasi:** import source berhasil dan lengkap untuk lingkup file first-party yang disepakati. Tidak ada file first-party yang tertinggal.

**Yang sengaja dikecualikan dari import (bukan kegagalan):**

- `.env` dan seluruh kredensial
- `.git` (termasuk `.git` bersarang di vendor)
- `.venv`, `node_modules`, `__pycache__`, `*.pyc`
- `data/` (SQLite, log, backup, IPC runtime)
- `output/` (hasil render, audio, fixture)
- `vendor/` (MoneyPrinterTurbo 342 MB, `mpt-venv` 1,2 GB, `mpt-runtime` 202 MB)
- `renderer/remotion/public/.runs/` (cache render 17 MB)

---

## 2. Struktur Repository

```
OLYPHANTS_VIDEO_STUDIO/
├── .env.example                  # contoh konfigurasi, tanpa nilai rahasia
├── .gitignore
├── README.md
├── requirements.txt
├── backend/
│   ├── __init__.py
│   └── main.py                   # 2.832 baris: HTTP server, DB, MANUAL, AUTO, worker
├── frontend/
│   ├── index.html                # SPA shell
│   ├── css/app.css
│   ├── css/menu.css
│   └── js/app.js                 # 204 baris: dashboard SPA
├── modules/
│   ├── __init__.py
│   ├── audio_analyzer/
│   │   ├── __init__.py
│   │   └── segmenter.py          # segmentasi kata (tidak diimpor modul aktif)
│   ├── auto/
│   │   ├── __init__.py
│   │   ├── engines.py            # registry AI ENGINE
│   │   ├── mpt.py                # adapter MoneyPrinterTurbo (legacy/opsional)
│   │   ├── mpt_worker.py         # worker IPC MPT (legacy)
│   │   ├── qwen.py               # klien Qwen via Groq
│   │   ├── research.py           # DDGS + validasi URL anti-SSRF
│   │   └── yomi.py               # rotasi 3 API key Groq
│   ├── cartesia/
│   │   ├── __init__.py
│   │   └── client.py             # TTS Cartesia SSE
│   ├── ffmpeg/
│   │   ├── __init__.py
│   │   └── renderer.py           # quality_check FFprobe + legacy render_and_qc
│   ├── hyperframes/
│   │   ├── __init__.py
│   │   └── composition.py        # legacy HTML composition (tidak diimpor)
│   ├── lemo/
│   │   └── style_catalog.json    # 20 gaya visual
│   ├── openai_engine.py          # legacy OpenAI service (tidak diimpor)
│   └── remotion/
│       ├── __init__.py
│       ├── edge_tts_worker.py    # Edge TTS + WordBoundary
│       ├── render_manager.py     # staging audio/media + render + QC
│       ├── renderer.py           # bridge Python -> Node
│       ├── scene_generator.py    # pembersihan naskah, timing scene
│       └── tts.py                # dispatch Edge/Cartesia
├── renderer/remotion/
│   ├── package.json
│   ├── package-lock.json         # lockfileVersion 3, 221 paket
│   ├── render.mjs                # bundler + selectComposition + renderMedia
│   └── src/
│       ├── index.tsx
│       ├── root.tsx
│       ├── video.tsx
│       ├── visuals.tsx
│       ├── lemo-style-scene.tsx
│       ├── lemo-watercolor.tsx
│       └── types.ts
└── tests/
    ├── test_auto.py              # 37 test
    └── test_remotion.py          # 9 test
```

### Daftar file penting

| File | Baris | Peran |
|---|---:|---|
| `backend/main.py` | 2.832 | Inti API, SQLite, orkestrasi MANUAL/AUTO, worker/scheduler, static server |
| `modules/auto/qwen.py` | 326 | Naskah, metadata, rencana visual, kata kunci via Groq |
| `modules/auto/yomi.py` | 148 | Rotasi kunci Groq, cooldown 429, status aman |
| `modules/auto/engines.py` | 38 | Registry `groq_qwen` = YOMI ENGINE 1.0 |
| `modules/auto/research.py` | 328 | DDGS search, validasi URL, unduhan media tervalidasi |
| `modules/auto/mpt.py` | 543 | Adapter MPT v1 + runtime internal (legacy) |
| `modules/auto/mpt_worker.py` | 113 | Worker IPC MPT (legacy) |
| `modules/cartesia/client.py` | 158 | TTS Cartesia `sonic-3.6` + word timestamps |
| `modules/remotion/tts.py` | 124 | Dispatch Edge TTS / Cartesia |
| `modules/remotion/edge_tts_worker.py` | 38 | Sintesis Edge TTS + timestamp kata |
| `modules/remotion/render_manager.py` | 174 | Staging, normalisasi audio, frame scenes, render, QC |
| `modules/remotion/renderer.py` | 65 | Cek Node/Chrome, bundle, eksekusi `render.mjs` |
| `modules/remotion/scene_generator.py` | 133 | `clean_spoken_script`, `split_narration`, `time_scenes`, inferensi visual |
| `modules/ffmpeg/renderer.py` | 80 | `quality_check` FFprobe; `render_and_qc` legacy |
| `modules/lemo/style_catalog.json` | 237 | 20 gaya visual |
| `renderer/remotion/render.mjs` | — | Render H.264/yuv420p/crf 20, progress file |
| `renderer/remotion/src/video.tsx` | 34 | `TransitionSeries`, pemilihan gaya per scene, audio |
| `renderer/remotion/src/visuals.tsx` | 106 | Scene generatif (kinetic, chart, map, UI, media, hybrid) |
| `renderer/remotion/src/lemo-style-scene.tsx` | 134 | 19 slug gaya Lemo + tema |
| `frontend/js/app.js` | 204 | Dashboard: New Video, AUTO, Settings, Integrations, Approval |
| `tests/test_auto.py` | 511 | 37 test AUTO, YOMI, settings, DDGS |
| `tests/test_remotion.py` | 164 | 9 test katalog, timing, manifest, audio normalizer |

---

## 3. Arsitektur Sistem

```
┌─────────────────────────────────────────────────────────────┐
│ Browser SPA (frontend/index.html + js/app.js + css)         │
│ Overview · New Video · AUTO · Settings · Integrations       │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP JSON / multipart
┌───────────────────────────▼─────────────────────────────────┐
│ Python stdlib ThreadingHTTPServer (backend/main.py)         │
│ - Routing /api/*, validasi, static file, range video        │
│ - SQLite: data/studio.sqlite3                               │
│ - Worker AUTO + scheduler (thread daemon)                   │
└───────┬───────────────────────┬─────────────────────────────┘
        │ MANUAL                │ AUTO
        ▼                       ▼
 Cartesia TTS            YOMI/Qwen (Groq)
        │                       │ naskah + rencana visual
        ▼                       ▼
 FFmpeg audio            Edge TTS / Cartesia TTS
        │                       │
        └──────────┬────────────┘
                   ▼
        Remotion (Node 22+, Chrome)
        renderer/remotion/render.mjs
                   ▼
        FFprobe QC (modules/ffmpeg/renderer.py)
                   ▼
        output/<project|auto>/...mp4

Opsional/legacy:
- MoneyPrinterTurbo adapter (vendor tidak ada di repo)
- DDGS research (route /api/auto/research)
- OpenAI engine (tidak diimpor)
- Hyperframes renderer (tidak diimpor)
```

**Penyimpanan runtime:** `data/studio.sqlite3` (dibuat otomatis oleh `connect_db()`), `output/` untuk audio/media/video, `renderer/remotion/public/.runs/` untuk staging render.

**Eksternal:** Groq API (AUTO), Cartesia API (MANUAL, opsional AUTO), Edge TTS (jaringan Microsoft), DDGS (opsional), Chrome/Chromium, FFmpeg/FFprobe.

---

## 4. Alur Produksi Video

### 4.1 MANUAL (Cartesia + Remotion)

1. **Buat proyek** — `POST /api/projects` → tabel `projects`.
2. **Isi naskah/topik/gaya** — `PUT /api/projects/:id`.
3. **Buat/atur scene** — `POST /api/projects/:id/scenes`, `POST /api/projects/:id/scenes/auto` (YOMI membuat arahan visual), upload media `POST /api/projects/:id/scenes/:sid/media`.
4. **Produksi** — `POST /api/projects/:id/produce` → `start_manual_production()` (`backend/main.py:1815`).
5. **TTS Cartesia** — `create_audio_asset()` (`:1595`) → `modules/cartesia/client.py`.
6. **Normalisasi audio** — `process_audio()` (`:1801`) → FFmpeg mono 48 kHz PCM.
7. **Manifest Remotion** — `create_project_composition()` (`:1728`): timing dari word alignment (`time_scenes`), media opsional, visual generated jika tidak ada media.
8. **Render** — `modules_render()` (`:1892`) → `render_manager.render_composition()` (`modules/remotion/render_manager.py:111`).
9. **Staging** — audio dinormalisasi ke durasi target (default 60 detik), media disalin ke `public/.runs/<run_id>`, `props.json` ditulis.
10. **Node/Remotion** — `modules/remotion/renderer.py` memanggil `renderer/remotion/render.mjs` → `bundle` + `selectComposition('OlyphantsVideo')` + `renderMedia` (H.264, yuv420p, crf 20).
11. **QC** — `quality_check()` (`modules/ffmpeg/renderer.py:14`) via FFprobe; status PASS/WARNING/FAILED.
12. **Simpan hasil** — tabel `jobs`, artifact `output/<project>/final-<job>.mp4`, endpoint video/unduh.

### 4.2 AUTO (YOMI/Qwen + Edge/Cartesia + Remotion)

1. **Konfigurasi** — `POST /api/auto/apply` → `apply_auto()` (`backend/main.py:937`): validasi settings, engine `groq_qwen`, API key Groq, TTS.
2. **Siklus** — `_new_auto_cycle()` (`:886`) membuat `auto_cycles` + antrean `auto_queue`.
3. **Worker** — `_auto_worker_loop()` (`:1303`) memilih item `queued/processing` lalu `_run_auto_item_internal()` (`:1172`).
4. **Naskah** — Qwen `generate_script()` (`modules/auto/qwen.py:149`) dengan durasi target 15/30/45/60 detik; `limit_narration_words()`.
5. **Bersihkan naskah** — `clean_spoken_script()` + `split_narration()` (`modules/remotion/scene_generator.py`).
6. **Rencana visual** — Qwen `generate_visual_plan()` (`qwen.py:208`) memilih tipe visual, mode, prompt, transisi, dan gaya dari katalog pilihan pengguna.
7. **TTS** — `synthesize_auto_tts()` (`modules/remotion/tts.py:109`): Edge TTS langsung atau Cartesia.
8. **Timing** — `time_scenes()` memakai timestamp kata dari TTS.
9. **Render** — manifest 30 fps, resolusi sesuai format (1080×1920 / 1920×1080), `target_duration_seconds` sesuai pilihan; `render_composition()`.
10. **QC** — `_validate_auto_video()` (`backend/main.py:1013`) memeriksa resolusi, audio, durasi.
11. **Selesai** — `_finish_auto_cycle()` (`:1058`) memperbarui status siklus; output di `output/auto/<cycle_id>/`.

### 4.3 Yang tidak dipakai pipeline AUTO aktif

- `_run_auto_item()` (`backend/main.py:1081`) adalah jalur MPT lama dan **tidak dipanggil**; worker hanya memanggil `_run_auto_item_internal()` (`:1316`).
- DDGS research dan MPT submission hanya ada di jalur lama tersebut atau route terpisah.
- `openai_engine.py`, `hyperframes/composition.py`, `audio_analyzer/segmenter.py`, `render_and_qc()` tidak diimpor/dipanggil oleh jalur aktif.

---

## 5. Hubungan Antar Modul

```
backend.main
 ├── modules.auto.qwen ──► modules.auto.yomi
 ├── modules.auto.engines ──► modules.auto.qwen
 ├── modules.auto.research (route /api/auto/research; jalur MPT lama)
 ├── modules.auto.mpt (route mpt-root/llm-providers; jalur MPT lama)
 ├── modules.cartesia.client (MANUAL TTS)
 ├── modules.remotion.scene_generator ──► modules.auto.qwen
 ├── modules.remotion.tts ──► edge_tts_worker / cartesia.client
 ├── modules.remotion.render_manager
 │    ├── modules.ffmpeg.renderer.quality_check
 │    └── modules.remotion.renderer ──► renderer/remotion/render.mjs
 └── modules.remotion.render_manager / renderer ──► backend.main (ROOT, read_settings)

frontend/js/app.js ──► /api/* (backend.main.Handler)
tests/* ──► backend.main, modules.auto, modules.remotion
```

Catatan: `modules/remotion/render_manager.py` dan `renderer.py` mengimpor `backend.main` (circular secara statis), tetapi import dilakukan di dalam fungsi sehingga tidak gagal saat startup.

---

## 6. Audit Keamanan

### 6.1 Hasil scan

| Pemeriksaan | Hasil |
|---|---|
| `.env` di repository | Tidak ada |
| API key/token/password literal | Tidak ditemukan |
| Private key / certificate | Tidak ditemukan |
| Credential di URL | Tidak ditemukan |
| Database/log/runtime | Tidak ada |
| Riwayat commit | Hanya 2 commit; tidak ada pola rahasia |
| `.env.example` | Hanya placeholder kosong dan nilai non-rahasia |

### 6.2 Kontrol keamanan yang sudah ada

- **Bind loopback saja** — `backend/main.py:2813-2818` menolak `OVS_HOST` selain `127.0.0.1`, `localhost`, `::1`.
- **Validasi path** — static frontend (`:1938`), media (`:2153`), video (`:1957`, `:2055`), audio (`:2215`), composition (`:1898`), DB (`:73-77`) memakai `is_relative_to`.
- **Upload media** — validasi MIME/isi file dan batas 100 MB (`:1647-1705`); nama file dibersihkan.
- **Anti-SSRF riset** — `research._public_http_url`/`validate_url` menolak skema non-HTTP dan alamat privat/loopback.
- **Redaksi log** — `log_message` membuang query string dan tidak mencatat body (`:1912-1914`).
- **Secret masking** — status secret dikembalikan sebagai boolean (`auto_secrets_configured`), bukan nilai.
- **`.env` lokal** — ditulis mode `0600` oleh `write_auto_secrets` (`:375-398`), tidak di-commit.

### 6.3 Risiko

1. **Tidak ada autentikasi/CSRF/Origin check** pada API lokal. Bind loopback membatasi akses jaringan, tetapi proses lokal atau halaman web dapat mencoba request (endpoint upload multipart adalah simple request). Tidak ada header CSP/HSTS/X-Frame-Options.
2. **`mpt.start_service()` meneruskan `os.environ` penuh** ke subprocess MPT (`modules/auto/mpt.py:383`), berbeda dari `_worker_env()` yang memfilter. Jalur ini legacy, tetapi berisiko jika diaktifkan.
3. **Token Telegram tidak digunakan**; `telegram_approval` eksplisit `not_implemented` (`backend/main.py:1469`). Menyimpan token tanpa fitur menambah permukaan rahasia.
4. **`.gitignore` belum mencakup** `vendor/`, `renderer/remotion/public/.runs/`, `*.sqlite3`, `*.db`, `*.log`; risiko commit tidak sengaja di masa depan.
5. **Backup `.env`** pernah ada di `data/backups/` pada mesin sumber (tidak masuk repo). Perlu kebijakan rotasi/penghapusan backup kredensial.
6. **`cartesia_connection_check()` tidak dipanggil**; status “configured” hanya mengecek keberadaan env/voice, bukan validitas kunci.

---

## 7. Audit Kelengkapan

### 7.1 Status modul

| Modul | Status | Catatan |
|---|---|---|
| AUTO pipeline (Qwen/Groq → TTS → Remotion) | Lengkap | Jalur aktif `_run_auto_item_internal` |
| MANUAL pipeline (Cartesia → FFmpeg → Remotion) | Lengkap | Backend mendukung scene tanpa media; UI membatasi |
| AI ENGINE / YOMI | Lengkap | Hanya `groq_qwen` terdaftar |
| Qwen adapter | Lengkap | Model default perlu verifikasi akun Groq |
| MoneyPrinterTurbo | **Tidak lengkap di repo** | Source `vendor/` dikecualikan; route MPT akan gagal |
| TTS Edge | Lengkap | `edge-tts` + worker timestamp |
| TTS Cartesia | Lengkap | SSE + word timestamps |
| Voice cloning | Tidak ada | Hanya pemilihan voice TTS; bukan cloning |
| Media Search / Research | Parsial | `research.py` lengkap; hanya route `/api/auto/research` dan jalur MPT lama |
| Remotion renderer | Lengkap | 20 gaya, 30 fps, H.264 |
| Renderer/QC | Lengkap | `quality_check` FFprobe; `render_and_qc` legacy |
| Dashboard | Lengkap untuk fitur inti | Approval/Integrations masih placeholder |
| Konfigurasi | Parsial | `.env.example` tidak memuat semua variabel |
| Tests | Lengkap secara unit | Tidak ada test HTTP/end-to-end/render nyata |
| Dokumentasi | Parsial | Beberapa klaim tidak sesuai implementasi |

### 7.2 Dependency

**Python (requirements.txt):**

- `ddgs==9.16.1` — hanya untuk `modules/auto/research.py` (route riset/jalur MPT lama)
- `edge-tts==7.3.1` — TTS Edge
- `openai>=2.0.0` — hanya `modules/openai_engine.py` yang tidak diimpor
- `pydantic>=2.0.0` — tidak ditemukan penggunaan langsung di source first-party
- `httpx>=0.28.0` — tidak ditemukan penggunaan langsung di source first-party
- **Python 3.11+** diperlukan karena `tomllib` (`modules/auto/mpt.py`) dan `is_relative_to`; tidak didokumentasikan di README.

**Node/Remotion (renderer/remotion/package.json):**

- `@remotion/bundler`, `@remotion/media`, `@remotion/renderer`, `@remotion/transitions` versi `4.0.543`
- `react`, `react-dom` versi `19.2.0`
- `remotion` versi `4.0.543`
- **Node.js 22+** diperlukan (dicek di `capabilities()`), tidak didokumentasikan di README.

**Sistem:**

- Chrome/Chromium (atau `OLYPHANTS_CHROME_PATH`)
- FFmpeg dan FFprobe
- Akses jaringan untuk Edge TTS, Groq, Cartesia (opsional), DDGS (opsional)

**Tidak ada di repo (harus disiapkan terpisah):**

- `vendor/dependencies/MoneyPrinterTurbo` + `mpt-venv` + `mpt-runtime`
- `data/` dan `output/` (dibuat runtime)
- `renderer/remotion/node_modules` (`npm install`)
- `.env` (dari `.env.example`)

### 7.3 Konfigurasi minimum agar berjalan

1. Salin `.env.example` → `.env`, isi:
   - `GROQ_API_KEY` (wajib AUTO dan rencana visual MANUAL)
   - `CARTESIA_API_KEY` + `CARTESIA_VOICE_ID` (wajib MANUAL; opsional AUTO)
   - `OVS_HOST=127.0.0.1`, `OVS_PORT=8765`, `OVS_DB_PATH=data/studio.sqlite3`
2. `.venv/bin/python -m pip install -r requirements.txt`
3. `npm --prefix renderer/remotion install`
4. Pastikan Node 22+, Chrome/Chromium, FFmpeg, FFprobe.
5. Jalankan `.venv/bin/python backend/main.py`.
6. Untuk fitur MPT legacy: sediakan `vendor/dependencies/MoneyPrinterTurbo` dan set `MPT_ROOT`/`MONEYPRINTERTURBO_ROOT`.

**Variabel yang dipakai kode tetapi tidak ada di `.env.example`:**

`GROQ_API_KEY_2`, `GROQ_API_KEY_3`, `GROQ_KEY_COOLDOWN_SECONDS`, `AUTO_LLM_API_KEY`, `MPT_API_KEY`, `MPT_ROOT`, `MONEYPRINTERTURBO_ROOT`, `OLYPHANTS_CHROME_PATH`, `OLLAMA_MODELS`.

### 7.4 Dokumentasi vs implementasi

| Klaim dokumentasi | Implementasi | Dampak |
|---|---|---|
| “Semua output Remotion disetel tepat 60 detik” (`README.md:17`) | AUTO mendukung 15/30/45/60 (`backend/main.py:327,630-637`, `:1266`); test `test_remotion_uses_selected_auto_video_duration` memverifikasi 30 detik | Dokumentasi menyesatkan |
| “AUTO dapat memilih Edge TTS dari runtime MoneyPrinterTurbo” (`README.md:3`) | AUTO aktif memakai `modules/remotion/tts.py` langsung; README lain (`:19`) menyatakan tanpa runtime MPT | Inkonsistensi internal |
| “Source dan konfigurasi MoneyPrinterTurbo lama tetap tersedia” (`README.md:19`) | `vendor/` tidak ada di repository | Fitur MPT tidak dapat dijalankan dari repo ini |
| “Visual dapat dibuat dari naskah tanpa media eksternal” (`README.md:3`) | Backend mendukung; **UI mensyaratkan setiap scene punya media** (`frontend/js/app.js:104`) | Alur MANUAL tanpa media terblokir di dashboard |
| “Tidak ada subtitle otomatis” (`README.md:17`) | UI tetap menampilkan toggle subtitle (`frontend/js/app.js:154`); hanya dipakai jalur MPT lama (`backend/main.py:1151`) | Toggle tidak berefek di AUTO aktif |
| “Musik latar” (UI) | Hanya diteruskan ke MPT lama (`:1151`); manifest Remotion tidak memuat musik | Toggle tidak berefek di AUTO aktif |
| Zona waktu target (UI) | Hardcoded `Asia/Jakarta` (`:960`, `:1320`); `auto_timezone` divalidasi tetapi tidak dipakai | Konfigurasi zona waktu tidak berfungsi |
| Format WebM (Settings) | Remotion selalu H.264/MP4; `render_composition` memaksa `render_format=mp4` (`modules/remotion/render_manager.py:170`) | Output bisa salah ekstensi/format |
| Resolusi/FPS bebas 144–3840 / 1–120 (Settings) | Renderer hanya menerima 1080×1920 atau 1920×1080 @30fps (`render_manager.py:123-124`) | Render gagal jika diubah |

### 7.5 Pengujian

- **Jumlah test:** 46 (37 `test_auto.py` + 9 `test_remotion.py`).
- **Clone bersih:** 44 lulus, **2 error**:
  - `test_python_to_remotion_manifest_contract_without_real_render`
  - `test_remotion_uses_selected_auto_video_duration`
  - Penyebab: `read_settings()` mengakses tabel `settings` yang belum dibuat (`sqlite3.OperationalError: no such table: settings`).
- **Setelah `initialize_db()`:** seluruh **46 test lulus** (12,3 detik).
- **Tidak diuji:** render Remotion nyata, HTTP handler, upload multipart, path traversal, CSRF, pipeline AUTO end-to-end, integrasi Groq/Cartesia/Edge TTS live.

---

## 8. Temuan Masalah dan Potensi Bug

| # | Severity | Temuan | Bukti | Dampak | Rekomendasi |
|---|---|---|---|---|---|
| 1 | **Tinggi** | UI MANUAL memblokir produksi jika scene tidak punya media | `frontend/js/app.js:104` (`scene.media.length>0`) vs backend `start_manual_production` (`backend/main.py:1815`) yang hanya mensyaratkan narasi | Scene animasi generated tidak bisa diproduksi dari dashboard | Selaraskan gate UI dengan backend; izinkan media kosong |
| 2 | **Tinggi** | Settings resolusi/FPS/format tidak cocok dengan renderer | `frontend/js/app.js:187` vs `modules/remotion/render_manager.py:123-124,170` | Render gagal atau file salah format setelah pengguna mengubah Settings | Batasi pilihan UI atau dukung nilai tersebut di renderer |
| 3 | **Sedang** | `auto_music_enabled` dan `auto_subtitle_enabled` tidak berefek di AUTO aktif | Hanya dipakai `_run_auto_item` (`backend/main.py:1151`) yang tidak dipanggil; worker memakai `_run_auto_item_internal` (`:1316`) | Pengguna mengira musik/subtitle aktif padahal tidak | Hapus toggle atau implementasikan di manifest Remotion |
| 4 | **Sedang** | `auto_timezone` diabaikan | Hardcoded `Asia/Jakarta` di `backend/main.py:960,1320`; validasi `:611-618` | Jadwal/limit harian tidak mengikuti zona waktu pengguna | Gunakan `settings["auto_timezone"]` |
| 5 | **Sedang** | Transisi `wipe`/`slide` dari Qwen dirender sebagai fade | `modules/auto/qwen.py:241-242,254` vs `renderer/remotion/src/video.tsx:19-21` (hanya `fade()`) | Ketidaksesuaian rencana visual dan hasil | Implementasikan presentasi wipe/slide atau batasi ke fade/cut |
| 6 | **Sedang** | Vendor MoneyPrinterTurbo tidak ada di repo | `README.md:19`; default path `backend/main.py:334`; import `app.config` di `modules/auto/mpt.py:132`; route `:2015,2313` | Fitur MPT/legacy dan `/api/auto/llm-providers` gagal | Dokumentasikan setup MPT atau sertakan source yang diperlukan |
| 7 | **Sedang** | Test bergantung pada DB yang sudah diinisialisasi | `tests/test_remotion.py:113,144` → `read_settings()` | `unittest discover` gagal pada clone bersih | Panggil `initialize_db()` di setUp atau gunakan DB sementara |
| 8 | **Sedang** | `.env.example` tidak lengkap | Variabel di §7.3 | Setup membingungkan; fitur rotasi kunci/MPT tidak terdokumentasi | Tambahkan semua variabel dengan komentar |
| 9 | **Sedang** | API lokal tanpa autentikasi/CSRF/Origin check | `backend/main.py:1912-2820`; hanya bind loopback | Proses lokal/halaman web dapat memicu aksi | Tambahkan token lokal/CSRF + validasi Origin; dokumentasikan |
| 10 | **Sedang** | `mpt.start_service` meneruskan seluruh `os.environ` | `modules/auto/mpt.py:383` vs `_worker_env` `:29-33` | Potensi kebocoran kredensial ke subprocess | Gunakan `_worker_env` |
| 11 | **Rendah** | `cartesia_connection_check()` tidak dipanggil | `backend/main.py:730`; `capabilities()` `:725` hanya cek env | Status “configured” bisa menyesatkan | Panggil saat tes koneksi atau hapus fungsi |
| 12 | **Rendah** | `auto_dashboard` mem-parse JSON tanpa guard | `backend/main.py:1396,1403,1411,1426` | Data korup → HTTP 500 | Bungkus `json.loads` dengan fallback |
| 13 | **Rendah** | Kode legacy/tidak terpakai | `_run_auto_item` (`:1081`), `openai_engine.py`, `hyperframes/composition.py`, `audio_analyzer/segmenter.py`, `render_and_qc` (`modules/ffmpeg/renderer.py:65`), `mpt.start_service` (`:361`) | Beban perawatan dan permukaan audit | Tandai deprecated atau hapus pada rilis berikutnya |
| 14 | **Rendah** | `.gitignore` belum mencakup runtime sensitif | `.gitignore` | Risiko commit `vendor/`, `.runs`, DB, log | Tambahkan pola |
| 15 | **Rendah** | Tidak ada file LICENSE | Root repo | Ketidakjelasan lisensi first-party; README menyebut lisensi pihak ketiga | Tambahkan LICENSE yang sesuai |
| 16 | **Info** | Model Groq default belum diverifikasi | `modules/auto/qwen.py:19` (`qwen/qwen3.8-27b`) | Bisa gagal jika model tidak tersedia di akun | Verifikasi dengan `/api/auto/groq-test` |
| 17 | **Info** | Dependency tidak terpakai di jalur aktif | `openai`, `pydantic`, `httpx`, `ddgs` | Instalasi lebih besar dari kebutuhan | Dokumentasikan atau rapikan |
| 18 | **Info** | `renderer/remotion/public/` tidak dilacak Git | Dibuat runtime oleh `render_manager` | Clone bersih tidak punya folder; render Python membuatnya | Tidak masalah untuk jalur Python; dokumentasikan untuk render manual |

---

## 9. File yang Perlu Diperiksa Lebih Dalam

1. `backend/main.py` — routing, validasi input, keamanan, worker AUTO, scheduler, MANUAL.
2. `modules/remotion/render_manager.py` — staging, normalisasi audio, frame scenes, batas resolusi/FPS.
3. `modules/remotion/tts.py` + `edge_tts_worker.py` — alignment kata, penanganan error jaringan.
4. `modules/cartesia/client.py` — SSE, word timestamps, validasi respons.
5. `modules/auto/qwen.py` + `yomi.py` — prompt, parsing JSON, rotasi kunci, cooldown.
6. `modules/auto/mpt.py` + `mpt_worker.py` — konfigurasi vendor, env isolation, IPC.
7. `modules/auto/research.py` — SSRF, validasi konten media, unduhan.
8. `renderer/remotion/src/video.tsx`, `visuals.tsx`, `lemo-style-scene.tsx` — transisi, tipe visual, gaya.
9. `frontend/js/app.js` — gate produksi, form settings, error handling.
10. `tests/test_auto.py`, `tests/test_remotion.py` — isolasi DB, cakupan HTTP/end-to-end.
11. `.env.example`, `.gitignore`, `README.md` — kelengkapan konfigurasi dan akurasi dokumentasi.
12. `vendor/dependencies/MoneyPrinterTurbo` (di luar repo) — jika fitur MPT akan diaktifkan.

---

## 10. Rekomendasi Prioritas

**P0 — sebelum produksi rutin**

1. Perbaiki isolasi test: inisialisasi DB sementara di `setUp` agar `unittest discover` lulus pada clone bersih.
2. Selaraskan gate UI MANUAL dengan backend (izinkan scene tanpa media).
3. Selaraskan Settings resolusi/FPS/format dengan kemampuan renderer, atau batasi input UI.
4. Putuskan status MPT: sertakan source yang diperlukan atau tandai eksplisit sebagai opsional/legacy di README dan UI.
5. Tambahkan autentikasi lokal/CSRF + validasi Origin, atau dokumentasikan tegas bahwa API hanya untuk loopback dan tidak boleh diekspos.

**P1 — kualitas dan konsistensi**

6. Perbaiki klaim README (durasi 60 detik, MPT, subtitle, media).
7. Implementasikan atau hapus toggle musik/subtitle yang tidak berefek.
8. Gunakan `auto_timezone` di scheduler dan limit harian.
9. Implementasikan wipe/slide atau batasi rencana Qwen ke fade/cut.
10. Lengkapi `.env.example` dan dokumentasi versi Python/Node/Chrome.
11. Hapus/tandai kode legacy dan panggil `cartesia_connection_check` bila diperlukan.

**P2 — hardening dan pemeliharaan**

12. Bungkus parsing JSON di `auto_dashboard` dengan fallback.
13. Perluas `.gitignore` (`vendor/`, `.runs/`, `*.sqlite3`, `*.db`, `*.log`).
14. Tambahkan LICENSE dan kebijakan backup kredensial.
15. Tambahkan test HTTP handler, upload, path traversal, dan pipeline render nyata.

---

## 11. Lampiran: Metode dan Batasan

**Metode:**

- Clone branch remote secara terpisah, verifikasi `HEAD`, daftar file, dan diff isi terhadap clone import lokal.
- Inventaris struktur dan line count.
- Pembacaan kode statis untuk routing, AUTO/MANUAL, renderer, TTS, keamanan, dan dokumentasi.
- Scan pola rahasia pada working tree dan riwayat commit.
- Menjalankan 46 unit test dengan `.venv` Python 3.12.3 pada clone bersih, lalu mengulang setelah `initialize_db()`.
- Tidak membaca `.env` atau file kredensial apa pun.

**Batasan:**

- Tidak ada render Remotion nyata; tidak ada panggilan live ke Groq, Cartesia, Edge TTS, DDGS, atau MPT.
- Tidak ada pengujian HTTP handler, upload multipart, CSRF, atau path traversal secara dinamis.
- Tidak ada audit performa, skalabilitas, atau kualitas visual.
- Penilaian “lengkap” mengacu pada file first-party yang diimpor; `vendor/` dan runtime sengaja dikecualikan.

**Status akhir:** Audit statis selesai. Source tidak diubah. Laporan ini siap di-commit ke branch `import/olyphants-video-studio-20261117`.
