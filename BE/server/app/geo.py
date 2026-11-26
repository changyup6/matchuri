# -*- coding: utf-8 -*-
"""거리 — 사용자 위치에서 가게까지 몇 m 인가.

카드에 `distance_m` 을 붙이고 `within_m` 필터를 만드는 자리다. 역삼동 한 동네(위도
37.490~37.510, 경도 127.025~127.052)라 정확한 측지선은 필요 없지만 **하버사인은 쓴다** —
평면 근사는 위도/경도 1도의 길이가 다른 것(여기서 111km 대 88km)을 손으로 보정해야 하고,
그 보정을 잊으면 동서로 25% 짧게 재는 버그가 조용히 남는다.

좌표는 가게 테이블에서 한 번 뽑아 캐시한다(`recsys.store` 의 mtime 이 바뀌면 다시 만든다 —
`app/loader.py` 와 같은 방식). 2,795곳 × 좌표 두 개라 메모리는 무시할 만하고, 필터가
요청마다 전부 훑어도 dict 조회 한 번씩이다.
"""
from __future__ import annotations

import math
import threading

from recsys import store as svc

#: 지구 평균 반지름 (m). WGS84 장반경이 아니라 평균값 — 하버사인 자체가 구 근사다.
EARTH_R = 6_371_008.8

_lock = threading.Lock()
_mtime = None
_coords: dict[str, tuple[float, float]] = {}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 점 사이 대권 거리 (m)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def coords() -> dict[str, tuple[float, float]]:
    """store_id -> (lat, lon). 좌표가 없는 가게는 아예 들어 있지 않다.

    '좌표 없음' 을 (0, 0) 으로 채우면 적도 어딘가에 있는 가게가 되어 거리 필터가
    조용히 거짓말을 한다. 없는 것은 없는 대로 두고 호출자가 None 을 받게 한다.
    """
    global _mtime
    svc.load()
    cur = tuple(sorted(svc._mt.items()))
    with _lock:
        if _mtime == cur and _coords:
            return _coords
        _coords.clear()
        for sid in svc.all_store_ids():
            s = svc.get_store(sid) or {}
            lat, lon = s.get("lat"), s.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                _coords[sid] = (float(lat), float(lon))
        _mtime = cur
        return _coords


def coord_of(store_id: str) -> tuple[float, float] | None:
    return coords().get(store_id)


def distance_m(store_id: str, lat: float | None, lon: float | None) -> int | None:
    """가게까지의 거리(m, 정수). 위치를 모르거나 가게 좌표가 없으면 None."""
    if lat is None or lon is None:
        return None
    c = coord_of(store_id)
    if c is None:
        return None
    return round(haversine_m(lat, lon, c[0], c[1]))


def within(lat: float, lon: float, radius_m: float) -> set[str]:
    """반경 안의 store_id 집합. 좌표가 없는 가게는 **빠진다**(모르면 통과시키지 않는다)."""
    return {sid for sid, (slat, slon) in coords().items()
            if haversine_m(lat, lon, slat, slon) <= radius_m}
