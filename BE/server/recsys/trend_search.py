# -*- coding: utf-8 -*-
"""검색 축 — 계절·트렌드·검색량을 **같은 눈금**으로. **관측 전용**이다.

`trend_history.py` 와 형제다. 그쪽은 우리가 수집한 **리뷰**를 보고 여기는 네이버
**검색**을 본다. 추천 점수에 한 톨도 들어가지 않는다.

## `trend_history.national()` 과 뭐가 다른가

그 함수는 업종 **사이**의 크기를 비교할 수 없다. 업종마다 따로 요청하고 네이버가
응답 안에서 최댓값을 100 으로 맞추기 때문이고, 실측하면 71개 캐시 파일이 **전부
최댓값 정확히 100.0** 이다. 그래서 "스테디셀러 = 변동성과 무관하게 절대값이 월등한
것" 을 그것으로는 만들 수 없다.

`analysis/fetch_trend_category.py --level` 이 그 구멍을 메운다 — 요청마다 같은
**앵커 키워드를 한 칸 더** 넣어 두 그룹이 같은 정규화 상수를 공유하게 하고
`업종 중앙값 / 앵커 중앙값` 을 `data/analysis/trend_level.json` 에 적는다. 앵커
중앙값이 응답마다 2.49~27.23 으로 **11배 흔들리는 게**(실측) 바로 그 상수이고,
비율을 내면 약분된다.

**시계열은 새로 안 받는다.** 수준 응답의 업종 시계열은 기존 캐시의 것과 상수배로
완전히 비례한다(806일 전부 이탈 0.0% 실측 — 같은 실제 검색량에 다른 상수가 곱해진
것뿐이다). 그래서 기존 캐시를 **자기 중앙값으로 나눈 뒤 수준을 곱해** 쓴다. 단위는
'앵커 중앙값 = 1' 이라 업종을 한 축에 겹쳐 그릴 수 있다. 캐시를 두 벌 들고 있을
이유가 없어 수준 수집 캐시는 커밋하지 않는다(`.gitignore`).

## 세 화면을 나누는 이유 — 그리고 창도 셋이다

한 표에 섞으면 성질이 다른 세 질문이 서로를 가린다.

    계절    이 음식이 **매년** 이맘때 뜨나     12달로 접은 모양 + 두 해 반복성
    트렌드  **요즘** 뜨나                     최근 창 대 전년 같은 창
    검색량  애초에 **얼마나** 검색되나         앵커 대비 수준 (이게 절대값이다)

질문이 다르면 보는 **창**도 다르다. 계절은 "어느 해들을 접나", 트렌드는 "요즘이
언제부터인가", 검색량은 "어디를 그리나" 다. 그래서 창을 셋 따로 받는다
(`season_*`·`trend_*`·`frm/to`). 창을 하나로 묶어 놓으면 검색량 그래프를 석 달로
좁힐 때 계절 프로파일까지 석 달치로 접혀 무너진다.

**창을 옮기면 분류도 같이 움직인다.** 계절 창이 두 해를 못 덮으면 `repeat` 이 없어
계절형이 안 나오고, 트렌드 창을 옮기면 `growth` 가 바뀐다. 그래서 응답에 쓴 창을
`windows` 로 되돌려 주고 화면이 그것을 그대로 적는다 — 어떤 창에서 나온 분류인지
안 보이면 표를 못 믿는다.

## 증가율은 **중앙값을 뺀다** — 검색 축에도 교란이 있었다

리뷰 축의 교란 ①("전체 리뷰가 늘어서 모든 업종이 상승으로 나온다")과 **부호만 반대인
같은 문제**가 여기에도 있다. 71개 업종의 전년 대비 증가율이 **64개가 음수**이고
중앙값이 **-0.194** 다(실측). 업종이 다 같이 죽은 게 아니라 **네이버 검색 자체가 한
해에 약 18% 줄었다.** 원값으로 순위를 내면 표가 "전부 하락" 으로 채워져 아무 말도
안 한다.

-> `growth_rel = growth - (전체 중앙값)` 을 쓴다. 원값(`growth`)도 같이 준다 —
뺀 값만 주면 "그래서 실제로 늘었나" 를 답할 수 없다.

## 유행 판정에서 **반복되는 것은 뺀다**

`repeat` 이 0.5 이상이면 그 움직임은 달력이다. 안 빼면 떡볶이(반복 +0.89)·도시락
(+0.76)·햄버거(+0.70) 가 "유행 하락" 으로 들어온다 — 실제로 그렇게 나왔다.

## 절대값은 문턱이고 변동성은 순위다

둘 중 하나를 고르는 문제가 아니었다. 변동성만 보면 분모가 작은 것이 순위를 먹고
(깐부치킨 9.3배·밀크티 11.3배는 잡음이다), 절대값만 보면 아메리카노가 영원히 1위라
아무 말도 안 해 준다. 그래서 **절대값으로 걸러 낸 뒤 변동성으로 줄을 세운다.**
문턱은 `gate` 로 화면에서 조절한다 — 코드에 박아 두면 그 값이 왜 그 값인지
아무도 모른다.

## ⚠️ 이건 '업종명이 얼마나 검색되나' 다

수요가 아니라 **검색어**의 크기다. `카페` 6.61 대 `커피전문점` 0.02 가 그 증거다 —
같은 것을 가리키는데 사람들이 쓰는 말만 다르다. 업종명이 사람들의 말과 다르면 그
업종의 수준은 과소평가된다. 순위를 볼 때 이걸 잊으면 안 된다.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import statistics
import threading
from pathlib import Path

from .trend_history import SERIES_DIR

#: 수준 산출물. 없으면 `available: false` 이고 **오류가 아니다** — 리뷰 축과
#: 전국 증가율은 이것 없이도 그대로 돈다.
LEVEL_FILE: Path = SERIES_DIR.parent / "trend_level.json"

#: 화면에 적는 출처. API 응답의 `source` 는 기계용 식별자라 사람이 읽을 이름을 따로 둔다.
SOURCE = {
    "id": "naver_datalab_search_trend",
    "label": "네이버 데이터랩 검색어 트렌드",
    "scope": "전국 · 일별 · 업종명을 검색어로",
    "url": "https://datalab.naver.com/keyword/trendSearch.naver",
    "note": "지역 파라미터가 없는 API 라 전국 값이다. 업종명이 사람들이 쓰는 말과 "
            "다르면(커피전문점 대 카페) 그 업종은 과소평가된다.",
}

#: 스테디셀러 문턱의 **기본값**. 71개 업종 수준의 중앙값이 0.306 이라 그 근처로 둔다
#: (이 값 이상이 36개). 화면에서 조절하므로 여기 값은 첫 화면만 정한다.
LEVEL_GATE = 0.3

#: 계절로 인정할 최소 진폭(성수기/비수기 배수)과 최소 반복성(두 해 모양 상관).
#: 진폭만으로는 부족하다 — 한 해만 튄 것도 진폭이 크다. **반복돼야** 계절이다.
SEASON_SWING = 1.5
SEASON_REPEAT = 0.5

#: 유행으로 인정할 최소 |log(최근/전년동기)|. 0.2 이면 약 ±22% 다.
FAD_LOG = 0.2

#: 트렌드 창의 **기본** 길이(일). `analysis/fetch_trend_category.py: WINDOW_DAYS` 와
#: 같은 값으로 둔다 — 화면의 기본값과 `trend_category.json` 의 점수가 서로 설명돼야 한다.
GROWTH_WINDOW = 180

#: 트렌드 창의 최소 길이(일). 이보다 짧으면 며칠짜리 스파이크가 중앙값을 끌고 간다.
MIN_TREND_DAYS = 14

#: 한 달로 인정할 최소 관측 일수. 수집 구간의 양 끝은 반쪽 달이라 빼야 한다.
MIN_MONTH_DAYS = 20

#: 트렌드 그래프의 눈금(일). 최근 창과 전년 같은 창을 **같은 칸 수**로 접어 겹쳐 그린다.
TREND_CHUNK = 7

_lock = threading.Lock()
_base: tuple | None = None


def _month(day: str) -> str:
    return day[:7]


def _median(v: list[float]) -> float:
    return statistics.median(v) if v else 0.0


def _date(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _stamp() -> tuple:
    """산출물이 바뀌었나. 파일을 열지 않고 `stat` 만 본다."""
    try:
        lv = LEVEL_FILE.stat().st_mtime_ns
    except OSError:
        lv = 0
    files = sorted(SERIES_DIR.glob("*.json")) if SERIES_DIR.is_dir() else []
    return lv, len(files), max((f.stat().st_mtime_ns for f in files), default=0)


# ────────────────────────────────────────────── 파생 지표
def _fold(by_month: dict[str, float]) -> dict:
    """달력 달로 접은 프로파일(1~12월). **자기 전체 평균으로 나눠 모양만** 남긴다.

    크기는 `level` 이 따로 들고 있으므로 여기서 또 실을 이유가 없고, 나눠 두면 업종이
    달라도 같은 눈금으로 겹쳐 그릴 수 있다. **1.0 이 그 업종의 연평균**이다.
    """
    per: dict[int, list[float]] = collections.defaultdict(list)
    for mo, v in by_month.items():
        per[int(mo[5:7])].append(v)
    base = sum(by_month.values()) / len(by_month) if by_month else 0.0
    if base <= 0:
        return {"season": [None] * 12, "swing": None, "peak": None, "trough": None}
    # 반올림 **전** 값으로 진폭을 낸다. 반올림한 칸으로 나누면 12.0 배가 12.004 로
    # 나온다(실제로 겪었다) — 화면에 싣는 배수라 그 오차가 눈에 보인다.
    exact: list[float | None] = []
    for m in range(1, 13):
        vs = per.get(m)
        exact.append(sum(vs) / len(vs) / base if vs else None)
    prof = [None if v is None else round(v, 4) for v in exact]
    got = [(v, m) for m, v in enumerate(exact, 1) if v is not None]
    if len(got) < 12:          # 한 바퀴가 안 차면 진폭을 말하지 않는다
        return {"season": prof, "swing": None, "peak": None, "trough": None}
    hi, lo = max(got), min(got)
    return {"season": prof,
            "swing": round(hi[0] / lo[0], 3) if lo[0] > 0 else None,
            "peak": hi[1], "trough": lo[1]}


def _repeat(by_month: dict[str, float]) -> float | None:
    """두 해의 월별 **모양**이 일치하나. 계절이 잡음이 아니라는 증거다.

    각 해의 평균으로 나눠 수준 차이를 지우고 모양만 견준다. 그냥 상관을 내면 두 해
    사이의 전체 성장이 상관을 밀어 올려, 계절이 아닌 것도 높게 나온다.
    """
    ms = sorted(by_month)
    if len(ms) < 24:
        return None
    a, b = ms[-24:-12], ms[-12:]
    if [m[5:7] for m in a] != [m[5:7] for m in b]:   # 달이 어긋나면 비교가 안 된다
        return None
    va = [by_month[m] for m in a]
    vb = [by_month[m] for m in b]
    ma, mb = sum(va) / 12, sum(vb) / 12
    if ma <= 0 or mb <= 0:
        return None
    try:
        r = statistics.correlation([x / ma for x in va], [y / mb for y in vb])
    except statistics.StatisticsError:      # 한쪽이 상수면 상관이 정의되지 않는다
        return None
    return round(r, 3)


def _monthly(daily: dict[str, float], lo: str, hi: str) -> dict[str, float]:
    """`lo`~`hi` 안의 일별 값을 달 평균으로. 관측이 모자란 달은 뺀다."""
    per: dict[str, list[float]] = collections.defaultdict(list)
    for d, v in daily.items():
        if lo <= d <= hi:
            per[_month(d)].append(v)
    return {m: sum(v) / len(v) for m, v in per.items() if len(v) >= MIN_MONTH_DAYS}


def _window(daily: dict[str, float], lo: str, hi: str) -> list[float]:
    return [v for d, v in daily.items() if lo <= d <= hi]


def _growth(daily: dict[str, float], lo: str, hi: str,
            plo: str, phi: str) -> float | None:
    """`log(창 중앙값 / 전년 같은 창 중앙값)`.

    평균이 아니라 중앙값인 이유는 `fetch_trend_category._level` 과 같다 — 하루짜리
    스파이크가 창을 끌고 가면 안 된다.
    """
    now, ago = _median(_window(daily, lo, hi)), _median(_window(daily, plo, phi))
    if now <= 0 or ago <= 0:
        return None
    return round(math.log(now / ago), 4)


def _chunks(daily: dict[str, float], lo: str, hi: str) -> list[float | None]:
    """창을 `TREND_CHUNK` 일 단위로 접는다. 최근 창과 전년 창을 **같은 칸 수**로 만들어
    겹쳐 그리기 위한 것이라, 칸의 기준은 날짜가 아니라 **창 시작으로부터의 일수**다."""
    a, b = _date(lo), _date(hi)
    if not a or not b or b < a:
        return []
    n = (b - a).days // TREND_CHUNK + 1
    acc: list[list[float]] = [[] for _ in range(n)]
    for d, v in daily.items():
        if lo <= d <= hi:
            k = (_date(d) - a).days // TREND_CHUNK
            acc[k].append(v)
    return [round(sum(c) / len(c), 4) if c else None for c in acc]


# ────────────────────────────────────────────── 원자료 (캐시 뒤)
def _build() -> dict:
    """업종별 비교 가능한 일별 시계열. 파생 지표는 **여기서 내지 않는다** — 창이 요청마다
    다르기 때문이다. 71개 파일 × 806일 파싱만 캐시한다(매 요청마다 하면 `http.slow`
    500ms 에 걸린다); 접기·자르기는 그 위에서 수 ms 다."""
    out: dict = {
        "available": False, "level_file": str(LEVEL_FILE),
        "series_dir": str(SERIES_DIR), "rows": [], "days": [],
        "hint": "`python -m analysis.fetch_trend_category --level` 로 업종 사이 "
                "비교 가능한 검색 수준을 먼저 구워야 합니다. 증가율만 보는 전국 "
                "추이(/api/trend/national)는 그것 없이도 동작합니다.",
    }
    try:
        meta = json.loads(LEVEL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    levels = meta.get("levels") or {}
    if not levels or not SERIES_DIR.is_dir():
        return out

    rows: list[dict] = []
    seen: set[str] = set()
    for f in sorted(SERIES_DIR.glob("*.json")):
        try:
            res = (json.loads(f.read_text(encoding="utf-8")).get("results") or [{}])[0]
        except (OSError, ValueError, IndexError):
            continue
        name = res.get("title") or f.stem
        level = levels.get(name)
        if level is None or name in seen:
            continue
        seen.add(name)
        raw: dict[str, float] = {}
        for row in res.get("data") or []:
            day = (row.get("period") or "").strip()
            if len(day) != 10:
                continue
            try:
                raw[day] = float(row.get("ratio"))
            except (TypeError, ValueError):
                continue
        if not raw:
            continue
        # 자기 중앙값으로 나눠 응답별 정규화 상수를 지우고, 수준을 곱해 공통 눈금에 올린다.
        mid = _median(list(raw.values()))
        scale = (level / mid) if mid > 0 else 0.0
        rows.append({"name": name, "level": round(float(level), 5), "points": len(raw),
                     "_daily": {d: v * scale for d, v in raw.items()}})

    if not rows:
        return out
    for i, r in enumerate(sorted(rows, key=lambda r: -r["level"]), 1):
        r["level_rank"] = i
    out.update(available=True, rows=rows,
               days=sorted({d for r in rows for d in r["_daily"]}),
               anchor=meta.get("anchor"), anchor_keywords=meta.get("anchor_keywords"),
               as_of=meta.get("as_of"), fetched=meta.get("range"))
    out.pop("hint", None)
    return out


def _cached() -> dict:
    global _base
    stamp = _stamp()
    with _lock:
        if _base is not None and _base[0] == stamp:
            return _base[1]
    built = _build()
    with _lock:
        _base = (stamp, built)
    return built


def _bucket(day: str, unit: str) -> str:
    """날짜를 눈금 이름으로. 주는 그 주 **월요일**이다(ISO)."""
    if unit == "month":
        return day[:7]
    if unit == "week":
        d = dt.date.fromisoformat(day)
        return (d - dt.timedelta(days=d.weekday())).isoformat()
    return day


# ────────────────────────────────────────────── 분류
def classify(row: dict, gate: float) -> dict:
    """세 깃발과 대표 이름. **서로 배타가 아니다** — 스테디셀러이면서 계절형일 수 있다.

    물어본 것이 정확히 그것이었다: "스테디셀러인데 특정 계절에 더 뜨는 특징이 있는지".
    배타적인 한 칸으로 만들면 그 질문에 답을 못 한다.

    `steady` 는 **절대값만** 본다 — "변동성이 크든 적든 절대값이 월등한 것" 이 그
    정의다. 계절이냐 유행이냐는 그 위에 겹쳐 붙는 성질이다.

    유행은 `growth_rel`(전체 중앙값을 뺀 값)로 재고, **반복되는 것은 제외한다.**
    둘 다 안 하면 표가 "전부 하락" 으로 채워지고 떡볶이가 유행으로 들어온다.
    """
    steady = row["level"] >= gate
    repeats = row["repeat"] is not None and row["repeat"] >= SEASON_REPEAT
    seasonal = repeats and row["swing"] is not None and row["swing"] >= SEASON_SWING
    g = row.get("growth_rel")
    fad = g is not None and abs(g) >= FAD_LOG and not repeats
    if not steady:
        kind = "관측 부족"        # 문턱 미달. 변동성이 커도 분모가 작으면 잡음이다
    elif seasonal:
        kind = "계절형"           # 계절이 먼저다 — 유행처럼 보이는 게 달력일 때가 많다
    elif fad:
        kind = "유행 상승" if g > 0 else "유행 하락"
    else:
        # 문턱을 넘었고 달력도 유행도 아니다. 모양은 반복되지만(`repeats`) 진폭이
        # 작아 계절로 부를 게 아닌 것들이 여기 온다 — `swing` 을 같이 봐야 한다.
        kind = "스테디셀러"
    return {"steady": steady, "seasonal": seasonal, "repeats": repeats,
            "fad": fad, "kind": kind}


# ────────────────────────────────────────────── 창
def _clamp(lo: str, hi: str, first: str, last: str) -> tuple[str, str]:
    """빈 값은 자료의 끝으로, 뒤집힌 값은 바로 세워서, 자료 밖은 안으로."""
    lo = lo if _date(lo) else first
    hi = hi if _date(hi) else last
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, first), min(hi, last)


def _shift(day: str, days: int) -> str:
    return (_date(day) + dt.timedelta(days=days)).isoformat()


def search(frm: str = "", to: str = "", gate: float = LEVEL_GATE,
           unit: str = "month", top: int = 0,
           season_from: str = "", season_to: str = "",
           trend_from: str = "", trend_to: str = "") -> dict:
    """계절·트렌드·검색량을 한 번에. **창이 셋이다.**

    - `season_from`~`season_to` : 계절을 **어느 해들로** 접나. 비면 전체. 두 해를 못
      덮으면 `repeat` 이 없어 계절형이 안 나온다 — 응답 `windows.season.months` 로 안다
    - `trend_from`~`trend_to` : "요즘" 이 언제부터인가. 비면 마지막 180일. 비교 대상은
      **정확히 365일 전의 같은 창**이다(`windows.trend.prior_*`)
    - `frm`~`to` + `unit` : 검색량 그래프를 어디를 어떤 눈금으로 그리나

    - `level` : 앵커 중앙값 대비 검색 수준. **업종 사이 비교가 되는 값**이고 이것이
      스테디셀러의 기준이다. `gate` 미만은 `kind: "관측 부족"` 이 된다
    - `season` : 12달로 접은 모양(1.0 = 그 업종의 연평균). `swing` 은 성수기/비수기
      배수, `repeat` 은 두 해 모양 상관 — **반복돼야** 계절로 인정한다
    - `growth` / `growth_rel` : 트렌드 창의 증가율과 전체 중앙값을 뺀 값
    - `trend_recent` / `trend_prior` : 트렌드 창과 전년 창을 7일 칸으로 접은 계열.
      **같은 칸 수**라 겹쳐 그린다
    - `series` : 검색량 창을 `unit` 으로 접은 시계열
    - `points` : 그 업종의 관측 일수(= 데이터량). 화면에 출처와 함께 싣는다

    검색은 일별 원자료가 있어 `unit="day"` 가 되지만 **리뷰 축(`trend_history.local`)은
    월별만** 된다 — 역삼동 전체가 하루 23건이라 일별로 자르면 0·1건짜리 톱니가 된다.
    """
    base = _cached()
    if not base["available"]:
        return {k: v for k, v in base.items() if not k.startswith("_")}

    days = base["days"]
    first, last = days[0], days[-1]
    unit = unit if unit in ("day", "week", "month") else "month"

    # 창 셋. 트렌드 창은 전년 같은 창이 자료 안에 있어야 하므로 시작이 first+365 이상.
    v_lo, v_hi = _clamp(frm, to, first, last)
    s_lo, s_hi = _clamp(season_from, season_to, first, last)
    t_lo, t_hi = _clamp(trend_from or _shift(last, -(GROWTH_WINDOW - 1)), trend_to,
                        first, last)
    t_lo = max(t_lo, _shift(first, 365))
    if t_hi < t_lo:
        t_hi = t_lo
    short_trend = (_date(t_hi) - _date(t_lo)).days + 1 < MIN_TREND_DAYS
    p_lo, p_hi = _shift(t_lo, -365), _shift(t_hi, -365)

    axis: list[str] = []
    seen: set[str] = set()
    for d in days:
        if v_lo <= d <= v_hi:
            b = _bucket(d, unit)
            if b not in seen:
                seen.add(b)
                axis.append(b)
    t_axis: list[str] = []
    a = _date(t_lo)
    while a <= _date(t_hi):
        t_axis.append(a.isoformat())
        a += dt.timedelta(days=TREND_CHUNK)

    rows: list[dict] = []
    for r in base["rows"]:
        daily = r["_daily"]
        by_month = _monthly(daily, s_lo, s_hi)
        row = {"name": r["name"], "level": r["level"], "level_rank": r["level_rank"],
               "points": r["points"], "months": len(by_month),
               "repeat": _repeat(by_month),
               "growth": None if short_trend else _growth(daily, t_lo, t_hi, p_lo, p_hi),
               "trend_recent": _chunks(daily, t_lo, t_hi),
               "trend_prior": _chunks(daily, p_lo, p_hi)}
        row.update(_fold(by_month))
        acc: dict[str, list[float]] = collections.defaultdict(list)
        for d, v in daily.items():
            if v_lo <= d <= v_hi:
                acc[_bucket(d, unit)].append(v)
        series = [round(sum(acc[b]) / len(acc[b]), 4) if b in acc else None for b in axis]
        got = [v for v in series if v is not None]
        row.update(series=series,
                   window_mean=round(sum(got) / len(got), 4) if got else None,
                   window_max=round(max(got), 4) if got else None,
                   window_days=sum(1 for d in daily if v_lo <= d <= v_hi))
        rows.append(row)

    # 전체 이동을 뺀다. 71개 중 64개가 음수이고 중앙값이 -0.194 였다(실측) — 그건
    # 업종이 아니라 네이버 검색 자체가 줄어든 것이다. 뺀 뒤가 업종 얘기다.
    gs = [r["growth"] for r in rows if r["growth"] is not None]
    shift = statistics.median(gs) if gs else 0.0
    for r in rows:
        r["growth_rel"] = None if r["growth"] is None else round(r["growth"] - shift, 4)
        r.update(classify(r, gate))

    rows.sort(key=lambda c: -c["level"])
    return {
        "available": True,
        "source": SOURCE["id"],
        "sources": {"search": {**SOURCE, "fetched": base["fetched"], "as_of": base["as_of"],
                               "anchor": base["anchor"],
                               "anchor_keywords": base["anchor_keywords"],
                               "categories": len(rows), "days": len(days)}},
        "anchor": base["anchor"], "anchor_keywords": base["anchor_keywords"],
        "as_of": base["as_of"], "fetched": base["fetched"],
        "gate": gate, "unit": unit, "growth_shift": round(shift, 4),
        "range": {"from": v_lo, "to": v_hi, "buckets": len(axis),
                  "first": first, "last": last, "days": len(days)},
        "windows": {
            "season": {"from": s_lo, "to": s_hi,
                       "months": max((r["months"] for r in rows), default=0),
                       "two_years": any(r["repeat"] is not None for r in rows)},
            "trend": {"from": t_lo, "to": t_hi, "prior_from": p_lo, "prior_to": p_hi,
                      "days": (_date(t_hi) - _date(t_lo)).days + 1,
                      "too_short": short_trend, "min_days": MIN_TREND_DAYS,
                      "chunk_days": TREND_CHUNK, "axis": t_axis},
            "volume": {"from": v_lo, "to": v_hi, "unit": unit, "buckets": len(axis)},
        },
        "thresholds": {"level_gate": gate, "season_swing": SEASON_SWING,
                       "season_repeat": SEASON_REPEAT, "fad_log": FAD_LOG,
                       "growth_window_days": GROWTH_WINDOW},
        "kinds": dict(collections.Counter(c["kind"] for c in rows)),
        "axis": axis, "categories": rows[:top] if top else rows,
        "note": "level 은 '업종명이 얼마나 검색되나' 다. 수요가 아니라 검색어의 "
                "크기라, 카페 6.61 대 커피전문점 0.02 처럼 같은 것을 가리키는 말끼리도 "
                "크게 다르다. 업종명이 사람들이 쓰는 말과 다르면 그 업종은 과소평가된다.",
    }
