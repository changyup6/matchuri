"""Actual codec tests and isolated HTTP/SSRF tests; no remote downloads."""
from __future__ import annotations

import io
import json
import random
import socket
from types import SimpleNamespace

import pytest
from PIL import Image

from app import catalog_media as catalog


URL = "https://images.example.test/original.jpg?size=original"


def source(size=(80, 40), mode="RGB", color="red", format="JPEG", **kwargs):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format, **kwargs)
    return buffer.getvalue()


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog.media, "DIR", tmp_path / "media")
    monkeypatch.setenv("MATCHURI_MEDIA_PUBLIC_BASE_URL", "https://api.example.test/")
    return tmp_path / "media"


def test_encoding_preserves_aspect_bounds_and_progressive_without_metadata():
    exif = Image.Exif()
    exif[274] = 6
    exif[271] = "Private camera"
    exif[34853] = {1: "N", 2: (37, 30, 0), 3: "E", 4: (127, 0, 0)}
    raw = source((4000, 2000), exif=exif)
    data, metrics = catalog.encode(raw)
    with Image.open(io.BytesIO(data)) as output:
        assert output.format == "JPEG"
        assert output.mode == "RGB"
        assert output.size == (320, 640)
        assert output.info.get("progressive")
        assert not output.getexif()
        assert not output.info.get("icc_profile")
    assert (metrics["src_width"], metrics["src_height"]) == (4000, 2000)
    assert (metrics["width"], metrics["height"]) == (320, 640)


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_small_sources_never_upscale(format):
    output, _ = catalog.encode(source((37, 23), format=format))
    assert Image.open(io.BytesIO(output)).size == (37, 23)


def test_alpha_flattens_white_and_animation_uses_first_frame():
    data, _ = catalog.encode(source((40, 30), mode="RGBA", color=(0, 0, 0, 0), format="PNG"))
    assert Image.open(io.BytesIO(data)).getpixel((10, 10)) == (255, 255, 255)
    animation = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(
        animation, "GIF", save_all=True,
        append_images=[Image.new("RGB", (40, 30), "blue")], duration=100, loop=0)
    data, _ = catalog.encode(animation.getvalue())
    r, g, b = Image.open(io.BytesIO(data)).getpixel((10, 10))
    assert r > 240 and g < 10 and b < 10


def test_mobile_multi_picture_jpeg_encodes_primary_frame_as_progressive_jpeg():
    multipicture = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(
        multipicture, "MPO", save_all=True,
        append_images=[Image.new("RGB", (40, 30), "blue")])
    raw = multipicture.getvalue()
    original = Image.open(io.BytesIO(raw))
    assert original.format == "MPO" and original.n_frames == 2
    data, metrics = catalog.encode(raw)
    with Image.open(io.BytesIO(data)) as output:
        assert output.format == "JPEG"
        assert output.info.get("progressive")
        r, g, b = output.getpixel((10, 10))
        assert r > 240 and g < 10 and b < 10
    assert metrics["src_bytes"] == len(raw)


def test_large_jpeg_uses_decoder_reduction_without_weakening_global_pixel_bound(monkeypatch):
    # Scale the safety boundary down for the fixture instead of allocating a
    # >100 megapixel bitmap. Pillow's normal opener rejects this JPEG first.
    raw = source((80, 40), progressive=True)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    monkeypatch.setattr(catalog, "MAX_PIXELS", 1000)
    monkeypatch.setattr(catalog, "MAX_SIDE", 8)
    with pytest.raises(Image.DecompressionBombError):
        Image.open(io.BytesIO(raw))
    encoded, metrics = catalog.encode(raw)
    assert Image.MAX_IMAGE_PIXELS == 1000
    assert (metrics["src_width"], metrics["src_height"]) == (80, 40)
    with Image.open(io.BytesIO(encoded)) as output:
        assert output.size == (8, 4)
        assert output.info.get("progressive")


def test_extreme_jpeg_header_rejected_before_allocating_bitmap():
    raw = bytearray(source((40, 30)))
    marker = raw.index(b"\xff\xc0")  # baseline JPEG start-of-frame dimensions
    raw[marker + 5:marker + 9] = b"\xff\xff\xff\xff"
    with pytest.raises(ValueError, match="JPEG source"):
        catalog.encode(bytes(raw))


