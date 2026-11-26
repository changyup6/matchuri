# -*- coding: utf-8 -*-
"""트렌드 추이 — **관측 전용**. 추천 점수에 한 톨도 들어가지 않는다.

`trend.py` 는 '지금 뜨는가' 를 점수로 만들어 추천에 얹는 층이고(기본 OFF), 이 모듈은
**무엇이 어떻게 움직였나를 눈으로 보는** 자리다. 나중에 가중치를 줄지 판단하려면 먼저
신호가 있는지 봐야 하는데, 그걸 볼 화면이 없었다.

    전국 추이   네이버 검색어 트렌드 (외부, 오프라인 산출물)   `national()`
    역삼동 추이  우리가 수집한 리뷰                          `local()`

**역삼동 쪽이 더 강한 신호다.** 검색은 의도이고 리뷰는 결과다. 그리고 우리 리뷰는
역삼동 가게의 것이라 지역 불일치 문제가 없다 — 네이버 검색어 트렌드 API 에는 **지역
파라미터가 아예 없어서**(device·gender·ages 만 있다) 전국 값을 역삼에 갖다 대야 한다.

## 교란 요인이 세 겹이다 — 하나씩 벗겨야 신호가 나온다

실제로 다 겪었다. 층을 하나 벗길 때마다 다음 층이 드러났다.

**① 전체 리뷰가 늘었다.** 월 총량이 한 해에 두 배다(2025-10 **718건** -> 2026-08
**1,513건**). 건수로 보면 **모든 업종이 상승**으로 나온다.
-> 각 달 총량으로 나눈 **점유율**을 쓴다.

**② 계절이 지배한다.** 최근 3달 대 직전 3달로 보면 상위가 냉면·콩국수·감자전(여름),
하위가 감자탕·곱창전골·육개장(겨울)으로 채워진다. 유행이 아니라 달력이다.
-> **전년 같은 3달**과 견준다(`yoy_3m`).

**③ 업종 구성이 움직였다.** 그런데 전년 대비로도 상승이 전부 한식(칼국수·냉면·순대·
비빔밥), 하락이 전부 비한식(떡볶이·우동·짜장면·탕수육)으로 나왔다. 원인은 음식이
아니라 구성이었다 — 전년 같은 3달 대비 **한식 +5.4pp, 카페 -2.9pp**. 전체 대비
점유율로는 그 이동이 모든 한식 메뉴를 함께 밀어 올린다.
-> 키워드를 **그 메뉴가 속한 업종의 리뷰 대비**로 잰다(`scope`).

세 겹을 다 벗기고 나서야 꿔바로우·마파두부·마라샹궈(중식)·마제소바(일식)가 올라왔다.
그게 실제 유행이다.

## 표본이 얇은 업종은 표시만 한다

업종 안에서 정규화하면 분모가 작아진다. 분식은 전체의 2.3%라 월 30건 수준이고, 그
분모로 비율을 내면 순대 +21.74pp, 떡볶이 -15.75pp 같은 값이 나온다 — 움직인 게 아니라
분모가 흔들린 것이다. `MIN_SCOPE_MONTH` 미만인 업종은 `thin: true` 로 표시하고
**순위에서 뺀다**(값은 그대로 주니 화면에서 흐리게 그리면 된다).

## 마지막 달은 자른다

크롤이 달 중간에 끝나면 그 달은 반쪽이다(2026-09 는 09-02 크롤이라 49건). 넣으면
"9월에 급락" 이라는 거짓 신호가 된다. 마지막 리뷰 날짜가 그 달 말일이 아니면 그 달을
**빼고** 계산하고, `partial_month` 로 알려 준다.

## 불용어 목록이 필요 없다

업종별로 키워드를 묶던 설계에서는 `아메리카노`(339곳) 같은 것이 상위를 독점해서
불용어 목록이 필요했다. 여기서는 **키워드마다 자기 시계열**을 보므로 평평한 것은
그냥 순위에 안 올라온다 — 손으로 관리할 목록이 없다.

## 한계 — 아는 키워드만 본다

키워드를 우리 메뉴 어휘에서 뽑으므로, **메뉴에 없는 새 유행은 안 보인다.** 크롤을
갱신하면 메뉴도 갱신되니 다음 회차에 들어온다. 그 전에 급히 보고 싶은 것은
`extra_keywords` 로 넣는다.
"""
from __future__ import annotations

