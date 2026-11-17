# OLYPHANTS VIDEO STUDIO

Studio produksi video lokal dengan alur MANUAL dan AUTO. Remotion menjadi engine komposisi animasi untuk keduanya: visual dapat dibuat dari naskah tanpa media eksternal, atau digabung dengan media lokal yang sudah diunggah/diperoleh AUTO. Cartesia digunakan oleh MANUAL; AUTO dapat memilih Edge TTS dari runtime MoneyPrinterTurbo atau Cartesia; Edge TTS tetap pilihan bawaan. FFmpeg memproses audio dan FFprobe memeriksa hasil MP4.

## Menjalankan dashboard

```sh
.venv/bin/python -m pip install -r requirements.txt
npm --prefix renderer/remotion install
.venv/bin/python backend/main.py
```

Buka `http://127.0.0.1:8765`. Render memerlukan Node.js, Chrome/Chromium, FFmpeg, dan FFprobe. Dashboard memakai database SQLite di `data/studio.sqlite3`.

## Remotion

Implementasi Remotion terpisah di `renderer/remotion` dan Python mengirim manifest scene/audio ke Node.js. Semua output Remotion disetel tepat 60 detik; Render vertikal memakai 1080×1920, 30 fps, MP4 H.264/AAC. FFmpeg menyesuaikan tempo audio dan mengisi sisa durasi bila narasi lebih pendek; scene ikut diskalakan agar sinkron. Engine mencakup kinetic typography, explainer/ilustrasi 2D, grafik, peta, simulasi UI, media eksternal opsional, hybrid visuals, transisi, dan audio narasi berkelanjutan. TTS alignment dipakai untuk membagi scene menurut waktu kata. Tidak ada subtitle otomatis; judul/teks desain visual tetap dapat digunakan.

Katalog berisi 20 gaya visual: sepuluh gaya dasar dan sepuluh gaya pilihan baru yang dikelompokkan menjadi Informasi & Edukasi (Whiteboard Explainer tanpa tangan, Colored-Pencil Science Illustration, Copperplate Engraving, Stained Glass), Wisata & Perjalanan (Urban Sketch · Pen & Wash, Silkscreen Travel Poster), serta Seni & Budaya (Risograph Print, Ukiyo-e, Red Paper-cut, Woodcut Print). Pada AUTO, pengguna memilih satu atau beberapa gaya di Pengaturan. Qwen membaca naskah lalu memilih gaya yang cocok dari pilihan pengguna untuk tiap scene; Remotion menerapkan animasi lokal. Katalog memuat pemetaan sepuluh gaya dasar dari source Lemo-Opuscar; ini bukan klaim bahwa seluruh 100 film contoh Lemo sudah menjadi renderer plug-in. AUTO memakai Qwen melalui Groq, Edge TTS langsung (tanpa runtime MoneyPrinterTurbo), lalu Remotion dan FFmpeg. Source dan konfigurasi MoneyPrinterTurbo lama tetap tersedia untuk kompatibilitas. Source Lemo mengandung kode MIT serta panduan gaya CC BY-NC 4.0 dan aset dengan lisensi lain; implementasi tidak menyalin demo, panduan, font, atau asetnya.

Scene otomatis MANUAL menggunakan YOMI untuk membuat arahan visual. AUTO juga meminta rencana visual YOMI. Jika media eksternal diminta tetapi tidak tersedia, Remotion menghasilkan ilustrasi animasi dan memberi label `ILUSTRASI`. Media yang diunggah pengguna tetap dapat dipakai. Tidak ada visual yang dibuat-buat yang diklaim sebagai rekaman kejadian nyata.

## AUTO dan AI

- `YOMI ENGINE 1.0` memakai Groq/Qwen untuk naskah dan instruksi visual serta mempertahankan model dan endpoint yang dikonfigurasi. Atur `GROQ_API_KEY`, `GROQ_API_KEY_2`, `GROQ_API_KEY_3` sesuai kebutuhan.
- YOMI memilih key round-robin. 429 menerapkan cooldown berdasar header atau kebijakan terbatas; 401 menandai key invalid; 403, kesalahan model/format, dan timeout tidak dicoba ulang tanpa batas. Jika semua key tidak tersedia, AUTO berhenti dan antrean dibatalkan. Rotasi tidak menambah kuota akun.
- Peristiwa pemilihan key, rate limit, perpindahan, keberhasilan, dan penghentian tampil di detail progres AUTO. Nilai key tidak ditampilkan.
- AUTO memakai Qwen untuk naskah, metadata, dan rencana visual; gaya visual dipilih pengguna, lalu Qwen menentukan penerapannya per scene dari pilihan itu. TTS dan Remotion merender video; pembuatan video tidak bergantung pada DDGS atau MoneyPrinterTurbo. Durasi, format, dan suara dipilih di Pengaturan sebelum menekan Otomatis. Tombol Manual membuka alur proyek manual dengan media yang diberikan pengguna.

## Pengujian

Jalankan tes unit dengan:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Render sampel video dilakukan melalui pipeline Remotion backend; output dan hasil QC tersimpan di bawah `output/`.
