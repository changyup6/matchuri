# -*- coding: utf-8 -*-
"""네이버 데이터랩 검색어 트렌드로 **메뉴 키워드** 트렌드를 굽는다 — 업종이 못 가르는 가게를 가른다.

산출물은 `data/analysis/trend_menu.json` 이다. `fetch_trend_category.py` 의 업종 산출물과
같은 API·같은 창·같은 점수 정의를 쓰고(그 모듈의 함수를 그대로 부른다), 다른 것은
**키워드가 업종명이 아니라 가게 메뉴명**이라는 점 하나다.

## 왜 메뉴인가

데이터랩은 개별 상호를 못 잰다 — 검색량이 0 이라 상대값에서 전부 0 으로 돌아온다
(`recsys/trend.py: _category`). 그래서 업종 71개 값을 가게 1,629곳에 펴는데, 같은
국밥집 20곳이 같은 값을 받으면 트렌드 슬롯의 "상위 10곳" 은 동점이라 뽑을 수가 없다.

가게를 가르는 것은 **그 가게가 무엇을 파나**다. 메뉴명은 풀 1,629곳 전부에 있고
(2026-09-18 실측), 서로 다른 이름이 14,902개, 그중 상위 1,000개가 가게의 76% 에
하나 이상 걸린다(2,000개면 83%). 같은 국밥집이라도 냉면을 파는 집은 7월에 올라온다.

    상위 K개    걸리는 가게    데이터랩 호출
       200        60%            40
     1,000        76%           200
     2,000        83%           400

월 한도 50,000건에 견주면 무시할 양이다. 기본 K 는 1,000 이다.

## 점수 넷 — 하나로 합치지 않는다

업종 산출물의 기본 비교 창은 `yoy`(전년동기) 였다. 그건 **계절을 지운** 값이다 —
냉면이 7월에 8.6배 오르는 건 작년 7월에도 올랐으니 yoy 에서는 0 근처가 된다. 남는 건
"작년 이맘때보다 더 뜨나" 라는 유행 성분뿐이다. 계절과 유행은 다른 신호라 따로 굽고,
무게는 `recsys/trend.py` 가 정한다(손이 아니라 백테스트·슬롯 like 율로).

| 키 | 정의 | 잡는 것 |
|---|---|---|
| `yoy` | log(최근 180일 중앙값 / 작년 같은 180일 중앙값) | 유행 — 작년보다 더 뜨나 |
| `seq` | log(최근 180일 / 직전 180일) | 최근 반년 방향 (계절 포함) |
| `season` | log(작년 **다음 4주** 중앙값 / 작년 **지난 4주** 중앙값) | 계절 선행 — 이제 오르는 철인가 |
| `in_season` | log(작년 다음 4주 중앙값 / 작년 1년 중앙값) | 지금이 그 메뉴의 철인가 (수준) |

`season` 이 이 스크립트를 만든 이유다. 계절은 해마다 반복된다는 근거가 있고
(`TREND-EVIDENCE.md` 2.3 — 두 해 모양 상관 0.7 이상이 67개 중 36개, 정점 달 일치가
우연의 2.5배), 반복되면 **작년 이 시점의 다음 4주**가 올해 다음 4주의 예측값이다.
딥러닝이 아니라 계절 나이브(seasonal naive)다 — 시계열 68~1,000개 규모에서는 이게
기준선이고, 더 복잡한 모델은 이걸 홀드아웃에서 이길 때만 들인다.

작년 정렬은 365 가 아니라 **364일**(52주)이다. 검색량은 요일 주기가 커서(주말에 뛰는
메뉴, 평일 점심에 뛰는 메뉴) 요일을 맞춰야 4주 중앙값이 흔들리지 않는다. 업종 yoy 가
365 를 쓰는 것과 어긋나지만, 그쪽은 180일 창이라 하루 차이가 묻히고 여기는 28일 창이다.

## 메뉴명 정규화

`"카페 라떼"` 와 `"카페라떼"` 는 같은 검색어여야 한다. 괄호·수량·단위·기호를 지우고
공백을 없앤다. 검색어로 뜻이 없는 말(`공기밥`·`추가`·`세트`·`콜라`)은 뺀다 — 검색량은
있어도 그 가게의 트렌드가 아니다. 규칙은 `normalize`·`STOPWORDS` 에 있고 `--dry` 로
호출 없이 상위 목록을 눈으로 확인한다.

## 서버는 이 파일을 어떻게 쓰나

`store_keywords` 가 가게 → 키워드 목록이고 `keywords` 가 키워드 → 점수다. 가게 점수는
서버(`recsys/trend.py`)가 그 가게 키워드들의 점수를 모아 만든다 — 어떻게 모을지
(최댓값·상위 3 평균)는 백테스트가 정할 일이라 여기서 굳히지 않는다.

캐시 `data/analysis/datalab_menu/` 는 커밋하지 않는다(1,000개 × 28KB). 업종 캐시 71개는
`/admin/trends` 가 그림을 그리느라 이미지에 실렸지만, 메뉴는 화면이 없고 점수만 쓰므로
스칼라만 담은 산출물을 커밋한다 — `trend_level.json` 과 같은 규칙이다.

사용:
    python analysis/fetch_trend_menu.py --dry          # 호출 없이 키워드 목록·커버리지만
    python analysis/fetch_trend_menu.py --limit 3      # 키 확인용, 3개만
    python analysis/fetch_trend_menu.py                # 상위 1,000개 (캐시 있으면 재사용)
    python analysis/fetch_trend_menu.py --top 2000     # 더 넓게
    python analysis/fetch_trend_menu.py --refresh      # 전부 다시 받는다
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch_trend_category as ftc  # noqa: E402  (키·호출·캐시·창 정의를 그대로 쓴다)

ROOT = ftc.ROOT
MENUS = ROOT / "data" / "kakao" / "menus.jsonl"
POOL = ROOT / "data" / "kakao" / "serving" / "pool_ids.json"
#: 응답 캐시. **gitignore** — 1,000개면 28MB 다. 산출물(아래)만 커밋한다.
CACHE = ROOT / "data" / "analysis" / "datalab_menu"
#: 산출물. `data/analysis` 는 커밋되고 Dockerfile 이 이미지에 굽는 폴더다(`trend_level.json` 과 같다).
OUT = ROOT / "data" / "analysis" / "trend_menu.json"

TOP_K = 1000
#: 계절 창·작년 정렬은 업종 산출물과 같아야 한다 — `fetch_trend_category.SEASON_DAYS` 등.
SEASON_DAYS = ftc.SEASON_DAYS
YEAR_ALIGNED = ftc.YEAR_ALIGNED

#: 검색어로 뜻이 없는 메뉴명. 트렌드가 아니라 반찬·옵션·기성품이다.
STOPWORDS = {
    "공기밥", "공깃밥", "밥", "추가", "세트", "사이드", "음료", "주류", "소주", "맥주", "생맥주",
    "콜라", "사이다", "제로콜라", "환타", "생수", "물", "탄산수", "토닉워터", "스프라이트",
    "와인", "하이볼", "막걸리", "청하", "소맥", "얼음", "테이크아웃", "포장", "배달",
    "런치", "디너", "코스", "오마카세", "단품", "정식", "특", "대", "중", "소",
}
#: 지우는 것: 괄호 안, 대괄호 안, 수량·단위, 한글·영문 이외.
_PAREN = re.compile(r"\(.*?\)|\[.*?\]|（.*?）")
_QTY = re.compile(r"\d+(\.\d+)?\s*(인분|인|개|장|줄|판|병|잔|캔|g|kg|ml|l|oz|pcs|p|호|근|마리|조각|피스|ea|cm|inch)?", re.I)
_KEEP = re.compile(r"[^가-힣a-zA-Z]+")


def normalize(name: str | None) -> str | None:
    """메뉴명 → 검색어. 뜻이 없거나 너무 짧으면 None."""
    s = _PAREN.sub(" ", str(name or ""))
    s = _QTY.sub(" ", s)
    s = _KEEP.sub("", s)          # 공백까지 지운다 — "카페 라떼" == "카페라떼"
    if len(s) < 2 or len(s) > 12 or s in STOPWORDS:
        return None
    return s


def pool_ids() -> set[str] | None:
    try:
        raw = json.loads(POOL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ids = raw if isinstance(raw, list) else raw.get("ids")
    return set(ids) if ids else None


def keyword_stores() -> dict[str, set[str]]:
    """키워드 → 그 메뉴를 파는 가게 집합. 추천 풀 안의 가게만 센다(풀 파일이 없으면 전부)."""
    pool = pool_ids()
    out: dict[str, set[str]] = defaultdict(set)
    with MENUS.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            sid = str(d.get("store_id") or "")
            if pool is not None and sid not in pool:
                continue
            kw = normalize(d.get("menu_name"))
            if kw:
                out[kw].add(sid)
    return out


def top_keywords(kw_stores: dict[str, set[str]], k: int) -> list[str]:
    """가게 수가 많은 순. 같으면 이름순(재현 가능하게)."""
    return [kw for kw, _ in sorted(kw_stores.items(),
                                   key=lambda t: (-len(t[1]), t[0]))[:k]]


seasonal = ftc.seasonal


def _cell(v: float | None) -> str:
    return "   —  " if v is None else f"{v:+.3f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=TOP_K, help=f"상위 몇 개 메뉴 키워드 (기본 {TOP_K})")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만 호출 (키 확인용)")
    ap.add_argument("--dry", action="store_true", help="호출 없이 키워드 목록과 커버리지만")
    ap.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받는다")
    ap.add_argument("--legacy", action="store_true", help="개발자센터 옛 키 (2027-06-30 종료)")
    args = ap.parse_args()

    kw_stores = keyword_stores()
    n_stores = len({s for ss in kw_stores.values() for s in ss})
    kws = top_keywords(kw_stores, args.top)
    covered = {s for kw in kws for s in kw_stores[kw]}
    print(f"메뉴 키워드 {len(kw_stores):,}개 (가게 {n_stores:,}곳) → 상위 {len(kws)}개가 "
          f"{len(covered):,}곳({len(covered) / max(n_stores, 1):.0%})에 걸린다")
    if args.dry:
        print("상위 40:", ", ".join(f"{k}({len(kw_stores[k])})" for k in kws[:40]))
        print("끝쪽 20:", ", ".join(f"{k}({len(kw_stores[k])})" for k in kws[-20:]))
        return
    if args.limit:
        kws = kws[:args.limit]

    cid, sec = ftc.load_keys(args.legacy)
    end = dt.date.today() - dt.timedelta(days=ftc.LAG_DAYS)
    start = end - dt.timedelta(days=ftc.FETCH_BACK_DAYS - 1)

    def back(n: int) -> str:
        return (end - dt.timedelta(days=n)).isoformat()

    recent = (back(ftc.WINDOW_DAYS - 1), end.isoformat())
    windows = {"seq": (back(2 * ftc.WINDOW_DAYS - 1), back(ftc.WINDOW_DAYS)),
               "yoy": (back(ftc.YEAR + ftc.WINDOW_DAYS - 1), back(ftc.YEAR))}
    print(f"받는 구간 {start} ~ {end} · 최근 창 {recent[0]} ~ {recent[1]} · "
          f"계절 창 작년 {(end - dt.timedelta(days=YEAR_ALIGNED)).isoformat()} 다음 {SEASON_DAYS}일\n")

    scores: dict[str, dict] = {}
    n_calls = 0
    for i, kw in enumerate(kws, 1):
        path = CACHE / (re.sub(r"[^\w가-힣]+", "_", kw) + ".json")
        fresh = args.refresh or not path.exists()
        raw = ftc.cached(kw, args.refresh,
                         lambda k=kw: ftc.fetch(k, cid, sec, start.isoformat(),
                                                end.isoformat(), args.legacy),
                         CACHE)
        n_calls += int(fresh)
        try:
            data = raw["results"][0]["data"]
        except (KeyError, IndexError, TypeError):
            data = []
        g = {k: ftc.growth(raw, recent, w) for k, w in windows.items()}
        season, level = seasonal(data, end)
        # `density` — 전 구간 중앙값(응답 안에서 최댓값이 100). 검색량이 거의 없는 키워드는
        # 0 이 대부분이고 며칠만 튀어서 중앙값이 0 근처다. 그런 키워드의 season ±2 는 신호가
        # 아니라 스파이크라, 서버가 이 값으로 거른다(`recsys/trend.py: MIN_DENSITY`).
        density = statistics.median(d["ratio"] for d in data) if data else None
        scores[kw] = {"yoy": None if g["yoy"] is None else round(g["yoy"], 4),
                      "seq": None if g["seq"] is None else round(g["seq"], 4),
                      "season": None if season is None else round(season, 4),
                      "in_season": None if level is None else round(level, 4),
                      "density": None if density is None else round(density, 3),
                      "stores": len(kw_stores[kw]),
                      "days": len(data)}
        print(f"  [{i}/{len(kws)}] {kw:14s} 가게 {len(kw_stores[kw]):4d}  "
              f"yoy {_cell(g['yoy'])}  seq {_cell(g['seq'])}  "
              f"season {_cell(season)}  in_season {_cell(level)}")

    store_keywords: dict[str, list[str]] = defaultdict(list)
    for kw in kws:
        for sid in kw_stores[kw]:
            store_keywords[sid].append(kw)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "keywords": scores,
        "store_keywords": {sid: sorted(v) for sid, v in sorted(store_keywords.items())},
        "as_of": end.isoformat(),
        "window_days": ftc.WINDOW_DAYS,
        "season_days": SEASON_DAYS,
        "year_aligned_days": YEAR_ALIGNED,
        "top_k": args.top,
        "source": "naver_api_hub_search_trend",
        "note": "키워드별 yoy(유행)·seq(직전)·season(계절 선행)·in_season(철 수준). "
                "가게 점수는 서버가 store_keywords 로 모아 만든다(recsys/trend.py).",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 요약 — 신호가 실제로 가게를 가르는지
    valid = {k: v for k, v in scores.items() if v["season"] is not None}
    print(f"\n{OUT}")
    print(f"  키워드 {len(scores)}개 기록, 새 호출 {n_calls}건, season 이 있는 키워드 {len(valid)}개, "
          f"키워드가 붙은 가게 {len(store_keywords):,}곳")
    for key in ("season", "in_season", "yoy"):
        rows = sorted(((v[key], k) for k, v in scores.items() if v[key] is not None), reverse=True)
        if len(rows) > 3:
            vals = [r[0] for r in rows]
            print(f"  {key:9s} 표준편차 {statistics.pstdev(vals):.3f}  "
                  f"오르는: {', '.join(f'{k} {v:+.2f}' for v, k in rows[:6])}")
            print(f"  {'':9s}                 내리는: {', '.join(f'{k} {v:+.2f}' for v, k in rows[-6:])}")
    # 같은 업종 안에서 가게가 갈리는가 — 업종 값은 같고 메뉴 값은 다르니 표준편차가 0 보다 커야 한다
    try:
        cat_of = {}
        with ftc.STORES.open(encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                cat_of[str(d.get("id") or d.get("store_id"))] = d.get("cat3") or d.get("cat2")
        by_cat: dict[str, list[float]] = defaultdict(list)
        for sid, kws_ in store_keywords.items():
            vals = [scores[k]["season"] for k in kws_ if scores[k]["season"] is not None]
            if vals:
                by_cat[cat_of.get(sid, "?")].append(max(vals))
        spread = [(statistics.pstdev(v), c, len(v)) for c, v in by_cat.items() if len(v) >= 5]
        spread.sort(reverse=True)
        print("  업종 안 가게별 season(max) 퍼짐 — 큰 쪽:",
              ", ".join(f"{c} sd {s:.2f} (n={n})" for s, c, n in spread[:5]))
        print("                                     작은 쪽:",
              ", ".join(f"{c} sd {s:.2f} (n={n})" for s, c, n in spread[-3:]))
    except OSError:
        pass


if __name__ == "__main__":
    main()
