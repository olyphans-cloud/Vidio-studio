"""Live DDGS search and cautious URL checks for AUTO research references."""

from __future__ import annotations

import ipaddress
import hashlib
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from ddgs import DDGS


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _public_http_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False, "invalid_scheme_or_authority"
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False, "non_public_host"
        try:
            addresses = {ipaddress.ip_address(host)}
        except ValueError:
            addresses = {
                ipaddress.ip_address(entry[4][0].split("%", 1)[0])
                for entry in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            }
        if not addresses or any(not address.is_global for address in addresses):
            return False, "non_public_host"
        return True, ""
    except (ValueError, OSError, UnicodeError):
        return False, "invalid_or_unresolvable_url"


def validate_url(url: str, *, timeout: float = 5.0, max_redirects: int = 3) -> dict:
    """Check a public HTTP URL without downloading its body or following unchecked redirects."""
    current = url.strip()
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(max_redirects + 1):
        safe, reason = _public_http_url(current)
        if not safe:
            return {"status": "unsafe_or_invalid", "reason": reason, "http_status": None, "content_type": ""}
        request = urllib.request.Request(current, method="HEAD", headers={"User-Agent": "OLYPHANTS-Research/1.0"})
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location", "")
                exc.close()
                if not location:
                    return {"status": "unreachable", "reason": "redirect_without_location", "http_status": exc.code, "content_type": ""}
                current = urllib.parse.urljoin(current, location)
                continue
            if exc.code in {403, 405, 501}:
                # Some public sources deny HEAD. A one-byte range request checks
                # reachability without downloading a complete page or media file.
                request = urllib.request.Request(current, headers={"Range": "bytes=0-0", "User-Agent": "OLYPHANTS-Research/1.0"})
                try:
                    response = opener.open(request, timeout=timeout)
                    response.read(1)
                    code = response.status
                    content_type = response.headers.get("Content-Type", "")
                    response.close()
                    return {"status": "valid" if code in {200, 206} else "unreachable", "reason": "head_not_allowed", "http_status": code, "content_type": content_type}
                except urllib.error.HTTPError as ranged:
                    code = ranged.code
                    content_type = ranged.headers.get("Content-Type", "")
                    ranged.close()
                    return {"status": "blocked" if code in {401, 403} else "unreachable", "reason": "access_denied" if code in {401, 403} else "range_request_failed", "http_status": code, "content_type": content_type}
                except (urllib.error.URLError, TimeoutError, OSError):
                    return {"status": "unreachable", "reason": "network_error", "http_status": None, "content_type": ""}
            else:
                return {"status": "unreachable", "reason": "http_error", "http_status": exc.code, "content_type": exc.headers.get("Content-Type", "")}
        except (urllib.error.URLError, TimeoutError, OSError):
            return {"status": "unreachable", "reason": "network_error", "http_status": None, "content_type": ""}
        else:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            response.close()
            return {"status": "valid" if 200 <= status < 400 else "unreachable", "reason": "", "http_status": status, "content_type": content_type}
    return {"status": "unreachable", "reason": "too_many_redirects", "http_status": None, "content_type": ""}


def _make_query(topic: str, script: str = "", keywords: list[str] | None = None) -> str:
    words = re.findall(r"[\wÀ-ÿ'-]{5,}", script.lower())
    stop_words = {"adalah", "dengan", "untuk", "dalam", "yang", "pada", "dari", "karena", "mereka", "sebagai", "tentang", "video", "scene"}
    terms = list(dict.fromkeys(word for word in words if word not in stop_words))[:6]
    visual_terms = [str(term).strip()[:120] for term in (keywords or []) if str(term).strip()][:6]
    parts = list(dict.fromkeys([topic.strip(), *terms, *visual_terms]))
    return " ".join(parts).strip()[:500]


def _run_search(method: str, query: str, limit: int, options: dict | None = None) -> list[dict]:
    last_error = None
    for attempt in range(2):
        try:
            return list(getattr(DDGS(timeout=8), method)(query, max_results=limit, **(options or {})))
        except Exception as exc:  # DDGS wraps provider/network failures with several exception types.
            last_error = type(exc).__name__
            if attempt == 0:
                time.sleep(0.4)
    raise RuntimeError(f"DDGS {method} search failed ({last_error})")