import collections
import datetime as dt
import math
import re
import threading

from . import store as svc
from . import trend

#: 기본으로 보여 줄 완전한 달 수.
#:
#: **25다(2년 + 1).** 13이면 전년 동월이 창의 첫 달 하나뿐이라 3달 평균으로 견줄 수
#: 없다. 25면 `최근 3달` 과 `전년 같은 3달` 이 둘 다 창 안에 들어온다. 리뷰가 월 488건
#: (2024-08) 이상인 구간이라 점유율을 재기에 충분하다 — 그 앞은 월 수백 건으로 떨어져
#: 점유율이 튄다(2013-04 부터 있지만 2023년에야 월 372건이 된다).
MONTHS = 25

#: 키워드 후보의 최소 취급 가게 수. 낮추면 목록이 폭발하고(2곳 이상이 5,560개),
#: 높이면 유행 초기를 놓친다. 5는 `data/kakao/menus.jsonl` 실측으로 1,741개다.
MIN_MENU_STORES = 5

#: 키워드 후보 상한. 리뷰 본문 33,300건 × 키워드 수가 스캔 비용이다(400개에 1.16초).
MAX_KEYWORDS = 400

#: 키워드 길이. 1글자는 아무 데나 걸리고, 긴 것은 세트명이라 검색어가 아니다.
KW_MIN, KW_MAX = 2, 10

#: 상승·하락을 재는 창(달). 최근 3달 대 직전 3달.
SWING = 3

#: 업종 안 정규화가 의미를 갖는 최소 월평균 리뷰 수. 이 미만이면 분모가 흔들려
#: 비율이 튄다(분식은 월 30건 수준이라 순대가 +21pp 로 나왔다).
MIN_SCOPE_MONTH = 60

#: `inflow` 가 의미를 갖는 최소 리뷰 수(창 안). 실측: 최근 180일 창 안 리뷰 수가
#: 가게당 중앙값 **2건**이다. 2건 대 1건으로 증가율을 내면 거의 노이즈라, 이 값
#: 미만인 가게는 순위에서 뺀다(5건 이상인 곳이 398곳, 전체의 14%다).
MIN_INFLOW_N = 5

#: 괄호·대괄호 안. 세트 구성·용량 설명이 들어 있다.
_PAREN = re.compile(r"[\(\[（【][^)\]）】]*[\)\]）】]")
#: 앞머리의 번호·기호.
_LEAD = re.compile(r"^\s*(\d+[.)]|[-•*])\s*")
#: 수량·용량 표기부터 끝까지.
_QTY = re.compile(r"\s*\d+\s*(인분?|조각|pcs?|개|ml|g|cm)\b.*$", re.I)
#: 끝에 붙은 크기 표기. **단어 경계를 요구한다** — 안 그러면 `에스프레소` 의 `소` 를
#: 크기로 보고 잘라 `에스프레` 를 만든다(실제로 겪었다).
_SIZE = re.compile(r"[\s(]+(대|중|소|라지|레귤러|[RLS])\)?\s*$")

_lock = threading.Lock()
_cache: dict[str, object] = {}


def _norm(name: str) -> str:
    """메뉴 이름을 검색·집계용으로 정규화한다.

    **띄어쓰기를 없앤다.** `페퍼로니 피자` 와 `페퍼로니피자` 가 따로 세어지면 둘 다
    순위에 못 오른다(실측: `카페라떼` 248 + `카페 라떼` 72 로 갈라져 있었다).
    """
    n = _PAREN.sub(" ", name or "")
    n = _LEAD.sub("", n)
    n = _QTY.sub("", n)
    n = _SIZE.sub("", n)
    n = re.sub(r"[+/&,]", " ", n)
    return re.sub(r"\s+", "", n).strip()


def _month(date: str | None) -> str | None:
    d = (date or "").strip()
    return d[:7] if len(d) >= 7 and d[4] == "-" else None