def test_non_jpeg_pixel_bound_is_not_bypassed(monkeypatch):
    monkeypatch.setattr(catalog, "MAX_PIXELS", 1000)
    with pytest.raises(ValueError, match="source image"):
        catalog.encode(source((80, 40), format="PNG"))


def test_rejects_invalid_too_large_and_unsupported_images(monkeypatch):
    with pytest.raises(Exception):
        catalog.encode(b"not an image")
    with pytest.raises(ValueError, match="unsupported"):
        catalog.encode(source(format="BMP"))
    monkeypatch.setattr(catalog, "MAX_PIXELS", 100)
    with pytest.raises(ValueError, match="pixels"):
        catalog.encode(source((11, 10)))
    monkeypatch.setattr(catalog, "MAX_SOURCE_BYTES", 8)
    with pytest.raises(ValueError, match="size"):
        catalog.encode(b"123456789")


def test_completed_cache_is_readable_immutable_and_resumed_without_network(cache_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(catalog, "download", lambda url: calls.append(url) or source((300, 200)))
    first = catalog.cache_image(URL)
    destination = catalog.path_of(URL)
    original = destination.read_bytes()
    assert first["status"] == "generated"
    assert destination.stat().st_mode & 0o777 == 0o644
    assert catalog.public_url(URL) == catalog.url_of(URL)
    assert catalog.public_url(URL).startswith("https://api.example.test/media/catalog/v2/card/")
    again = catalog.cache_image(URL)
    assert again["status"] == "cached"
    assert calls == [URL]
    assert destination.read_bytes() == original
    assert not list(cache_dir.rglob("*.tmp"))


def seed_legacy(url=URL, size=(1280, 960)):
    data = source(size, quality=80, progressive=True)
    path = catalog.path_of(url, variant="v1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, data


def test_v2_profiles_have_distinct_sizes_quality_and_immutable_names(cache_dir, monkeypatch):
    raw = source((2000, 1000))
    calls = []
    decode_calls = []
    original_encoder = catalog._encode_variants
    monkeypatch.setattr(catalog, "download", lambda url: calls.append(url) or raw)
    def tracked_encoder(data, variants):
        decode_calls.append(variants)
        return original_encoder(data, variants)
    monkeypatch.setattr(catalog, "_encode_variants", tracked_encoder)
    result = catalog.cache_image(URL)
    assert calls == [URL] and decode_calls == [("card", "thumb")]
    assert result["input_origin"] == "source"
    assert result["network_bytes"] == len(raw)
    assert result["status"] == "generated"
    for variant, dimensions, quality in [("card", (640, 320), 72), ("thumb", (320, 160), 70)]:
        path = catalog.path_of(URL, variant=variant)
        assert f"/catalog/v2/{variant}/" in str(path)
        assert path.stat().st_mode & 0o777 == 0o644
        with Image.open(path) as actual:
            reference = Image.open(io.BytesIO(source((20, 10), quality=quality)))
            assert actual.size == dimensions
            assert actual.info.get("progressive")
            assert actual.quantization == reference.quantization
        profile = result["profiles"][variant]
        assert profile["quality"] == quality and profile["dst_bytes"] == path.stat().st_size
        assert catalog.public_url(URL, variant=variant) == catalog.url_of(URL, variant=variant)
    assert not catalog.path_of(URL, variant="v1").exists()


def test_existing_v1_backfills_both_profiles_without_downloading_or_changing_v1(cache_dir, monkeypatch):
    legacy, original = seed_legacy()
    previous_stat = legacy.stat()
    monkeypatch.setattr(catalog, "download", lambda _: pytest.fail("v1 backfill must not download"))
    result = catalog.cache_image(URL)
    assert result["input_origin"] == "v1_cache"
    assert result["network_bytes"] == 0
    assert result["src_bytes"] == len(original)
    assert all(result["profiles"][variant]["status"] == "generated" for variant in catalog.VARIANTS)
    assert legacy.read_bytes() == original
    assert legacy.stat().st_mtime_ns == previous_stat.st_mtime_ns
    assert legacy.stat().st_ino == previous_stat.st_ino
    assert catalog.cache_image(URL)["status"] == "cached"


def test_partial_profile_resume_preserves_completed_card_and_only_creates_thumb(cache_dir, monkeypatch):
    legacy, _ = seed_legacy()
    # Detailed texture exposes differences between JPEG draft factors; a flat
    # color can hide a resume path that silently changes the immutable bytes.
    noise = io.BytesIO()
    Image.frombytes("RGB", (1280, 960), random.Random(17).randbytes(1280 * 960 * 3)).save(
        noise, "JPEG", quality=80, progressive=True)
    legacy.write_bytes(noise.getvalue())
    expected_thumb = catalog._encode_variants(noise.getvalue(), ("card", "thumb"))["thumb"][0]
    monkeypatch.setattr(catalog, "download", lambda _: pytest.fail("use legacy bytes"))
    original_publish = catalog._publish
    def interrupt_thumb(path, data, metrics):
        if "thumb" in path.parts:
            raise OSError("simulated interrupted thumb publication")
        return original_publish(path, data, metrics)
    monkeypatch.setattr(catalog, "_publish", interrupt_thumb)
    first = catalog.cache_image(URL)
    assert first["status"] == "failed"
    assert first["profiles"]["card"]["status"] == "generated"
    assert first["profiles"]["thumb"]["status"] == "failed"
    card = catalog.path_of(URL)
    previous_bytes, previous_mtime = card.read_bytes(), card.stat().st_mtime_ns
    monkeypatch.setattr(catalog, "_publish", original_publish)
    second = catalog.cache_image(URL)
    assert second["status"] == "generated"
    assert second["profiles"]["card"]["status"] == "cached"
    assert second["profiles"]["thumb"]["status"] == "generated"
    assert card.read_bytes() == previous_bytes and card.stat().st_mtime_ns == previous_mtime
    assert catalog.path_of(URL, variant="thumb").read_bytes() == expected_thumb


def test_resolver_fallback_never_substitutes_wrong_v2_profile(cache_dir, monkeypatch):
    thumb = catalog.path_of(URL, variant="thumb")
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(source((320, 160)))
    monkeypatch.setattr(catalog, "download", lambda _: pytest.fail("resolver must not download"))
    monkeypatch.setattr(catalog, "encode", lambda *a, **kw: pytest.fail("resolver must not encode"))
    assert catalog.public_url(URL) == URL
    assert catalog.public_url(URL, variant="thumb") == catalog.url_of(URL, variant="thumb")
    legacy, _ = seed_legacy()
    assert "/catalog/v1/" in str(legacy)
    assert catalog.public_url(URL) == catalog.url_of(URL, variant="v1")
    card = catalog.path_of(URL)
    card.parent.mkdir(parents=True)
    card.write_bytes(source((640, 320)))
    thumb.unlink()
    assert catalog.public_url(URL) == catalog.url_of(URL)
    assert catalog.public_url(URL, variant="thumb") == catalog.url_of(URL, variant="v1")
    legacy.unlink()
    assert catalog.public_url(URL, variant="thumb") == URL


def test_cli_reports_each_profile_and_zero_network_for_legacy_backfill(cache_dir, tmp_path, monkeypatch, capsys):
    from cache_catalog_images import main
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "photos.jsonl").write_text(json.dumps({"url": URL}) + "\n")
    (data_dir / "menus.jsonl").write_text(json.dumps({"photo_url": URL}) + "\n")
    (data_dir / "reviews.jsonl").write_text("")
    seed_legacy()
    monkeypatch.setattr(catalog, "download", lambda _: pytest.fail("do not download v1 source again"))
    report = tmp_path / "audit.jsonl"
    args = ["--data-dir", str(data_dir), "--media-dir", str(cache_dir),
            "--report", str(report), "--workers", "2"]
    assert main(args) == 0
    result = json.loads(report.read_text())
    assert result["status"] == "generated" and result["network_bytes"] == 0
    assert set(result["profiles"]) == {"card", "thumb"}
    complete = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert complete["generated"] == 1 and complete["network_bytes"] == 0
    assert complete["profiles"]["card"]["generated"] == 1
    assert complete["profiles"]["thumb"]["generated"] == 1
    assert main(args) == 0
    second = json.loads(report.read_text().splitlines()[-1])
    assert second["status"] == "cached"
    assert all(profile["status"] == "cached" for profile in second["profiles"].values())


@pytest.mark.parametrize("base", ["", "/media", "ftp://example.test", "http://[broken",
                                  "https://user:pass@example.test", "https://example.test?q=x"])
def test_missing_or_invalid_public_base_keeps_original(cache_dir, monkeypatch, base):
    monkeypatch.setattr(catalog, "download", lambda _: source())
    assert catalog.cache_image(URL)["status"] == "generated"
    monkeypatch.setenv("MATCHURI_MEDIA_PUBLIC_BASE_URL", base)
    assert catalog.public_url(URL) == URL


def test_missing_failed_and_local_sources_keep_original_without_download(cache_dir, monkeypatch):
    monkeypatch.setattr(catalog, "download", lambda _: pytest.fail("resolver must not download"))
    assert catalog.public_url(URL) == URL
    assert catalog.public_url(None) is None
    assert catalog.public_url("") == ""
    assert catalog.public_url("/media/user-upload.jpg") == "/media/user-upload.jpg"
    assert catalog.path_of(URL) != catalog.path_of(URL + "&different=1")


def test_failed_conversion_never_publishes_and_retries_are_bounded(cache_dir, monkeypatch):
    monkeypatch.setattr(catalog, "download", lambda _: b"bad image")
    assert catalog.cache_image(URL)["status"] == "failed"
    assert not catalog.path_of(URL).exists()
    calls = []
    def fail(_):
        calls.append(1)
        raise catalog.DownloadError("temporary", retryable=True)
    monkeypatch.setattr(catalog, "download", fail)
    monkeypatch.setattr(catalog.time, "sleep", lambda _: None)
    assert catalog.cache_image(URL, retries=2)["status"] == "failed"
    assert len(calls) == 3


def test_all_catalog_sources_are_deduplicated_without_store_filter(tmp_path):
    rows = {
        "photos.jsonl": [{"url": "https://one.test/a"}, {"url": "https://two.test/b"}],
        "menus.jsonl": [{"photo_url": "https://one.test/a"}, {"photo_url": "https://three.test/c"}],
        "reviews.jsonl": [{"photo_urls": ["https://four.test/d"],
                           "photos": [{"url": "https://five.test/e"}, {"url": "https://two.test/b"}]}],
    }
    for filename, items in rows.items():
        (tmp_path / filename).write_text("\n".join(map(json.dumps, items)))
    assert list(catalog.iter_catalog_urls(tmp_path)) == [
        "https://one.test/a", "https://two.test/b", "https://three.test/c",
        "https://four.test/d", "https://five.test/e"]


def addresses(*ips):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM,
             socket.IPPROTO_TCP, "", (ip, 443)) for ip in ips]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1",
                                "::1", "fc00::1", "224.0.0.1", "::ffff:127.0.0.1"])