def _normalize_results(kind: str, results: list[dict], *, validate: bool) -> list[dict]:
    seen, clean = set(), []
    for item in results:
        source_url = str(item.get("href") or item.get("url") or item.get("content") or "").strip()
        if not source_url or source_url in seen:
            continue
        seen.add(source_url)
        media_url = str(item.get("image") or item.get("content") or item.get("video") or item.get("embed_url") or "").strip()
        url_check = validate_url(source_url) if validate else {"status": "not_checked", "reason": "", "http_status": None, "content_type": ""}
        media_validation = validate_url(media_url) if media_url and validate else {
            "status": "not_checked" if media_url else "missing", "reason": "", "http_status": None, "content_type": ""
        }
        if media_url and not validate:
            media_validation["status"] = "not_checked"
        if kind == "images" and media_validation["status"] == "valid" and not media_validation["content_type"].lower().startswith("image/"):
            media_validation["status"] = "invalid_media_type"
        clean.append({
            "title": str(item.get("title") or "").strip()[:300],
            "url": source_url[:2000],
            "media_type": {"text": "article", "images": "image", "videos": "video"}[kind],
            "description": str(item.get("body") or item.get("description") or item.get("snippet") or "").strip()[:1200],
            "media_url": media_url[:2000],
            "media_url_status": media_validation["status"],
            "media_url_validation": media_validation,
            "source": str(item.get("source") or item.get("publisher") or "").strip()[:200],
            "url_validation": url_check,
        })
    return clean


def search_kind(topic: str, kind: str, script: str = "", limit: int = 5, *, validate: bool = True, options: dict | None = None, keywords: list[str] | None = None) -> dict:
    """Perform one live DDGS query type and report failures without fake results."""
    topic = topic.strip()
    if not topic:
        raise ValueError("Tema riset kosong")
    if kind not in {"text", "images", "videos"}:
        raise ValueError("Jenis DDGS harus text, images, atau videos")
    if not 1 <= int(limit) <= 10:
        raise ValueError("limit pencarian harus antara 1 dan 10")
    query = _make_query(topic, script, keywords)
    try:
        items = _normalize_results(kind, _run_search(kind, query, int(limit), options), validate=validate)
        return {"query": query, "kind": kind, "results": items, "error": ""}
    except RuntimeError as exc:
        return {"query": query, "kind": kind, "results": [], "error": str(exc)}


def _fallback_text_queries(topic: str, script: str = "") -> list[str]:
    """Build progressively broader queries without changing the requested subject."""
    candidates = []
    topic = topic.strip()
    if ":" in topic:
        candidates.append(topic.split(":", 1)[1].strip())

    stop_words = {
        "adalah", "dengan", "untuk", "dalam", "yang", "pada", "dari", "karena",
        "mereka", "sebagai", "tentang", "video", "scene", "berita", "terbaru",
        "apa", "sudah", "belum", "terkonfirmasi", "terkait", "seputar", "hari",
        "ini", "itu", "politik", "indonesia", "november", "januari", "februari",
        "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober",
        "desember",
    }
    for text in (candidates[0] if candidates else topic, topic):
        terms = [word for word in re.findall(r"[\wÀ-ÿ'-]+", text.lower())
                 if len(word) > 2 and word not in stop_words and not word.isdigit()]
        if terms:
            candidates.append(" ".join(dict.fromkeys(terms[:10])))

    # A short topic-only query can recover sources when generated script and
    # visual terms make a search too restrictive. Keep it last and deduplicate.
    candidates.append(_make_query(topic))
    primary = _make_query(topic, script)
    return [query for query in dict.fromkeys(candidates) if query and query != primary][:3]


