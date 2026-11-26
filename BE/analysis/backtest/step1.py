# -*- coding: utf-8 -*-
"""Step 1 실행기 — 메뉴 다중 벡터를 백테스트 계약대로 재고 근거 문서를 쓴다.

    ~/venvs/recsys/bin/python -m analysis.backtest.step1

두 단계로 나눈다.

  1. **N=5 스윕** — 변종(top8 / top8_idf / top20_idf) × 집계(mean/max/lse) ×
     dislike 가중 β 를 전부 N=5 에서만 돌린다. 45 + 대조군 2 개를 N 네 개에 다 돌리면
     표가 읽히지 않고, 제품의 주 구간도 N ≤ 5 다(Step 0).
  2. **결선** — 스윕 1위 콘텐츠 모델, 단일 벡터 대조군, 하이브리드(λ 는 5-fold),
     그리고 `random / popularity / quality / current` 를 N ∈ {3,5,10,20} 에서 전부 잰다.

**정직하게 적을 것 하나.** 변종 선택은 테스트 사용자 위에서 했다(N=5 AUC 1등을 골랐다).
λ 만 사용자 5-fold 로 골랐다. 그래서 결선 표의 1위 값에는 선택 편향이 조금 섞여 있다 —
CI 가 0 을 아슬아슬하게 넘긴 정도라면 그것을 근거로 삼지 않는다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

from analysis.content import menus as menu_content

from . import cache, config, cv, report, run, scorers
from .bootstrap import paired_diff_ci
from .data import load_dataset
from .evaluate import evaluate
from .pool import attach_crawled_quality, build_pool
from .rng import generator
from .scorers import ScoreContext
from .scorers_menu import AGGS, DISLIKE_BETA, LAMBDAS

SWEEP_VARIANTS = ("top8", "top8_idf", "top20_idf")
REFS = {"task_p": "popularity", "task_r": "quality"}
REFERENCE_MODELS = ["random", "popularity", "quality", "current"]

#: 임베딩 자체를 찔러보는 쌍 — (메뉴명 A, 메뉴명 B, 왜 보는가).
#: PLAN 0절이 든 예("곱창전골↔모츠나베, 냉면↔메밀소바는 업종이 달라도 취향은 같다")를
#: 모델이 실제로 그렇게 보는지 확인한다. 가게-가게 MaxSim 이 결국 이 값들의 최댓값이라
#: 여기가 무너지면 위층도 무너진다.
PROBE_PAIRS = [
    ("곱창전골", "모츠나베", "PLAN 0절이 든 교차 업종 예"),
    ("냉면", "메밀소바", "PLAN 0절이 든 교차 업종 예"),
    ("육회", "사시미", "생식 — 한식/일식"),
    ("삼겹살", "야키니쿠동", "구이 — 한식/일식 (`야키니쿠` 는 데이터에 없다)"),
    ("얼큰 샤브샤브", "훠궈 무한리필(평일점심)", "끓는 육수 — 일식/중식"),
    ("초밥", "지라시스시", "같은 음식, 다른 표기"),
    ("짬뽕", "마라탕", "매운 국물 — 중식 안"),
    ("마라탕", "마라탕면", "같은 음식의 표기 변형 (대조)"),
    ("아메리카노", "오메리카노", "한 글자 차이 (대조)"),
    ("메밀소바", "멜론소다", "뜻은 무관, 표기만 닮음 (대조)"),
]

#: 사례로 보여줄 가게 — 업종 경계를 넘는지 보려고 요리 갈래를 흩어 골랐다.
EXAMPLE_STORE_HINTS = [
    ("곱창", ("곱창", "막창")),
    ("냉면", ("냉면",)),
    ("마라탕", ("마라탕", "마라샹궈")),
    ("카페", ("커피", "카페", "coffee")),
    ("스시", ("스시", "초밥")),
]


def _mean(v) -> float:
    v = np.asarray(v, dtype=float)
    return float(np.nanmean(v)) if np.isfinite(v).any() else float("nan")


def _sweep_names() -> list[str]:
    names = [f"menu_{v}_{a}{s}" for v in SWEEP_VARIANTS
             for a in AGGS for s in DISLIKE_BETA]
    return names + ["menu_single_vector", "menu_single_vector_top8_idf"]


# ────────────────────────────────────────────────── 1단계: N=5 스윕
def run_sweep(splits, ctx, n_history: int, ref_result, *, resamples: int,
              seed: int) -> list[dict]:
    rows = []
    for name in _sweep_names():
        res = evaluate(scorers.get(name), splits, ctx, n_history)
        ci = paired_diff_ci(res.p["auc"], ref_result.p["auc"],
                            resamples=resamples, seed=seed)
        rows.append({
            "model": name,
            "auc": _mean(res.p["auc"]), "auc_pm": _mean(res.p["auc_pm"]),
            f"hit@{config.HIT_K}": _mean(res.p[f"hit@{config.HIT_K}"]),
            "r_auc": _mean(res.r_auc),
            "vs_pop_auc": ci,
        })
        print(f"  {name:<34} AUC={rows[-1]['auc']:.4f} "
              f"auc_pm={rows[-1]['auc_pm']:.4f}", flush=True)
    rows.sort(key=lambda r: -r["auc"])
    return rows


def sweep_table(rows: list[dict]) -> str:
    head = ("| # | 모델 | AUC(P) | `popularity` 대비 CI | auc_pm | "
            f"Hit@{config.HIT_K} | AUC(R) |")
    out = [head, "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        ci = r["vs_pop_auc"]
        mark = "**" if ci["significant"] else ""
        out.append(f"| {i} | `{r['model']}` | {r['auc']:.4f} | "
                   f"{mark}{ci['diff']:+.4f}{mark} "
                   f"[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}] | "
                   f"{r['auc_pm']:.4f} | {r[f'hit@{config.HIT_K}']:.4f} | "
                   f"{r['r_auc']:.4f} |")
    return "\n".join(out)


def sweep_pivot(rows: list[dict], variants, key: str = "auc") -> str:
    """집계 × 변종 피벗 (β=0). mean 과 max 를 나란히 놓고 가설을 본다."""
    by = {r["model"]: r for r in rows}
    out = ["| 집계 | " + " | ".join(f"`{v}`" for v in variants) + " |",
           "|---|" + "---|" * len(variants)]
    for agg, (label, _) in AGGS.items():
        cells = [f"{by[f'menu_{v}_{agg}'][key]:.4f}" for v in variants]
        out.append(f"| {label} (`{agg}`) | " + " | ".join(cells) + " |")
    return "\n".join(out)


# ────────────────────────────────────────────────── 2단계: 하이브리드 λ
def hybrid_cv(best_content: str, splits, ctx, ns, *, seed: int):
    """λ 격자를 사용자 5-fold 로 골라 out-of-fold 로 평가한다."""
    candidates = {f"{lam:g}": (lambda lam=lam: scorers.get(
        f"hybrid_pop_{best_content}_l{lam:g}")) for lam in LAMBDAS}
    raw, chosen = {}, {}
    for n in ns:
        res, folds = cv.outer_cv(candidates, splits, ctx, n, metric="auc", seed=seed)
        raw[n] = res
        chosen[str(n)] = folds
        print(f"  hybrid CV  N={n:<3} λ(폴드별)={folds} "
              f"AUC(P)={_mean(res.p['auc']):.4f}", flush=True)
    return raw, chosen


# ────────────────────────────────────────────────── 근거: 사례 설명
def _store_row(ds, sid):
    return ds.stores.get(sid, {})


def explain_users(ds, pool, space, variant, sim, splits, best_model, ctx,
                  *, n_history: int, seed: int, n_users: int = 5,
                  top_k: int = 10) -> str:
    """평가 사용자 몇 명의 like 이력과 추천 상위, 그리고 매칭된 메뉴 쌍."""
    packed = space.packed(variant)
    scorer = scorers.get(best_model)
    scorer.prepare(ctx)
    pick = generator(seed, "step1", "example-users").choice(
        len(splits), size=min(n_users, len(splits)), replace=False)

    out: list[str] = []
    for u in sorted(int(i) for i in pick):
        sp = splits[u]
        hist = sp.signals(n_history, ctx.history_mode)
        likes = [sid for sid, like in hist if like and sid in pool.pos]
        if not likes:
            continue
        out.append(f"**사용자 `{sp.user_id}`** — 이력 N={n_history} 중 like {len(likes)}곳")
        out.append("")
        out.append("| like 한 가게 | cat2 | 대표 메뉴 |")
        out.append("|---|---|---|")
        for sid in likes:
            s = _store_row(ds, sid)
            menus = ", ".join(space.selection(variant, sid)[:5]) or "—"
            out.append(f"| {s.get('name', sid)} | {s.get('cat2') or '—'} | {menus} |")
        out.append("")

        scores = np.asarray(scorer.score(hist, ctx, user_id=sp.user_id), dtype=float)
        seen = np.zeros(len(pool), dtype=bool)
        for sid in sp.seen_stores:
            seen[pool.pos[sid]] = True
        scores = np.where(seen, -np.inf, scores)
        future = {sid for sid, _ in sp.future}
        order = np.argsort(-scores)[:top_k]

        out.append(f"추천 상위 {top_k} (`{best_model}`). '매칭' 은 like 가게 중 가장 "
                   "닮은 곳과 그 둘에서 가장 잘 맞은 메뉴 쌍이다.")
        out.append("")
        out.append("| # | 추천 가게 | cat2 | 점수 | 매칭된 like 가게 | 메뉴 쌍 (코사인) | 정답? |")
        out.append("|---|---|---|---|---|---|---|")
        for rank, j in enumerate(order, 1):
            j = int(j)
            s = _store_row(ds, pool.ids[j])
            best_h, best_v = None, -np.inf
            for sid in likes:
                v = sim[pool.pos[sid], j]
                if np.isfinite(v) and v > best_v:
                    best_h, best_v = sid, float(v)
            if best_h is None:
                pair, hname = "—", "—"
            else:
                a, b, c = menu_content.best_menu_pair(packed, pool.pos[best_h], j)
                pair = f"{a} → {b} ({c:.3f})" if a else "—"
                hname = _store_row(ds, best_h).get("name", best_h)
            hit = "**O**" if pool.ids[j] in future else ""
            out.append(f"| {rank} | {s.get('name', pool.ids[j])} | "
                       f"{s.get('cat2') or '—'} | {scores[j]:.4f} | {hname} | "
                       f"{pair} | {hit} |")
        out.append("")
    return "\n".join(out)


def example_neighbours(ds, pool, space, variant, sim, *, top_k: int = 5) -> tuple[str, list]:
    """요리 갈래별 예시 가게의 최근접 이웃 — 업종 경계를 넘는지 눈으로 본다."""
    packed = space.packed(variant)
    chosen: list[tuple[str, str]] = []
    used: set[str] = set()
    for label, keys in EXAMPLE_STORE_HINTS:
        best = None
        for i, sid in enumerate(pool.ids):
            if sid in used or packed.counts[i] == 0:
                continue
            name = (_store_row(ds, sid).get("name") or "").lower()
            menus = " ".join(space.selection(variant, sid)).lower()
            in_name = any(k.lower() in name for k in keys)
            in_menu = any(k.lower() in menus for k in keys)
            if not (in_name or in_menu):
                continue
            # 가게 **이름**에 있는 쪽을 먼저 고른다. 메뉴에만 있으면 '냉면도 파는
            # 삼겹살집' 처럼 갈래를 대표하지 못하는 가게가 뽑힌다.
            rank = (1 if in_name else 0, float(pool.review_count[i]))
            if best is None or rank > best[0]:
                best = (rank, sid)
        if best:
            used.add(best[1])
            chosen.append((label, best[1]))

    out = ["| 기준 가게 (cat2) | 이웃 | cat2 | sim | 메뉴 쌍 |", "|---|---|---|---|---|"]
    rows_json = []
    for label, sid in chosen:
        i = pool.pos[sid]
        s = _store_row(ds, sid)
        order = np.argsort(-np.nan_to_num(sim[i], nan=-np.inf))
        order = [int(j) for j in order if int(j) != i][:top_k]
        head = (f"**{s.get('name', sid)}** ({s.get('cat2') or '—'}, {label})"
                f"<br><sub>{', '.join(space.selection(variant, sid)[:5])}</sub>")
        entry = {"label": label, "store_id": sid, "name": s.get("name"),
                 "cat2": s.get("cat2"), "neighbours": []}
        for k, j in enumerate(order):
            t = _store_row(ds, pool.ids[j])
            a, b, c = menu_content.best_menu_pair(packed, i, j)
            out.append(f"| {head if k == 0 else ''} | {t.get('name', pool.ids[j])} | "
                       f"{t.get('cat2') or '—'} | {sim[i, j]:.4f} | "
                       f"{a} → {b} ({c:.3f}) |")
            entry["neighbours"].append({"store_id": pool.ids[j], "name": t.get("name"),
                                        "cat2": t.get("cat2"), "sim": float(sim[i, j]),
                                        "menu_a": a, "menu_b": b, "cos": c})
        rows_json.append(entry)
    return "\n".join(out), rows_json


def probe_menu_pairs(space, *, n_random: int = 200_000, seed: int = 0) -> tuple[str, dict]:
    """메뉴명 쌍의 코사인과 **전체 23k 이름 중 몇 등인지**를 잰다.

    코사인 절댓값만 보면 크기를 알 수 없다 — 무작위 두 이름의 코사인이 이미 0.37 쯤
    되기 때문이다. 그래서 순위와 무작위 분포를 같이 적는다.
    """
    mat = np.asarray(space.matrix)
    rng = np.random.default_rng(seed)
    parts = []
    for lo in range(0, n_random, 20_000):          # 청크로 — 20만 쌍을 한 번에 꺼내면 800MB
        k = min(20_000, n_random - lo)
        ia = rng.integers(0, mat.shape[0], size=k)
        ib = rng.integers(0, mat.shape[0], size=k)
        ok = ia != ib
        parts.append(np.einsum("ij,ij->i", mat[ia[ok]], mat[ib[ok]]))
    rnd = np.concatenate(parts)
    base = {"mean": float(rnd.mean()), "p95": float(np.percentile(rnd, 95)),
            "p99": float(np.percentile(rnd, 99)), "n": int(rnd.size)}

    lines = ["| 메뉴 A | 메뉴 B | 코사인 | A 기준 B 의 순위 | A 의 최근접 이웃 | 왜 |",
             "|---|---|---|---|---|---|"]
    rows = []
    for a, b, why in PROBE_PAIRS:
        ra, rb = space.row_of.get(a), space.row_of.get(b)
        if ra is None or rb is None:
            missing = a if ra is None else b
            lines.append(f"| {a} | {b} | — | — | — | 데이터에 `{missing}` 없음 |")
            rows.append({"a": a, "b": b, "why": why, "missing": missing})
            continue
        sims = mat @ mat[ra]
        cos = float(sims[rb])
        rank = int((sims > cos).sum())              # 자기 자신(1.0)이 항상 앞에 있다
        top = int(np.argsort(-sims)[1])
        top_name = space.matrix_name(top)
        lines.append(f"| {a} | {b} | {cos:.3f} | {rank:,} / {mat.shape[0]:,} | "
                     f"{top_name} ({float(sims[top]):.3f}) | {why} |")
        rows.append({"a": a, "b": b, "why": why, "cos": cos, "rank": rank,
                     "top_neighbour": top_name, "top_cos": float(sims[top])})
    lines.append("")
    lines.append(f"무작위 두 메뉴명의 코사인: 평균 {base['mean']:.3f} · "
                 f"95분위 {base['p95']:.3f} · 99분위 {base['p99']:.3f} "
                 f"({base['n']:,}쌍 표본). **0.4 정도는 '닮았다' 가 아니라 바닥값**이다.")
    return "\n".join(lines), {"pairs": rows, "random_baseline": base}


def cross_category(pool, sim: np.ndarray, *, top_k: int = 5) -> dict:
    """이웃 상위 K 중 cat2 가 다른 비율 — '카테고리를 넘는 취향' 의 전역 수치.

    비교 기준은 **무작위 이웃**일 때의 기대값이다: 같은 cat2 가게가 풀에 몇 %인지.
    이 값보다 낮으면 유사도가 업종 안에 갇혀 있다는 뜻이다.
    """
    cats = np.array([c or "?" for c in pool.cat2])
    have = np.isfinite(sim).any(axis=1)
    diff, base, n_used = [], [], 0
    for i in np.flatnonzero(have):
        row = sim[i].copy()
        row[i] = -np.inf
        order = np.argsort(-np.nan_to_num(row, nan=-np.inf))[:top_k]
        diff.append(float(np.mean(cats[order] != cats[i])))
        # 무작위 이웃 기대값: 같은 cat2 가 아닌 가게의 비율(자기 자신 제외)
        same = int((cats[have] == cats[i]).sum()) - 1
        base.append(1.0 - same / max(int(have.sum()) - 1, 1))
        n_used += 1
    return {"top_k": top_k, "stores": n_used,
            "cross_cat2_rate": float(np.mean(diff)) if diff else float("nan"),
            "random_rate": float(np.mean(base)) if base else float("nan")}


def generic_table(space, n: int = menu_content.GENERIC_TOP) -> str:
    rows = space.generic_names(n)
    out = ["| # | 메뉴명 | df (풀 가게 수) | idf |", "|---|---|---|---|"]
    for i, (name, df, idf) in enumerate(rows, 1):
        out.append(f"| {i} | {name} | {df} | {idf:.2f} |")
    return "\n".join(out)


# ────────────────────────────────────────────────── main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.backtest.step1",
        description="PLAN.md 2절 '메뉴 다중 벡터 (Step 1)' 을 백테스트한다.")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES))
    ap.add_argument("--sweep-N", type=int, default=5, help="1단계 스윕을 돌릴 이력 길이")
    ap.add_argument("--out", default="step1-menu")
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--no-cache", action="store_true")
    # Step 1 은 계약 수정 **전에** 돌았다. 이 실행기의 기본은 그래서 `like_dislike`
    # 이고, 바꾸면 step1-menu.md 의 숫자를 재현하지 못한다(Step 1.5 는 step1_5.py 다).
    ap.add_argument("--history", default="like_dislike",
                    choices=list(config.HISTORY_MODES),
                    help="이력 해석. 기본은 Step 1 이 쓴 like_dislike")
    ap.add_argument("--rebuild-md", action="store_true",
                    help="실험을 다시 돌리지 않고 저장된 json 에서 문장·표만 다시 쓴다")
    args = ap.parse_args(argv)
    ns = [int(x) for x in args.N.split(",") if x.strip()]

    if args.rebuild_md:
        return rebuild_md(args.out)

    print("데이터 로드 …", flush=True)
    ds = load_dataset()
    pool = build_pool(ds)
    splits, _ = cache.load_or_build(ds, pool, use_cache=not args.no_cache)
    attach_crawled_quality(pool, ds, {s.user_id for s in splits})
    ctx = ScoreContext(pool=pool, seed=args.seed, history_mode=args.history,
                       extras={"candidates": run.seen_masks(splits, pool),
                               "dataset": ds,
                               "eval_users": {s.user_id for s in splits},
                               "store_names": {s: (ds.stores.get(s, {}).get("name") or "")
                                               for s in pool.ids}})
    print(f"  후보 풀 {len(pool):,}곳 · 평가 사용자 {len(splits)}명", flush=True)

    # ── 메뉴 공간과 유사도 행렬
    from .scorers_menu import menu_space, similarity
    space = menu_space(ctx)
    for v in SWEEP_VARIANTS:
        mat, hit = menu_content.load_or_build_similarity(
            space, menu_content.VARIANTS[v], use_cache=not args.no_cache)
        ctx.extras.setdefault("menu", {}).setdefault("sim", {})[v] = mat
        print(f"  유사도 {v}: {'캐시' if hit else '계산'}", flush=True)

    packed8 = space.packed(menu_content.VARIANTS["top8_idf"])
    no_menu = [pool.ids[i] for i in np.flatnonzero(~packed8.has_menu)]
    no_menu_set = set(no_menu)
    future_no_menu = sum(1 for sp in splits for sid, _ in sp.future if sid in no_menu_set)
    future_total = sum(len(sp.future) for sp in splits)
    hist_no_menu = sum(1 for sp in splits for sid, _ in sp.signals(5, args.history)
                       if sid in no_menu_set)
    hist_total = sum(len(sp.signals(5, args.history)) for sp in splits)

    # ── 1단계: N=5 스윕
    print(f"1단계 — N={args.sweep_N} 스윕 ({len(_sweep_names())}종)", flush=True)
    ref5 = evaluate(scorers.get("popularity"), splits, ctx, args.sweep_N)
    sweep = run_sweep(splits, ctx, args.sweep_N, ref5,
                      resamples=args.resamples, seed=args.seed)
    best_content = next(r["model"] for r in sweep
                        if not r["model"].startswith("menu_single_vector"))
    best_variant = next(v for v in SWEEP_VARIANTS
                        if best_content.startswith(f"menu_{v}_"))
    print(f"  → 콘텐츠 1위: {best_content} (변종 {best_variant})", flush=True)

    # ── 2단계: 결선
    content_finalists = [best_content]
    if "_idf_" not in best_content:
        # 1위가 무가중이면 'IDF 가 도움이 되나' 라는 질문에 CI 로 답할 수 없다.
        # 같은 조건의 IDF 최고치를 한 줄 더 올려 나란히 둔다.
        best_idf = next((r["model"] for r in sweep if "_idf_" in r["model"]
                         and not r["model"].startswith("menu_single_vector")), None)
        if best_idf:
            content_finalists.append(best_idf)
    finalists = REFERENCE_MODELS + content_finalists + [
        "menu_single_vector", "menu_single_vector_top8_idf"]
    hybrid_name = f"hybrid_pop_{best_content}_cv"
    print("2단계 — 결선", flush=True)
    raw: dict[str, dict] = {}
    for name in finalists:
        raw[name] = {}
        for n in ns:
            raw[name][n] = evaluate(scorers.get(name), splits, ctx, n)
            print(f"  {name:<34} N={n:<3} "
                  f"AUC(P)={_mean(raw[name][n].p['auc']):.4f}", flush=True)
    raw[hybrid_name], chosen_lambda = hybrid_cv(best_content, splits, ctx, ns,
                                                seed=args.seed)
    model_names = finalists + [hybrid_name]

    results = run.aggregate(raw, model_names, ns, REFS,
                            resamples=args.resamples, seed=args.seed)
    users_meta = run.summarise_users(splits, raw, model_names, ns,
                                     history_mode=args.history)

    # ── 근거
    sim_best = ctx.extras["menu"]["sim"][best_variant]
    variant_obj = menu_content.VARIANTS[best_variant]
    neigh_md, neigh_json = example_neighbours(ds, pool, space, variant_obj, sim_best)
    probe_md, probe_json = probe_menu_pairs(space, seed=args.seed)
    cross = cross_category(pool, sim_best)
    users_md = explain_users(ds, pool, space, variant_obj, sim_best, splits,
                             best_content, ctx, n_history=args.sweep_N, seed=args.seed)

    blob = {
        "run": args.out,
        "title": "Step 1 — 메뉴 다중 벡터 (MaxSim)",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": run._git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "references": REFS,
        "baseline_model": REFS["task_p"],
        "history_sizes": ns,
        "intro": [
            "`analysis/PLAN.md` 2절 '메뉴 다중 벡터 (Step 1)' 와 5절의 결정을 구현한 것이다.",
            "가게를 메뉴 벡터의 **집합**으로 두고 MaxSim 으로 가게-가게 유사도를 만든 뒤,",
            "사용자의 like 이력 위에서 mean / max / logsumexp 로 접었다.",
            "기준은 과제 P 가 `popularity`, 과제 R 이 `quality` 다(PLAN 5절 1번).",
        ],
        "command": [
            "~/venvs/recsys/bin/python -m analysis.backtest.step1 \\",
            f"    --N {','.join(str(n) for n in ns)} --sweep-N {args.sweep_N} "
            f"--out {args.out}",
        ],
        "config": {
            "bayes_c": config.BAYES_C,
            "hit_k": config.HIT_K, "recall_k": config.RECALL_K, "ndcg_k": config.NDCG_K,
            "bootstrap_resamples": args.resamples,
            "split_cache_fingerprint": cache.fingerprint(),
            "menus_manifest_sha256": space.manifest_sha,
            "lambdas": list(LAMBDAS),
            "sweep_n_history": args.sweep_N,
        },
        "pool": {"size": len(pool), "stores_total": len(ds.stores),
                 "reviews_total": len(ds.reviews)},
        "users": users_meta,
        "menus": {
            "pool_stores_with_menus": int(packed8.has_menu.sum()),
            "pool_stores_without_menus": len(no_menu),
            "unique_menu_names_in_pool": len(space.df),
            "future_positives_without_menus": future_no_menu,
            "future_positives_total": future_total,
            "history_signals_without_menus": hist_no_menu,
            "history_signals_total": hist_total,
            "generic_names": [{"name": n, "df": d, "idf": i}
                              for n, d, i in space.generic_names()],
            "variants": {v: menu_content.VARIANTS[v].description
                         for v in SWEEP_VARIANTS},
        },
        "sweep": {"n_history": args.sweep_N, "rows": sweep,
                  "best_content": best_content, "best_variant": best_variant},
        "hybrid": {"model": hybrid_name, "content": best_content,
                   "lambdas": list(LAMBDAS), "chosen_per_fold": chosen_lambda},
        "examples": {"neighbours": neigh_json, "probe_pairs": probe_json,
                     "cross_category": cross},
        "models": dict(
            [(m, {"description": scorers.get(m).description}) for m in finalists]
            + [(hybrid_name, {"description":
                              f"popularity_z + λ·({best_content})_z, "
                              f"λ 는 사용자 5-fold out-of-fold 선택"})]),
        "results": results,
        "headline": _headline(results, sweep, best_content, hybrid_name, ns,
                              len(no_menu), cross, probe_json),
        "sections_before": [{
            "heading": f"1단계 — N={args.sweep_N} 전수 스윕",
            "body": _sweep_section(sweep, args.sweep_N),
        }],
        "sections": [
            {"heading": "하이브리드의 λ", "body":
                _lambda_section(hybrid_name, chosen_lambda, ns)},
            {"heading": "범용 메뉴와 IDF", "body":
                "stoplist 대신 IDF 가중을 썼다(PLAN 5절 4번). "
                f"`idf = log(S_pool / df)`, S_pool = {len(pool):,}. df 는 그 이름을 "
                "올린 **후보 풀 가게 수**다. 가장 범용적인 "
                f"{menu_content.GENERIC_TOP}개:\n\n" + generic_table(space)},
            {"heading": "사례 — 이 모델은 무엇을 보고 추천했나", "body":
                f"평가 사용자 5명(seed 고정)의 like 이력(N={args.sweep_N})과 "
                f"`{best_content}` 의 추천 상위 10곳이다.\n\n" + users_md},
            {"heading": "사례 — 요리 갈래별 최근접 이웃", "body":
                f"`{best_variant}` 유사도로 본 예시 가게 5곳의 이웃 5곳씩. "
                "업종(cat2)이 다른 이웃이 나오는지가 '카테고리를 넘는 취향' 가설의 "
                "눈으로 보는 증거다.\n\n" + neigh_md + "\n\n"
                f"전역으로도 셌다: 메뉴가 있는 {cross['stores']:,}곳 각각의 최근접 "
                f"{cross['top_k']}곳 중 cat2 가 다른 비율이 "
                f"**{cross['cross_cat2_rate']:.1%}** 다. 이웃을 무작위로 골랐다면 "
                f"{cross['random_rate']:.1%} 였을 것이다 — 유사도는 업종 **안쪽**을 "
                "강하게 선호한다."},
            {"heading": "임베딩 자체를 찔러보기 — 이 공간은 무엇을 닮았다고 하나", "body":
                "가게-가게 MaxSim 은 결국 메뉴명 코사인의 최댓값이다. 그래서 PLAN 0절이 "
                "든 예를 모델이 실제로 그렇게 보는지 직접 쟀다(전체 "
                f"{len(space.row_of):,}개 고유 메뉴명 기준).\n\n" + probe_md},
            {"heading": "배운 것 / 증명하지 못한 것", "body":
                _learnings(results, sweep, best_content, hybrid_name, ns,
                           len(no_menu), len(pool), cross, probe_json)},
        ],
        "notes": _notes(len(no_menu), future_no_menu, future_total,
                        hist_no_menu, hist_total, args.sweep_N),
    }

    out_dir = Path(config.RESULTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.out}.json").write_text(
        json.dumps(blob, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8")
    (out_dir / f"{args.out}.md").write_text(report.to_markdown(blob), encoding="utf-8")
    print(f"기록: {out_dir / (args.out + '.json')}\n"
          f"      {out_dir / (args.out + '.md')}", flush=True)
    return 0


def rebuild_md(out: str) -> int:
    """이미 저장된 json 의 숫자로 해설 문장만 다시 만들고 .md 를 새로 쓴다.

    실험 결과(숫자)는 건드리지 않는다 — 바뀌는 것은 '한눈에' 와 λ·배운 것 절뿐이다.
    글을 고칠 때마다 15분짜리 실험을 다시 돌리지 않기 위한 문이다.
    """
    path = Path(config.RESULTS_DIR) / f"{out}.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    ns = blob["history_sizes"]
    ex = blob["examples"]
    sweep, hyb = blob["sweep"], blob["hybrid"]
    blob["notes"] = _notes(
        blob["menus"]["pool_stores_without_menus"],
        blob["menus"]["future_positives_without_menus"],
        blob["menus"]["future_positives_total"],
        blob["menus"]["history_signals_without_menus"],
        blob["menus"]["history_signals_total"],
        blob["config"]["sweep_n_history"])
    blob["headline"] = _headline(blob["results"], sweep["rows"], sweep["best_content"],
                                 hyb["model"], ns,
                                 blob["menus"]["pool_stores_without_menus"],
                                 ex["cross_category"], ex["probe_pairs"])
    for sec in blob["sections"]:
        if sec["heading"] == "하이브리드의 λ":
            sec["body"] = _lambda_section(hyb["model"], hyb["chosen_per_fold"], ns)
        elif sec["heading"].startswith("배운 것"):
            sec["body"] = _learnings(
                blob["results"], sweep["rows"], sweep["best_content"], hyb["model"],
                ns, blob["menus"]["pool_stores_without_menus"], blob["pool"]["size"],
                ex["cross_category"], ex["probe_pairs"])
    path.write_text(json.dumps(blob, ensure_ascii=False, indent=2, default=float),
                    encoding="utf-8")
    (Path(config.RESULTS_DIR) / f"{out}.md").write_text(report.to_markdown(blob),
                                                        encoding="utf-8")
    print(f"문서만 다시 씀: {path.with_suffix('.md')}", flush=True)
    return 0


def _lambda_section(hybrid_name: str, chosen: dict, ns) -> str:
    lam_line = " · ".join(f"N={n}: {', '.join(chosen[str(n)])}" for n in ns)
    grid = ", ".join(f"{l:g}" for l in LAMBDAS)
    picks = {v for n in ns for v in chosen[str(n)]}
    edge = ""
    if picks == {f"{min(LAMBDAS):g}"}:
        edge = ("\n\n**폴드 전부가 격자의 맨 아래 값 "
                f"λ={min(LAMBDAS):g} 를 골랐다.** 격자 경계에서 멈춘 선택은 "
                "'이게 최적' 이 아니라 '더 작았으면 더 좋았을 수도 있다' 는 뜻이다. "
                "즉 콘텐츠 점수는 인기 점수에 **아주 조금** 섞을 때만 도움이 된다. "
                "격자를 더 아래로 넓히지 않은 것은 이 Step 의 결론(콘텐츠 신호가 약하다)"
                "을 바꾸지 못하기 때문이다.")
    return (f"`{hybrid_name}` 은 λ ∈ {{{grid}}} 를 사용자 5-fold 로 고른 뒤 "
            "**자기가 선택에 참여하지 않은 폴드**에서만 평가한 값이다"
            "(`cv.outer_cv`). 폴드별로 고른 λ:\n\n"
            f"- {lam_line}\n\n"
            "폴드마다 같은 λ 가 나왔다면 선택이 안정적이라는 뜻이고, 흩어졌다면 "
            "λ 가 별로 중요하지 않다는 뜻이다." + edge)


def _sweep_section(sweep: list[dict], n_history: int) -> str:
    body = [
        f"변종 3 × 집계 5 × dislike 가중 3 = 45종에 단일 벡터 대조군 2종을 더해 "
        f"N={n_history} 에서만 먼저 돌렸다. 기준은 `popularity`(과제 P).",
        "",
        sweep_table(sweep),
        "",
        "**집계 × 변종 (β=0, AUC(P))** — '평균이 다봉형 취향을 뭉갠다' 가설을 "
        "정면으로 보는 표다.",
        "",
        sweep_pivot(sweep, SWEEP_VARIANTS, "auc"),
        "",
        "**같은 표를 `auc_pm`(인기 매칭) 으로**",
        "",
        sweep_pivot(sweep, SWEEP_VARIANTS, "auc_pm"),
    ]
    return "\n".join(body)


def _headline(results, sweep, best_content, hybrid_name, ns, n_no_menu,
              cross, probe) -> list[str]:
    n5 = str(5 if 5 in ns else ns[0])
    top = sweep[0]
    pop = results["popularity"][n5]["task_p"]
    best = results[best_content][n5]["task_p"]
    hyb = results[hybrid_name][n5]["task_p"]
    ci_b, ci_h = best["auc"]["vs_baseline"], hyb["auc"]["vs_baseline"]
    single = results["menu_single_vector"][n5]["task_p"]["auc"]["mean"]

    def verdict(ci):
        return ("CI 가 0 을 넘겼다" if ci["significant"]
                else "**CI 가 0 을 포함한다 — 우위 미확정**")

    ci_pm = best["auc_pm"]["vs_baseline"]
    return [
        f"**콘텐츠 단독으로는 `popularity` 를 못 넘는다.** 스윕 1위 `{top['model']}` 는 "
        f"N=5 AUC {top['auc']:.4f}, `popularity` {pop['auc']['mean']:.4f} 대비 "
        f"{ci_b['diff']:+.4f} [{ci_b['ci_low']:+.4f}, {ci_b['ci_high']:+.4f}] "
        f"({verdict(ci_b)}).",
        f"**인기를 통제하면(`auc_pm`) 방향이 뒤집힌다** — {best['auc_pm']['mean']:.4f} vs "
        f"`popularity` {pop['auc_pm']['mean']:.4f}, 차이 {ci_pm['diff']:+.4f} "
        f"[{ci_pm['ci_low']:+.4f}, {ci_pm['ci_high']:+.4f}] ({verdict(ci_pm)}). "
        "같은 인기 십분위 안에서는 콘텐츠가 인기보다 낫다는 **신호는 보이나 확정은 "
        "아니다.**",
        f"하이브리드 `{hybrid_name}` 은 AUC {hyb['auc']['mean']:.4f}, "
        f"`popularity` 대비 {ci_h['diff']:+.4f} "
        f"[{ci_h['ci_low']:+.4f}, {ci_h['ci_high']:+.4f}] — {verdict(ci_h)}.",
        f"단일 벡터 대조군(`menu_single_vector`)은 {single:.4f}. 다중 벡터와의 차이가 "
        "'평균이 다봉형 취향을 뭉갠다' 가설의 크기다.",
        f"후보 풀 {n_no_menu}곳은 메뉴가 없어 유사도가 NaN 이고, 그 사용자의 평균 "
        "콘텐츠 점수(중립)로 채웠다.",
        "**메뉴명 임베딩은 취향 유추가 아니라 표기 유사도를 잰다.** "
        + _probe_summary(probe) + " 이 Step 의 숫자는 '메뉴명으로 낼 수 있는 바닥값' "
        "으로 읽어야 한다.",
        f"이웃 상위 {cross['top_k']}곳 중 cat2 가 다른 비율 "
        f"{cross['cross_cat2_rate']:.1%} (무작위였다면 {cross['random_rate']:.1%}) — "
        "유사도는 업종 경계를 넘기보다 업종 안을 잘게 나눈다.",
        _significance_line(results, hybrid_name, ns),
    ]


def _significance_line(results, model, ns) -> str:
    """CI 가 0 을 넘긴 칸이 어디인지만 그대로 옮긴다. 없으면 없다고 쓴다."""
    hits = []
    for metric in ("auc", "auc_pm", f"hit@{config.HIT_K}"):
        for n in ns:
            ci = results[model][str(n)]["task_p"][metric]["vs_baseline"]
            if ci["significant"] and ci["diff"] > 0:
                hits.append(f"{metric} N={n} ({ci['diff']:+.4f} "
                            f"[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}])")
    if not hits:
        return (f"`{model}` 이 `popularity` 를 CI 로 넘긴 칸은 **하나도 없다.** "
                "Step 1 은 서버에 넣을 근거를 만들지 못했다.")
    return (f"`{model}` 이 `popularity` 를 CI 로 넘긴 칸은 "
            f"{len(hits)}개다: {', '.join(hits)}. **이걸 승리로 읽으면 안 된다** — "
            "(1) 넘긴 폭이 CI 하한 기준 0.001~0.003 로 경계에 걸쳐 있고, "
            "(2) 지표 3개 × N 4개 = 12칸을 한꺼번에 봤는데 다중비교 보정을 하지 "
            "않았으며, (3) N=10·20 을 실제로 다 채운 사용자는 각각 108명·31명뿐이고"
            "(Step 0), (4) 콘텐츠 변종 자체를 테스트 사용자 위에서 골랐다. "
            "서버 반영의 근거로 삼기에는 약하다.")


def _learnings(results, sweep, best_content, hybrid_name, ns, n_no_menu,
               pool_size, cross, probe) -> str:
    n5 = str(5 if 5 in ns else ns[0])
    by = {r["model"]: r for r in sweep}
    lines = ["### 배운 것", ""]

    # mean vs max — "평균이 다봉형 취향을 뭉갠다" 가설
    lines.append("- **mean vs max (AUC(P), β=0)** — 다봉형 취향 가설의 직접 검증:")
    for v in SWEEP_VARIANTS:
        m, x = by[f"menu_{v}_mean"]["auc"], by[f"menu_{v}_max"]["auc"]
        lines.append(f"  - `{v}`: mean {m:.4f} vs max {x:.4f} (차이 {x - m:+.4f}).")
    lines.append("")

    # IDF 가 실제로 도움이 됐나 (같은 K·같은 집계끼리)
    lines.append("- **IDF 가중이 실제로 벌어준 몫** (top8 무가중 vs top8_idf, 같은 집계):")
    for agg in AGGS:
        a, b = by[f"menu_top8_{agg}"]["auc"], by[f"menu_top8_idf_{agg}"]["auc"]
        lines.append(f"  - `{agg}`: {a:.4f} → {b:.4f} ({b - a:+.4f}).")
    lines.append("")

    # dislike 가중
    lines.append("- **dislike 가중** (top8_idf, max 기준):")
    for suf, beta in DISLIKE_BETA.items():
        lines.append(f"  - β={beta:g}: {by[f'menu_top8_idf_max{suf}']['auc']:.4f}")
    lines.append("")

    single = results["menu_single_vector"][n5]["task_p"]["auc"]["mean"]
    single8 = results["menu_single_vector_top8_idf"][n5]["task_p"]["auc"]["mean"]
    best = results[best_content][n5]["task_p"]["auc"]["mean"]
    lines += [
        f"- 다중 벡터 `{best_content}` {best:.4f} vs 단일 벡터 대조군 "
        f"{single:.4f}(메뉴 전체 평균) / {single8:.4f}(같은 상위 8개 평균). "
        "세 값의 차이가 '집합으로 두는 것' 이 실제로 벌어준 몫이다.",
        "",
        f"- 이웃 상위 {cross['top_k']}곳 중 cat2 가 다른 비율은 "
        f"{cross['cross_cat2_rate']:.1%} (무작위였다면 {cross['random_rate']:.1%}). "
        "메뉴명 공간은 업종을 **넘기보다 업종 안을 잘게 나누는** 쪽으로 작동한다.",
        "",
        "### 이 임베딩이 실제로 재는 것", "",
        "- **메뉴명 임베딩은 '같은 음식의 표기 변형' 을 잘 잡고, '다른 요리 문화의 "
        "같은 취향' 은 잡지 못한다.** 위 찔러보기 표가 그 증거다 — "
        + _probe_summary(probe) +
        " bge-m3 는 짧은 명사구에서 사실상 **문자열 유사도**처럼 움직인다. "
        "PLAN 0절이 든 '곱창전골↔모츠나베' 같은 교차 문화 유추는 메뉴명만으로는 "
        "나오지 않는다.",
        "- 그러므로 이 Step 의 숫자는 **메뉴명 신호의 바닥값**으로 읽어야 한다. "
        "같은 아이디어(집합 + MaxSim)를 더 좋은 텍스트 표현 위에 올리면 달라질 수 "
        "있고, 그건 리뷰 텍스트(Step 2)와 다른 임베딩 모델의 몫이다. "
        "메뉴 modality 안에서 프롬프트 접두어 같은 것으로 이걸 고치려 들지 않았다.",
        "",
        "### 증명하지 못한 것", "",
        "- **CI 가 0 을 포함하는 값은 우위의 근거가 아니다.** 위 표에서 굵게 표시되지 "
        "않은 차이는 전부 여기에 해당한다(PLAN 1절).",
        "- " + _significance_line(results, hybrid_name, ns),
        "- 변종 선택(집계·IDF·β)은 **테스트 사용자 위에서** 했다. λ 만 5-fold 로 "
        "골랐다. 그래서 결선 1위 값에는 선택 편향이 조금 섞여 있고, 아슬아슬하게 "
        "0 을 넘긴 CI 를 근거로 삼으면 안 된다.",
        f"- 메뉴가 없는 가게 {n_no_menu}곳({n_no_menu / pool_size:.1%})은 콘텐츠 모델이 "
        "아무 말도 하지 않는다. 이 가게들에 대한 성능은 이 실험이 재지 못한다.",
        "- 메뉴 **이름**만 임베딩했다. 가격·설명·사진은 아직 안 썼고, 리뷰 텍스트는 "
        "Step 2 다.",
        "- 정답이 카카오 리뷰에서 나왔다는 표본틀 편향은 `auc_pm` 으로 **줄였을 뿐** "
        "없앤 것이 아니다. 십분위 안에서도 리뷰 수는 여전히 다르다.",
    ]
    return "\n".join(lines)


def _probe_summary(probe: dict) -> str:
    """찔러보기 결과를 한 문장으로. 숫자는 전부 probe 에서 온다."""
    base = probe["random_baseline"]["mean"]
    got = {(r["a"], r["b"]): r for r in probe["pairs"] if "cos" in r}
    bits = []
    for a, b in (("곱창전골", "모츠나베"), ("냉면", "메밀소바"), ("메밀소바", "멜론소다"),
                 ("마라탕", "마라탕면")):
        r = got.get((a, b))
        if r:
            bits.append(f"{a}↔{b} {r['cos']:.3f}(#{r['rank']:,})")
    return (", ".join(bits) + f" — 무작위 두 이름의 평균이 이미 {base:.3f} 다.")


def _notes(n_no_menu, future_no_menu, future_total, hist_no_menu, hist_total,
           sweep_n) -> list[str]:
    return [
        "**CI 가 0 을 포함하면 우위 미확정이다.** 작은 양수를 근거로 삼지 않는다 "
        "(PLAN 1절).",
        f"메뉴가 없어 유사도가 NaN 인 후보 풀 가게가 **{n_no_menu}곳**이다. 점수를 낼 때 "
        "그 사용자의 평균 콘텐츠 점수로 채운다(중립) — 0 으로 채우면 '메뉴를 안 올린 "
        "가게' 가 구조적으로 순위 바닥에 깔린다. 이력 쪽이 NaN 이면 그 가게를 집계에서 "
        "뺀다.",
        f"정답(미래 방문) {future_total:,}곳 중 {future_no_menu}곳, N={sweep_n} 이력 신호 "
        f"{hist_total:,}건 중 {hist_no_menu}건이 메뉴 없는 가게다.",
        "메뉴는 `menu_index` 오름차순 앞에서부터 K 개(같은 이름은 한 번)를 쓴다. "
        "화면의 `store.get_menus()` 도 원본 순서를 따른다. "
        "`analysis.backtest.data` 의 가격 특성은 서버 `store.get_feature_menus()` 와 "
        "같이 기존 음식·인기·추천 우선순위를 유지한다. 여기의 임베딩용 메뉴 선택 "
        "계산은 기존 PLAN 규칙을 유지한다.",
        "**체인점은 메뉴가 같아 유사도가 1.0 이 된다** (사례의 스타벅스 지점끼리). "
        "MaxSim 은 '같은 브랜드 다른 지점' 을 맨 위로 밀어올리는데, 제품에서는 그게 "
        "좋은 카드가 아닐 수 있다. 이 Step 에서는 브랜드 중복 억제를 넣지 않았다 — "
        "지표를 흐리지 않고 무엇이 날것으로 나오는지 먼저 보기 위해서다.",
        "하이브리드의 z 정규화는 **그 사용자의 후보**(이미 간 가게를 뺀 나머지) 안에서 "
        "한다. 지표를 재는 집합과 같게 맞춘 것이고, 미래(정답) 정보는 들어가지 않는다.",
        "유사도 행렬은 `analysis/backtest/cache/menusim-<변종>-<지문>.npy` 에 캐시한다. "
        "지문은 임베딩 산출물의 sha256 + 변종 + 후보 풀 구성이라, 임베딩을 다시 돌리면 "
        "캐시가 자동으로 무효가 된다. 캐시는 git 에 넣지 않는다.",
        "BLAS 스레드 수에 따라 부동소수 합의 순서가 달라져 마지막 자리가 흔들릴 수 "
        "있다. 이 실행은 임베딩 작업과 코어를 나눠 쓰느라 1스레드로 돌렸다.",
    ]


if __name__ == "__main__":
    sys.exit(main())