def test_private_or_special_dns_results_are_rejected(monkeypatch, ip):
    monkeypatch.setattr(catalog.socket, "getaddrinfo", lambda *a, **kw: addresses("1.1.1.1", ip))
    with pytest.raises(catalog.DownloadError, match="non-public"):
        catalog._target(URL)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:pass@example.test/x",
                                 "https://example.test:1234/x", "https://example.test/\r\nx"])
def test_unsafe_urls_rejected_before_dns(monkeypatch, url):
    monkeypatch.setattr(catalog.socket, "getaddrinfo", lambda *a, **kw: pytest.fail("no DNS"))
    with pytest.raises(catalog.DownloadError):
        catalog._target(url)


def test_connection_pins_verified_ip_and_preserves_tls_hostname(monkeypatch):
    sock = SimpleNamespace(close=lambda: None)
    calls = []
    monkeypatch.setattr(catalog.socket, "create_connection",
                        lambda address, timeout: calls.append((address, timeout)) or sock)
    monkeypatch.setattr(catalog, "_TLS_CONTEXT", SimpleNamespace(
        wrap_socket=lambda socket, server_hostname: calls.append(server_hostname) or socket))
    connection = catalog._connection("https", "images.example.test", 443, "1.1.1.1", 15)
    assert calls == [(("1.1.1.1", 443), 15), "images.example.test"]
    assert connection.sock is sock