def _complete_months(last: str, n: int) -> tuple[list[str], str | None]:
    """마지막 **완전한** 달부터 거꾸로 n개. 반쪽인 달은 따로 돌려준다.

    `last` 는 가장 최근 리뷰 날짜(`YYYY-MM-DD`)다. 그 날이 그 달 말일이 아니면 그 달은
    크롤이 중간에 끊긴 것이므로 계산에서 뺀다.
    """
    try:
        d = dt.date.fromisoformat(last)
    except ValueError:
        return [], None
    eom = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    partial = None
    if d < eom:
        partial = d.strftime("%Y-%m")
        d = d.replace(day=1) - dt.timedelta(days=1)      # 직전 달 말일로 물린다
    out = []
    cur = d.replace(day=1)
    for _ in range(n):
        out.append(cur.strftime("%Y-%m"))
        cur = (cur - dt.timedelta(days=1)).replace(day=1)
    return list(reversed(out)), partial


def _keywords(cat_of: dict[str, str],
              limit: int = MAX_KEYWORDS) -> dict[str, str]:
    """추적 키워드 -> 그 키워드의 **소속 업종**. 취급 가게가 많은 순으로 자른다.

    소속 업종은 그 메뉴를 파는 가게들의 최빈 `cat2` 다. 이 값이 정규화의 분모를
    정한다 — `냉면` 은 전체 리뷰가 아니라 **한식 리뷰** 대비로 재야 업종 구성 이동에
    휘둘리지 않는다(머리말 ③).
    """
    stores: dict[str, set] = collections.defaultdict(set)
    cats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for m in svc.all_menus():
        n = _norm(m.get("menu_name"))
        if not (KW_MIN <= len(n) <= KW_MAX) or n.isdigit():
            continue
        sid = m.get("store_id")
        stores[n].add(sid)
        cats[n][cat_of.get(sid) or "미분류"] += 1
    ranked = sorted(stores.items(), key=lambda kv: -len(kv[1]))
    out: dict[str, str] = {}
    for k, v in ranked:
        if len(v) < MIN_MENU_STORES:
            break
        out[k] = cats[k].most_common(1)[0][0]
        if len(out) >= limit:
            break
    return out


def _compare(series: dict[str, float], recent: list[str], base: list[str]) -> dict:
    """두 구간의 점유율 평균을 견준다.

    `delta` 는 점유율 차이(퍼센트포인트)이고 `ratio` 는 배수다. **둘을 같이 주는 이유:**
    0.1% -> 0.3% 는 배수로 3배지만 화면에서는 아무것도 아니고, 5% -> 6% 는 배수로
    1.2배지만 실제로 큰 움직임이다. 하나만 보면 반드시 오해한다.
    """
    if not recent or not base:
        return {"delta": None, "ratio": None}
    a = sum(series.get(m, 0.0) for m in recent) / len(recent)
    b = sum(series.get(m, 0.0) for m in base) / len(base)
    return {"delta": round(a - b, 5), "ratio": (round(a / b, 3) if b else None)}


def _windows(months: list[str]) -> tuple[list[str], list[str], list[str]]:
    """`(최근 3달, 직전 3달, 전년 같은 3달)`.

    **이 셋을 나눠 두는 것이 이 모듈의 핵심이다.** 최근 3달 대 직전 3달은 계절을 잰다 —
    실측으로 그 순위표가 냉면·콩국수·감자전(여름) 상승, 감자탕·곱창전골·육개장(겨울)
    하락으로 채워졌다. 유행이 아니라 달력이다.

    전년 같은 3달과 견주면 계절이 상쇄된다. 남는 것이 유행이다.
    """
    if len(months) < SWING * 2:
        return [], [], []
    recent = months[-SWING:]
    prior = months[-SWING * 2:-SWING]
    yoy = months[-SWING - 12:-12] if len(months) >= SWING + 12 else []
    return recent, prior, yoy


