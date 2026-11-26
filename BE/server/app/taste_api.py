# -*- coding: utf-8 -*-
"""취향 전달 — 사용자에게 보여 줄 그림 **하나**: 취향 지형도 위의 내 궤적.

`/admin/map` 은 관리자용이다(2,216개 점 · 성분 슬라이더 8개 · 연결선). 사용자에게는
그중 **하나만** 남긴다. 왜 이것 하나인가:

  · **성분 가중치 8개는 사용자가 읽을 수 없다.** `공방문 +0.45` 가 무슨 뜻인지는
    모델을 아는 사람만 안다. 그건 관리자 화면의 언어다.
  · **카테고리 막대는 옛 정책의 언어다.** 이 정책이 세는 것은 카테고리가 아니라
    콘텐츠 유사도이고(곱창전골 ↔ 모츠나베는 업종이 다르다), 막대를 그리면 우리가
    쓰지 않는 모델을 사용자에게 설명하는 셈이 된다.
  · 지형도는 **콘텐츠 유사도로 편 같은 땅** 위에 '내 취향의 중심이 어디에 있고
    어디로 움직였나' 를 점 하나의 이동으로 말한다 — **변화가 곧 그림이다.** 스와이프를
    더 하면 그림이 달라지는 것을 사용자가 눈으로 본다(다른 어떤 표현도 그러지 못한다).

지형(`map.regions`)은 기본 cat2 배경에서 cat3, 개별 가게로 확대할 수 있다.
`detail=1·2·3`은 전체 배경의 표시 단계만 바꾸며, 같은 전역 좌표의 궤적과 현재 점은 유지한다.
분류와 가게 모두 기존 UMAP 좌표를 사용하고, 확대용 분류는 취향 점수를 뜻하지 않는다.

**지도의 위치와 취향 설명은 다르다.** `region`은 현재 2D 점 주변 가게의 업종을
읽은 공간 라벨이다. 좋아요 좌표의 평균이 선택하지 않은 업종 근처에 놓일 수 있으므로
그 이름이나 `drift.distance`로 음식 취향·취향 확정·변화를 단정하지 않는다. UMAP은
국소 관계를 시각화한 투영이며, 10 like 창이라고 이동 거리가 취향 변화량이 되는 것도 아니다.
`headline`은 좌표와 독립적으로 현재 세션의 유효 좋아요를 요약한다. 추천 모델과 같은
최근 반응 감쇠를 쓰되 업종은 관측 설명에만 쓰고 고차원 추천 점수를 대체하지 않는다.

**EMA 중심의 함정과 `near`.** 궤적의 점은 like 좌표의 EMA(α=0.35)다. 취향이 두 덩어리면
(한식도 좋고 일식도 좋다) 그 평균은 **두 군집 사이의 빈 땅**에 앉는다 — 아무 가게도 없는
곳이 '내 취향' 으로 보인다. 그래서 현재 점에는 항상 `near` 3곳(그 점에 가장 가까운 **내가
좋아요한 가게**)을 같이 싣는다. 화면은 좌표 대신 그 이름들을 읽으면 되고, 이름은 좌표가
거짓말을 해도 거짓말하지 않는다.

**읽기 전용이다.** `/admin/map` 과 같은 규율(`app/admin.py` 의 지형도 절 참고):
카드를 `shown` 에 넣지 않고, 노출 로그를 쓰지 않고, 어떤 모델도 갱신하지 않는다. 세션은
`taste.sessions()` 로 **읽기만** 한다 — 사용자가 자기 취향 화면을 몇 번 열어도
팀-드래프트 인터리빙의 like 율이 오염되지 않아야 한다
(`tests/test_taste_api.py::test_취향_API_는_아무것도_바꾸지_않는다`).
"""
from __future__ import annotations

import json
import math
import statistics
import threading
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import identities
from app import loader
from app import observe
from app import taste_hierarchy
from app import users as user_registry
from recsys import content, store, taste

