"""Offline cache of catalog photos; API calls only resolve already completed files.

v2 card: 640 px / quality 72; thumb: 320 px / quality 70. All files are
progressive JPEG with EXIF orientation applied, metadata removed, white alpha
background and no upscaling. Immutable v1 (1280 px / quality 80) remains readable
and supplies backfill input, avoiding a second download of the original catalog.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import os
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from PIL import Image, ImageOps, JpegImagePlugin

from app import media

VERSION = "v2"
MAX_SIDE = 640
QUALITY = 72
VARIANTS = ("card", "thumb")
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_JPEG_SOURCE_PIXELS = 100_000_000
REQUEST_TIMEOUT = 45.0
SOCKET_TIMEOUT = 15.0
MAX_REDIRECTS = 5
FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "GIF"}
_TLS_CONTEXT = ssl.create_default_context()
_ENCODE_SLOTS = threading.BoundedSemaphore(2)


def _profile(variant: str) -> tuple[int, int]:
    if variant == "card":
        return MAX_SIDE, QUALITY
    if variant == "thumb":
        return 320, 70
    if variant == "v1":
        return 1280, 80
    raise ValueError(f"unknown catalog image variant: {variant}")


def _relative_path(source_url: str, variant: str) -> Path:
    _profile(variant)
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    directory = Path("catalog/v1") if variant == "v1" else Path("catalog") / VERSION / variant
    return directory / digest[:2] / f"{digest}.jpg"


def path_of(source_url: str, media_dir: Path | str | None = None, *,
            variant: str = "card") -> Path:
    return Path(media_dir or media.DIR) / _relative_path(source_url, variant)


def url_of(source_url: str, *, variant: str = "card") -> str:
    base = os.environ.get("MATCHURI_MEDIA_PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/media/{_relative_path(source_url, variant).as_posix()}"


def public_url(source_url: str | None, *, variant: str = "card") -> str | None:
    """Requested completed profile → legacy v1 → source, without network/encode."""
    if not source_url or not source_url.startswith(("http://", "https://")):
        return source_url
    base = os.environ.get("MATCHURI_MEDIA_PUBLIC_BASE_URL", "")
    try:
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return source_url
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            return source_url
    except ValueError:
        return source_url
    for candidate in dict.fromkeys((variant, "v1")):
        if path_of(source_url, variant=candidate).is_file():
            return url_of(source_url, variant=candidate)
    return source_url


def iter_catalog_urls(data_dir: Path | str):
    """Unique source URLs across all stores, preserving each source's raw order."""
    seen: set[str] = set()
    for filename in ("photos.jsonl", "menus.jsonl", "reviews.jsonl"):
        path = Path(data_dir) / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as rows:
            for line in rows:
                if not line.strip():
                    continue
                row = json.loads(line)
                if filename == "photos.jsonl":
                    values = [row.get("url")]
                elif filename == "menus.jsonl":
                    values = [row.get("photo_url")]
                else:
                    values = list(row.get("photo_urls") or [])
                    values.extend(p.get("url") for p in row.get("photos") or []
                                  if isinstance(p, dict))
                for value in values:
                    if isinstance(value, str) and value and value not in seen:
                        seen.add(value)
                        yield value


class DownloadError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _target(url: str) -> tuple[str, str, int, str, str]:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("only HTTP(S) image URLs are supported")
        if parts.username is not None or parts.password is not None:
            raise ValueError("URL credentials are not allowed")
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in {80, 443}:
            raise ValueError("only public HTTP(S) ports are allowed")
        if any(ord(c) < 32 or ord(c) == 127 for c in url):
            raise ValueError("URL contains control characters")
    except (ValueError, UnicodeError) as exc:
        raise DownloadError(str(exc)) from exc
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise DownloadError("DNS resolution failed", retryable=True) from exc
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise DownloadError("DNS returned no addresses", retryable=True)
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise DownloadError("non-public image host is not allowed")
    # Pin the checked address. A second DNS lookup at connect time would allow
    # rebinding, and redirects need their own independent public-address check.
    address = next((ip for ip in addresses if ":" not in ip), addresses[0])
    target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        target += "?" + quote(parts.query, safe="%/:?@!$&'()*+,;=-._~[]")
    return parts.scheme, host, port, address, target