def _build(months_n: int, extra: tuple[str, ...]) -> dict:
    """리뷰를 한 번 훑어 월별 집계를 만든다(캐시된다 — 키워드 스캔이 1초대다)."""
    reviews = svc.all_reviews()
    cat_of = {s["store_id"]: s.get("cat2") for s in svc.all_stores()}

    dates = [r.get("date") for r in reviews if r.get("date")]
    if not dates:
        return {"months": [], "partial_month": None, "total": {},
                "by_category": [], "by_keyword": [], "reviews": 0}
    months, partial = _complete_months(max(dates)[:10], months_n)
    keep = set(months)
    recent, prior, yoy = _windows(months)

    total: collections.Counter = collections.Counter()
    by_cat: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_kw: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    scope_of = _keywords(cat_of)
    for k in extra:
        if k:
            scope_of.setdefault(k, "미분류")
    kws = list(scope_of)

    for r in reviews:
        mo = _month(r.get("date"))
        if mo not in keep:
            continue
        total[mo] += 1
        by_cat[cat_of.get(r.get("store_id")) or "미분류"][mo] += 1
        body = r.get("content") or ""
        if not body:
            continue
        for k in kws:
            if k in body:
                by_kw[k][mo] += 1

    def series(counter: collections.Counter, denom: collections.Counter) -> dict:
        return {m: (counter[m] / denom[m] if denom[m] else 0.0) for m in months}

    cats = []
    for name, counter in by_cat.items():
        share = series(counter, total)
        cats.append({"category": name, "n": sum(counter.values()),
                     "count": {m: counter[m] for m in months},
                     "share": {m: round(share[m], 5) for m in months},
                     "season_3m": _compare(share, recent, prior),
                     "yoy_3m": _compare(share, recent, yoy)})
    cats.sort(key=lambda r: -(r["yoy_3m"]["delta"] if r["yoy_3m"]["delta"]
                              is not None else -1))

    #: 업종별 월평균 리뷰 수 — 분모가 흔들리는 업종을 가려내는 값이다.
    span = [m for m in (recent + yoy) if m] or months
    thin_scope = {c: (sum(by_cat[c][m] for m in span) / len(span)) < MIN_SCOPE_MONTH
                  for c in by_cat}

    keywords = []
    for k, counter in by_kw.items():
        n = sum(counter.values())
        if n < 20:                      # 창 전체에서 20번도 안 나오면 추이가 없다
            continue
        scope = scope_of.get(k) or "미분류"
        share = series(counter, by_cat[scope])          # **업종 안에서** 정규화
        overall = series(counter, total)                # 전체 대비도 참고로 준다
        keywords.append({
            "keyword": k, "scope": scope, "n": n,
            "thin": bool(thin_scope.get(scope, True)),
            "count": {m: counter[m] for m in months},
            "share": {m: round(share[m], 5) for m in months},
            "share_overall": {m: round(overall[m], 5) for m in months},
            "season_3m": _compare(share, recent, prior),
            "yoy_3m": _compare(share, recent, yoy),
        })
    keywords.sort(key=lambda r: -(r["yoy_3m"]["delta"] if r["yoy_3m"]["delta"]
                                  is not None else -1))

    # 출처는 **자료에서 센다.** 화면에 "카카오+네이버" 라고 손으로 적었다가 틀렸다 —
    # 실제로는 33,300건 전부 카카오였다. 플랫폼이 늘면 여기 숫자가 따라 바뀐다.
    platforms: collections.Counter = collections.Counter(
        (r.get("source_platform") or r.get("source") or "unknown") for r in reviews)
    all_dates = sorted(d[:10] for d in dates)

    return {
        "months": months,
        "partial_month": partial,
        "windows": {"recent": recent, "prior": prior, "year_ago": yoy},
        "reviews": sum(total.values()),
        "source": {
            "label": "가게 리뷰 크롤(역삼동)",
            "platforms": dict(platforms),
            "reviews_total": len(reviews),
            "date_range": [all_dates[0], all_dates[-1]],
            "scope": "역삼동 · 월별 (하루 20건대라 일별은 톱니가 된다)",
        },
        "total": {m: total[m] for m in months},
        "min_scope_month": MIN_SCOPE_MONTH,
        "by_category": cats,
        "by_keyword": keywords,
    }


def local(months: int = MONTHS, extra_keywords: tuple[str, ...] = ()) -> dict:
    """역삼동 추이 — 우리가 수집한 리뷰 기준. 데이터가 바뀌면 다시 만든다."""
    svc.load()
    key = f"{months}|{','.join(extra_keywords)}"
    with _lock:
        hit = _cache.get(key)
        stamp = svc.data_stamp()
        if isinstance(hit, tuple) and hit[0] == stamp:
            return hit[1]
    built = _build(months, extra_keywords)
    with _lock:
        _cache[key] = (stamp, built)
    return built


