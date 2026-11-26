# -*- coding: utf-8 -*-
"""관리자 — 트렌드 추이 화면의 데이터. **관측 전용**이다.

추천 점수에 한 톨도 들어가지 않는다. 나중에 트렌드에 가중치를 줄지 판단하려면 먼저
'신호가 있는가' 를 봐야 하는데, 그걸 볼 자리가 없었다. 계산은 전부
`recsys/trend_history.py` 에 있고 여기는 경로만 만든다.

    전국 추이    네이버 검색어 트렌드      `/admin/api/trend/national`
    역삼동 추이   우리가 수집한 리뷰       `/admin/api/trend/movers` · `/history`

**`app/admin.py` 와 파일을 나눈 이유.** 그 파일에는 트렌드 **점수**를 보는 화면이
이미 있다(`/api/trend`, `/api/trend/compare` — 지금 뜨는 가게와 적용 전후 비교).
이쪽은 **추이**를 본다. 같은 주제라 한 파일에 둘 수도 있었지만, 그 파일이 관리자 화면
전체를 담고 있어 이미 크고 두 작업이 동시에 자라는 중이라 나눴다.

인증은 FastAPI 가 아니라 nginx basic auth 가 `/admin` 전체에 걸려 있으므로
(`nginx/default.conf`) 라우터를 나눠도 보호는 그대로다.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from recsys import trend_history as th
from recsys import trend_search as ts

STATIC = Path(__file__).parent / "static"

router = APIRouter(prefix="/admin", tags=["관리자"])


@router.get("/trends", response_class=HTMLResponse, include_in_schema=False)
@router.get("/trends/", response_class=HTMLResponse, include_in_schema=False)
def page() -> str:
    """트렌드 추이 화면. `app/admin.py` 가 `/admin` 을 내보내는 방식과 같다.

    외부 의존이 없다 — 차트는 인라인 SVG 로 그린다. 이 저장소의 관리자 화면 셋
    (`admin.html`·`map.html`·`taste.html`)이 다 그렇게 되어 있고, 관리자 화면이
    CDN 을 타면 망이 막힌 곳에서 통째로 안 뜬다.
    """
    return (STATIC / "trends.html").read_text(encoding="utf-8")


@router.get("/api/trend/movers", summary="트렌드 — 올라온 것과 내려간 것")
def movers(
    months: int = Query(th.MONTHS, ge=8, le=60, description="볼 달 수(완전한 달만)"),
    top: int = Query(15, ge=1, le=50, description="목록 길이"),
):
    """역삼동 리뷰 기준. **유행과 계절을 나눠서** 준다.

    한 목록으로 주면 반드시 잘못 읽힌다. 교란 요인이 세 겹이라 하나씩 벗겨야 신호가
    나온다 — 자세한 근거는 `recsys/trend_history.py` 머리말에 실측과 함께 있다.

    - `rising` · `falling` : **유행.** 전년 같은 3달과 견준 값(`yoy_3m`)이다. 계절이
      상쇄되고, 키워드를 그 메뉴가 속한 업종 리뷰 대비로 재서 업종 구성 이동도 빠진다
    - `in_season` · `out_of_season` : **계절.** 직전 3달과 견준 값(`season_3m`).
      추천에 쓰기로 한다면 유행보다 이쪽이 쓸모 있다("8월에 냉면")
    - `partial_month` : 크롤이 중간에 끊긴 달. 계산에서 **빠져 있다**
    - 표본이 얇은 업종(`thin`)은 순위에서 뺐다. `/history` 에는 값이 그대로 있다
    """
    return th.movers(months=months, top=top)


@router.get("/api/trend/history", summary="트렌드 — 월별 시계열 전체")
def history(
    months: int = Query(th.MONTHS, ge=8, le=60, description="볼 달 수"),
    keyword: str = Query("", description="추가로 추적할 키워드(쉼표로 여러 개). "
                                         "메뉴에 없는 새 유행을 급히 볼 때"),
):
    """업종·키워드별 월별 시계열. 그래프를 그리는 쪽이 쓰는 원자료다.

    `count` 는 건수, `share` 는 비율이다. **순위는 비율로 본다** — 월 총량이 한 해에
    두 배가 돼서(718 -> 1,513건) 건수로 보면 전부 상승으로 나온다.

    키워드의 `share` 는 **그 업종 리뷰 대비**이고 `share_overall` 이 전체 대비다.
    """
    extra = tuple(k.strip() for k in keyword.split(",") if k.strip())
    return th.local(months=months, extra_keywords=extra)


@router.get("/api/trend/stores", summary="트렌드 — 리뷰가 늘고 있는 가게")
def stores(
    months: int = Query(6, ge=1, le=24, description="창 길이(달)"),
    top: int = Query(30, ge=1, le=100, description="목록 길이"),
):
    """가게별 리뷰 증가율(`inflow`). **표본이 되는 가게만** 나온다.

    창 안 리뷰가 가게당 중앙값 **2건**이라(실측), 안 걸러내면 순위표가 2건 대 0건짜리
    잡음으로 채워진다. `min_reviews` 미만은 아예 뺀다 — `eligible` 이 그래서 남은
    가게 수다.
    """
    return th.store_inflow(months=months, top=top)


@router.get("/api/trend/concentration", summary="트렌드 — 노출 집중도(켜기 전 관문)")
def concentration(
    days: int = Query(0, ge=0, le=365, description="최근 며칠. 0 이면 전 기간"),
):
    """**쏠림을 감시하는 자리.** `analysis/TREND.md` 7절이 요구한 셋째 항목이다.

    트렌드는 **전역 값**이라 모두를 같은 방향으로 민다 → 인기 쏠림(rich-get-richer).
    이 수를 안 보고 켜면 쏠림이 생겨도 모른다. 그래서 여기는 관측이 아니라 **관문**이다.

    - `gini` : 0 = 완전 균등, 1 = 한 곳이 다 먹음. **노출 0 인 가게를 분모에 넣고**
      낸다 — 빼면 '한 번도 안 나온 가게' 가 사라져 집중도가 좋아 보인다
    - `never_shown` : 한 번도 안 나온 가게 수. 지니 한 숫자로는 "얇고 넓게" 와
      "몇몇만 자주" 가 안 갈려서 같이 싣는다
    - `top_share` : 상위 10·50·100곳의 노출 점유율. 풀이 1,600곳대라 완전 균등이면
      각각 0.6% · 3% · 6% 다
    - `by_policy` : legacy 대 taste. **합치면 인터리빙이 둘을 섞어 아무것도 안 보인다**
    - `trend_enabled` : 지금 `false` 이므로 이 값은 **끄고 잰 기준선**이다.
      켠 뒤 같은 수를 다시 재서 견주라는 뜻이다

    기록이 없으면 `available: false` 이고 **오류가 아니다**(노출은 카드가 실제로
    나갈 때만 쌓이고 기동 복원에서는 안 쌓인다).
    """
    from recsys import exposure
    return exposure.concentration(days=days)


@router.get("/api/trend/search", summary="트렌드 — 계절·유행·검색량(전국 검색, 절대값 비교 가능)")
def search(
    frm: str = Query("", alias="from", description="시작일 YYYY-MM-DD. 비면 전체"),
    to: str = Query("", description="끝일 YYYY-MM-DD. 비면 전체"),
    unit: str = Query("month", pattern="^(day|week|month)$",
                      description="눈금. 검색은 일별 원자료가 있어 day 가 된다"),
    gate: float = Query(ts.LEVEL_GATE, ge=0.0, le=10.0,
                        description="스테디셀러 문턱(검색 수준 최소치)"),
    top: int = Query(0, ge=0, le=200, description="0 이면 전부"),
    season_from: str = Query("", description="계절을 접을 구간 시작. 비면 전체"),
    season_to: str = Query("", description="계절을 접을 구간 끝"),
    trend_from: str = Query("", description="'요즘' 창 시작. 비면 마지막 180일"),
    trend_to: str = Query("", description="'요즘' 창 끝. 전년 같은 창과 견준다"),
):
    """**`/national` 이 못 하는 것을 한다 — 업종 사이의 절대값 비교.**

    **창이 셋이다.** 계절(`season_*`)·트렌드(`trend_*`)·검색량(`from`/`to`)을 따로
    받는다 — 질문이 다르면 보는 창도 다르다. 검색량을 석 달로 좁힐 때 계절까지 석 달로
    접히면 프로파일이 무너진다. 어떤 창을 썼는지는 `windows` 로 되돌려 준다.

    그 경로의 `ratio` 는 업종끼리 견줄 수 없다(응답마다 최댓값이 100 이고, 실측하면
    71개 파일 **전부** 100.0 이다). 그래서 "스테디셀러 = 변동성과 무관하게 절대값이
    월등한 것" 을 그것으로는 만들 수 없었다. 여기는 수집 때 **같은 앵커를 한 칸 더**
    넣어 정규화 상수를 약분시킨 `trend_level.json` 을 써서 공통 눈금에 올린다.

    한 표가 아니라 **세 질문**으로 갈라 읽는다.

        계절    매년 이맘때 뜨나      `season`(12달로 접은 모양) · `swing` · `repeat`
        트렌드  요즘 뜨나            `growth_rel`
        검색량  애초에 얼마나 검색되나  `level` ← **이것이 절대값이다**

    - `gate` : 절대값 문턱. **미만은 `kind: "관측 부족"`** 이 된다. 변동성이 커도
      분모가 작으면 잡음이라(깐부치킨 진폭 9.34 · 반복 -0.23) 먼저 걸러야 한다
    - `growth_rel` : 전체 중앙값을 뺀 증가율. **원값으로 보면 안 된다** — 71개 중
      64개가 음수이고 중앙값이 -0.194 다(네이버 검색 자체가 한 해에 ~18% 줄었다)
    - `repeat` : 두 해의 월별 모양 상관. **반복돼야** 계절이고, 반복되는 것은 유행
      판정에서 뺀다(안 빼면 떡볶이 반복 +0.89 가 "유행 하락" 으로 들어온다)
    - `points` · `window_days` : 데이터량. 출처(`source`)와 같이 화면에 싣는다
    - `unit="day"` 는 **검색 축에서만** 된다. 리뷰 축은 역삼동 전체가 하루 23건이라
      일별로 자르면 0·1건짜리 톱니가 된다 — `/history` 는 월별만 준다

    ⚠️ `level` 은 '업종명이 얼마나 검색되나' 다. 수요가 아니라 **검색어**의 크기라
    `카페` 6.61 대 `커피전문점` 0.02 처럼 같은 것을 가리키는 말끼리도 크게 다르다.
    """
    return ts.search(frm=frm, to=to, gate=gate, unit=unit, top=top,
                     season_from=season_from, season_to=season_to,
                     trend_from=trend_from, trend_to=trend_to)


@router.get("/api/trend/national", summary="트렌드 — 전국 검색어 추이(네이버)")
def national(
    months: int = Query(th.MONTHS, ge=8, le=60, description="역삼동 추이와 맞출 달 수"),
):
    """네이버 검색어 트렌드. **`analysis/fetch_trend_category.py` 의 산출물을 읽는다.**

    수집은 그 스크립트가 한다(API HUB 이관·키·재시도·캐시가 전부 그쪽에 있다).
    여기서 네이버를 직접 부르지 않는다 — 외부 장애가 관리자 화면을 멈추게 해선 안 된다.
    파일이 없으면 `available: false` 이고 **오류가 아니다**(역삼동 추이는 그대로 돈다).

    - `scores` : 업종별 증가율(`basis` 가 `yoy` 면 전년 대비). 수집이 낸 값 그대로다
    - `series` : 일별 원본을 **달 평균으로 접은 것.** 역삼동 추이와 같은 월 축이라
      나란히 그릴 수 있다
    - ⚠️ **업종 사이의 `ratio` 크기를 비교하지 말 것.** 업종마다 따로 요청하고 네이버가
      응답 안에서 최댓값을 100 으로 맞추므로 `국수` 의 50 과 `국밥` 의 50 은 다른
      절대량이다. 한 업종의 **시간 변화**만 뜻이 있다
    - ⚠️ 이 값은 **전국**이다. 검색어 트렌드 API 에는 지역 파라미터가 아예 없다
      (device·gender·ages 만 있다). 역삼동은 오피스 상권이라 점심 수요가 전국 패턴과
      다를 수 있으니 역삼동 추이와 **대조하는 용도**로 보라
    """
    return th.national(months=months)


@router.get("/api/trend/slot", summary="트렌드 — 슬롯이 실제로 내보낸 카드")
def slot_cards(
    limit: int = Query(100, ge=1, le=1000, description="최근 몇 장"),
    days: int = Query(0, ge=0, description="최근 N일. 0 이면 전 기간"),
):
    """트렌드 슬롯(`policy="trend"`)이 **누구에게 어떤 카드를 내보냈고 어떻게 넘겼나** — 카드 단위.

    `/admin/api/policies` 의 `slot` 은 집계(like 율·구간)만 준다. 실험 초반에는 "실제로 무엇이
    나갔나" 를 눈으로 봐야 한다 — 같은 가게가 반복되는지, 계절 문구가 맞는 가게인지, 응답이
    붙는지. 그래서 노출 로그에서 `trend` 줄만 골라 선호 로그와 `(사용자·세션·카드)` 로 조인한다
    (`policy.outcome_stats` 와 같은 키·같은 규칙 — 첫 노출이 이긴다).

    카드마다 `menu`(계절 항에 들어간 메뉴 키워드)와 `trend_raw`(출처별 원점수)를 붙인다 —
    "왜 이 카드가 트렌드였나" 의 답이다(`recsys/trend.py: explain`). **읽기 전용**이다.
    """
    import datetime as dt

    from app import loader, state, recommendation_sessions as rec_sessions
    from recsys import policy, store as svc, trend

    rated: dict[tuple[str, str, int], tuple[str, str | None]] = {}
    for p in state.read_preferences():
        uid, cid, pref = p.get("user_id"), p.get("card_id"), p.get("prefer")
        if uid is None or cid is None or pref not in ("like", "dislike"):
            continue
        rated[(uid, p.get("session_id") or rec_sessions.legacy_id(uid), int(cid))] = (pref, p.get("at"))

    cut = ""
    if days > 0:
        cut = (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat()

    rows: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    by_store: dict[str, dict] = {}
    for e in state.read_exposures():
        if e.get("policy") != policy.SLOT:
            continue
        uid, cid = e.get("user_id"), e.get("card_id")
        if uid is None or cid is None:
            continue
        if cut and (e.get("at") or "") < cut:
            continue
        key = (uid, e.get("session_id") or rec_sessions.legacy_id(uid), int(cid))
        if key in seen:                       # 재노출은 첫 줄만 (outcome_stats 와 같다)
            continue
        seen.add(key)
        sid = loader.to_store_id(int(cid))
        s = (svc.get_store(sid) or {}) if sid else {}
        ex = trend.explain(sid) if sid else None
        pref = rated.get(key)
        row = {"at": e.get("at"), "user_id": uid, "card_id": int(cid), "store_id": sid,
               "name": s.get("name"), "category": s.get("cat3") or s.get("cat2"),
               "batch": e.get("batch"), "rank": e.get("rank"),
               "prefer": pref[0] if pref else None,
               "answered_at": pref[1] if pref else None,
               "menu": [m["keyword"] for m in (ex or {}).get("menu") or []],
               "trend_raw": (ex or {}).get("raw")}
        rows.append(row)
        agg = by_store.setdefault(sid or f"card:{cid}", {"store_id": sid, "name": s.get("name"),
                                                         "shown": 0, "liked": 0, "disliked": 0})
        agg["shown"] += 1
        if pref:
            agg["liked" if pref[0] == "like" else "disliked"] += 1

    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    liked = sum(1 for r in rows if r["prefer"] == "like")
    disliked = sum(1 for r in rows if r["prefer"] == "dislike")
    return {
        "slot_every": trend.slot_every(),
        "days": days,
        "shown": len(rows), "liked": liked, "disliked": disliked,
        "unrated": len(rows) - liked - disliked,
        "users": len({r["user_id"] for r in rows}),
        "stores": len(by_store),
        "by_store": sorted(by_store.values(), key=lambda a: -a["shown"]),
        "cards": rows[:limit],
    }