router = APIRouter(tags=["취향"])
router.include_router(taste_hierarchy.router)
STATIC = Path(__file__).parent / "static"

LAYOUT_PATH = content.SERVING / "layout.json"

#: 지형 응답의 버전. 앱이 지형을 캐시하므로(하루에 바뀔 값이 아니다) 모양이 바뀌면
#: 이 숫자를 올려 클라이언트가 캐시를 버릴 근거를 준다.
MAP_VERSION = 1

#: 궤적 한 점의 EMA 계수. 0.35 는 '최근 like 3~4장이 점의 위치를 대체로 정한다' 는
#: 뜻이다(0.35 의 반감기 ≈ 1.6장). 더 작으면 궤적이 안 움직여 그림이 죽고, 더 크면
#: 카드 한 장이 점을 지도 반대편으로 던져 **변화가 아니라 소음**이 그려진다.
TRAIL_ALPHA = 0.35

#: 화면에 남길 궤적 길이. 폰 화면에 60점이면 이미 촘촘하고, 응답도 가벼워야 한다.
TRAIL_MAX = 60

#: `drift` 가 보는 창 — 몇 like 전과 비교하나.
DRIFT_WINDOW = 10

#: 현재 점에 붙일 앵커(좋아요한 가게) 수.
NEAR_K = 3

#: 지도상 위치 라벨에 투표하는 이웃 수. 기존 표시용 휴리스틱이며 취향 판정에 쓰지 않는다.
REGION_K = 15

#: 현재 유효 좋아요가 서로 다른 3곳 미만이면 분류 요약 대신 수집 안내를 보낸다.
MIN_LIKES = 3

_lock = threading.Lock()
_cache: dict = {"key": None, "value": None}

Detail = Annotated[int, Query(ge=1, le=3, description="전체 지도 표시 단계: 1=cat2, 2=cat3, 3=가게")]


# ───────────────────────────────────────────────── 응답 모델 (`/openapi.json`)
# 화면 하나가 쓰는 값이 전부 여기 있다. 좌표는 [0,1]² 이고 두 축의 배율이 같다 —
# 프론트는 프레임 크기만 곱하면 된다.
class RegionOut(BaseModel):
    """지형의 원 하나 — 업종(cat2) 한 무리. **그리기 위한 요약이다**(경계가 아니다)."""

    name: str = Field(description="업종 이름", examples=["한식"])
    x: float = Field(description="구성원 좌표의 중앙값 x ([0,1])", examples=[0.2131])
    y: float = Field(description="구성원 좌표의 중앙값 y ([0,1])", examples=[0.4408])
    r: float = Field(description="중심까지 거리의 중앙값. 절반은 이 원 밖에 있다",
                     examples=[0.1042])
    count: int = Field(description="그 업종의 가게 수", examples=[710])


class NearOut(BaseModel):
    """현재 점 근처의 **내가 좋아요한 가게** — 좌표의 통역이다."""

    name: str = Field(description="가게 이름", examples=["역삼동참치"])
    card_id: int | None = Field(
        description="그 가게의 카드 id(`GET /cards/{card_id}` 로 열 수 있다). "
                    "후보 풀 밖이면 `null`", examples=[1108])


class TrailPointOut(BaseModel):
    """궤적의 한 점 — like 한 장이 점 하나다(위치는 like 좌표의 EMA α=0.35)."""

    n: int | None = Field(description="그 like 가 몇 번째 스와이프였나", examples=[12])
    x: float = Field(description="점의 x ([0,1])", examples=[0.3555])
    y: float = Field(description="점의 y ([0,1])", examples=[0.31])
    region: str | None = Field(
        description="지도상 위치의 주변 업종(15개 이웃 다수결). 선택 음식이나 선호 업종을 뜻하지 않음. 지형이 비면 null",
        examples=["한식"])
    store: str = Field(description="그 like 한 가게 이름", examples=["역삼동참치"])
    card_id: int | None = Field(description="그 가게의 카드 id. 풀 밖이면 `null`",
                               examples=[1108])


