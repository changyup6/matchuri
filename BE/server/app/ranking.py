# -*- coding: utf-8 -*-
"""정렬 보정 — 태그 계층과 거리. 필터가 '누가 후보인가' 를 정하면 여기서 '누가 먼저인가'.

필터는 **통과/탈락**만 말한다. 태그를 OR 로 바꾼 뒤(칩을 더 누르면 넓어져야 한다) 그것만으로는
부족해졌다: `단체모임`·`분위기` 를 둘 다 고른 사람에게 **둘 다 맞는 가게**와 하나만 맞는 가게가
같은 자격으로 섞여 나온다. 사용자가 칩을 두 개 누른 뜻은 '둘 다면 더 좋다' 이므로 **겹치는
태그 수가 1순위 정렬 키**다(`tiers`). 거리도 그렇다 — `within_m` 은 벽(500m 밖은 없는 것)이지
'더 가까우면 더 좋다' 를 말하지 못한다. 그래서 거리를 **점수에 섞는다**(`distance_bonus`).

    정렬 = (겹치는 태그 수 내림차순, 그 안에서 정책 점수 + 거리 보너스 내림차순)

**두 정책이 같은 계층·같은 보너스를 받는다.** 팀-드래프트 인터리빙은 '같은 조건에서 누가 더
잘 맞히나' 를 재는 실험이라, 한쪽에만 보정을 주면 그 비교가 무의미해진다. 그래서 이 모듈의
결과는 `policy.pick_many` 를 지나 두 정책에 **그대로** 내려간다.

**거리는 학습하지 않는다.** 선택 시점의 보정일 뿐이고 `m`·`v` 에는 한 톨도 들어가지 않는다.
위치는 요청마다 바뀌는 값인데 학습 상태는 `state/preferences.jsonl` 하나로 소수점까지
복원돼야 한다(`tests/test_replay.py`) — 동적인 신호가 `m` 에 들어가면 그 성질이 깨진다.
같은 이유로 `peek`·`score_state`·관리자 화면·취향 지형도는 이 보정을 **받지 않는다**
(그 화면들이 묻는 것은 '모델이 무엇을 믿는가' 이고, 거기에 지금 위치가 섞이면 답이 흐려진다).
"""
from __future__ import annotations

import os

from app import filters, geo

#: 거리 보너스의 무게. z 점수(표준편차 단위)에 곱한다 — 0.4 는 '한 표준편차 더 가까운 곳이
#: 정책 점수 0.4 만큼 앞선다' 는 뜻이다. 환경변수로 조절할 수 있게 둔 이유는 이 값이 실측이
#: 아니라 첫 추정이기 때문이다(온라인에서 like 율을 보고 고칠 값이다).
DIST_WEIGHT_ENV = "MATCHURI_DIST_WEIGHT"
DEFAULT_DIST_WEIGHT = 0.4

#: z 를 이 범위로 자른다 — `content.Z_CLIP` 과 같은 이유다. 역삼동 안에서도 2km 떨어진
#: 가게 하나가 z 를 끌고 가면 그 한 칸이 정렬 전체를 정한다(정규화는 눈금을 맞추지만
#: 꼬리를 못 자른다).
Z_CLIP = 3.0

#: 태그를 **이만큼 이상** 골랐을 때만 계층을 만든다. 하나만 고른 사람에게는 모든 후보가
#: 그 태그를 가졌으므로(OR 필터) 계층이 전부 1 이고, 정렬에 아무 정보도 주지 않는다.
MIN_TAGS_FOR_TIER = 2

#: '걸어서 갈 만하다' 의 경계. 카드 이유 문장을 거리로 바꿀지 판단할 때 `main` 이 쓴다.
NEAR_M = 500


def weight() -> float:
    """거리 보너스의 무게. 요청마다 읽는다(환경변수를 바꿔 다시 배포하지 않아도 되게)."""
    try:
        return float(os.environ.get(DIST_WEIGHT_ENV, DEFAULT_DIST_WEIGHT))
    except (TypeError, ValueError):
        return DEFAULT_DIST_WEIGHT


def tiers(cats: list[str], tags: list[str]) -> dict[str, int]:
    """store_id -> **고른 태그 중 몇 개를 가졌나**. 태그를 2개 이상 골랐을 때만 만든다.

    업종(`cat`)은 계층에 넣지 않는다 — 가게의 `category_group` 은 정확히 하나뿐이라
    허용 집합 안에서는 전부 1 이고, 그건 순서에 대해 아무 말도 하지 않는다.

    0개인 가게는 dict 에 넣지 않는다(정렬은 없는 키를 0 으로 본다) — 2,795곳 중 대부분이
    0 이므로 그만큼 작게 유지된다.
    """
    want = set(tags)
    if len(want) < MIN_TAGS_FOR_TIER:
        return {}
    out: dict[str, int] = {}
    for sid in filters.svc.all_store_ids():
        s = filters.svc.get_store(sid) or {}
        n = len(want & filters.client_tags(s))
        if n:
            out[sid] = n
    return out


