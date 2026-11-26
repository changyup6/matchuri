# -*- coding: utf-8 -*-
"""설문 사진 저장.

여기서 잠그는 것 중 하나는 **개인정보**다. 휴대폰 사진에는 촬영 좌표가 EXIF 로 들어
있고, 그것을 공개 URL 로 내보내면 사용자가 어디 있었는지가 새어 나간다. 예외도 로그도
남지 않고, 사진은 정상으로 보인다. 그래서 여기서 막지 않으면 잡을 방법이 없다.

두 번째는 **회전**이다. EXIF 를 지우기 전에 Orientation 을 적용해야 하는데, 순서가
바뀌면 세로 사진이 눕는다. 이것도 조용히 일어난다.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app import media


def _jpeg(size=(120, 60), exif: Image.Exif | None = None, color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    if exif is not None:
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def _with_gps() -> bytes:
    """촬영 좌표가 박힌 사진. 실제 휴대폰 사진이 이 모양이다."""
    ex = Image.Exif()
    ex[271] = "TestPhone"                      # Make
    ex[272] = "TestModel"                      # Model
    ex[306] = "2026:09:15 12:34:56"            # DateTime
    ex[34853] = {                              # GPSInfo — 역삼역 근처
        1: "N", 2: (37.0, 30.0, 2.0),
        3: "E", 4: (127.0, 2.0, 11.0),
    }
    return _jpeg(exif=ex)


@pytest.fixture(autouse=True)
def 임시저장소(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "DIR", tmp_path / "media")
    return tmp_path


# ──────────────────────────────────────────── 개인정보
def test_촬영_좌표가_남지_않는다():
    """이게 깨지면 사용자의 위치가 공개 URL 로 나간다."""
    got = media.save(_with_gps(), "image/jpeg")
    out = Image.open(media.path_of(got["photo_id"]))
    ex = out.getexif()
    assert not ex.get(34853), "GPS 가 남았다"
    assert not ex.get(271) and not ex.get(272), "기기 정보가 남았다"
    assert not ex.get(306), "촬영 시각이 남았다"


def test_원본에는_좌표가_있었다():
    """위 테스트가 '원래도 없었다' 로 통과하는 것을 막는다."""
    assert Image.open(io.BytesIO(_with_gps())).getexif().get(34853)


# ──────────────────────────────────────────── 회전
def test_세로로_찍은_사진이_눕지_않는다():
    """Orientation=6 은 '왼쪽으로 90도 돌려 보여라'. 적용하면 가로·세로가 바뀐다."""
    ex = Image.Exif()
    ex[274] = 6
    got = media.save(_jpeg(size=(120, 60), exif=ex), "image/jpeg")
    assert (got["width"], got["height"]) == (60, 120)


def test_회전_정보가_없으면_그대로다():
    got = media.save(_jpeg(size=(120, 60)), "image/jpeg")
    assert (got["width"], got["height"]) == (120, 60)


# ──────────────────────────────────────────── 크기
def test_긴_변을_상한으로_줄인다():
    got = media.save(_jpeg(size=(4000, 2000)), "image/jpeg")
    assert got["width"] == media.MAX_SIDE and got["height"] == media.MAX_SIDE // 2


def test_작은_사진을_늘리지_않는다():
    got = media.save(_jpeg(size=(200, 100)), "image/jpeg")
    assert (got["width"], got["height"]) == (200, 100)


# ──────────────────────────────────────────── 거절
def test_이미지가_아니면_거절한다():
    with pytest.raises(media.Rejected):
        media.save(b"PK\x03\x04 this is a zip", "image/jpeg")


def test_빈_파일을_거절한다():
    with pytest.raises(media.Rejected):
        media.save(b"", "image/jpeg")


def test_너무_크면_거절한다():
    with pytest.raises(media.Rejected):
        media.save(b"\xff\xd8" + b"x" * media.MAX_BYTES, "image/jpeg")


def test_지원하지_않는_형식을_거절한다():
    with pytest.raises(media.Rejected):
        media.save(_jpeg(), "image/gif")


def test_잘린_파일을_거절한다():
    with pytest.raises(media.Rejected):
        media.save(_jpeg(size=(400, 400))[:200], "image/jpeg")


# ──────────────────────────────────────────── 저장
def test_png_도_jpeg_로_저장된다():
    buf = io.BytesIO()
    Image.new("RGBA", (80, 80), (0, 200, 0, 128)).save(buf, "PNG")
    got = media.save(buf.getvalue(), "image/png")
    assert Image.open(media.path_of(got["photo_id"])).format == "JPEG"


def test_투명_배경은_흰색이_된다():
    """안 하면 JPEG 에 알파가 없어서 검게 나온다."""
    buf = io.BytesIO()
    Image.new("RGBA", (40, 40), (255, 255, 255, 0)).save(buf, "PNG")
    got = media.save(buf.getvalue(), "image/png")
    assert Image.open(media.path_of(got["photo_id"])).getpixel((20, 20)) == (255, 255, 255)


def test_이름이_순서대로가_아니다():
    """공개 URL 이다. 순번이면 남의 사진을 훑을 수 있다."""
    ids = {media.save(_jpeg(), "image/jpeg")["photo_id"] for _ in range(5)}
    assert len(ids) == 5
    assert all(len(i) == 32 for i in ids)


def test_url_과_경로가_맞는다():
    got = media.save(_jpeg(), "image/jpeg")
    pid = got["photo_id"]
    assert got["url"] == f"/media/{pid[:2]}/{pid}.jpg"
    assert media.path_of(pid).exists()


def test_임시파일이_남지_않는다():
    got = media.save(_jpeg(), "image/jpeg")
    assert not list(media.path_of(got["photo_id"]).parent.glob("*.tmp"))


def test_지운다():
    got = media.save(_jpeg(), "image/jpeg")
    assert media.delete(got["photo_id"]) is True
    assert not media.path_of(got["photo_id"]).exists()
    assert media.delete(got["photo_id"]) is False      # 두 번 지워도 터지지 않는다
