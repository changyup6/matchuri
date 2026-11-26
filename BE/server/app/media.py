# -*- coding: utf-8 -*-
"""설문에 붙는 사진을 받아 저장한다.

**왜 따로 두나.** `state/*.jsonl` 은 기동 때 전부 다시 먹이는 기록이다. 사진은 기록이
아니라 파일이고, 재생할 것이 없다. 한 볼륨에 섞으면 복원이 사진 폴더까지 훑게 되고
백업 단위도 엉킨다. 그래서 `media` 볼륨을 따로 붙인다.

**왜 DB 에 넣지 않나.** 바이너리를 행에 넣으면 조회마다 서버를 거쳐야 하고, 백업이
수십 배로 커지고, 캐시도 안 된다. 파일로 두면 nginx 가 바로 내보낸다(`/media/`).
DB 가 필요해지는 것은 조회 조건이 복잡해질 때이고, 사진은 그럴 것이 없다.

**들어온 것을 그대로 저장하지 않는다.** 전부 JPEG 로 다시 굽는다. 이유가 넷이다.

    ① EXIF 를 없앤다      휴대폰 사진에는 **촬영 좌표가 들어 있다**. 그대로 공개 URL 로
                          내보내면 사용자의 동선이 새어 나간다. 다시 구우면 메타데이터가
                          구조적으로 사라진다 — 지우는 것이 아니라 애초에 안 넣는다
    ② 회전을 굽는다        EXIF 를 없애기 **전에** Orientation 을 적용해야 한다. 안 하면
                          세로로 찍은 사진이 눕는다 (메타데이터만 지우면 반드시 생긴다)
    ③ 크기를 묶는다        긴 변 1,600px. 카드에 쓰기 충분하고, 앱이 4,000px 짜리를
                          받아서 줄이는 비용을 없앤다(사진 렌더링이 느린 원인이다)
    ④ 진짜 이미지인지 본다  확장자와 Content-Type 은 보내는 쪽이 정한다. 디코딩해 보는
                          것만이 확인이다

**파일 이름은 uuid 다.** 공개 URL 이므로 순서대로 붙이면 남의 사진을 훑을 수 있다.
앞 두 글자로 폴더를 나눈다 — 한 폴더에 수만 개가 쌓이면 파일시스템이 느려진다.
"""
from __future__ import annotations

import io
import logging
import os
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app import observe

#: `BE/server/app/media.py` -> parents[2] = BE
ROOT = Path(__file__).resolve().parents[2]
DIR = Path(os.environ.get("MATCHURI_MEDIA") or (ROOT / "media"))

#: 받는 원본의 상한. nginx 에도 같은 값을 걸어야 한다(`client_max_body_size`) —
#: 여기까지 오기 전에 잘라야 10MB 를 메모리에 올리지 않는다.
MAX_BYTES = 10 * 1024 * 1024

#: 저장할 때 긴 변의 상한(px).
MAX_SIDE = 1600

#: 한 사람이 한 가게에 붙일 수 있는 장수.
MAX_PER_CARD = 3

#: 한 사람이 올릴 수 있는 사진 총량.
#:
#: 전에는 '길찾기한 카드에만 올릴 수 있다' 가 사실상의 상한이었다. 설문이 길찾기를
#: 요구하지 않게 되면서(`app/journeys.py: answer`) 그 울타리가 없어졌고, 그러면
#: 카드 2,795장 × 3장 = 8,385장까지 한 사람이 밀어 넣을 수 있다. 디스크가 먼저 찬다.
#: 열 곳 × 3장이면 정상 사용에 충분하다.
MAX_PER_USER = 30

#: 받는 형식. 값은 표시용 이름이다. 저장은 전부 JPEG 다.
#: HEIC 는 받지 않는다 — Pillow 가 기본으로 못 읽고, 앱의 image_picker 는 JPEG 를 준다.
ACCEPT = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WebP"}