def distance_bonus(lat: float | None, lon: float | None,
                   ids: list[str]) -> dict[str, float]:
    """store_id -> 점수에 더할 거리 보너스. 위치를 모르면 빈 dict(= 보정 없음).

    `-거리` 를 **후보 안에서** z 점수로 만든다(가까울수록 큼). 절대 거리(m)를 그대로 쓰면
    눈금이 정책 점수와 맞지 않아 한 항이 전부를 먹고, 동네가 바뀌면 그 비율도 바뀐다.
    좌표를 모르는 가게는 **0.0**(= 후보 평균, '모른다' 를 벌하지도 봐주지도 않는다) —
    `content.zscore_masked` 가 없는 modality 를 다루는 규칙과 같다.
    """
    if lat is None or lon is None or not ids:
        return {}
    coords = geo.coords()
    known = [(sid, geo.haversine_m(lat, lon, *coords[sid])) for sid in ids
             if sid in coords]
    if not known:
        return {}
    values = [d for _sid, d in known]
    mean = sum(values) / len(values)
    var = sum((d - mean) ** 2 for d in values) / len(values)
    sd = var ** 0.5
    w = weight()
    out = {sid: 0.0 for sid in ids}
    if sd <= 0:                       # 후보가 모두 같은 거리(또는 한 곳) — 순서에 할 말이 없다
        return out
    for sid, d in known:
        z = (mean - d) / sd           # 가까울수록 +
        out[sid] = w * max(-Z_CLIP, min(Z_CLIP, z))
    return out


def build(user_filter: dict | None, position: tuple[float, float] | None,
          allow: set[str] | None) -> tuple[dict[str, int], dict[str, float]]:
    """(계층, 보너스). `build_detailed` 에서 보너스의 출처 분해만 뺀 것 — 기존 호출자용."""
    tier, bonus, _ = build_detailed(user_filter, position, allow)
    return tier, bonus


def build_detailed(user_filter: dict | None, position: tuple[float, float] | None,
                   allow: set[str] | None
                   ) -> tuple[dict[str, int], dict[str, float], dict[str, dict[str, float]]]:
    """`main` 이 쓰는 한 줄 — 저장된 필터·위치에서 (계층, 보너스, 보너스의 출처 분해)를 만든다.

    셋째 값은 `{store_id: {"distance": d, "trend": t}}` 다. 정책들은 보너스를 **하나의 배열**로
    받아 점수에 더하므로 거리와 트렌드를 구분하지 못한다. 카드 응답의 `attribution` 이
    "가까워서인가, 계절 때문인가" 에 답하려면 합치기 전의 둘이 필요해서 따로 낸다.

    거리 z 는 **이번 요청의 후보 안에서** 낸다: 필터가 걸렸으면 허용 집합 위에서,
    없으면 추천 후보 풀 전체 위에서. 그래야 '500m 안만 보는 사람' 에게도 그 안에서의
    가까움이 순서를 만든다(전체 풀 기준으로 z 를 내면 500m 안은 전부 비슷하게 +가 된다).

    보너스는 **거리 + 트렌드** 다. 둘은 성질이 같아서 같은 자리에 산다 — 시간·위치처럼
    요청마다 변하는 값이라 `m`·`v` 에 들어가면 안 되는 것들이다(`recsys/trend.py` 참조).
    합쳐서 내보내는 이유는 아래 층(`policy.pick_many`)이 보정을 **하나의 배열**로 받기
    때문이고, 둘 다 두 정책에 똑같이 내려가므로 인터리빙 비교는 공정하게 유지된다.

    트렌드는 기본으로 꺼져 있다(`MATCHURI_TREND_WEIGHT=0`). 켜지 않으면 `trend_bonus`
    가 빈 dict 라 이 함수의 결과는 예전과 **비트 단위로 같다**.
    """
    from recsys import swipe, trend      # app -> recsys 는 요청 시점에만 (층위 유지)

    saved = user_filter or {}
    tier = tiers(list(saved.get("cat") or []), list(saved.get("tag") or []))
    ids = sorted(allow) if allow is not None else list(swipe.pool()["ids"])
    dist = distance_bonus(position[0] if position else None,
                          position[1] if position else None, ids)
    tr = trend.bonus(ids)
    bonus = dict(dist)
    for sid, v in tr.items():
        bonus[sid] = bonus.get(sid, 0.0) + v
    parts = {sid: {"distance": dist.get(sid, 0.0), "trend": tr.get(sid, 0.0)}
             for sid in set(dist) | set(tr)}
    return tier, bonus, parts