class NowOut(BaseModel):
    """지금 내 취향의 자리. `near` 를 함께 읽어야 좌표가 거짓말을 해도 안전하다."""

    x: float = Field(description="현재 점의 x", examples=[0.3555])
    y: float = Field(description="현재 점의 y", examples=[0.31])
    region: str | None = Field(description="지도상 위치의 주변 업종. 실제 선호 업종이나 취향 확정이 아님", examples=["한식"])
    near: list[NearOut] = Field(
        description="그 점에 가장 가까운 좋아요 가게 3곳(좋아요가 적으면 그만큼)")


class DriftOut(BaseModel):
    """최근 10 like 전과 지금의 지도상 이동. 취향 변화량이나 음식 전환이 아니다."""

    from_region: str | None = Field(description="10 like 전 지도상 위치의 주변 업종. 당시 선호 업종을 뜻하지 않음",
                                    examples=["한식"])
    to_region: str | None = Field(description="현재 지도상 위치의 주변 업종. 현재 선호 업종을 뜻하지 않음", examples=["일식"])
    distance: float = Field(description="[0,1]² 지도 좌표의 유클리드 거리. 고차원 취향 변화량이 아님", examples=[0.1421])
    window: int = Field(description="비교한 창의 크기(like 개수)", examples=[10])


class TasteMapOut(BaseModel):
    """`GET /taste/map` — 모두가 같은 땅. `version` 이 달라지면 캐시를 버리세요."""

    version: int = Field(description="지형 버전(모양이 바뀌면 올라간다)", examples=[1])
    regions: list[RegionOut] = Field(description="업종 원 20개(구성원이 없는 업종은 빠진다)")


class UserTasteMapOut(BaseModel):
    """User-view background; detail changes only this map, never the taste trail."""

    version: Literal[3] = Field(3, description="사용자 지도 형식 버전. 기존 v1·v2 캐시를 갱신")
    level: Literal[1, 2, 3] = Field(description="현재 표시 단계: 1=전체 cat2, 2=전체 cat3, 3=전체 가게")
    regions: list[taste_hierarchy.TasteMapNodeOut] = Field(
        description="현재 단계의 전체 분류 또는 가게. 특정 상위 분류로 제한하지 않음")


class UserTasteOut(BaseModel):
    """`GET /users/{user_id}/taste` — 화면 한 장에 필요한 전부. 읽기 전용이다."""

    user_id: str = Field(description="그 사용자의 id", examples=["u_3f2a1b4c5d6e"])
    name: str | None = Field(description="이름. 없으면 `null`", examples=["김철수"])
    swipes: int = Field(description="누적 스와이프 수", examples=[42])
    likes: int = Field(description="그중 like 수", examples=[17])
    map: UserTasteMapOut = Field(description="고정 지형의 전체 표시 단계. detail 기본1, 2=cat3, 3=가게")
    trail: list[TrailPointOut] = Field(
        description="like 마다 한 점(최근 60개, 오래된 것부터)")
    now: NowOut | None = Field(description="현재 점. like 가 없으면 `null`")
    drift: DriftOut | None = Field(
        description="최근 10 like 의 이동. like 가 11개 미만이면 `null`")
    headline: str = Field(
        description="현재 세션의 유효 좋아요를 최근 반응에 더 무게를 두어 요약한 관측 설명. 지도 위치·취향 확정·추천 점수와 별개",
        examples=["최근 좋아요에서는 육류,고기 쪽이 두드러져요"])


