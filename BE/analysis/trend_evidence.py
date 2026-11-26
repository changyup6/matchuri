# -*- coding: utf-8 -*-
"""트렌드가 필요한가 — `analysis/TREND-EVIDENCE.md` 의 수치를 다시 만든다.

    cd BE && python -m analysis.trend_evidence

읽기 전용이다. 아무것도 쓰지 않는다 — 화면에 표를 찍을 뿐이다.

## 왜 스크립트로 남기나

근거 자료의 숫자가 문서에만 있으면 데이터가 갱신됐을 때 **문서가 조용히 거짓이 된다.**
크롤을 다시 돌린 뒤 이것을 돌려 보면 결론이 그대로인지 바로 안다.

## 네 가지를 잰다

    1  낡음      누적 리뷰 순위 vs 최근 180일 순위       우리 리뷰 (표본 두껍다)
    2a 실패      키워드 월 점유율의 최대/최소            우리 리뷰 (표본 얇다 — 잡음)
    2b 진폭      업종 검색지수의 최대/최소               전국 검색 (두껍다)
    2c 반복성    두 해의 월별 모양이 일치하나            전국 검색 (이게 핵심 증거다)

2a 를 남겨 둔 이유: **그 측정이 왜 못 쓰는지**가 이 자료의 일부다. 지우면 다음 사람이
같은 것을 다시 만들어 11배짜리 가짜 계절성을 믿게 된다.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

#: 네이버 데이터랩 수집 캐시(`analysis/fetch_trend_category.py` 가 만든다).
CACHE = ROOT / "data" / "analysis" / "datalab"

#: 최근 창(일). `recsys/trend.py: WINDOW_DAYS` 와 같은 값으로 둔다.
WINDOW_DAYS = 180

#: 한 달로 인정할 최소 관측 일수. 수집 구간의 양 끝은 반쪽 달이라 빼야 한다.
MIN_DAYS_IN_MONTH = 20

#: 순위 상관을 낼 때의 최소 리뷰 수. 1~2건짜리가 순위를 채우면 상관이 의미를 잃는다.
MIN_REVIEWS = 5


def head(n: int, title: str) -> None:
    print(f"\n{'─' * 72}\n{n}. {title}\n")


# ────────────────────────────────────────────── 1. 낡음
def staleness(reviews, stores) -> None:
    head(1, "낡음 — 누적 점수가 '지금'을 반영하나 (우리 리뷰)")
    last = max((r.get("date") or "")[:10] for r in reviews if r.get("date"))
    cut = (dt.date.fromisoformat(last) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    year = (dt.date.fromisoformat(last) - dt.timedelta(days=365)).isoformat()
    print(f"   최신 리뷰 {last} · 최근 창 {cut} 이후")

    total: collections.Counter = collections.Counter()
    recent: collections.Counter = collections.Counter()
    newest: dict[str, str] = {}
    for r in reviews:
        sid, d = r.get("store_id"), (r.get("date") or "")[:10]
        if not sid:
            continue
        total[sid] += 1
        if d:
            newest[sid] = max(newest.get(sid, ""), d)
            if d >= cut:
                recent[sid] += 1

    pool = [s for s in total if total[s] > 0]
    top = sorted(pool, key=lambda s: -total[s])[:100]
    dead = [s for s in top if recent.get(s, 0) == 0]
    stale = [s for s in pool if newest.get(s, "") < year]
    print(f"   리뷰 있는 가게 {len(pool):,}곳")
    print(f"   누적 상위 100곳 중 최근 {WINDOW_DAYS}일 리뷰 0건: {len(dead)}곳")
    print(f"   마지막 리뷰가 1년 넘은 가게: {len(stale):,}곳 ({len(stale) * 100 // len(pool)}%)")

    common = [s for s in pool if total[s] >= MIN_REVIEWS]
    ra = {s: i for i, s in enumerate(sorted(common, key=lambda s: -total[s]))}
    rb = {s: i for i, s in enumerate(sorted(common, key=lambda s: -recent.get(s, 0)))}
    n = len(common)
    d2 = sum((ra[s] - rb[s]) ** 2 for s in common)
    rho = 1 - 6 * d2 / (n * (n * n - 1))
    print(f"   누적 순위 vs 최근 {WINDOW_DAYS}일 순위 스피어만 상관: {rho:.3f}  (n={n:,})")

    flip = sorted(common, key=lambda s: rb[s] - ra[s])
    print(f"\n   {'가게':<26}{'누적':>6}{'최근':>6}{'누적순위':>9}{'최근순위':>9}")
    for s in flip[-3:] + flip[:3]:
        name = (stores.get(s, {}).get("name") or s)[:24]
        print(f"   {name:<26}{total[s]:>6}{recent.get(s, 0):>6}{ra[s] + 1:>9}{rb[s] + 1:>9}")


# ────────────────────────────────────────────── 2a. 못 쓰는 측정
def keyword_season_is_noise() -> None:
    head("2a", "우리 리뷰로 메뉴 계절성 재기 — **못 쓴다**(잡음이다)")
    from recsys import trend_history as th

    h = th.local()
    ms = h["months"]
    rows = []
    for r in h["by_keyword"]:
        if r["n"] < 40 or r["thin"]:
            continue
        bym: dict[str, list[float]] = collections.defaultdict(list)
        for m in ms:
            bym[m[5:]].append(r["share"][m])
        avg = {k: sum(v) / len(v) for k, v in bym.items()}
        hi = max(avg.items(), key=lambda kv: kv[1])
        lo = min(avg.items(), key=lambda kv: kv[1])
        if lo[1] <= 0:
            continue
        # 인접한 달인가 — 계절이면 이웃 달이 비슷해야 한다.
        gap = min(abs(int(hi[0]) - int(lo[0])), 12 - abs(int(hi[0]) - int(lo[0])))
        rows.append((hi[1] / lo[1], r["keyword"], r["scope"], hi, lo, gap, r["n"]))
    rows.sort(reverse=True)
    print(f"   {'키워드':<12}{'업종':<7}{'언급':>5}{'최대':>11}{'최소':>11}{'배수':>7}{'달 간격':>8}")
    for ratio, k, sc, hi, lo, gap, n in rows[:6]:
        print(f"   {k:<12}{sc:<7}{n:>5}  {hi[0]}월 {hi[1] * 100:>5.2f}%"
              f"  {lo[0]}월 {lo[1] * 100:>5.2f}%{ratio:>7.1f}x{gap:>8}")
    near = [r for r in rows if r[5] <= 1]
    print(f"\n   최대·최소가 **인접한 달**인 경우: {len(near)}/{len(rows)}개")
    print("   → 계절이면 이웃 달이 비슷해야 한다. 표본이 얇아(키워드당 월 1~6건)")
    print("     작은 수의 비가 폭발한 것이다. 이 표는 쓰면 안 된다.")


# ────────────────────────────────────────────── 전국 검색 공통
def _monthly(data: list[dict]) -> dict[str, float]:
    bym: dict[str, list[float]] = collections.defaultdict(list)
    for d in data:
        p = d.get("period") or ""
        if len(p) >= 7:
            try:
                bym[p[:7]].append(float(d.get("ratio")))
            except (TypeError, ValueError):
                continue
    return {k: sum(v) / len(v) for k, v in bym.items() if len(v) >= MIN_DAYS_IN_MONTH}


def _series() -> list[tuple[str, dict[str, float], int]]:
    out = []
    if not CACHE.is_dir():
        return out
    for f in sorted(CACHE.glob("*.json")):
        try:
            res = json.loads(f.read_text(encoding="utf-8"))["results"][0]
        except (OSError, ValueError, KeyError, IndexError):
            continue
        data = res.get("data") or []
        out.append((res.get("title") or f.stem, _monthly(data), len(data)))
    return out


# ────────────────────────────────────────────── 2b. 진폭
def season_amplitude(series) -> None:
    head("2b", "계절 진폭 — 업종 검색지수의 최대/최소 (전국 검색, 표본 두껍다)")
    rows = []
    for name, by, npts in series:
        per: dict[str, list[float]] = collections.defaultdict(list)
        for k, v in by.items():
            per[k[5:]].append(v)
        avg = {k: sum(v) / len(v) for k, v in per.items()}
        if len(avg) < 12:
            continue
        hi = max(avg.items(), key=lambda kv: kv[1])
        lo = min(avg.items(), key=lambda kv: kv[1])
        if lo[1] <= 0:
            continue
        rows.append((hi[1] / lo[1], name, hi, lo))
    rows.sort(reverse=True)
    print(f"   업종당 관측 {series[0][2]} 포인트 · {len(rows)}개 업종")
    print(f"\n   {'업종':<14}{'최성수기':>13}{'최비수기':>13}{'배수':>8}")
    for ratio, name, hi, lo in rows[:8]:
        print(f"   {name:<14}{hi[0]}월 {hi[1]:>7.1f}{lo[0]}월 {lo[1]:>7.1f}{ratio:>7.2f}x")
    for t in (1.5, 2.0):
        print(f"\n   {t}배 이상 흔들리는 업종: {sum(1 for r in rows if r[0] >= t)}/{len(rows)}개")


# ────────────────────────────────────────────── 2c. 반복성 (핵심)
def season_repeats(series) -> None:
    head("2c", "계절이 잡음이 아니라는 증거 — 두 해에 같은 모양이 반복되나")
    print("   각 해의 평균으로 나눠 **수준 차이를 지우고 모양만** 비교한다.")
    rows = []
    for name, by, _ in series:
        pairs = [(by[f"2025-{m:02d}"], by[f"2026-{m:02d}"]) for m in range(4, 9)
                 if f"2025-{m:02d}" in by and f"2026-{m:02d}" in by]
        if len(pairs) < 5:
            continue
        a = [x for x, _ in pairs]
        b = [y for _, y in pairs]
        ma, mb = statistics.mean(a), statistics.mean(b)
        if ma <= 0 or mb <= 0:
            continue
        na = [x / ma for x in a]
        nb = [y / mb for y in b]
        try:
            r = statistics.correlation(na, nb)
        except statistics.StatisticsError:
            continue
        rows.append((r, name, max(range(5), key=lambda i: na[i]) + 4,
                     max(range(5), key=lambda i: nb[i]) + 4))
    if not rows:
        print("   비교할 구간이 없다(수집이 1년 미만이다).")
        return
    rows.sort(reverse=True)
    n = len(rows)
    print(f"\n   {'업종':<14}{'모양 상관':>10}{'25 정점':>9}{'26 정점':>9}")
    for r, name, p25, p26 in rows[:8]:
        print(f"   {name:<14}{r:>10.2f}{p25:>8}월{p26:>8}월")
    print()
    for t in (0.9, 0.7, 0.5):
        k = sum(1 for x in rows if x[0] >= t)
        print(f"   모양 상관 {t:.1f} 이상: {k}/{n}개 ({k * 100 // n}%)")
    same = sum(1 for x in rows if x[2] == x[3])
    print(f"\n   두 해의 정점 달이 같은 업종: {same}/{n}개 ({same * 100 // n}%)")
    print(f"   무작위라면 기대값: {n // 5}개 (20%)")


# ────────────────────────────────────────────── 2d. 지역 업종 트렌드는 왜 안 쓰나
def local_category_is_noise(series, stores) -> None:
    head("2d", "역삼동 리뷰로 **업종** 계절 재기 — 표본과 반대로 움직인다(잡음이다)")
    from recsys import trend_history as th

    h = th.local()
    ms = h["months"]
    n_store: collections.Counter = collections.Counter(
        (s.get("cat2") or "").strip() for s in stores.values())
    rows = []
    for r in h["by_category"]:
        per: dict[str, list[float]] = collections.defaultdict(list)
        for m in ms:
            v = r["share"].get(m)
            if v is not None:
                per[m[5:]].append(v)
        if len(per) < 12:
            continue
        avg = {k: sum(v) / len(v) for k, v in per.items()}
        lo = min(avg.values())
        if lo <= 0:
            continue
        rows.append((r["category"], n_store[r["category"]], max(avg.values()) / lo))

    big = [a for _, n, a in rows if n >= 200]
    small = [a for _, n, a in rows if n < 100]
    print(f"   업종 {len(rows)}개 · 진폭 = 12달로 접은 점유율의 최대/최소")
    if big:
        print(f"   가게 200곳 이상 업종 {len(big)}개: 진폭 중앙값 {statistics.median(big):.2f}x")
    if small:
        print(f"   가게 100곳 미만 업종 {len(small)}개: 진폭 중앙값 {statistics.median(small):.2f}x")
    print("   → 진폭이 표본 크기와 **반대로** 움직이면 계절이 아니라 분모가 작아 튄 것이다.")

    # 전국 검색과 모양이 맞나 — 같은 업종을 같은 달로 맞춰 자기 평균으로 나눈 뒤 상관.
    nat = {name: by for name, by, _ in series}
    cors = []
    for r in h["by_category"]:
        by = nat.get(r["category"])
        if not by:
            continue
        common = [m for m in ms if m in by and r["share"].get(m)]
        if len(common) < 12:
            continue
        a = [r["share"][m] for m in common]
        b = [by[m] for m in common]
        ma, mb = statistics.mean(a), statistics.mean(b)
        if ma <= 0 or mb <= 0:
            continue
        try:
            cors.append((statistics.correlation([x / ma for x in a], [y / mb for y in b]),
                         r["category"], n_store[r["category"]]))
        except statistics.StatisticsError:
            continue
    if cors:
        cors.sort(reverse=True)
        print(f"\n   같은 업종의 역삼동 리뷰 점유율 대 전국 검색, 월별 모양 상관 ({len(cors)}개):")
        print(f"   중앙값 {statistics.median(c for c, _, _ in cors):+.2f}"
              f" · 0.5 이상 {sum(1 for c, _, _ in cors if c >= 0.5)}개")
        for c, name, n in cors[:3] + cors[-3:]:
            print(f"     {name:<12}{n:>5}곳  {c:+.2f}")
        print("   → 사실상 무관하다. 지역 업종 트렌드는 화면에서 '대조용' 으로만 둔다.")


# ────────────────────────────────────────────── 3b. 검색 축 — 수준은 따로 받아야 했다
def search_axis_needs_level() -> None:
    head("3b", "전국 검색 — 응답별 정규화 때문에 **수준**은 앵커 없이는 비교가 안 된다")
    import math
    files = sorted(CACHE.glob("*.json")) if CACHE.is_dir() else []
    mx, growth = [], []
    for f in files:
        try:
            res = json.loads(f.read_text(encoding="utf-8"))["results"][0]
        except (OSError, ValueError, KeyError, IndexError):
            continue
        daily = {d["period"]: float(d["ratio"]) for d in res.get("data") or []}
        if not daily:
            continue
        mx.append(max(daily.values()))
        last = dt.date.fromisoformat(max(daily))

        def med(back: int) -> float:
            b = last - dt.timedelta(days=back)
            a = b - dt.timedelta(days=WINDOW_DAYS - 1)
            v = [x for d, x in daily.items() if a.isoformat() <= d <= b.isoformat()]
            return statistics.median(v) if v else 0.0

        now, ago = med(0), med(365)
        if now > 0 and ago > 0:
            growth.append(math.log(now / ago))
    if not mx:
        print("   캐시가 없다.")
        return
    print(f"   캐시 {len(mx)}개 중 최댓값이 정확히 100.0 인 것: "
          f"{sum(1 for m in mx if abs(m - 100) < 0.01)}개")
    print("   → 업종마다 따로 요청해서 응답 안에서 정규화된다. `국수` 의 50 과 `국밥` 의")
    print("     50 은 다른 절대량이다. 증가율은 상수가 약분돼 괜찮지만 **수준은 안 된다.**")
    if growth:
        neg = sum(1 for g in growth if g < 0)
        print(f"\n   전년 같은 {WINDOW_DAYS}일 대비 증가율 {len(growth)}개: 음수 {neg}개 · "
              f"중앙값 {statistics.median(growth):+.3f}")
        print("   → 업종이 다 같이 죽은 게 아니라 네이버 검색 자체가 줄었다(TREND.md 5.4).")
        print("     화면은 이 중앙값을 뺀 값으로 순위를 낸다(`recsys/trend_search.py`).")

    level = CACHE.parent / "trend_level.json"
    try:
        lv = json.loads(level.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"\n   {level} 이 없다 — `--level` 로 앵커를 같이 받아야 수준이 생긴다.")
        return
    L = lv.get("levels") or {}
    top = sorted(L.items(), key=lambda kv: -kv[1])
    print(f"\n   앵커 `{lv.get('anchor')}` 대비 수준 {len(L)}개 (같은 요청에 앵커를 넣어 상수를 약분)")
    print("   큰 쪽:", ", ".join(f"{k} {v:.2f}" for k, v in top[:5]))
    print("   작은 쪽:", ", ".join(f"{k} {v:.3f}" for k, v in top[-4:]))
    if "카페" in L and "커피전문점" in L:
        print(f"   ⚠️ 카페 {L['카페']:.2f} 대 커피전문점 {L['커피전문점']:.3f} — 같은 것을 가리키는데")
        print("      사람들이 쓰는 말만 다르다. 이 값은 수요가 아니라 **업종명의 검색량**이다.")


def main() -> None:
    from recsys import store as svc

    svc.load()
    reviews = svc.all_reviews()
    stores = {s["store_id"]: s for s in svc.all_stores()}
    print("트렌드 근거 자료 — analysis/TREND-EVIDENCE.md 의 수치")
    print(f"리뷰 {len(reviews):,}건 · 가게 {len(stores):,}곳")

    staleness(reviews, stores)
    keyword_season_is_noise()
    series = _series()
    if series:
        season_amplitude(series)
        season_repeats(series)
        local_category_is_noise(series, stores)
        search_axis_needs_level()
    else:
        print(f"\n{CACHE} 가 비어 있다 — 전국 검색 절을 건너뛴다.")
        print("`python -m analysis.fetch_trend_category` 로 수집한다.")


if __name__ == "__main__":
    main()