def search(topic: str, script: str = "", limit: int = 5, *, validate: bool = True, keywords: list[str] | None = None) -> dict:
    """Search DDGS sources, broadening text queries if validation leaves too few.

    Broader queries still use live DDGS results and the same URL safety checks;
    they never substitute generated or unverified citations.
    """
    topic = topic.strip()
    if not topic:
        raise ValueError("Tema riset kosong")
    if not 1 <= int(limit) <= 10:
        raise ValueError("limit pencarian harus antara 1 dan 10")
    query = _make_query(topic, script, keywords)
    groups, errors = {}, {}
    for kind in ("text", "images", "videos"):
        result = search_kind(topic, kind, script, limit, validate=validate, keywords=keywords)
        groups[kind] = result["results"]
        if result["error"]:
            errors[kind] = result["error"]

        if kind == "text":
            count = lambda items: sum(
                not validate or item.get("url_validation", {}).get("status") == "valid"
                for item in items
            )
            fallback_errors = []
            fallback_queries = []
            for fallback_query in _fallback_text_queries(topic, script):
                if count(groups["text"]) >= 2:
                    break
                fallback_queries.append(fallback_query)
                try:
                    raw = _run_search("text", fallback_query, int(limit))
                    extra = _normalize_results("text", raw, validate=validate)
                except RuntimeError as exc:
                    fallback_errors.append(str(exc))
                    continue
                known = {item["url"] for item in groups["text"]}
                groups["text"].extend(item for item in extra if item["url"] not in known)
            if count(groups["text"]) >= 2:
                errors.pop("text", None)
            elif fallback_errors:
                errors["text_fallback"] = "; ".join(fallback_errors)
            if fallback_queries:
                groups["text_search_queries"] = fallback_queries
    return {"query": query, **groups, "search_errors": errors}


def download_images(results: list[dict], destination: str, *, limit: int = 8, max_bytes: int = 15_000_000) -> list[str]:
    """Download only previously validated public DDGS image URLs, with strict size/type bounds."""
    from pathlib import Path

    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    outputs = []
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    for item in results:
        if len(outputs) >= limit:
            break
        url = str(item.get("media_url") or "").strip()
        if not url or item.get("media_url_status") != "valid":
            continue
        safe, _ = _public_http_url(url)
        if not safe:
            continue
        request = urllib.request.Request(url, headers={"User-Agent": "OLYPHANTS-Research/1.0"})
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=20) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type not in extensions:
                    continue
                content = response.read(max_bytes + 1)
                if len(content) > max_bytes or not content:
                    continue
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            continue
        suffix = extensions[content_type]
        path = target / (hashlib.sha256(url.encode()).hexdigest()[:20] + suffix)
        path.write_bytes(content)
        outputs.append(str(path))
    return outputs


def download_short_videos(
    results: list[dict], destination: str, *, limit: int = 1,
    max_bytes: int = 20_000_000, max_duration: float = 30.0,
) -> list[str]:
    """Download validated public DDGS video URLs, accepting only short MP4/WebM clips."""
    from pathlib import Path

    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return []
    extensions = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}
    outputs = []
    for item in results:
        if len(outputs) >= limit:
            break
        url = str(item.get("media_url") or "").strip()
        if not url or item.get("media_url_status") != "valid":
            continue
        safe, _ = _public_http_url(url)
        if not safe:
            continue
        request = urllib.request.Request(url, headers={"User-Agent": "OLYPHANTS-Research/1.0"})
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=20) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                extension = extensions.get(content_type)
                if not extension:
                    continue
                content = response.read(max_bytes + 1)
                if len(content) > max_bytes or len(content) < 4096:
                    continue
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            continue
        if extension in {".mp4", ".mov"} and (len(content) < 12 or content[4:8] != b"ftyp"):
            continue
        if extension == ".webm" and not content.startswith(b"\x1a\x45\xdf\xa3"):
            continue
        path = target / (hashlib.sha256(url.encode()).hexdigest()[:20] + extension)
        try:
            with path.open("xb") as output:
                output.write(content)
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0
        except (OSError, ValueError, subprocess.SubprocessError):
            path.unlink(missing_ok=True)
            continue
        if not 0 < duration <= max_duration:
            path.unlink(missing_ok=True)
            continue
        outputs.append(str(path))
    return outputs