# ────────────────────────────────────────────────────────── 지형 (고정)
def _fold(layout: dict) -> dict:
    """`layout.json` 을 **화면이 쓰는 모양**으로 한 번 접는다.

    지역 하나는 그 cat2 구성원 좌표의 **중앙값**(x, y)과, 중심까지 거리의 **중앙값**
    r 이다. 평균이 아니라 중앙값인 이유: UMAP 은 군집을 떼어 놓기 때문에 한 업종이
    두 덩어리로 갈리는 일이 흔하고(카페가 그렇다) 평균은 그 사이 빈 땅에 앉는다.
    r 도 마찬가지로 최대값이면 이상점 하나가 원을 지도만큼 키운다 — '대충 이 근처'
    를 그리는 원이라 중앙값이 맞다.

    **중심과 r 은 그리기 전용이다.** 점에 이름을 붙이는 일은 `_region_at` 의 kNN
    다수결이 한다(그쪽 문서 참고) — 그래서 여기서 좌표 배열(2,216×2)과 가게별 업종을
    같이 만들어 둔다. 조회 한 번이 벡터 거리 한 번 + `argpartition` 한 번이 되도록
    **한 번만** 만드는 것이 요점이다(궤적 60점이면 60번 부른다).

    가게 좌표·이름도 같이 접어 둔다. 궤적은 like 한 가게의 좌표만 필요하고, 이름은
    `layout.json` 안에 이미 있어 여기서 `recsys.store` 를 다시 뒤질 이유가 없다.
    """
    cats = list(layout.get("cats") or [])
    ids = list(layout.get("ids") or [])
    xs, ys = layout["x"], layout["y"]
    cat_of = layout["cat"]
    names = layout.get("name") or []

    members: dict[int, list[int]] = {}
    for i in range(len(ids)):
        members.setdefault(int(cat_of[i]), []).append(i)

    regions = []
    for c, name in enumerate(cats):
        idx = members.get(c)
        if not idx:                       # 구성원이 없는 업종은 그릴 원이 없다
            continue
        cx = statistics.median(xs[i] for i in idx)
        cy = statistics.median(ys[i] for i in idx)
        r = statistics.median(math.dist((xs[i], ys[i]), (cx, cy)) for i in idx)
        regions.append({"name": name, "x": round(cx, 4), "y": round(cy, 4),
                        "r": round(r, 4), "count": len(idx)})

    # 투표에 쓰는 것들 — 가게별 업종 이름과, 동점일 때 이길 순서(풀에 가게가 많은 업종).
    cat_name = [cats[int(c)] if int(c) < len(cats) else None for c in cat_of]
    rank = {rg["name"]: (rg["count"], -i) for i, rg in enumerate(regions)}

    return {
        "regions": regions,
        "xy": {sid: (xs[i], ys[i]) for i, sid in enumerate(ids)},
        "name": {sid: (names[i] if i < len(names) else None) for i, sid in enumerate(ids)},
        "pts": np.array([xs, ys], dtype=float).T if ids else np.zeros((0, 2)),
        "cat_name": cat_name,
        "rank": rank,
    }