#: 디코딩 폭탄 방어. 작은 파일이 거대한 비트맵으로 펴지는 것을 막는다.
#: Pillow 기본값(약 1.78억)은 너무 크다 — 1,600px 로 줄일 것이니 5천만이면 넉넉하다.
Image.MAX_IMAGE_PIXELS = 50_000_000


class Rejected(Exception):
    """받을 수 없는 파일. 메시지는 사용자에게 그대로 보여도 되는 말로 쓴다."""


def path_of(photo_id: str) -> Path:
    return DIR / photo_id[:2] / f"{photo_id}.jpg"


def url_of(photo_id: str) -> str:
    """nginx 가 `/media/` 를 이 폴더로 내보낸다(`nginx/default.conf`)."""
    return f"/media/{photo_id[:2]}/{photo_id}.jpg"


def save(raw: bytes, content_type: str | None) -> dict:
    """사진 하나를 저장하고 `{photo_id, url, bytes, width, height}`.

    받을 수 없으면 `Rejected`. 저장에 실패하면 `OSError` 를 그대로 올린다 — 사진은
    기록과 달리 조용히 버리면 안 된다(사용자는 올렸다고 생각한다).
    """
    if not raw:
        raise Rejected("빈 파일입니다")
    if len(raw) > MAX_BYTES:
        raise Rejected(f"파일이 너무 큽니다 (최대 {MAX_BYTES // (1024 * 1024)}MB)")
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct and ct not in ACCEPT:
        raise Rejected(f"지원하지 않는 형식입니다: {ct} "
                       f"(가능: {', '.join(ACCEPT.values())})")

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()                       # 여기서 실제로 디코딩한다 = 진짜 이미지인지 확인
    except UnidentifiedImageError:
        raise Rejected("이미지로 읽을 수 없습니다")
    except Image.DecompressionBombError:
        raise Rejected("이미지가 너무 큽니다")
    except (OSError, ValueError) as exc:
        # 잘린 파일·깨진 파일. 어떤 예외가 오는지 형식마다 달라 넓게 잡는다.
        raise Rejected("이미지가 손상되었습니다") from exc

    if img.format and img.format not in ACCEPT.values():
        raise Rejected(f"지원하지 않는 형식입니다: {img.format}")

    # **회전을 먼저 굽는다.** 아래에서 메타데이터가 사라지므로 순서가 바뀌면 눕는다.
    img = ImageOps.exif_transpose(img)

    # 투명 배경은 흰색에 얹는다 — JPEG 에 알파가 없어서, 안 하면 검게 나온다.
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[-1])
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)   # 확대는 하지 않는다

    buf = io.BytesIO()
    # exif 를 넘기지 않으므로 메타데이터가 들어가지 않는다. progressive JPEG는
    # 지원하는 디코더에서 전체 윤곽이 먼저 보이고 점차 선명해질 수 있다.
    img.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    data = buf.getvalue()

    photo_id = uuid.uuid4().hex
    p = path_of(photo_id)
    tmp = p.with_suffix(".jpg.tmp")
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_bytes(data)
        tmp.replace(p)                   # 원자적 — 반쯤 쓰인 사진이 서빙되지 않는다
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    observe.log("media.save", photo_id=photo_id, bytes=len(data),
                src_bytes=len(raw), w=img.width, h=img.height)
    return {"photo_id": photo_id, "url": url_of(photo_id), "bytes": len(data),
            "width": img.width, "height": img.height}


def delete(photo_id: str) -> bool:
    """파일을 지운다. 없으면 `False`(이미 지워진 것도 성공으로 본다)."""
    try:
        path_of(photo_id).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        observe.log("media.delete_fail", level=logging.WARNING,
                    photo_id=photo_id, error=type(exc).__name__)
        return False


def writable() -> bool:
    """`/readyz` 가 쓴다 — 볼륨이 안 붙었으면 업로드가 전부 실패한다."""
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        probe = DIR / ".probe"
        probe.write_bytes(b"1")
        probe.unlink()
        return True
    except OSError:
        return False