def _connection(scheme: str, host: str, port: int, address: str,
                timeout: float) -> http.client.HTTPConnection:
    # The HTTP connection is populated with an already connected socket so it
    # cannot resolve the hostname again. TLS still verifies the original host.
    if scheme == "https":
        # Preserve HTTPS's default_port=443 when http.client builds Host. Using
        # HTTPConnection around a TLS socket would incorrectly send host:443,
        # which some catalog CDNs route to a different/404 virtual host.
        connection = http.client.HTTPSConnection(host, port, timeout=timeout,
                                                context=_TLS_CONTEXT)
    else:
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
    sock = socket.create_connection((address, port), timeout=timeout)
    try:
        if scheme == "https":
            sock = _TLS_CONTEXT.wrap_socket(sock, server_hostname=host)
        connection.sock = sock
    except BaseException:
        sock.close()
        raise
    return connection


def download(source_url: str) -> bytes:
    """Bounded GET with verified public targets, including every redirect."""
    deadline = time.monotonic() + REQUEST_TIMEOUT
    current = source_url
    for redirect in range(MAX_REDIRECTS + 1):
        scheme, host, port, address, target = _target(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DownloadError("download deadline exceeded", retryable=True)
        connection = None
        try:
            connection = _connection(scheme, host, port, address,
                                     min(SOCKET_TIMEOUT, remaining))
            connection.request("GET", target, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Matchuri-Catalog-Image-Cache/1.0)",
                "Accept": "image/jpeg,image/png,image/webp,image/gif", "Accept-Encoding": "identity",
                "Referer": ("https://blog.naver.com/" if host == "pstatic.net"
                            or host.endswith(".pstatic.net") else "https://map.kakao.com/"),
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect == MAX_REDIRECTS:
                    raise DownloadError("invalid or excessive image redirects")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise DownloadError(f"HTTP {response.status}",
                                    retryable=response.status in {408, 429} or response.status >= 500)
            content_length = response.getheader("Content-Length")
            expected_size = (int(content_length) if content_length is not None
                             and not response.chunked else None)
            if expected_size is not None and expected_size < 0:
                raise ValueError("negative content length")
            if expected_size is not None and expected_size > MAX_SOURCE_BYTES:
                raise DownloadError("source image exceeds 32 MiB")
            chunks: list[bytes] = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DownloadError("download deadline exceeded", retryable=True)
                if connection.sock is not None:
                    connection.sock.settimeout(min(SOCKET_TIMEOUT, remaining))
                chunk = response.read1(min(64 * 1024, MAX_SOURCE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise DownloadError("source image exceeds 32 MiB")
            if expected_size is not None and size != expected_size:
                raise DownloadError("incomplete image response", retryable=True)
            if not size:
                raise DownloadError("empty image response")
            return b"".join(chunks)
        except DownloadError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise DownloadError(f"network error: {type(exc).__name__}", retryable=True) from exc
        except ValueError as exc:
            raise DownloadError("invalid HTTP content length") from exc
        finally:
            if connection is not None:
                connection.close()
    raise DownloadError("excessive image redirects")


def encode(raw: bytes, *, variant: str = "card") -> tuple[bytes, dict]:
    """Encode one profile; batch caching decodes once for both v2 profiles."""
    with _ENCODE_SLOTS:
        return _encode_variants(raw, (variant,))[variant]


def _encode_variants(raw: bytes, variants: tuple[str, ...]) -> dict[str, tuple[bytes, dict]]:
    profiles = {variant: _profile(variant) for variant in variants}
    # A thumb must have identical bytes when generated alone during resume and
    # together with a card. Always use the card-sized decode for v2 profiles.
    decode_side = max(MAX_SIDE, *(side for side, _ in profiles.values()))
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("invalid source size")
    # Direct JPEG headers allow safe decoder reduction before the decoded-pixel
    # bound, without weakening Pillow limits in the upload subsystem.
    is_jpeg = raw.startswith(b"\xff\xd8\xff")
    opener = JpegImagePlugin.JpegImageFile if is_jpeg else Image.open
    outputs = {}
    with opener(io.BytesIO(raw)) as original:
        if original.format not in FORMATS:
            raise ValueError(f"unsupported image format: {original.format}")
        width, height = original.size
        if is_jpeg and width * height > MAX_JPEG_SOURCE_PIXELS:
            raise ValueError("JPEG source image exceeds 100 million pixels")
        if not is_jpeg and width * height > MAX_PIXELS:
            raise ValueError("source image exceeds 40 million pixels")
        original.seek(0)
        original.draft("RGB", (decode_side, decode_side))
        if original.width * original.height > MAX_PIXELS:
            raise ValueError("decoded image exceeds 40 million pixels")
        original.load()
        img = ImageOps.exif_transpose(original)
        img.thumbnail((decode_side, decode_side), Image.Resampling.LANCZOS)
        if img.mode in {"RGBA", "LA", "P"}:
            rgba = img.convert("RGBA")
            img = Image.new("RGB", rgba.size, "white")
            img.paste(rgba, mask=rgba.getchannel("A"))
        elif img.mode != "RGB":
            img = img.convert("RGB")
        for variant, (side, quality) in profiles.items():
            resized = img.copy()
            resized.thumbnail((side, side), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            resized.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
            outputs[variant] = (output.getvalue(), {
                "src_bytes": len(raw), "src_width": width, "src_height": height,
                "width": resized.width, "height": resized.height,
                "max_side": side, "quality": quality,
            })
            resized.close()
    return outputs


def _cached(path: Path) -> dict:
    with Image.open(path) as image:
        return {"status": "cached", "dst_bytes": path.stat().st_size,
                "width": image.width, "height": image.height}


def _publish(path: Path, data: bytes, dimensions: dict) -> dict:
    """Publish complete JPEG bytes atomically without replacing any profile."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".catalog-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.chmod(0o644)
        try:
            os.link(temporary, path)
            return {**dimensions, "status": "generated", "dst_bytes": len(data)}
        except FileExistsError:
            return _cached(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cache_image(source_url: str, media_dir: Path | str | None = None,
                retries: int = 1) -> dict:
    """Create both v2 profiles once, reusing v1 or downloading a new source once.

    Existing v1 bytes are never changed. Backfill is explicitly derived from that
    JPEG; new URLs are encoded directly from their downloaded original. Reports
    distinguish those inputs and include each profile's independent status/size.
    """
    started = time.monotonic()
    profiles = {}
    result = {"url": source_url, "version": VERSION, "profiles": profiles,
              "input_origin": None, "network_bytes": 0}
    try:
        missing = []
        for variant in VARIANTS:
            path = path_of(source_url, media_dir, variant=variant)
            if path.is_file():
                profiles[variant] = {**_cached(path), "max_side": _profile(variant)[0],
                                     "quality": _profile(variant)[1]}
            else:
                missing.append(variant)
        if missing:
            legacy = path_of(source_url, media_dir, variant="v1")
            if legacy.is_file():
                raw = legacy.read_bytes()
                result["input_origin"] = "v1_cache"
            else:
                for attempt in range(retries + 1):
                    try:
                        raw = download(source_url)
                        break
                    except DownloadError as exc:
                        if not exc.retryable or attempt == retries:
                            raise
                        time.sleep(min(2 ** attempt, 4))
                result["input_origin"] = "source"
                result["network_bytes"] = len(raw)
            result["src_bytes"] = len(raw)
            with _ENCODE_SLOTS:
                encoded = _encode_variants(raw, tuple(missing))
            for variant in missing:
                try:
                    data, dimensions = encoded[variant]
                    profiles[variant] = _publish(path_of(source_url, media_dir, variant=variant),
                                                 data, dimensions)
                except Exception as exc:
                    profiles[variant] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        for variant in VARIANTS:
            if variant not in profiles:
                profiles[variant] = {"status": "failed", "error": result["error"]}
    statuses = {profile["status"] for profile in profiles.values()}
    result["status"] = ("failed" if "failed" in statuses else
                        "generated" if "generated" in statuses else "cached")
    result["dst_bytes"] = sum(profile.get("dst_bytes", 0) for profile in profiles.values())
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result