@pytest.mark.parametrize("scheme,port,expected", [
    ("https", 443, b"Host: images.example.test\r\n"),
    ("http", 80, b"Host: images.example.test\r\n"),
    ("https", 80, b"Host: images.example.test:80\r\n"),
])
def test_pinned_socket_preserves_scheme_default_port_in_actual_host_header(
        monkeypatch, scheme, port, expected):
    sent = []
    sock = SimpleNamespace(close=lambda: None, sendall=lambda data: sent.append(data))
    monkeypatch.setattr(catalog.socket, "create_connection", lambda *a, **kw: sock)
    monkeypatch.setattr(catalog, "_TLS_CONTEXT", SimpleNamespace(
        wrap_socket=lambda socket, server_hostname: socket))
    connection = catalog._connection(scheme, "images.example.test", port, "1.1.1.1", 15)
    connection.request("GET", "/original.jpg")
    wire = b"".join(sent)
    assert expected in wire
    if port == 443:
        assert b"Host: images.example.test:443" not in wire


class Response:
    def __init__(self, status=200, headers=None, body=b"valid-image-placeholder"):
        self.status = status
        self.headers = headers or {}
        self.chunked = self.headers.get("Transfer-Encoding", "").lower() == "chunked"
        self.body = io.BytesIO(body)
    def getheader(self, name):
        return self.headers.get(name)
    def read1(self, size):
        return self.body.read(size)


