"""Qwen model client through Groq's OpenAI-compatible API for AUTO."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from modules.auto import yomi


class GroqError(RuntimeError):
    """Safe-to-display provider error; never contains request headers or secrets."""


# Compatibility for callers that imported the former provider-specific name.
QwenError = GroqError
DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
USER_AGENT = "OLYPHANTS-VIDEO-STUDIO/1.0"
_USAGE = threading.local()


def last_usage() -> dict:
    return getattr(_USAGE, "value", {})


def configuration() -> dict:
    base_url = os.environ.get("GROQ_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    return {
        "configured": bool(yomi.configured() and base_url.startswith("https://")),
        "provider": "groq",
        "model": os.environ.get("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "base_url": base_url,
    }


def _http_error(status: int) -> GroqError:
    if status == 401:
        return GroqError("Groq menolak API key (HTTP 401). Periksa konfigurasi API Key Groq di .env.")
    if status == 403:
        return GroqError("Akses Groq ditolak (HTTP 403). Periksa izin akun, model, atau project.")
    if status == 429:
        return GroqError("Batas permintaan atau kuota Groq tercapai (HTTP 429).")
    if status in (400, 404, 422):
        return GroqError("Groq menolak model atau format permintaan (HTTP " + str(status) + "). Periksa GROQ_MODEL.")
    if status in (408, 504):
        return GroqError("Permintaan ke Groq melewati batas waktu (HTTP " + str(status) + "). Tidak diulang otomatis.")
    return GroqError("Groq API gagal (HTTP " + str(status) + "). Tidak ada fallback provider.")


def chat(prompt: str, *, system: str = "", max_tokens: int | None = None) -> tuple[str, dict]:
    config = configuration()
    if not config["configured"]:
        raise GroqError("YOMI: Tidak ada API Key Groq aktif. Siklus AUTO dihentikan; atur minimal satu key.")
    if not config["base_url"].startswith("https://"):
        raise GroqError("GROQ_BASE_URL harus menggunakan HTTPS")
    try:
        token_limit = int(max_tokens or os.environ.get("GROQ_MAX_OUTPUT_TOKENS", "1600"))
    except ValueError:
        token_limit = 1600
    token_limit = min(4096, max(64, token_limit))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({
        "model": config["model"], "messages": messages,
        "max_tokens": token_limit, "temperature": 0.4,
    }).encode()
    _USAGE.value = {}
    attempted = 0
    last_key_number = None
    while attempted < len(yomi.KEY_ENV_NAMES):
        selected = yomi.next_key()
        if selected is None:
            raise GroqError(yomi.availability_message())
        key_number, api_key = selected
        attempted += 1
        yomi.emit(f"YOMI: {'Beralih ke ' if last_key_number is not None else 'Menggunakan '}API Key {key_number}")
        request = urllib.request.Request(
            config["base_url"] + "/chat/completions", data=body, method="POST",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read(2_000_000).decode("utf-8"))
            yomi.mark_success(key_number)
            yomi.emit(f"YOMI: API Key {key_number} berhasil")
            break
        except urllib.error.HTTPError as exc:
            status, headers = exc.code, exc.headers
            exc.close()
            if status == 429:
                yomi.mark_rate_limited(key_number, headers)
                yomi.emit(f"YOMI: API Key {key_number} terkena rate limit")
                last_key_number = key_number
                continue
            if status == 401:
                yomi.mark_invalid(key_number)
                yomi.emit(f"YOMI: API Key {key_number} ditolak (HTTP 401)")
                last_key_number = key_number
                continue
            raise _http_error(status) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise GroqError("Groq API tidak dapat dijangkau atau melewati batas waktu. Tidak diulang otomatis.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GroqError("Groq mengembalikan respons yang tidak valid.") from None
    else:
        raise GroqError(yomi.availability_message())
    try:
        content = str(data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        content = ""
    if not content:
        error_type = ((data.get("error") or {}).get("type") if isinstance(data.get("error"), dict) else "")
        if error_type:
            raise GroqError("Groq tidak dapat menjalankan model " + config["model"] + ". Periksa ketersediaan model pada akun.")
        raise GroqError("Groq tidak mengembalikan teks.")
    usage = data.get("usage") or {}
    _USAGE.value = usage
    return content, usage


def _decode_json_response(text: str, opening: str, closing: str):
    candidates = [text.strip()]
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        candidates.append("\n".join(lines[1:-1]).strip() if len(lines) > 2 else "")
    for candidate in list(candidates):
        start, end = candidate.find(opening), candidate.rfind(closing)
        if start >= 0 and end >= start:
            candidates.append(candidate[start:end + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise GroqError("Model Qwen melalui Groq menghasilkan JSON terstruktur tidak valid")


def generate_script(topic: str, position: int, research: list[dict] | None = None,
                    target_duration_seconds: int = 60) -> str:
    target_duration_seconds = min(60, max(15, int(target_duration_seconds)))
    target_words = max(24, round(target_duration_seconds * 1.6))
    if not research:
        text, _ = chat(
            f"Topik tetap dari pengguna: {topic}\nVideo ke-{position}. Buat naskah penjelasan bahasa Indonesia untuk video {target_duration_seconds} detik, sekitar {target_words - 10}–{target_words} kata dalam 3–5 bagian narasi. "
            "Gunakan hanya pengetahuan umum yang mapan. Jangan mengarang kutipan, angka, tanggal, nama narasumber, atau bukti. "
            "Untuk hal yang masih diperdebatkan, jelaskan bahwa itu teori atau belum diketahui pasti. Hindari klaim berita terkini. "
            "Jika tidak dapat menulis naskah yang dapat dipertanggungjawabkan dari topik ini, jawab tepat PERLU_SUMBER.",
            system="Anda penulis naskah edukasi yang berhati-hati. Jangan menyampaikan dugaan sebagai fakta.",
        )
        return text
    refs = "\n".join(
        f"- {x.get('title','')} | {x.get('description','')} | {x.get('url','')}"
        for x in research[:12]
    )
    text, _ = chat(
        f"Topik tetap dari pengguna: {topic}\nVideo ke-{position}. Gunakan hanya fakta yang didukung sumber berikut. "
        f"Buat naskah narasi bahasa Indonesia yang ringkas untuk video {target_duration_seconds} detik, sekitar {target_words - 10}–{target_words} kata. "
        "Susun 4-6 scene; setiap scene berisi narasi dan arahan visual. Jangan mengarang. "
        "Untuk berita politik, laporkan perkembangan dan proses yang disebut sumber; jangan menyimpulkan tuduhan yang masih disengketakan sebagai fakta atau hasil yang sudah diputuskan. "
        "Jika minimal dua sumber secara langsung melaporkan peristiwa utama, tulis naskah berdasarkan fakta bersama yang tersedia meski prosesnya belum selesai. "
        "Jawab tepat RISET_TIDAK_CUKUP hanya jika kurang dari dua sumber mendukung peristiwa utama.\nSUMBER:\n" + refs,
        system="Anda penulis naskah berita faktual. Atribusikan klaim kepada sumber dan bedakan pengajuan proses dari keputusan akhir. Jangan mengganti topik pengguna.",
    )
    return text


def generate_terms(topic: str, script: str) -> list[str]:
    text, _ = chat(
        f"Buat 6 kata kunci pencarian footage relevan untuk topik {topic} dan naskah berikut. "
        "Keluarkan hanya JSON array string.\n" + script,
        system="Anda menyusun kata kunci visual untuk pencarian video.", max_tokens=400,
    )
    items = _decode_json_response(text, "[", "]")
    if not isinstance(items, list) or not items:
        raise GroqError("Model Qwen melalui Groq tidak menghasilkan kata kunci footage")
    return [str(x).strip()[:200] for x in items if str(x).strip()][:12]


def generate_metadata(topic: str, script: str) -> dict:
    text, _ = chat(
        f"Buat metadata video bahasa Indonesia untuk topik {topic} dan naskah berikut. "
        'Balas hanya satu objek JSON valid dengan kunci "title", "description", "caption", dan "hashtags". '
        'hashtags harus array string. Gunakan tanda kutip ganda JSON, tanpa markdown atau teks pembuka/penutup.\n' + script,
        system="Anda membuat metadata faktual. Jangan menambah klaim di luar naskah.", max_tokens=900,
    )
    data = _decode_json_response(text, "{", "}")
    if not isinstance(data, dict) or not str(data.get("title", "")).strip():
        raise GroqError("Model Qwen melalui Groq tidak menghasilkan judul video")
    return {
        "title": str(data["title"])[:180],
        "description": str(data.get("description", ""))[:3000],
        "caption": str(data.get("caption", ""))[:3000],
        "hashtags": data.get("hashtags", []) if isinstance(data.get("hashtags", []), list) else [],
    }


def generate_visual_plan(topic: str, narration_segments: list[str], visual_style: str = "", style_name: str = "",
                         *, style_options: dict[str, str] | None = None) -> list[dict]:
    """Turn fixed narration segments into safe Remotion scene instructions."""
    if not narration_segments or len(narration_segments) > 8:
        raise GroqError("Jumlah instruksi visual harus antara 1 dan 8 scene")
    blocks = "\n".join(f"SCENE {index}: {segment}" for index, segment in enumerate(narration_segments, 1))
    style_options = style_options or {}
    style_ids = list(style_options)
    style_instruction = ""
    visual_style_brief = f"GAYA VISUAL: {style_name or visual_style or 'Motion graphics standar'} ({visual_style or 'remotion'})."
    if style_options:
        style_listing = "\n".join(f"- {slug}: {name}" for slug, name in style_options.items())
        if style_name == "auto":
            anchor_instruction = "Tidak ada gaya acuan tetap. Pilih gaya sepenuhnya berdasarkan keseluruhan naskah dan isi tiap bagian. "
            visual_style_brief = "GAYA VISUAL: dipilih otomatis dari katalog untuk tiap scene sesuai naskah."
        else:
            anchor_instruction = f"Gaya pilihan pengguna ({style_name or visual_style or 'tidak ditentukan'}) boleh menjadi gaya utama bila cocok. "
            visual_style_brief = f"GAYA ACUAN: {style_name or visual_style or 'otomatis dari naskah'} ({visual_style or 'otomatis'})."
        style_instruction = (
            ' Tambahkan properti "visual_style" berisi satu ID gaya dari katalog pada setiap objek. '
            f"Katalog gaya yang diizinkan:\n{style_listing}\n"
            f"Baca seluruh naskah lalu pilih 2 sampai {min(3, len(style_ids))} gaya yang paling cocok untuk video ini. "
            "Gunakan gaya yang sama pada bagian yang berhubungan dan ubah gaya hanya saat isi narasi memberi alasan visual. "
            f"{anchor_instruction}"
            "Jangan membuat pergantian gaya terasa acak.\n"
        )
    else:
        visual_style_brief += " Pertahankan gaya terpilih secara konsisten untuk semua scene."
    text, _ = chat(
        "Buat instruksi visual untuk Remotion berdasarkan narasi yang sudah terkunci. "
        "Jangan mengubah, menambah, atau mengulang narasi. Jangan membuat caption/subtitle. "
        "Keluarkan hanya JSON array dengan tepat satu objek per scene dan properti: "
        '"heading" (maksimal 7 kata), "visual_type" (kinetic, explainer, infographic, chart, map, illustration, ui, media, hybrid), '
        '"visual_mode" (generated, media, hybrid), "visual_prompt" (deskripsi komposisi animasi), '
        '"illustrative_only" (boolean), "transition" (fade, wipe, slide, cut). '
        "Pilih generated untuk konsep abstrak. Pilih media/hybrid hanya bila gambar/rekaman nyata benar-benar diperlukan "
        "untuk konteks peristiwa; bila media tidak tersedia, hasil akan diganti ilustrasi berlabel. "
        "Jangan menyajikan ilustrasi sebagai dokumentasi asli. Peta boleh simbolis, jangan mengarang batas negara atau lokasi tepat.\n"
        f"{visual_style_brief}\n"
        f"TOPIK: {topic}\n{style_instruction}NARASI PER SCENE:\n{blocks}",
        system="Anda perancang motion graphics dan visual yang membedakan rekaman nyata dari ilustrasi.",
        max_tokens=1800,
    )
    result = _decode_json_response(text, "[", "]")
    allowed_types = {"kinetic", "explainer", "infographic", "chart", "map", "illustration", "ui", "media", "hybrid"}
    allowed_modes = {"generated", "media", "hybrid"}
    allowed_transitions = {"fade", "wipe", "slide", "cut"}
    if not isinstance(result, list):
        raise GroqError("YOMI tidak menghasilkan daftar instruksi visual yang valid")
    if len(result) != len(narration_segments):
        # Preserve the narration-to-scene mapping even when Qwen returns too
        # few or too many visual objects. Missing scenes get a safe, generated
        # illustration prompt based on their own narration.
        yomi.emit("YOMI: Jumlah arahan visual disesuaikan dengan jumlah bagian narasi")
        result = result[:len(narration_segments)]
        while len(result) < len(narration_segments):
            result.append({})
    normalized = []
    for index, item in enumerate(result):
        if not isinstance(item, dict):
            raise GroqError("Instruksi visual YOMI tidak valid")
        fallback_heading = " ".join(narration_segments[index].split()[:5]).strip() or f"Bagian {index + 1}"
        normalized.append({
            "narration": narration_segments[index],
            "heading": str(item.get("heading") or fallback_heading).strip()[:80],
            "visual_type": item.get("visual_type") if item.get("visual_type") in allowed_types else "explainer",
            "visual_mode": item.get("visual_mode") if item.get("visual_mode") in allowed_modes else "generated",
            "visual_prompt": str(item.get("visual_prompt") or ("Ilustrasi animasi yang menggambarkan konsep utama narasi: " + narration_segments[index][:700])).strip()[:1000],
            "illustrative_only": bool(item.get("illustrative_only", False)),
            "transition": item.get("transition") if item.get("transition") in allowed_transitions else "fade",
            **({"visual_style": item.get("visual_style") if item.get("visual_style") in style_options else (visual_style if visual_style in style_options else style_ids[0])} if style_options else {}),
        })
    if style_options and len(normalized) > 1:
        used_styles = {item["visual_style"] for item in normalized}
        target_distinct = min(3, len(style_ids), len(normalized))
        if len(used_styles) < target_distinct and len(style_ids) > 1:
            primary = next(iter(used_styles))
            remaining_alts = [slug for slug in style_ids if slug not in used_styles]
            affinity = (
                (("peta", "rute", "proses", "tahap", "kanal", "terowongan", "pembangunan", "diagram", "alur"), ("iso-infographic", "dark-documentary")),
                (("sejarah", "perang", "arkeologi", "abad", "penemuan", "masa lalu", "arsip", "dokumen"), ("dark-documentary", "kino-montage", "copperplate-engraving", "woodcut-print")),
                (("alam", "hewan", "laut", "hutan", "perjalanan", "wisata", "sungai", "pantai"), ("watercolor", "isometric-picture-book", "silkscreen-travel-poster")),
                (("cara", "langkah", "panduan", "tutorial", "belajar", "penjelasan", "tips"), ("midcentury-toon", "iso-infographic", "whiteboard-explainer-no-hands")),
                (("sains", "ilmu", "biologi", "fisika", "kimia", "penelitian", "sel", "bintang", "molekul"), ("colored-pencil-science", "dark-documentary")),
                (("game", "gim", "pixel", "nostalgia", "karakter", "retro"), ("pixel-rpg", "cel-anime-80s")),
                (("merek", "desain", "logo", "kota", "arsitektur", "bangunan", "kafe", "jalan"), ("swiss-motion", "urban-sketch-pen-wash", "isometric-picture-book")),
                (("budaya", "tradisi", "jepang", "seni", "kuno", "oriental", "lukisan"), ("ukiyo-e", "red-paper-cut", "woodcut-print")),
                (("cetak", "poster", "grafis", "tekstur", "warna", "kertas"), ("risograph-print", "silkscreen-travel-poster")),
                (("katedral", "cahaya", "kaca", "rohani", "suci", "mosaik"), ("stained-glass", "copperplate-engraving")),
                (("balok", "mainan", "rakit", "struktur", "miniatur", "produk"), ("brick-toy", "isometric-picture-book")),
            )
            for index in range(1, len(normalized)):
                if len(used_styles) >= target_distinct:
                    break
                narration = narration_segments[index].lower()
                preferred = next((options for keywords, options in affinity if any(word in narration for word in keywords)), ())
                chosen = next((slug for slug in preferred if slug in remaining_alts), None)
                if not chosen and remaining_alts:
                    chosen = remaining_alts[0]
                if chosen:
                    normalized[index]["visual_style"] = chosen
                    used_styles.add(chosen)
                    if chosen in remaining_alts:
                        remaining_alts.remove(chosen)
        # Keep a coherent film: cap the mix at three styles, preserving the
        # most frequently selected looks and mapping rare outliers to the base.
        from collections import Counter
        counts = Counter(item["visual_style"] for item in normalized)
        keep = {style for style, _ in counts.most_common(3)}
        base_style = visual_style if visual_style in keep else counts.most_common(1)[0][0]
        for item in normalized:
            if item["visual_style"] not in keep:
                item["visual_style"] = base_style
    return normalized


def test_connection() -> dict:
    text, usage = chat("Balas hanya dengan kata SIAP.", system="Tes koneksi API.", max_tokens=32)
    return {"ok": bool(text), "provider": "groq", "model": configuration()["model"], "usage": usage}
