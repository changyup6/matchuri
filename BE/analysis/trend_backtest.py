# -*- coding: utf-8 -*-
"""트렌드 출처의 **시점 정합(as-of) 백테스트** — 과거 시점의 신호가 그 뒤 4주를 맞히나.

`recsys/trend.py` 의 출처 무게(season 1.0 · fad 0.5 · inflow 0.5)는 손으로 둔 첫 추정값이다.
이 스크립트는 그 무게에 **첫 실측**을 준다. 라벨은 like 가 아니라 **리뷰 유입**이다 —
선호 로그(`state/preferences.jsonl`)는 운영 서버에만 있어 로컬에 없고, 리뷰는 33,300건이
날짜와 함께 있다. "그 가게에 사람이 더 갔나" 의 대리 지표로 리뷰 유입은 like 보다 오히려
행동에 가깝다(like 는 카드를 넘긴 것뿐이다). 다만 표본이 얇다(가게당 월 0.5건) — 그래서
가게 하나가 아니라 **월마다 1,629곳을 순위로 견주고** 그것을 여러 달 평균한다.

## 시점 정합이 전부다

2026-09 의 트렌드로 2026-03 의 리뷰를 맞히면 누수다 — 그 가게가 뜬 이유가 그 리뷰일 수
있다. 그래서 시점 t 마다 **t 까지의 자료만으로** 신호를 만들고, 라벨은 **(t, t+28일]** 의
리뷰다. 검색 캐시는 하루 단위 806일이라 창을 과거로 옮기기만 하면 된다(`ftc.growth` ·
`ftc.seasonal` 의 `end` 인자). 리뷰 증가율도 t 까지의 리뷰만 센다.

    신호(t)  =  season(t) · fad(t) · inflow(t)     ← trend.py 와 같은 정의, 같은 수축
    라벨(t)  =  리뷰 수 (t, t+28] − 리뷰 수 (t−28, t]    → 올랐나(>0) / 내렸나(<0), 0 은 뺀다

## 세 물음

1. 출처 하나하나가 라벨과 **같은 방향**인가 — 월별 스피어만 상관과 AUC 의 평균
2. 셋을 합칠 때 **무게는 얼마**인가 — 달 안에서 z 를 낸 뒤 로지스틱(IRLS). 월 하나를
   빼고 맞춰 그 달로 재는 leave-one-month-out 으로 과적합을 본다
3. 검색 신호가 리뷰 신호와 **다른 정보**인가 — 둘의 상관. 같은 것을 두 번 재면 무게가 의미 없다

## 한계 — 적어 두지 않으면 잊는다

* 라벨이 리뷰라서, 리뷰 증가율(`inflow`)은 **자기 자신의 관성**을 재는 셈이다(지난 180일에
  늘었으면 다음 4주도 늘 확률). 그 유리함을 감안해 읽어야 한다. 검색 신호에는 그 유리함이 없다.
* `fad`(yoy) 는 545일 전 자료가 필요해 2026-01 부터만 잴 수 있다(8개월). `season` 은 392일이라
  2025-08 부터(13개월). 표는 둘 다 낸다.
* 전국 검색량이 역삼동 점심 상권과 같이 움직이는지가 이 검증의 실질이다 — 맞으면 계절
  신호는 유효하고, 안 맞으면 슬롯 like 율이 최종 판단이다.

사용:
    cd BE && python -m analysis.trend_backtest            # 표를 찍고 data/analysis/trend_backtest.json 에 남긴다
    cd BE && python -m analysis.trend_backtest --months 6 # 최근 6개월만
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import glob
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_trend_category as ftc  # noqa: E402
import fetch_trend_menu as ftm  # noqa: E402

ROOT = ftc.ROOT
OUT = ROOT / "data" / "analysis" / "trend_backtest.json"

#: `recsys/trend.py` 와 같아야 한다 — 서버가 실제로 쓰는 정의를 재는 것이다.
WINDOW = 180
SMOOTH = 2.0
MIN_DENSITY = 5.0
MENU_TOP_K = 3
POOL_K = 1.0
#: 라벨 창. `ftc.SEASON_DAYS` 와 같은 4주 — 계절 선행이 "다음 4주" 를 보기 때문이다.
HORIZON = ftc.SEASON_DAYS
FEATURES = ("season", "fad", "inflow")


# ────────────────────────────────────────────────────────── 자료
def _series(folder: Path) -> dict[str, list[dict]]:
    """캐시 폴더 → `이름 → 일별 [{period, ratio}]`. 이름은 응답의 title 이다(파일명이 아니라)."""
    out: dict[str, list[dict]] = {}
    for f in glob.glob(str(folder / "*.json")):
        try:
            raw = json.loads(Path(f).read_text(encoding="utf-8"))
            res = raw["results"][0]
            out[res["title"]] = res["data"]
        except (OSError, ValueError, KeyError, IndexError):
            continue
    return out


def _stores() -> tuple[dict[str, tuple[str | None, str | None]], list[str]]:
    cat: dict[str, tuple[str | None, str | None]] = {}
    with ftc.STORES.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            cat[str(d["store_id"])] = (d.get("cat3"), d.get("cat2"))
    pool = ftm.pool_ids() or set(cat)
    return cat, sorted(pool)


def _reviews() -> dict[str, list[str]]:
    days: dict[str, list[str]] = defaultdict(list)
    with (ROOT / "data" / "kakao" / "reviews.jsonl").open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            day = d.get("date")
            if isinstance(day, str) and len(day) >= 10:
                days[str(d["store_id"])].append(day[:10])
    return {sid: sorted(v) for sid, v in days.items()}


def _count(days: list[str], lo: str, hi: str) -> int:
    """`lo <= day <= hi` 인 리뷰 수 (이분 탐색)."""
    return bisect.bisect_right(days, hi) - bisect.bisect_left(days, lo)


# ────────────────────────────────────────────────────────── 시점 t 의 신호
def _upto(data: list[dict], end: dt.date) -> list[dict]:
    e = end.isoformat()
    return [d for d in data if d["period"] <= e]


def _growth(data: list[dict], recent: tuple[str, str], prior: tuple[str, str]) -> float | None:
    a = ftc._level(data, *recent)
    b = ftc._level(data, *prior)
    if not a or not b or a <= 0 or b <= 0:
        return None
    return math.log(a / b)


def signals_at(t: dt.date, cats: dict[str, list[dict]], kws: dict[str, list[dict]],
               kw_stores: dict[str, set[str]], store_cat, pool: list[str],
               reviews: dict[str, list[str]]) -> dict[str, dict[str, float | None]]:
    """가게별 (season, fad, inflow) — 전부 **t 까지의 자료만** 쓴다."""
    def back(n: int) -> str:
        return (t - dt.timedelta(days=n)).isoformat()
    recent = (back(WINDOW - 1), t.isoformat())
    prior = (back(2 * WINDOW - 1), back(WINDOW))
    yoy = (back(ftc.YEAR + WINDOW - 1), back(ftc.YEAR))

    # 업종·키워드 단위 값 (한 번씩)
    cat_val: dict[str, dict[str, float | None]] = {}
    for name, data in cats.items():
        d = _upto(data, t)
        cat_val[name] = {"season": ftc.seasonal(d, t)[0], "fad": _growth(d, recent, yoy)}
    kw_val: dict[str, dict[str, float | None]] = {}
    for name, data in kws.items():
        d = _upto(data, t)
        dens = statistics.median(x["ratio"] for x in d) if d else 0.0
        if dens < MIN_DENSITY:
            continue
        kw_val[name] = {"season": ftc.seasonal(d, t)[0], "fad": _growth(d, recent, yoy)}
    store_kws: dict[str, list[str]] = defaultdict(list)
    for kw, sids in kw_stores.items():
        if kw in kw_val:
            for sid in sids:
                store_kws[sid].append(kw)

    out: dict[str, dict[str, float | None]] = {}
    for sid in pool:
        c3, c2 = store_cat.get(sid, (None, None))
        row: dict[str, float | None] = {}
        for key in ("season", "fad"):
            cat = None
            for c in (c3, c2):
                v = (cat_val.get(c) or {}).get(key)
                if v is not None:
                    cat = v
                    break
            vals = sorted((kw_val[k][key] for k in store_kws.get(sid, ())
                           if kw_val[k][key] is not None), reverse=True)[:MENU_TOP_K]
            menu = sum(vals) / len(vals) if vals else None
            n = len(vals)
            if menu is None and cat is None:
                row[key] = None
            elif cat is None:
                row[key] = menu
            elif menu is None:
                row[key] = cat
            else:
                row[key] = (n * menu + POOL_K * cat) / (n + POOL_K)
        days = reviews.get(sid, [])
        r = _count(days, recent[0], recent[1])
        p = _count(days, prior[0], prior[1])
        row["inflow"] = math.log((r + SMOOTH) / (p + SMOOTH)) if days else None
        row["_r180"] = float(r)          # 라벨 B 의 자기 기준선용(신호가 아니다)
        out[sid] = row
    return out


def labels_at(t: dt.date, pool: list[str], reviews: dict[str, list[str]],
              store_cat) -> tuple[dict[str, int], dict[str, int], dict[str, float]]:
    """(다음 4주 − 지난 4주, 다음 4주 리뷰 수, 업종별 점유율 변화).

    라벨 A `delta` 는 **평균 회귀 함정**이 있다 — 지난 4주에 몰린 가게는 다음 4주가 줄기 마련이라,
    최근에 늘어난 가게(inflow 큼)가 자동으로 음수가 된다. 그래서 라벨 B(자기 180일 평균 대비
    초과: `next > r180 × 28/180`)와 라벨 C(업종 점유율 변화, 가게가 아니라 업종 70개 단위)를
    같이 낸다. 셋 다 같은 방향이면 믿고, 갈리면 어느 함정에 걸렸는지 보라.
    """
    nxt = ((t + dt.timedelta(days=1)).isoformat(), (t + dt.timedelta(days=HORIZON)).isoformat())
    prv = ((t - dt.timedelta(days=HORIZON - 1)).isoformat(), t.isoformat())
    delta, nextn = {}, {}
    cat_next: dict[str, int] = defaultdict(int)
    cat_prev: dict[str, int] = defaultdict(int)
    for sid in pool:
        days = reviews.get(sid, [])
        n, p = _count(days, *nxt), _count(days, *prv)
        delta[sid] = n - p
        nextn[sid] = n
        c = store_cat.get(sid, (None, None))
        key = c[0] or c[1]
        if key:
            cat_next[key] += n
            cat_prev[key] += p
    tn, tp = sum(cat_next.values()) or 1, sum(cat_prev.values()) or 1
    share_delta = {c: cat_next[c] / tn - cat_prev.get(c, 0) / tp
                   for c in set(cat_next) | set(cat_prev) if cat_next[c] + cat_prev.get(c, 0) >= 5}
    return delta, nextn, share_delta


# ────────────────────────────────────────────────────────── 통계
def _rank(x: np.ndarray) -> np.ndarray:
    """평균 순위(동점은 평균). scipy 없이."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=np.float64)
    sx = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sx[j + 1] == sx[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan")
    return float(np.corrcoef(_rank(x), _rank(y))[0, 1])


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Mann-Whitney AUC. 양성·음성이 하나라도 없으면 nan."""
    pos = scores[labels == 1]
    neg = scores[labels == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    r = _rank(np.concatenate([pos, neg]))
    return float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def zscore(v: np.ndarray) -> np.ndarray:
    """서버와 같은 규칙 — 아는 값 안에서 z, 모르는 값(nan)은 0 = 평균."""
    known = ~np.isnan(v)
    z = np.zeros(len(v))
    if known.sum() >= 2 and v[known].std() > 0:
        z[known] = (v[known] - v[known].mean()) / v[known].std()
    return z


def logistic_fit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 25) -> np.ndarray:
    """IRLS 로지스틱(절편 포함). 작은 L2 는 완전 분리를 막는 용도다."""
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    reg = np.eye(Xb.shape[1]) * l2
    reg[0, 0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ w))
        W = p * (1 - p)
        H = Xb.T @ (Xb * W[:, None]) + reg
        g = Xb.T @ (y - p) - reg @ w
        w = w + np.linalg.solve(H, g)
    return w


# ────────────────────────────────────────────────────────── 실행
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=0, help="최근 N개월만 (기본 전부)")
    args = ap.parse_args()

    cats = _series(ftc.CACHE)
    kws = _series(ftm.CACHE)
    kw_stores = ftm.keyword_stores()
    store_cat, pool = _stores()
    reviews = _reviews()
    latest = max(d[-1] for d in reviews.values())
    first_search = min(d[0]["period"] for d in cats.values())
    print(f"업종 시계열 {len(cats)}개 · 메뉴 키워드 {len(kws)}개 · 풀 {len(pool)}곳 · "
          f"리뷰 최신 {latest} · 검색 캐시 시작 {first_search}")

    # 시점: 매달 1일. 라벨 창(t+28)이 리뷰 최신일 안에 있어야 하고, season 은 392일·fad 는 545일 전 자료가 필요하다.
    last_ok = dt.date.fromisoformat(latest) - dt.timedelta(days=HORIZON)
    start = dt.date.fromisoformat(first_search)
    ts = []
    t = dt.date(start.year, start.month, 1)
    while t <= last_ok:
        if (t - start).days >= ftc.YEAR_ALIGNED + HORIZON:
            ts.append(t)
        t = dt.date(t.year + (t.month == 12), t.month % 12 + 1, 1)
    if args.months:
        ts = ts[-args.months:]
    fad_ok = {t for t in ts if (t - start).days >= ftc.YEAR + WINDOW}
    print(f"시점 {len(ts)}개: {ts[0]} ~ {ts[-1]} (fad 는 {len(fad_ok)}개부터)\n")

    per_month = []
    rows_X, rows_y, rows_m = [], [], []
    for t in ts:
        sig = signals_at(t, cats, kws, kw_stores, store_cat, pool, reviews)
        delta_d, next_d, share_d = labels_at(t, pool, reviews, store_cat)
        F = {f: np.array([np.nan if sig[s][f] is None else sig[s][f] for s in pool]) for f in FEATURES}
        r180 = np.array([sig[s]["_r180"] for s in pool])
        delta = np.array([delta_d[s] for s in pool], dtype=np.float64)
        nextn = np.array([next_d[s] for s in pool], dtype=np.float64)
        # 라벨 A: 다음 4주 − 지난 4주 부호 (0 은 뺀다)   — 평균 회귀 함정 있음
        moved = delta != 0
        yA = (delta > 0).astype(int)
        # 라벨 B: 다음 4주가 **자기 180일 평균 속도**를 넘나. 지난 1년에 리뷰가 있던 가게만
        active = r180 > 0
        yB = (nextn > r180 * HORIZON / WINDOW).astype(int)
        Z = {f: zscore(F[f]) for f in FEATURES}
        rec = {"t": t.isoformat(), "n": int(len(pool)), "moved": int(moved.sum()),
               "up": int(yA[moved].sum()), "active": int(active.sum()), "upB": int(yB[active].sum()),
               "known": {f: int((~np.isnan(F[f])).sum()) for f in FEATURES},
               "spearman": {}, "auc": {}, "aucB": {}, "cat_spearman": {}}
        # 라벨 C: 업종 점유율 변화 — 가게가 아니라 업종 단위. 검색 신호의 본래 해상도다.
        cat_season = {}
        for c in share_d:
            v = [sig[s]["season"] for s in pool
                 if (store_cat.get(s, (None, None))[0] or store_cat.get(s, (None, None))[1]) == c
                 and sig[s]["season"] is not None]
            if v:
                cat_season[c] = statistics.median(v)
        common = [c for c in share_d if c in cat_season]
        for f in FEATURES:
            if f == "fad" and t not in fad_ok:
                rec["spearman"][f] = rec["auc"][f] = rec["aucB"][f] = None
                continue
            rec["spearman"][f] = round(spearman(Z[f], delta), 4)
            rec["auc"][f] = round(auc(Z[f][moved], yA[moved]), 4)
            rec["aucB"][f] = round(auc(Z[f][active], yB[active]), 4)
        rec["cat_spearman"]["season"] = (round(spearman(np.array([cat_season[c] for c in common]),
                                                        np.array([share_d[c] for c in common])), 4)
                                         if len(common) >= 5 else None)
        rec["cat_n"] = len(common)
        rec["corr_season_inflow"] = round(float(np.corrcoef(Z["season"], Z["inflow"])[0, 1]), 3)
        per_month.append(rec)
        if t in fad_ok:
            rows_X.append(np.column_stack([Z[f][active] for f in FEATURES]))
            rows_y.append(yB[active])
            rows_m.append(np.full(int(active.sum()), len(rows_X) - 1))
        cells = []
        for f in FEATURES:
            a, b = rec["auc"][f], rec["aucB"][f]
            cells.append(f"{f} A {'  —  ' if a is None else f'{a:.3f}'} B {'  —  ' if b is None else f'{b:.3f}'}")
        cs = rec["cat_spearman"]["season"]
        print(f"  {t}  A:{rec['moved']:4d}곳 B:{rec['active']:4d}곳  " + "  ".join(cells)
              + f"  업종 ρ {'  —  ' if cs is None else f'{cs:+.3f}'}(n={rec['cat_n']})")

    def _mean(key: str, f: str) -> float | None:
        vals = [r[key][f] for r in per_month if r[key].get(f) is not None]
        return round(statistics.mean(vals), 4) if vals else None

    print("\n월 평균 (AUC 0.5 = 동전)")
    print("  라벨 A = 다음 4주 − 지난 4주 부호 (평균 회귀 함정) · 라벨 B = 자기 180일 속도 초과 · "
          "업종 ρ = 업종 계절 대 업종 리뷰 점유율 변화")
    summary = {"spearman": {}, "auc": {}, "aucB": {}, "cat_spearman": {}}
    for f in FEATURES:
        summary["spearman"][f] = _mean("spearman", f)
        summary["auc"][f] = _mean("auc", f)
        summary["aucB"][f] = _mean("aucB", f)
        print(f"  {f:7s} A: ρ {summary['spearman'][f]:+.4f} AUC {summary['auc'][f]:.4f}   "
              f"B: AUC {summary['aucB'][f]:.4f}")
    summary["cat_spearman"]["season"] = _mean("cat_spearman", "season")
    print(f"  업종 단위 season ρ (n≈{statistics.mean(r['cat_n'] for r in per_month):.0f}업종): "
          f"{summary['cat_spearman']['season']:+.4f}")

    # 무게 — 달 안에서 z 를 낸 표본을 모아 로지스틱. leave-one-month-out 으로 과적합을 본다.
    X = np.vstack(rows_X)
    y = np.concatenate(rows_y)
    m = np.concatenate(rows_m)
    w = logistic_fit(X, y)
    coef = {f: round(float(w[i + 1]), 4) for i, f in enumerate(FEATURES)}
    base = abs(coef["season"]) or 1.0
    rel = {f: round(coef[f] / base, 3) for f in FEATURES}
    print(f"\n로지스틱 — 라벨 B (표본 {len(y):,} 가게·월, 양성 {y.mean():.1%})")
    print("  계수      ", coef)
    print("  season=1  ", rel, "   ← 지금 기본값 season 1.0 · fad 0.5 · inflow 0.5")

    def _loo(cols: list[int]) -> float:
        aucs = []
        for k in np.unique(m):
            tr, te = m != k, m == k
            wk = logistic_fit(X[tr][:, cols], y[tr])
            s = X[te][:, cols] @ wk[1:] + wk[0]
            aucs.append(auc(s, y[te]))
        return round(float(np.nanmean(aucs)), 4)

    combos = {"season 만": [0], "fad 만": [1], "inflow 만": [2], "검색 둘(season+fad)": [0, 1],
              "셋 다": [0, 1, 2]}
    loo = {name: _loo(cols) for name, cols in combos.items()}
    # 지금 기본 무게 그대로 (학습 없이)
    default = X @ np.array([1.0, 0.5, 0.5])
    loo["기본 무게 1.0/0.5/0.5 (학습 없음)"] = round(auc(default, y), 4)
    print("\nleave-one-month-out AUC (다른 달로 맞추고 그 달로 잼)")
    for k, v in loo.items():
        print(f"  {k:28s} {v:.4f}")

    # 천장 — 이 라벨을 **가장 잘 맞힐 수 있는** 단순 예측기가 얼마나 맞히나. 신호의 성적은
    # 0.5 가 아니라 이 수에 견줘야 한다. 리뷰가 가게당 월 0.4건이고 다음 4주 0건인 가게가 75% 면
    # 라벨 대부분이 포아송 잡음이라, 천장이 낮으면 "신호가 없다" 가 아니라 "이 라벨로는 못 잰다" 다.
    ceiling = []
    for t in ts:
        nxt_w = ((t + dt.timedelta(days=1)).isoformat(), (t + dt.timedelta(days=HORIZON)).isoformat())
        p1_w = ((t - dt.timedelta(days=HORIZON - 1)).isoformat(), t.isoformat())
        p2_w = ((t - dt.timedelta(days=2 * HORIZON - 1)).isoformat(), (t - dt.timedelta(days=HORIZON)).isoformat())
        r_w = ((t - dt.timedelta(days=WINDOW - 1)).isoformat(), t.isoformat())
        nxt = np.array([_count(reviews.get(s, []), *nxt_w) for s in pool], float)
        p1 = np.array([_count(reviews.get(s, []), *p1_w) for s in pool], float)
        p2 = np.array([_count(reviews.get(s, []), *p2_w) for s in pool], float)
        r180 = np.array([_count(reviews.get(s, []), *r_w) for s in pool], float)
        act = r180 > 0
        yB = (nxt > r180 * HORIZON / WINDOW).astype(int)
        ceiling.append({"t": t.isoformat(),
                        "popularity_rho": round(spearman(p1, nxt), 4),            # 지난 4주 수 → 다음 4주 수
                        "momentum_rho_A": round(spearman(p1 - p2, nxt - p1), 4),   # 지난 변화 → 다음 변화 (라벨 A)
                        "prev_exceed_auc_B": round(auc((p1 > r180 * HORIZON / WINDOW).astype(float)[act], yB[act]), 4),
                        "zero_next_share": round(float((nxt == 0).mean()), 3),
                        "mean_next": round(float(nxt.mean()), 3)})
    ceil_mean = {k: round(statistics.mean(c[k] for c in ceiling), 4)
                 for k in ("popularity_rho", "momentum_rho_A", "prev_exceed_auc_B", "zero_next_share", "mean_next")}
    print("\n천장 (신호 없이 리뷰 수만으로)")
    print(f"  지난 4주 수 → 다음 4주 수  ρ {ceil_mean['popularity_rho']:+.3f}   (인기 지속)")
    print(f"  지난 변화 → 다음 변화     ρ {ceil_mean['momentum_rho_A']:+.3f}   (라벨 A 는 평균 회귀가 지배한다)")
    print(f"  '직전 4주가 자기 속도 초과' → 라벨 B  AUC {ceil_mean['prev_exceed_auc_B']:.3f}   ← 라벨 B 의 실질 천장")
    print(f"  다음 4주 리뷰 0건인 가게 {ceil_mean['zero_next_share']:.0%} · 가게당 평균 {ceil_mean['mean_next']:.2f}건")

    OUT.write_text(json.dumps({
        "as_of": latest, "horizon_days": HORIZON, "months": [r["t"] for r in per_month],
        "ceiling": {"per_month": ceiling, "mean": ceil_mean},
        "per_month": per_month, "summary": summary,
        "logistic": {"coef": coef, "relative_to_season": rel, "n": int(len(y)),
                     "positive_rate": round(float(y.mean()), 4)},
        "loo_auc": loo,
        "labels": {"A": "리뷰 수 (t, t+28] − (t−28, t] 의 부호 (0 은 뺌). 평균 회귀 함정",
                   "B": "다음 4주 리뷰 수 > 자기 최근 180일 속도 × 28/180 (지난 180일 리뷰 있는 가게만). 로지스틱·LOO 는 이 라벨",
                   "C": "업종 단위 — 업종 계절 중앙값 대 업종 리뷰 점유율 변화의 스피어만"},
        "note": "inflow 는 라벨과 같은 자료(리뷰)라 자기 관성의 유리함이 있다. 검색 둘에는 없다.",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{OUT}")


if __name__ == "__main__":
    main()