def movers(months: int = MONTHS, top: int = 15) -> dict:
    """올라온 것과 내려간 것 — **유행(전년 대비)과 계절(직전 대비)을 나눠서** 준다.

    한 목록으로 주면 반드시 잘못 읽힌다. 실측으로 직전 3달 대비 상승 상위는 냉면·
    콩국수·감자전이었는데 그건 8월이라서 그런 것이고 유행이 아니다.
    """
    h = local(months)
    # **얇은 업종은 순위에서 뺀다.** 값은 `by_keyword` 에 그대로 있으니 화면에서
    # 흐리게 그리면 된다 — 지우는 것이 아니라 헤드라인에 안 올리는 것이다.
    kw = [r for r in h["by_keyword"] if not r["thin"]]
    fad = [r for r in kw if r["yoy_3m"]["delta"] is not None]
    season = sorted((r for r in kw if r["season_3m"]["delta"] is not None),
                    key=lambda r: -r["season_3m"]["delta"])
    return {"months": h["months"], "partial_month": h["partial_month"],
            "windows": h["windows"],
            # 유행 — 계절이 상쇄된 뒤 남은 움직임
            "rising": fad[:top],
            "falling": list(reversed(fad[-top:])) if len(fad) > top else [],
            # 계절 — 지금 철에 뜨는 것. 추천에 쓰면 이쪽이 더 쓸모 있다
            "in_season": season[:top],
            "out_of_season": list(reversed(season[-top:])) if len(season) > top else [],
            "by_category": h["by_category"]}


def store_inflow(months: int = 6, min_n: int = MIN_INFLOW_N, top: int = 30) -> dict:
    """가게별 리뷰 증가율 — **표본이 되는 가게만**.

    `trend.py` 의 `inflow` 와 같은 식(`log((최근+1)/(직전+1))`)이지만, 여기서는
    창 안 리뷰가 `min_n` 건 미만인 가게를 **아예 뺀다.** 실측으로 가게당 창 안 리뷰가
    중앙값 2건이라, 안 빼면 순위표가 2건 대 0건짜리 잡음으로 채워진다.
    """
    svc.load()
    reviews = svc.all_reviews()
    dates = [r.get("date") for r in reviews if r.get("date")]
    if not dates:
        return {"window_days": months * 30, "stores": [], "eligible": 0}
    last = dt.date.fromisoformat(max(dates)[:10])
    a0 = (last - dt.timedelta(days=months * 30)).isoformat()
    b0 = (last - dt.timedelta(days=months * 60)).isoformat()

    recent: collections.Counter = collections.Counter()
    prior: collections.Counter = collections.Counter()
    for r in reviews:
        d, sid = (r.get("date") or "")[:10], r.get("store_id")
        if not d or not sid:
            continue
        if d >= a0:
            recent[sid] += 1
        elif d >= b0:
            prior[sid] += 1

    out = []
    for sid, n in recent.items():
        if n < min_n:
            continue
        s = svc.get_store(sid) or {}
        out.append({"store_id": sid, "name": s.get("name"), "cat2": s.get("cat2"),
                    "recent": n, "prior": prior.get(sid, 0),
                    "inflow": round(math.log((n + 1) / (prior.get(sid, 0) + 1)), 3)})
    out.sort(key=lambda r: -r["inflow"])
    return {"window_days": months * 30, "min_reviews": min_n,
            "eligible": len(out), "stores": out[:top]}


# ────────────────────────────────────────────── 전국 (네이버 검색어 트렌드)
#
# **여기서 네이버를 부르지 않는다.** 수집은 `analysis/fetch_trend_category.py` 가 하고
# (API HUB 이관·키·재시도·캐시가 전부 그쪽에 있다) 이 모듈은 그 산출물만 읽는다.
# 서버는 오프라인 산출물만 본다는 저장소 약속이고, 외부 장애가 관리자 화면을 멈추게
# 해서도 안 된다.
#
# 앵커 키워드를 쓰지 않는다 — 두 가지 이유가 있다(`fetch_trend_category.py` 머리말).
#   ① 쓰는 값이 `log(최근/직전)` 증가율이라 응답별 정규화 상수가 **약분된다**
#   ② 요청에 `맛집` 같은 큰 키워드를 같이 넣으면 그것이 100 을 먹고 정작 볼 키워드가
#      0 으로 눌린다 — 정밀도를 잃는다

#: 업종별 점수. `{"scores": {"<cat>": <로그 증가율>}, "basis": "yoy"|"seq", ...}`
SCORE_FILE = trend.CATEGORY_FILE          # data/analysis/ — 왜 거기인지는 trend.py 주석