class Connection:
    sock = None
    def __init__(self, response):
        self.response = response
        self.request_headers = None
        self.closed = False
    def request(self, method, target, headers):
        self.request_headers = headers
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


def test_redirect_cannot_reach_internal_target(monkeypatch):
    monkeypatch.setattr(catalog.socket, "getaddrinfo", lambda host, *a, **kw:
                        addresses("127.0.0.1" if host == "internal.test" else "1.1.1.1"))
    connection = Connection(Response(302, {"Location": "http://internal.test/private"}))
    calls = []
    monkeypatch.setattr(catalog, "_connection", lambda *a: calls.append(a) or connection)
    with pytest.raises(catalog.DownloadError, match="non-public"):
        catalog.download(URL)
    assert len(calls) == 1
    assert connection.closed


def test_download_bounds_actual_bytes_without_content_length_and_uses_referer(monkeypatch):
    monkeypatch.setattr(catalog.socket, "getaddrinfo", lambda *a, **kw: addresses("1.1.1.1"))
    connection = Connection(Response(body=b"123456789"))
    monkeypatch.setattr(catalog, "_connection", lambda *a: connection)
    monkeypatch.setattr(catalog, "MAX_SOURCE_BYTES", 8)
    with pytest.raises(catalog.DownloadError, match="exceeds"):
        catalog.download("https://blogfiles.pstatic.net/image.jpg")
    assert connection.request_headers["Referer"] == "https://blog.naver.com/"
    assert "avif" not in connection.request_headers["Accept"]
    assert connection.closed


def test_truncated_content_length_is_a_retryable_network_failure(monkeypatch):
    monkeypatch.setattr(catalog.socket, "getaddrinfo", lambda *a, **kw: addresses("1.1.1.1"))
    connection = Connection(Response(headers={"Content-Length": "100"}, body=b"truncated"))
    monkeypatch.setattr(catalog, "_connection", lambda *a: connection)
    with pytest.raises(catalog.DownloadError, match="incomplete") as error:
        catalog.download(URL)
    assert error.value.retryable
    assert connection.closed


def test_chunked_response_ignores_conflicting_content_length(monkeypatch):
    monkeypatch.setattr(catalog.socket, "getaddrinfo", lambda *a, **kw: addresses("1.1.1.1"))
    connection = Connection(Response(headers={"Transfer-Encoding": "chunked", "Content-Length": "100"},
                                     body=b"complete"))
    monkeypatch.setattr(catalog, "_connection", lambda *a: connection)
    assert catalog.download(URL) == b"complete"