def _load_layout() -> dict:
    """접은 지형을 **파일이 바뀔 때까지** 들고 있는다 (mtime·크기로 무효화).

    2,216곳의 중앙값을 화면을 열 때마다 다시 낼 이유가 없다. `/admin/api/map/layout`
    이 원본 바이트를 캐시하는 것과 같은 규칙이고, 캐시는 별도다 — 이쪽은 원본이 아니라
    접은 결과(20개 원)를 들고 있어야 한다.
    """
    if not LAYOUT_PATH.exists():
        raise HTTPException(503, "지형도 산출물이 없습니다 — "
                                 "`python -m analysis.build_serving --only layout`")
    st = LAYOUT_PATH.stat()
    key = (st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache["key"] == key and _cache["value"] is not None:
            return _cache["value"]
    got = _fold(json.loads(LAYOUT_PATH.read_bytes()))
    with _lock:
        _cache.update(key=key, value=got)
    return got


def _region_at(lay: dict, x: float, y: float) -> str | None:
    """지도상 점의 주변 업종: 가까운 K개 가게의 다수결.

    표시용 휴리스틱이며 사용자의 실제 선택이나 고차원 취향을 판정하지 않는다.
    EMA 점은 좋아요한 가게 사이 빈 공간에 놓일 수 있고, 투영의 이웃이 같은 메뉴를
    의미하지도 않는다. K=15는 기존 표시 범위를 유지하는 값이다. 동점은 기존과 같이
    풀의 가게 수, regions 순서로 결정한다. 후보가 적으면 있는 만큼만 센다.
    """
    pts = lay["pts"]
    n = len(pts)
    if not n:
        return None
    d = ((pts[:, 0] - x) ** 2) + ((pts[:, 1] - y) ** 2)
    k = min(REGION_K, n)
    idx = np.argpartition(d, k - 1)[:k] if k < n else np.arange(n)
    votes: dict[str, int] = {}
    for i in idx:
        name = lay["cat_name"][int(i)]
        if name is not None:
            votes[name] = votes.get(name, 0) + 1
    if not votes:
        return None
    rank = lay["rank"]
    return max(votes, key=lambda nm: (votes[nm], rank.get(nm, (0, 0))))


# ────────────────────────────────────────────────────────── 궤적
def _trail(sess: dict, lay: dict) -> list[dict]:
    """like 마다 한 점. 위치는 like 좌표의 EMA(α=`TRAIL_ALPHA`), 첫 like 가 시작점.

    순서는 **세션 이력(`hist`)** 을 쓴다 — `like_ids` 는 재평가하면 마지막 라벨만
    남기는 집합이라 '언제 좋아했나' 를 잃는다. 궤적은 시간이 축이므로 이력이 맞다
    (같은 가게를 다시 좋아요하면 그 점이 다시 찍힌다 — 실제로 다시 좋아한 것이다).

    지형도에 없는 가게(후보 풀 밖)는 **건너뛴다.** 좌표가 없으니 그릴 자리가 없고,
    없는 자리를 0 이나 중심으로 채우면 궤적이 거짓으로 움직인다.
    """
    out: list[dict] = []
    ex = ey = None
    for h in sess.get("hist") or []:
        if not h.get("like"):
            continue
        sid = h.get("store_id")
        xy = lay["xy"].get(sid)
        if xy is None:
            continue
        x, y = xy
        if ex is None:
            ex, ey = x, y
        else:
            ex += TRAIL_ALPHA * (x - ex)
            ey += TRAIL_ALPHA * (y - ey)
        out.append({"n": h.get("n"), "x": round(ex, 4), "y": round(ey, 4),
                    "region": _region_at(lay, ex, ey),
                    "store": lay["name"].get(sid) or sid,
                    "card_id": loader.card_id_by_store.get(sid)})
    return out


def _near(sess: dict, lay: dict, x: float, y: float) -> list[dict]:
    """현재 점에 가장 가까운 **내가 좋아요한 가게** 3곳 — 좌표의 통역이다.

    EMA 중심은 취향이 두 덩어리일 때 빈 땅에 앉는다(모듈 문서). 그때 사용자가 읽어야
    하는 것은 좌표가 아니라 '내가 좋아한 이 가게들 사이' 라는 이름이다. `like_ids`
    를 쓰므로 **재평가로 취소한 가게는 앵커가 되지 않는다**(마지막 라벨만 남는다).
    """
    cand = []
    for sid in sess.get("like_ids") or []:
        xy = lay["xy"].get(sid)
        if xy is None:
            continue
        cand.append((math.dist((x, y), xy), sid))
    cand.sort(key=lambda t: (t[0], t[1]))
    return [{"name": lay["name"].get(sid) or sid,
             "card_id": loader.card_id_by_store.get(sid)}
            for _d, sid in cand[:NEAR_K]]


def _drift(trail: list[dict]) -> dict | None:
    """최근 `DRIFT_WINDOW` like 전의 점 → 지금 점. 창이 안 차면 None.

    거리는 [0,1]² 화면 좌표의 유클리드다. 지도 선의 이동을 보여줄 뿐,
    고차원 취향 변화량이나 다른 음식을 선호하게 되었다는 근거가 아니다.
    """
    if len(trail) < DRIFT_WINDOW + 1:
        return None
    a, b = trail[-1 - DRIFT_WINDOW], trail[-1]
    return {"from_region": a["region"], "to_region": b["region"],
            "distance": round(math.dist((a["x"], a["y"]), (b["x"], b["y"])), 4),
            "window": DRIFT_WINDOW}


def _headline(sess: dict) -> str:
    """Summarize observed current-session likes, never infer cuisine from 2D.

    Use each still-liked store once, at its last feedback time. The same decay
    as recommendation evidence gives recent reactions more weight without a new
    window or fitted taste model. Category labels only describe these observed
    choices; they neither drive recommendation nor prove a settled preference.
    A strict weighted majority supports a label; unknown metadata stays in the
    denominator. Prefer a supported cat3, then cat2, otherwise do not name one.
    """
    liked = list(dict.fromkeys(sess.get("like_ids") or []))
    if len(liked) < MIN_LIKES:
        return "조금 더 넘겨 보면 취향 지도가 그려져요"
    n = sess.get("n", 0)
    evidence = sess.get("evidence_at") or {}
    ages = [max(0, n - evidence.get(sid, n)) for sid in liked]
    # A common factor cancels in the shares. Subtracting the youngest age avoids
    # underflow after many dislikes without discarding an arbitrary old window.
    youngest = min(ages)
    weights = [taste.RECENCY_DECAY ** (age - youngest) for age in ages]
    total = math.fsum(weights)
    fine: dict[tuple, list[float]] = {}
    broad: dict[str, list[float]] = {}
    for sid, weight in zip(liked, weights):
        row = store.get_store(sid) or {}
        def label(key):
            value = row.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else None
        cat2, cat3 = label("cat2"), label("cat3")
        if cat2:
            broad.setdefault(cat2, []).append(weight)
        if cat3:
            fine.setdefault((cat2, cat3), []).append(weight)
    for groups in (fine, broad):
        for key, values in groups.items():
            if math.fsum(values) > total / 2:
                name = key[1] if isinstance(key, tuple) else key
                return f"최근 좋아요에서는 {name} 쪽이 두드러져요"
    return "좋아요한 가게를 바탕으로 취향을 알아가고 있어요"


def _merged_session(user_id: str) -> dict | None:
    """그 사람의 세션들을 시간순으로 이어 붙인 **읽기 전용** 세션 한 개.

    왜 필요한가. 앱은 "내 취향" 한 장을 그리려고 이 주소를 먼저 부르고, 404 가 오면
    **세션 수만큼** 세션별 취향을 하나씩 받아다 직접 합친다(FE/app/lib/services/taste_api.dart).
    앱을 켤 때마다 세션이 하나씩 늘어(bootstrap_gate.dart) 92개가 된 사용자가 있고, 그 요청이
    한꺼번에 나가 요청 제한에 걸려 화면이 실패했다(2026-09-20). 여기서 합쳐서 한 번에 주면
    그 되받기가 아예 일어나지 않는다 — **설치된 앱을 고치지 않고 끝난다.**

    합치는 것은 **화면에 보이는 궤적뿐이다.** 추천 학습 상태(m·v)는 세션마다 그대로 독립이고
    이 함수는 아무것도 바꾸지 않는다(API.md "생성된 세션은 서로 독립적이며 이후 다른 세션의
    변경을 실시간으로 섞지 않습니다"). 새 세션이 과거 세션의 반응을 상속한다는 같은 문서의
    설명과도 어긋나지 않는다.

    이어 붙이는 방법:
      - 세션을 `created` 순으로 놓고 `hist` 를 잇는다. `n` 은 누적으로 다시 매겨 궤적의 가로축이
        뒤로만 간다.
      - `like_ids` 는 이력을 순서대로 먹여 **마지막 라벨만** 남긴다. 옛 세션에서 좋아요한 가게를
        나중 세션에서 싫어요했으면 앵커에서 빠진다(세션 하나를 볼 때와 같은 규칙).
      - `evidence_at` 은 그 가게를 마지막으로 만난 누적 번호다. `_headline` 의 최근 가중치가
        세션 경계를 넘어 그대로 작동한다.
    """
    rows = identities.owned(user_id)
    live = taste.sessions()
    got = [(live[r["session_id"]], r) for r in rows if r.get("session_id") in live]
    if not got:
        return None
    got.sort(key=lambda t: (t[0].get("created") or 0.0, t[1]["session_id"]))

    hist: list[dict] = []
    like_state: dict[str, bool] = {}
    evidence_at: dict[str, int] = {}
    total = likes = 0
    for sess, _row in got:
        base = total
        for h in sess.get("hist") or []:
            n = base + int(h.get("n") or 0)
            hist.append({**h, "n": n})
            sid = h.get("store_id")
            if sid:
                like_state[sid] = bool(h.get("like"))
                evidence_at[sid] = n
        total += int(sess.get("n") or 0)
        likes += int(sess.get("likes") or 0)
    return {"hist": hist, "n": total, "likes": likes, "evidence_at": evidence_at,
            "like_ids": [sid for sid, liked in like_state.items() if liked]}


def _taste_of(user_id: str, sess: dict, detail: int, scope: str) -> dict:
    """`sess`(한 세션이든 합친 것이든)로 화면 한 장을 만든다. 계산은 한 곳에만 둔다."""
    lay = _load_layout()
    background = taste_hierarchy.read_level(detail)
    pts = _trail(sess, lay)
    trail = pts[-TRAIL_MAX:]
    drift = _drift(pts)
    now = None
    if pts:
        last = pts[-1]
        now = {"x": last["x"], "y": last["y"], "region": last["region"],
               "near": _near(sess, lay, last["x"], last["y"])}
    observe.inc_taste_view()
    observe.log("taste.view", user_id=user_id, swipes=sess["n"], likes=sess["likes"],
                region=(now or {}).get("region"), trail=len(trail),
                drift=(drift or {}).get("distance"), scope=scope)
    return {
        "user_id": user_id,
        "name": user_registry.name_of(user_id),
        "swipes": sess["n"],
        "likes": sess["likes"],
        "map": {"version": 3, "level": background["level"], "regions": background["nodes"]},
        "trail": trail,
        "now": now,
        "drift": drift,
        "headline": _headline(sess),
    }


# ────────────────────────────────────────────────────────── 엔드포인트
@router.get("/taste/map", summary="취향 지형 — 업종 20개의 고정 지도",
            response_model=TasteMapOut)
def taste_map():
    """모든 사용자가 **같은 땅**을 본다. 사용자와 무관하므로 앱이 한 번만 받으면 된다.

    `regions` 는 후보 풀 2,216곳을 업종(cat2)으로 묶은 원 20개다.

    - `x`, `y` — 구성원 좌표의 **중앙값**. [0,1]² 이고 두 축의 배율이 같다
    - `r` — 중심까지 거리의 중앙값. 절반은 이 원 밖에 있다(정확한 경계가 아니라
      '대충 이 근처' 를 그리는 원이다)
    - `count` — 그 업종의 가게 수

    **이 원은 그리기 위한 요약이다.** 궤적 점의 `region` 이름은 원이 아니라 이웃 가게
    15곳의 다수결로 정한다. 표시용 공간 라벨이며 그 업종을 좋아한다는 뜻은 아니다.
    화면도 원을 경계로 그리지 말 것(큰 업종끼리 겹친다).

    좌표는 `data/kakao/serving/layout.json`(콘텐츠 유사도 → UMAP)에서 온다.
    **먼 두 지역 사이의 거리는 뜻이 없다** — 주변 배치는 시각적 참고이며 평균점의
    주변 업종이 선호 업종을 뜻하지 않습니다. 지도를 캐시할 때는 `version`이 달라지면 버리세요.

    산출물이 없으면 503 입니다.
    """
    lay = _load_layout()
    return {"version": MAP_VERSION, "regions": lay["regions"]}


@router.get("/users/{user_id}/taste", summary="내 취향 — 지형도 위의 궤적",
            response_model=UserTasteOut)
def user_taste(user_id: str, detail: Detail = 1):
    """그 사용자의 취향이 **어디에 있고 어디로 움직였나**. 화면 하나에 필요한 전부다.

    - `map` — `version=3`, 숫자 `level`, `regions`. `detail=1`(기본)은 전체 cat2,
      `detail=2`는 전체 cat3, `detail=3`은 전체 가게다. 한 요청으로 표시 단계를 바꾼다.
      regions의 name·x·y·r·count·key·card_id와 기존 고정 좌표를 유지한다
    - `trail` — like 마다 한 점(최근 60개). 위치는 like 한 가게 좌표의 EMA(α=0.35)라
      **한 장으로 튀지 않고 흐르듯 움직인다.** `n` 은 그 like 가 몇 번째 스와이프였나
    - `now` — 궤적의 마지막 점 + `near`: 그 점에 가장 가까운 **내가 좋아요한 가게**
      3곳. 취향이 두 덩어리면 EMA 중심이 빈 땅에 앉으므로, 화면은 좌표보다 이 이름을
      믿어야 한다
    - `drift` — 10 like 전과 지금. `distance` 는 [0,1]² 유클리드이며 like 가 11개
      미만이면 `null`
    - `headline` — 좌표와 별개로 현재 세션의 유효 좋아요를 설명한다. 서로 다른 가게
      3곳 미만이면 더 모으도록 안내한다. 마지막 반응 시점에 추천 모델의 최근 감쇠를
      적용해 과반인 cat3, 없으면 cat2만 언급하며 근거가 섞이거나 부족하면 업종을 단정하지
      않는다. 같은 가게의 반복 좋아요는 한 곳이고 취소한 좋아요는 설명에서 제외한다

    `region`과 `drift`는 지도상 위치·이동을 위한 값입니다. 음식 취향이나 고차원 취향
    변화량으로 읽지 마세요. headline은 관측 요약이며 취향 확정이나 추천 정확도 판정이 아닙니다.

    좋아요가 적으면 `trail` 은 있는 만큼, `drift` 는 `null` 이고 `headline` 이 더
    넘겨 보라고 말합니다. **읽기 전용입니다** — 이 호출은 추천·학습·노출 로그를
    한 톨도 바꾸지 않습니다.

    `detail`은 배경의 단계만 선택합니다. 궤적·현재 점·이동량·문장은 전체 세션 기준 그대로입니다.
    detail 범위밖·비정수는422, 없는 세션은404, 지도 미준비는503입니다.
    """
    user_registry.validate_id(user_id)
    _load_layout()          # 지형이 없으면 **세션을 찾기 전에** 503. 그릴 수 없는 것이 먼저다
    sess = taste.sessions().get(user_id)
    if sess is not None:                       # 세션 id — 예전 그대로 그 세션 하나
        return _taste_of(user_id, sess, detail, "session")
    merged = _merged_session(user_id)          # 사람 id — 그 사람의 세션을 이어 붙여서
    if merged is None:
        raise HTTPException(404, "session not found")
    return _taste_of(user_id, merged, detail, "person")


@router.get("/taste/example", response_class=HTMLResponse, include_in_schema=False)
def taste_example(user_id: str | None = Query(None), demo: int = Query(0)):
    """프론트에 주는 **예시 화면** 한 장(`app/static/taste.html`).

    화면이 `?user_id=실제사용자&session_id=세션`을 읽어 canonical 세션 API를 호출한다.
    기존 `?user_id=기존세션`과 `?demo=1`도 지원한다. 서버는 견본 HTML을 그대로 반환하며
    제품 화면이 아니므로 `/docs`에는 숨긴다.
    """
    return (STATIC / "taste.html").read_text(encoding="utf-8")
