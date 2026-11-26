# -*- coding: utf-8 -*-
"""트렌드 — '요즘 뜨는가'. 취향 모델 **밖**에 있는 시간 신호.

지금까지 이 서비스에는 시간 축이 없었다. `pop`(`log1p(리뷰 총수)`)도 `quality`
(베이지안 평균 평점)도 2013년부터의 **누적**이라, 10년 전에 500건 받고 식은 가게가
지난 세 달에 40건 받은 가게를 계속 이긴다. 그게 트렌드의 반대말이라 이 모듈을 만들었다.

    trend(가게) = w_season · z(계절 선행)            ← 네이버 데이터랩: 메뉴 키워드 + 업종
                + w_fad    · z(유행, 전년동기 대비)   ← 같은 곳
                + w_inflow · z(리뷰 증가율)           ← 우리 리뷰
                + w_rating · z(별점 변화 × SHRINK)    ← 우리 리뷰

**주 신호는 검색 트렌드고 리뷰는 보조다 (2026-09-18 이후).** 처음엔 리뷰 증가율이 주
신호였는데, 카카오 리뷰만으로는 가게당 최근 90일 중앙값이 **1건**이라 표본이 너무 얇았다.
네이버 데이터랩 검색량은 업종 71개·메뉴 키워드 1,000개가 하루 단위로 545일 이상 있고,
계절은 해마다 반복된다는 근거가 있다(`analysis/TREND-EVIDENCE.md` 2.3). 다만 데이터랩은
**개별 상호를 못 잰다**(검색량 0). 그래서 가게를 가르는 것은 **그 가게가 파는 메뉴**다 —
`analysis/fetch_trend_menu.py` 가 메뉴명 상위 1,000개의 검색 트렌드를 굽고, 이 모듈이
가게의 메뉴 키워드 점수를 모아 업종 값과 **부분 수축(partial pooling)** 으로 합친다:

    store = (n · 메뉴 상위 K 평균 + k · 업종) / (n + k)     n = 쓸 수 있는 메뉴 키워드 수(≤K)

메뉴가 없는 가게는 업종 값을, 메뉴가 많은 가게는 자기 값을 받는다. 같은 원리가 이미
`BAYES_C`(평점을 전역 평균으로 수축)에 있다 — 한 단계 더 들어갈 뿐이다.

`season`(작년 이 시점의 다음 4주가 지난 4주보다 오르나)과 `fad`(작년 같은 기간보다
더 뜨나)를 **따로** 두는 이유: 업종 산출물의 옛 기본값 `yoy` 는 계절을 지운 값이었다.
냉면이 7월에 오르는 건 작년 7월에도 올랐으니 yoy 에서는 0 이다. 계절과 유행은 다른
신호이고 무게도 따로 정해야 한다(백테스트·슬롯 like 율로).

**왜 성분(`content.COMPONENTS`)이 아니라 보정층인가.** 성분이 되면 `taste.record` 가
`z[:, i]` 를 그대로 학습에 먹인다. 그런데 서버는 재시작할 때마다 `preferences.jsonl` 을
처음부터 다시 먹여 `m` 을 복원하고(`app/state.py: restore`), 배포마다 그 일이 일어난다.
trend 는 시간에 따라 변하므로 **같은 로그를 다시 먹여도 다른 `m` 이 나온다** —
`tests/test_replay.py` 가 지키는 계약이 깨진다. `app/ranking.py` 의 거리 보너스가
같은 이유로 `m`·`v` 에 한 톨도 안 들어가는 것과 똑같다.

    로그의 함수여야 하는 것 → `m` (취향)
    시계의 함수여도 되는 것 → 보정층 (이 모듈)

**기본값은 꺼져 있다**(`MATCHURI_TREND_WEIGHT=0`). 지금 legacy/taste 팀-드래프트
인터리빙이 돌고 있고, 그 비교는 '같은 조건에서 누가 더 잘 맞히나' 를 재는 실험이다.
여기서 순서가 움직이면 기간 전후의 like 율을 합칠 수 없다. 켜는 것은 그 실험이
끝난 뒤의 결정이다. 켜도 **두 정책에 같은 값이 내려가므로**(`ranking.build` 를 지나
`policy.pick_many` 로) 공정성 자체는 유지된다.

**트렌드의 효과를 재려면 무게가 아니라 슬롯을 켠다**(`MATCHURI_TREND_EVERY`, 아래
`SLOT_ENV` 주석). 무게는 두 정책을 같이 밀어서 인터리빙으로 잴 수 없고, 슬롯은 N장에
한 장을 따로 뽑아 `policy="trend"` 라벨을 남기므로 like 율·방문율·만족도가 두 정책
옆에 그대로 나온다(`policy.outcome_stats`·`policy.survey_stats`).

성분 선택의 근거 (2026-09-14, `data/kakao/reviews.jsonl` 33,300건 실측)
--------------------------------------------------------------------
* **수준(최근 유입량)은 뺐다.** `corr(log1p(최근 180일 리뷰 수), pop) = +0.576` 으로
  **인기의 다른 이름**이다. `content.py` 가 `review` 성분을 버린 이유(스피어만 +0.67)와
  같은 판단이다. 반면 `corr(증가율, pop) = +0.079` 로 거의 독립이라 새 정보를 나른다.
* **증가율의 커버리지는 99.1%다.** 크롤이 가게당 30~40건에서 잘려 있지만(총 65,763건 중
  50.6% 수집), 잘린 가게도 그 30~40건이 180일보다 과거까지 뻗어 있어 **최근 윈도우는
  2,326/2,346곳이 온전하다**. 트렌드를 재기에 충분하다.
* **별점 변화는 약해서 수축한다.** 플라시보 테스트(시간 대신 무작위로 반 갈라 같은 계산)
  결과 실제 분산 0.916 대 잡음 분산 0.673 → **진짜 신호는 26.6%뿐**이다. 별점 분포가
  J자(1점 17.8% · 5점 50.8%)라 리뷰 1건의 표준편차가 1.546 이고, 버킷당 3~4건 평균은
  극도로 불안정하다. 그래서 `SHRINK_RATING` 으로 눌러 넣는다. 커버리지도 24% 뿐이다.
* **윈도우는 반드시 같은 길이여야 한다.** '최근 180일 대 그 이전 **전체**' 로 재면 평균
  변화가 −0.317, '최근 180일 대 **직전 180일**' 로 재면 −0.154 다. 비대칭 윈도우가
  없는 추세를 만들어낸다. 전역 평균 별점은 2021년 이후 3.70~3.80 으로 평평해서
  전역 보정은 필요 없다.

값이 없는 가게는 **0.0**(= 후보 평균)이다. `content.zscore_masked` 와
`ranking.distance_bonus` 가 없는 modality 를 다루는 규칙과 같다 — '모른다' 를
벌하지도 봐주지도 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import threading
from pathlib import Path

import numpy as np

from . import store as svc

#: 출처 이름. 관리자 API 가 기여를 분리해 보여줄 때 쓴다. 앞의 둘은 검색(데이터랩), 뒤의 둘은 리뷰.
SOURCES = ("season", "fad", "inflow", "rating")

#: 비교 윈도우(일). 최근 W일 대 **직전 W일** — 길이가 같아야 한다(모듈 문서 참조).
#: 180일인 이유는 커버리지다: 90일이면 3건 이상인 가게가 370곳뿐이고 180일이면 718곳이다.
WINDOW_DAYS = 180

#: 증가율의 평활 상수. `log((recent + A) / (prior + A))` 로 0 건 분모를 막는다.
#: 2.0 이면 0→2 건이 `log(4/2)=0.69` 로 매겨진다(무한대가 아니라).
SMOOTH = 2.0

#: 별점 변화에 곱하는 수축 계수. 플라시보 테스트가 준 신호 비율(26.6%)이다.
#: 이 값 없이 그대로 쓰면 관측치의 73%가 표본 잡음이라 잡음을 학습한다.
SHRINK_RATING = 0.27

#: 별점 변화를 계산할 최소 리뷰 수(두 윈도우 **각각**). 이보다 적으면 '모른다'(0.0).
MIN_RATING_N = 3

#: z 의 한계. `content.Z_CLIP`·`ranking.Z_CLIP` 과 같은 값·같은 이유다 —
#: 정규화는 눈금을 맞추지만 꼬리를 못 자른다.
Z_CLIP = 3.0

#: 데이터가 이만큼 낡으면 트렌드를 **통째로 끈다**(빈 dict). 크롤이 멈춘 채 반년이
#: 지나면 '최근 180일' 이 아무 의미가 없는데, 조용히 0 을 주면 아무도 모른다.
#: 경고 한 줄을 남기고 꺼진다 — `policy.active()` 가 산출물이 없을 때 하는 것과 같다.
MAX_STALE_DAYS = 120

#: 외부(네이버 검색어 트렌드) 업종 트렌드. 없으면 그 항은 0 이다 — 서버는 외부 API 를
#: **런타임에 부르지 않는다**. 오프라인에서 만들어 이 파일로 넣는다
#: (`analysis/fetch_trend_category.py`. 함정과 근거는 `analysis/TREND.md` 5절).
#:
#: 자리가 `data/kakao/serving/` 이 **아니라** `data/analysis/` 인 이유 — 그 폴더는
#: gitignore 라 저장소에 없고, 서버에서는 주간 배치가 통째로 갈아 끼우는 마운트다.
#: 이 파일은 크롤 산출물이 아니라 네이버 API 스냅샷이어서 서버가 만들지 않는다.
#: 거기 두면 (1) 배포가 나르지 않고 (2) 손으로 올려도 다음 배치에 사라진다 —
#: 2026-09-18 에 실제로 프로덕션에 없어서 `category` 항이 조용히 0 이었다.
#: 같은 파이프라인의 원본 응답(`datalab/*.json`)과 `trend_level.json` 은 이미
#: `data/analysis/` 에 커밋돼 있다. 형제 옆에 둔다 — 커밋이 곧 배포다.
#: 이미지에는 Dockerfile 이 `data/analysis/` 를 통째로 COPY 한다(`.dockerignore` 는 로컬 캐시만 뺀다).
CATEGORY_FILE = Path(__file__).resolve().parents[2] / "data" / "analysis" / \
    "trend_category.json"

#: 메뉴 키워드 트렌드(`analysis/fetch_trend_menu.py`). `data/analysis` 는 커밋되고 이미지에
#: 굽는 폴더다(Dockerfile). 없으면 메뉴 항이 비고 업종 값만 남는다 — 죽지 않는다.
MENU_FILE = Path(__file__).resolve().parents[2] / "data" / "analysis" / "trend_menu.json"

#: 메뉴 키워드의 최소 검색 밀도(전 구간 중앙값, 응답 안 최댓값 100 기준). 검색량이 거의
#: 없는 키워드는 0 이 대부분이고 며칠만 튀어 season ±2 같은 값이 나오는데 그건 신호가
#: 아니라 스파이크다. 실측(1,000개): 5 로 거르면 |season| > 1 로 남는 것이 콩국수·중국냉면·
#: 열무냉면(내림)과 유자차·모듬전·생굴(오름)뿐이다 — 전부 진짜 철이다.
MIN_DENSITY = 5.0

#: 가게 메뉴 점수 = 그 가게 키워드 값 **상위 K개의 평균**. 전체 평균이면 메뉴 30개짜리
#: 카페는 늘 0 근처가 되고, 최댓값이면 키워드가 많은 가게가 늘 이긴다. 상위 K 평균은
#: "그 가게에서 지금 오르는 메뉴 몇 개" 를 본다. K 는 첫 추정이고 백테스트가 고칠 값이다.
MENU_TOP_K = 3

#: 부분 수축의 업종 쪽 무게 k. 메뉴 키워드 1개짜리 가게는 업종과 반반, 3개면 3:1 이다.
POOL_K = 1.0

WEIGHT_ENV = "MATCHURI_TREND_WEIGHT"
#: 전체 무게의 기본값. **0 = 꺼짐** — 인터리빙 실험이 끝나기 전에는 켜지 않는다.
DEFAULT_WEIGHT = 0.0

#: 출처별 무게. 검색 계절 선행이 주 신호다(모듈 문서 — 리뷰는 표본이 얇다). 리뷰 증가율은
#: 같은 업종·같은 메뉴 안에서 가게를 가르는 보조다. **손으로 둔 첫 추정값**이고, 시점 정합
#: 백테스트와 트렌드 슬롯의 like 율이 고칠 값이다.
SOURCE_ENV = {"season": "MATCHURI_TREND_W_SEASON",
              "fad": "MATCHURI_TREND_W_FAD",
              "inflow": "MATCHURI_TREND_W_INFLOW",
              "rating": "MATCHURI_TREND_W_RATING"}
DEFAULT_SOURCE_W = {"season": 1.0, "fad": 0.5, "inflow": 0.5, "rating": 0.3}

#: **트렌드 슬롯** — 무게(`MATCHURI_TREND_WEIGHT`)와는 다른 길이다.
#:
#: 무게는 두 정책의 점수에 트렌드를 **섞는다**. 그러면 legacy 와 taste 가 둘 다 트렌드를
#: 받아 인터리빙으로는 트렌드의 효과를 잴 수 없다. 슬롯은 N장마다 한 장을 트렌드
#: 상위에서 **따로 뽑고 노출 로그에 `policy="trend"` 로 라벨을 붙인다**. 그러면
#: `exposures.jsonl` × `preferences.jsonl` 조인(`policy.outcome_stats`)이 그대로
#: 트렌드 카드의 like 율을 내준다 — 같은 사용자·같은 화면 안에서 두 정책과 비교되므로
#: 사용자를 둘로 나누는 A/B 가 필요 없다(사용자 70여 명으로는 A/B 는 검정력이 없다).
#:
#: `analysis/TREND.md` 8절이 "진짜 트래픽 분할이 필요해지면 `EXPLORE_EVERY` 옆에
#: `TREND_EVERY` 로 붙인다" 고 적어 둔 그 자리다. **0 = 꺼짐(기본).** 10 이면 10장에 1장.
SLOT_ENV = "MATCHURI_TREND_EVERY"
DEFAULT_SLOT_EVERY = 0

#: 슬롯 후보 — 트렌드 상위 몇 곳 중에서 뽑나. 1위만 뽑으면 모든 사용자가 같은 가게를
#: 받아 노출 집중도(`recsys/exposure.py`)가 바로 튄다. 슬롯의 목적은 "1위 가게" 가 아니라
#: **"트렌드 신호가 고른 카드"** 의 like 율이므로, 상위 10곳 안에서 씨앗 고정 난수로 고른다
#: (`taste` 의 탐색 슬롯이 불확실성 상위에서 고르는 것과 같은 모양).
SLOT_TOP_N = 10

_lock = threading.RLock()
_cache: dict | None = None
_stale_warned = False


# ────────────────────────────────────────────────────────── 설정
def weight() -> float:
    """트렌드 보정의 전체 무게. 요청마다 읽는다(환경변수를 바꿔 재배포하지 않아도 되게).

    `ranking.weight()` 와 같은 규칙이다 — 이 값은 실측이 아니라 첫 추정이고,
    온라인에서 like 율을 보고 고칠 값이다.
    """
    try:
        return float(os.environ.get(WEIGHT_ENV, DEFAULT_WEIGHT))
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT


def source_weight(name: str) -> float:
    try:
        return float(os.environ.get(SOURCE_ENV[name], DEFAULT_SOURCE_W[name]))
    except (KeyError, TypeError, ValueError):
        return DEFAULT_SOURCE_W.get(name, 0.0)


def enabled() -> bool:
    return weight() != 0.0


def slot_every() -> int:
    """트렌드 슬롯 주기. 0 이면 꺼짐. 요청마다 읽는다(`weight()` 와 같은 규칙)."""
    try:
        return max(0, int(os.environ.get(SLOT_ENV, DEFAULT_SLOT_EVERY)))
    except (TypeError, ValueError):
        return DEFAULT_SLOT_EVERY


def slot_enabled() -> bool:
    return slot_every() > 0


# ────────────────────────────────────────────────────────── 원자료
def _parse_day(raw) -> str | None:
    """리뷰의 `date` 에서 `YYYY-MM-DD` 만. 형식이 아니면 None(세지 않는다)."""
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    day = raw[:10]
    return day if day[4] == "-" and day[7] == "-" else None


def _reviews_mtime() -> float:
    try:
        return svc.REVIEWS.stat().st_mtime
    except OSError:
        return 0.0


def _collect() -> tuple[dict[str, list[tuple[str, int | None]]], str | None]:
    """store_id -> [(날짜, 별점)] 과 자료 전체의 **가장 최근 날짜**.

    `store.get_reviews` 를 통해 읽는다 — 그 모듈이 파일 변경을 감지해 다시 읽으므로
    (`store._changed`), 크롤을 갈아끼우면 여기도 따라 갱신된다. 서버가 이미 리뷰
    33,300건을 메모리에 들고 있어서 **새 파일도 새 산출물도 필요 없다**.
    """
    out: dict[str, list[tuple[str, int | None]]] = {}
    latest: str | None = None
    for sid in svc.all_store_ids():
        rows = []
        for r in svc.get_reviews(sid, limit=10 ** 9):
            day = _parse_day(r.get("date"))
            if day is None:
                continue
            rating = r.get("rating")
            rows.append((day, rating if isinstance(rating, int) else None))
            if latest is None or day > latest:
                latest = day
        if rows:
            out[sid] = rows
    return out, latest


# ────────────────────────────────────────────────────────── 출처별 점수
def _inflow(rows: list[tuple[str, int | None]], a0: str, b0: str) -> float:
    """리뷰 **증가율** — `log((최근 + A) / (직전 + A))`.

    수준(최근 유입량)이 아니라 증가율인 이유는 모듈 문서에 적었다: 수준은 `pop` 과
    상관 +0.576 으로 인기의 다른 이름이고, 증가율은 +0.079 로 독립이다.
    """
    recent = prior = 0
    for day, _ in rows:
        if day >= a0:
            recent += 1
        elif day >= b0:
            prior += 1
    return math.log((recent + SMOOTH) / (prior + SMOOTH))


def _rating(rows: list[tuple[str, int | None]], a0: str, b0: str) -> float | None:
    """별점 변화 × `SHRINK_RATING`. 두 윈도우 각각 `MIN_RATING_N` 건 미만이면 None.

    None 은 '모른다' 이고 z 단계에서 후보 평균(0.0)이 된다 — 벌하지도 봐주지도 않는다.
    실제로 이 조건을 넘는 가게는 후보의 24% 뿐이다.
    """
    recent = [r for day, r in rows if day >= a0 and r is not None]
    prior = [r for day, r in rows if b0 <= day < a0 and r is not None]
    if len(recent) < MIN_RATING_N or len(prior) < MIN_RATING_N:
        return None
    delta = sum(recent) / len(recent) - sum(prior) / len(prior)
    return SHRINK_RATING * delta


def _category_table() -> dict[str, dict[str, float]]:
    """외부 업종 트렌드 — 출처별 `{업종: 값}` 표. 파일이 없거나 깨졌으면 빈 dict(그 항은 0).

    `season` 은 산출물의 `season` 열, `fad` 는 `yoy` 열이다. 옛 산출물(열이 `scores` 하나)은
    기본 basis 가 yoy 였으므로 `fad` 로만 읽는다 — 계절은 그 파일에 없다.

    서버가 네이버 API 를 직접 부르지 않는 이유: 외부 장애가 추천을 멈추면 안 되고,
    이 저장소의 약속이 '서버는 오프라인 산출물만 읽는다' 이기 때문이다.
    """
    try:
        raw = json.loads(CATEGORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, float]] = {}
    season = raw.get("season")
    fad = raw.get("yoy") if isinstance(raw.get("yoy"), dict) else raw.get("scores")
    if isinstance(season, dict):
        out["season"] = season
    if isinstance(fad, dict):
        out["fad"] = fad
    return out


def _menu_table() -> tuple[dict[str, dict], dict[str, list[str]]]:
    """메뉴 키워드 트렌드 — (`키워드 → 점수들`, `가게 → 키워드들`). 없으면 둘 다 빈 dict."""
    try:
        raw = json.loads(MENU_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    kws = raw.get("keywords")
    of = raw.get("store_keywords")
    return (kws if isinstance(kws, dict) else {},
            of if isinstance(of, dict) else {})


def _menu(sid: str, key: str, kws: dict[str, dict],
          of: dict[str, list[str]]) -> tuple[float | None, int, list[tuple[str, float]]]:
    """그 가게 메뉴 키워드들의 `key`(season·fad) — **상위 `MENU_TOP_K` 평균**, 쓴 개수, 쓴 키워드.

    밀도가 `MIN_DENSITY` 미만인 키워드는 버린다(모듈 상수 참조). 쓸 것이 없으면 (None, 0, []).
    `fad` 는 산출물의 `yoy` 열이다.
    """
    col = "yoy" if key == "fad" else key
    vals: list[tuple[str, float]] = []
    for kw in of.get(sid) or ():
        row = kws.get(kw)
        if not isinstance(row, dict):
            continue
        v = row.get(col)
        if not isinstance(v, (int, float)) or (row.get("density") or 0.0) < MIN_DENSITY:
            continue
        vals.append((kw, float(v)))
    if not vals:
        return None, 0, []
    vals.sort(key=lambda t: (-t[1], t[0]))
    top = vals[:MENU_TOP_K]
    return sum(v for _, v in top) / len(top), len(top), top


#: 브랜드 키(`data/kakao/serving/brand_key.json`, `content` 산출물과 같은 파일). 슬롯 후보에서
#: 같은 체인을 한 곳으로 접을 때만 쓴다. 없으면 가게마다 다른 브랜드로 본다(= 접지 않음).
_brands: tuple[float, dict[str, str]] | None = None


def _brand_map() -> dict[str, str]:
    global _brands
    path = svc.DATA / "serving" / "brand_key.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _brands is None or _brands[0] != mtime:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            _brands = (mtime, raw if isinstance(raw, dict) else {})
        return _brands[1]


def _pooled(menu: float | None, n: int, cat: float | None) -> float | None:
    """부분 수축 — `(n·메뉴 + k·업종) / (n + k)`. 한쪽만 있으면 그쪽, 둘 다 없으면 None.

    메뉴 키워드가 없는 가게(21%)는 업종 값을, 많은 가게는 자기 값을 받는다. 증거가 적으면
    업종이 말하게 하고 많으면 자기가 말하는 것 — `BAYES_C` 가 평점을 전역 평균으로
    수축시키는 것과 같은 원리다(`analysis/ASSESSMENT-recsys.md` 4.3).
    """
    if menu is None and cat is None:
        return None
    if cat is None:
        return menu
    if menu is None:
        return cat
    return (n * menu + POOL_K * cat) / (n + POOL_K)


def _category(sid: str, table: dict) -> float | None:
    """그 가게 업종의 트렌드 값. `cat3` -> `cat2` 순으로 찾고 없으면 None.

    네이버 데이터랩은 **가게 단위로 쓸 수 없다** — 개별 상호는 검색량이 거의 0이라
    상대값(정수, 최대 100)에서 전부 0 으로 돌아온다. 업종·메뉴 키워드로만 의미가 있어서
    카테고리 값을 가게로 펴는 이 모양이 된다.
    """
    if not table:
        return None
    s = svc.get_store(sid) or {}
    for key in ("cat3", "cat2"):
        v = table.get(s.get(key))
        if isinstance(v, (int, float)):
            return float(v)
    return None


# ────────────────────────────────────────────────────────── 조립
def _build() -> dict:
    """가게별 출처 원점수. 무게·z 는 여기서 안 매긴다 — 후보 집합마다 달라지기 때문이다."""
    rows_by, latest = _collect()
    if latest is None:
        return {"as_of": None, "stale_days": None, "raw": {}, "used": {},
                "n": 0, "category_n": 0, "menu_n": 0, "menu_stores": 0}

    last = dt.date.fromisoformat(latest)
    a0 = (last - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    b0 = (last - dt.timedelta(days=2 * WINDOW_DAYS)).isoformat()
    cats = _category_table()
    kws, of = _menu_table()

    # 검색 항은 리뷰가 없는 가게에도 있다 — 우주는 리뷰가 아니라 가게 전체다.
    raw: dict[str, dict[str, float | None]] = {}
    used: dict[str, list[tuple[str, float]]] = {}
    for sid in svc.all_store_ids():
        rows = rows_by.get(sid)
        entry: dict[str, float | None] = {}
        for key in ("season", "fad"):
            m, n, top = _menu(sid, key, kws, of)
            entry[key] = _pooled(m, n, _category(sid, cats.get(key) or {}))
            if key == "season" and top:
                used[sid] = top
        entry["inflow"] = _inflow(rows, a0, b0) if rows else None
        entry["rating"] = _rating(rows, a0, b0) if rows else None
        if any(v is not None for v in entry.values()):
            raw[sid] = entry

    stale = (dt.date.today() - last).days
    return {"as_of": latest, "stale_days": stale, "raw": raw, "used": used,
            "window_days": WINDOW_DAYS, "n": len(raw),
            "category_n": len(cats.get("season") or cats.get("fad") or {}),
            "menu_n": len(kws), "menu_stores": len(of)}


def _artifact_mtimes() -> tuple[float, float]:
    """검색 산출물 둘의 mtime. 없으면 0 — 파일이 생기거나 바뀌면 캐시가 다시 만들어진다."""
    out = []
    for p in (CATEGORY_FILE, MENU_FILE):
        try:
            out.append(p.stat().st_mtime)
        except OSError:
            out.append(0.0)
    return out[0], out[1]


def _state() -> dict:
    """캐시. `reviews.jsonl` 이나 검색 산출물이 바뀌면 다시 만든다(크롤·수집 갱신 = 트렌드 갱신)."""
    global _cache
    with _lock:
        key = (_reviews_mtime(), *_artifact_mtimes())
        if _cache is None or _cache.get("mtime") != key:
            built = _build()
            built["mtime"] = key
            _cache = built
        return _cache


def _too_stale(st: dict) -> bool:
    """자료가 낡아 트렌드를 꺼야 하나. 끌 때는 **경고를 남긴다**(조용히 0 을 주지 않는다)."""
    global _stale_warned
    days = st.get("stale_days")
    if days is None or days <= MAX_STALE_DAYS:
        return False
    if not _stale_warned:
        from app import observe          # recsys -> app 은 부를 때만 (policy 와 같은 규칙)
        observe.log("trend.stale", level=logging.WARNING, as_of=st.get("as_of"),
                    stale_days=days, max_stale_days=MAX_STALE_DAYS,
                    hint="크롤을 다시 돌려 data/kakao/reviews.jsonl 을 갱신하세요")
        _stale_warned = True
    return True


def breakdown(ids: list[str]) -> dict[str, dict[str, float]]:
    """store_id -> `{출처: 무게 × z}`. `scores` 는 이것을 더한 값이다.

    카드 응답의 `attribution` 이 "계절 때문인가, 유행 때문인가, 리뷰 증가율 때문인가" 에
    답할 때 쓴다. 후보가 없거나 자료가 낡으면 빈 dict — `scores` 와 같은 조건이다.
    무게가 0 이거나 아는 가게가 둘 미만인 출처는 키 자체가 없다(0 이 아니라 '안 봤다').
    """
    if not ids:
        return {}
    st = _state()
    if not st["raw"] or _too_stale(st):
        return {}

    parts: dict[str, dict[str, float]] = {sid: {} for sid in ids}
    for name in SOURCES:
        w = source_weight(name)
        if w == 0.0:
            continue
        vals = np.array([_value(st, sid, name) for sid in ids], dtype=np.float64)
        known = ~np.isnan(vals)
        if known.sum() < 2:               # 아는 가게가 없으면 그 출처는 할 말이 없다
            continue
        mean = float(vals[known].mean())
        sd = float(vals[known].std())
        z = np.zeros(len(ids), dtype=np.float64)      # 모르는 가게는 0 = 후보 평균
        if sd > 0:
            z[known] = _saturate((vals[known] - mean) / sd)
        for i, sid in enumerate(ids):
            parts[sid][name] = float(w * z[i])
    return parts


def scores(ids: list[str]) -> dict[str, float]:
    """store_id -> 트렌드 z 점수(무게 **전**). 후보가 없거나 자료가 낡으면 빈 dict.

    z 는 **이번 후보 집합 안에서** 낸다 — `ranking.distance_bonus` 와 같은 이유다.
    '500m 안만 보는 사람' 에게는 그 안에서의 상대적 트렌드가 순서를 만들어야 한다.
    """
    parts = breakdown(ids)
    return {sid: float(sum(v.values())) for sid, v in parts.items()}


def _value(st: dict, sid: str, name: str) -> float:
    v = (st["raw"].get(sid) or {}).get(name)
    return float("nan") if v is None else float(v)


def _saturate(z: np.ndarray) -> np.ndarray:
    """z 를 ±`Z_CLIP` 안으로 **순서를 지키면서** 넣는다.

    다른 모듈처럼 `np.clip` 으로 자르면 안 되는 이유가 실측으로 나왔다. 증가율은
    오른쪽 꼬리가 긴 분포다(원값 평균 +0.075 · sd 0.656 · 최대 +3.00 — 직전 180일에
    0건이던 가게가 상한을 만든다). 풀 1,629곳에서 `|z|>3` 은 37곳(2.27%)뿐이라
    꼬리 자체는 얌전한데, **그 37곳이 정확히 상위권이다** — 하드 클립을 걸면
    상위 20곳이 전부 +3.00 으로 같아져 트렌드가 띄우려는 바로 그 구간이 동점이 된다.

    `tanh` 는 ±`Z_CLIP` 경계를 지키면서 **단조**라 동점을 만들지 않고, |z| < 1.5 인
    대부분 구간에서는 거의 선형이라 평범한 가게의 눈금도 바뀌지 않는다.
    """
    return Z_CLIP * np.tanh(z / Z_CLIP)


def bonus(ids: list[str]) -> dict[str, float]:
    """store_id -> **점수에 더할** 트렌드 보너스. 꺼져 있으면 빈 dict(= 보정 없음).

    `ranking.distance_bonus` 와 같은 자리·같은 모양이라 두 정책에 그대로 내려간다.
    학습(`m`·`v`)에는 들어가지 않는다 — 모듈 문서의 '로그의 함수 / 시계의 함수' 참조.
    """
    w = weight()
    if w == 0.0:
        return {}
    z = scores(ids)
    return {sid: w * v for sid, v in z.items()} if z else {}


def slot_pick(ids: list[str], n: int, *, seed: int,
              tiers: dict | None = None) -> list[str]:
    """트렌드 슬롯에 넣을 카드 n장. **오르는 가게(z > 0)만** 후보다.

    `ids` 는 정책 라우터가 이미 걸러 준 후보(풀 ∩ 필터 허용 집합 − 보여준 카드)다. 그 안에서
    z 를 내고(`scores`), 양수만 남겨 `(태그 계층, z)` 순으로 세운 뒤 상위 `SLOT_TOP_N`
    안에서 씨앗 고정 난수로 n장을 고른다. 계층을 앞에 두는 이유는 `ranking.tiers` 와 같다
    — 사용자가 고른 태그를 더 많이 가진 가게가 먼저다. 슬롯도 그 규칙 밖에 있지 않다.

    후보가 없으면(꺼짐·자료가 낡음·전부 내림세) **빈 리스트**를 준다. 슬롯은 채워야
    하는 자리가 아니라 뽑을 게 있을 때만 쓰는 자리다 — 없으면 두 정책이 그 칸을 채운다.
    """
    if n <= 0 or not ids:
        return []
    z = scores(ids)
    if not z:
        return []
    rising = [(int((tiers or {}).get(sid, 0)), v, sid)
              for sid, v in z.items() if v > 0.0]
    if not rising:
        return []
    rising.sort(key=lambda t: (-t[0], -t[1], t[2]))
    # 브랜드마다 한 곳. 메뉴 키워드 점수는 같은 체인이면 같아서(실측: 본죽 4곳이 상위 10 중
    # 4곳) 후보 10곳이 한 브랜드로 채워진다 — 그러면 슬롯이 "뜨는 가게" 가 아니라 "뜨는 체인" 을
    # 반복해서 내보낸다. 정책들의 '같은 브랜드 연속 금지' 와 같은 뜻이다.
    brands = _brand_map()
    seen: set[str] = set()
    top: list[str] = []
    for _, _, sid in rising:
        b = brands.get(sid, sid)
        if b in seen:
            continue
        seen.add(b)
        top.append(sid)
        if len(top) >= SLOT_TOP_N:
            break
    rng = np.random.default_rng(seed)
    take = min(n, len(top))
    return [top[i] for i in rng.choice(len(top), size=take, replace=False)]


# ────────────────────────────────────────────────────────── 관찰
def explain(store_id: str) -> dict | None:
    """한 가게의 출처별 원점수. 관리자 API 가 '왜 이게 뜬다고 보나' 에 답할 때 쓴다."""
    st = _state()
    raw = st["raw"].get(store_id)
    if raw is None:
        return None
    return {"store_id": store_id, "as_of": st["as_of"],
            "window_days": st.get("window_days"),
            "raw": {k: (None if v is None else round(float(v), 4))
                    for k, v in raw.items()},
            # 계절 항에 실제로 들어간 메뉴 키워드(상위 K). '왜 이 가게가 뜬다고 보나' 의 답이다.
            "menu": [{"keyword": kw, "season": round(v, 4)}
                     for kw, v in (st.get("used") or {}).get(store_id, [])]}


def status() -> dict:
    """트렌드 층의 상태. 켜졌나·자료가 얼마나 낡았나·출처별 무게."""
    st = _state()
    days = st.get("stale_days")
    return {"enabled": enabled(), "weight": weight(),
            "slot_enabled": slot_enabled(), "slot_every": slot_every(),
            "slot_top_n": SLOT_TOP_N,
            "source_weights": {n: source_weight(n) for n in SOURCES},
            "as_of": st.get("as_of"), "stale_days": days,
            "max_stale_days": MAX_STALE_DAYS,
            "stale": days is not None and days > MAX_STALE_DAYS,
            "stores": st.get("n", 0),
            "category_entries": st.get("category_n", 0),
            "category_file": str(CATEGORY_FILE),
            "menu_entries": st.get("menu_n", 0),
            "menu_stores": st.get("menu_stores", 0),
            "menu_file": str(MENU_FILE),
            "min_density": MIN_DENSITY, "menu_top_k": MENU_TOP_K, "pool_k": POOL_K,
            "window_days": WINDOW_DAYS, "shrink_rating": SHRINK_RATING}


def reset() -> None:
    """캐시를 버린다(테스트용)."""
    global _cache, _stale_warned
    with _lock:
        _cache = None
        _stale_warned = False