#: 업종별 원본 일별 시계열(수집 캐시). 차트를 그리려면 점수가 아니라 이게 필요하다.
SERIES_DIR = svc.DATA.parent / "analysis" / "datalab"


def _monthly(daily: list[dict], keep: set[str]) -> dict[str, float]:
    """일별 `ratio` 를 달 평균으로 접는다 — 역삼동 추이와 **같은 월 축**에 올린다.

    합이 아니라 평균인 이유: 달마다 날 수가 다르고(28~31) 수집 구간의 양 끝이 반쪽
    달일 수 있다. 합으로 접으면 그 차이가 추세로 보인다.
    """
    acc: dict[str, list[float]] = collections.defaultdict(list)
    for row in daily:
        mo = _month(row.get("period"))
        if mo in keep:
            try:
                acc[mo].append(float(row.get("ratio")))
            except (TypeError, ValueError):
                continue
    return {m: round(sum(v) / len(v), 3) for m, v in acc.items() if v}


def _series_stamp() -> tuple:
    """수집 산출물이 바뀌었나. `fetch_trend_category.py` 를 다시 돌리면 mtime 이 움직인다.

    파일을 열지 않고 `stat` 만 본다 — 무효화 검사가 무효화하려는 일보다 비싸면 안 된다.
    """
    try:
        score = SCORE_FILE.stat().st_mtime_ns
    except OSError:
        score = 0
    files = sorted(SERIES_DIR.glob("*.json")) if SERIES_DIR.is_dir() else []
    newest = max((f.stat().st_mtime_ns for f in files), default=0)
    return score, len(files), newest


def national(months: int = MONTHS) -> dict:
    """전국 추이. 산출물이 그대로면 다시 안 읽는다 — `local` 과 같은 캐시 관례다.

    71개 캐시 파일(업종당 800일치)을 매 요청마다 파싱하면 790ms 가 나와
    `http.slow`(한계 500ms)에 걸린다. 수집을 다시 돌리면 mtime 이 움직여 저절로 풀린다.
    """
    svc.load()
    key = f"national|{months}"
    stamp = (_series_stamp(), svc.data_stamp())
    with _lock:
        hit = _cache.get(key)
        if isinstance(hit, tuple) and hit[0] == stamp:
            return hit[1]
    built = _national(months)
    with _lock:
        _cache[key] = (stamp, built)
    return built


def _national(months: int) -> dict:
    """전국 추이 — `analysis/fetch_trend_category.py` 의 산출물을 읽는다.

    파일이 없는 것은 **오류가 아니다.** 네이버 키가 없어도 역삼동 추이(`local`)는
    그대로 돈다 — 두 축은 독립이다.

    ⚠️ **업종 사이의 `ratio` 크기를 비교하지 말 것.** 수집이 업종마다 따로 요청하고
    네이버가 **응답 안에서** 최댓값을 100 으로 맞추므로, `국수` 의 50 과 `국밥` 의 50 은
    다른 절대량이다. 한 업종의 **시간 변화**만 뜻이 있다(그래서 점수도 증가율이다).
    """
    import json
    out: dict = {"available": False, "score_file": str(SCORE_FILE),
                 "series_dir": str(SERIES_DIR), "scores": {}, "series": {},
                 "hint": "네이버 키(NAVER API HUB)를 받아 "
                         "`python -m analysis.fetch_trend_category` 를 돌리면 생깁니다. "
                         "키가 없어도 역삼동 추이는 그대로 동작합니다."}
    try:
        raw = json.loads(SCORE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out

    keep = set(local(months)["months"])
    series: dict[str, dict] = {}
    if SERIES_DIR.is_dir():
        for f in sorted(SERIES_DIR.glob("*.json")):
            try:
                cached = json.loads(f.read_text(encoding="utf-8"))
                res = (cached.get("results") or [{}])[0]
            except (OSError, ValueError, IndexError):
                continue
            name = res.get("title") or f.stem
            got = _monthly(res.get("data") or [], keep)
            if got:
                series[name] = got

    out.update(available=True, scores=raw.get("scores") or {},
               as_of=raw.get("as_of"), basis=raw.get("basis"),
               window_days=raw.get("window_days"), source=raw.get("source"),
               months=sorted(keep), series=series)
    out.pop("hint", None)
    return out

