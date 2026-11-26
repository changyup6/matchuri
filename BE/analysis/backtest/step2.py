# -*- coding: utf-8 -*-
"""Step 2 실행기 — 가게 요약문 · 리뷰 다중 벡터 · 구성요소 블렌드.

    ~/venvs/recsys/bin/python -m analysis.backtest.step2

PLAN.md 2절 '리뷰 다중 벡터 (Step 2)' 와 3절('구성요소 점수의 가중치 몇 개')을
백테스트한다. Step 1.5 가 남긴 질문("CF 와 콘텐츠는 이기는 지표가 다르다 — 섞으면
더 나아지는가")이 이 실행의 주제다.

  1. **가게 요약문**(`store_text`) — 카카오 ai_title/ai_summary/tags 를 문장 하나로
     이어 붙인 임베딩. 가게당 벡터 1개, 코사인. 집계 3종을 스윕한다.
  2. **리뷰 다중 벡터** — 가게당 리뷰 ≤ 12개를 집합으로 들고 메뉴와 같은 MaxSim.
     선택 변종 3종(`longest12`/`random12`/`recent12`) × 집계 3종 + 단일 벡터 대조군.
     **평가 사용자의 리뷰는 전부 제외한다**(PLAN 1절 누수 규칙).
  3. **구성요소 블렌드** — `z_pop + Σ λ_m z_m`, λ 를 (a) 격자 탐욕 선택과
     (b) 쌍대 로지스틱(BPR) 두 방법으로 바깥 5-fold 에서 학습한다.

기준은 두 개다. `popularity`(PLAN 5절 1번의 과제 P 참조)와, Step 1.5 가 세운 현재의
막대 `hybrid_pop_cf_item_sum`. **막대를 넘었는지가 이 Step 의 시험이다.**
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

from analysis.content import blend as blend_mod
from analysis.content import menus as menu_content
from analysis.content import reviews as review_content
from analysis.content import store_text as text_content

from . import cache, config, cv, report, run, scorers
from .bootstrap import paired_diff_ci
from .data import load_dataset
from .evaluate import evaluate
from .pool import attach_crawled_quality, build_pool
from .rng import generator
from .scorers import ScoreContext
from .scorers_menu import LAMBDAS
from .scorers_reviews import (NULL_VARIANT, REVIEW_AGGS, review_space,
                              similarity as review_sim)
from .scorers_text import TEXT_AGGS, similarity as text_sim, text_space
from .step1 import cross_category
from .step1_5 import _mean, _verdict, hybrid_name, significance_line

REFS = {"task_p": "popularity", "task_r": "quality"}
REFERENCE_MODELS = ["random", "popularity", "quality", "current"]

#: Step 1 스윕 1위 콘텐츠 모델 — 블렌드의 메뉴 구성요소.
STEP1_CONTENT = "menu_top8_max"

#: Step 1.5 가 세운 막대. 이 실행이 넘어야 할 상대다.
BAR_CONTENT = "cf_item_sum"

METRICS4 = ["auc", "auc_pm", f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}"]


def _md(text: str) -> str:
    """표 칸에 넣을 수 있게 만든다 — 리뷰 본문에 `|` 가 들어 있으면 표가 깨진다."""
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _fmt_ci(ci: dict) -> str:
    mark = "**" if ci["significant"] else ""
    return f"{mark}{ci['diff']:+.4f}{mark} [{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}]"


# ────────────────────────────────────────────────── 스윕 (N=5)
def sweep(models: list[str], splits, ctx, n_history: int, ref_result, *,
          resamples: int, seed: int) -> list[dict]:
    """모델 목록을 한 N 에서 재고 기준 대비 AUC CI 를 붙인다."""
    rows = []
    for name in models:
        res = evaluate(scorers.get(name), splits, ctx, n_history)
        rows.append({
            "model": name,
            "auc": _mean(res.p["auc"]), "auc_pm": _mean(res.p["auc_pm"]),
            f"hit@{config.HIT_K}": _mean(res.p[f"hit@{config.HIT_K}"]),
            f"ndcg@{config.NDCG_K}": _mean(res.p[f"ndcg@{config.NDCG_K}"]),
            "r_auc": _mean(res.r_auc),
            "vs_pop_auc": paired_diff_ci(res.p["auc"], ref_result.p["auc"],
                                         resamples=resamples, seed=seed),
            "vs_pop_ndcg": paired_diff_ci(res.p[f"ndcg@{config.NDCG_K}"],
                                          ref_result.p[f"ndcg@{config.NDCG_K}"],
                                          resamples=resamples, seed=seed),
        })
        print(f"  {name:<26} AUC={rows[-1]['auc']:.4f} "
              f"auc_pm={rows[-1]['auc_pm']:.4f} "
              f"hit@{config.HIT_K}={rows[-1][f'hit@{config.HIT_K}']:.4f} "
              f"ndcg@{config.NDCG_K}={rows[-1][f'ndcg@{config.NDCG_K}']:.4f}", flush=True)
    return rows


def sweep_table(rows: list[dict], *, sort: bool = True) -> str:
    ordered = sorted(rows, key=lambda r: -r[f"ndcg@{config.NDCG_K}"]) if sort else rows
    out = [f"| # | 모델 | AUC(P) | `popularity` 대비 CI | auc_pm | Hit@{config.HIT_K} | "
           f"NDCG@{config.NDCG_K} | AUC(R) |",
           "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ordered, 1):
        out.append(f"| {i} | `{r['model']}` | {r['auc']:.4f} | "
                   f"{_fmt_ci(r['vs_pop_auc'])} | "
                   f"{r['auc_pm']:.4f} | {r[f'hit@{config.HIT_K}']:.4f} | "
                   f"{r[f'ndcg@{config.NDCG_K}']:.4f} | {r['r_auc']:.4f} |")
    return "\n".join(out)


def review_pivot(rows: list[dict], key: str) -> str:
    """선택 변종 × 집계 피벗 — '어떤 12개를 고르나' 가 '어떻게 접나' 보다 중요한지."""
    by = {r["model"]: r for r in rows}
    variants = list(review_content.VARIANTS)
    out = ["| 집계 | " + " | ".join(f"`{v}`" for v in variants) + " |",
           "|---|" + "---|" * len(variants)]
    for agg in REVIEW_AGGS:
        cells = [f"{by[f'review_{v}_{agg}'][key]:.4f}" for v in variants]
        out.append(f"| `{agg}` | " + " | ".join(cells) + " |")
    cells = [f"{by[f'review_{v}_single'][key]:.4f}" for v in variants]
    out.append("| `single`(단일 벡터 대조군) | " + " | ".join(cells) + " |")
    if f"review_null_{REVIEW_AGGS[0]}" in by:
        cells = [f"{by[f'review_null_{a}'][key]:.4f}" for a in REVIEW_AGGS]
        out += ["", "난수 벡터 대조군(개수 구조만 남긴 것)은 변종과 무관하다: "
                + ", ".join(f"`{a}` **{c}**" for a, c in zip(REVIEW_AGGS, cells))
                + "."]
    return "\n".join(out)


# ────────────────────────────────────────────────── 결선 · 두 기준
def finalist_table(results: dict, results_bar: dict, models: list[str], ns: list[int],
                   metric: str, ref_name: str) -> str:
    """한 지표의 모델 × N 표. 셀 아래에 **기준 대비 CI** 를 적는다."""
    out = ["| 모델 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|" + "---|" * len(ns)]
    for m in models:
        cells = []
        for n in ns:
            e = results[m][str(n)]["task_p"][metric]
            ci = results_bar[m][str(n)]["task_p"][metric]["vs_baseline"]
            if m == ref_name:
                cells.append(f"{e['mean']:.4f} <br><sub>(기준)</sub>")
            else:
                cells.append(f"{e['mean']:.4f} <br><sub>{_fmt_ci(ci)}</sub>")
        out.append(f"| `{m}` | " + " | ".join(cells) + " |")
    return "\n".join(out)


# ────────────────────────────────────────────────── 블렌드
def blend_weight_table(fits: dict, ns: list[int], names: list[str]) -> str:
    """폴드별로 학습된 λ. 다섯 줄이 비슷하면 안정적이라는 뜻이다."""
    out = ["| 학습법 | N | 폴드 | " + " | ".join(f"λ({n})" for n in names[1:]) + " |",
           "|---|---|---|" + "---|" * (len(names) - 1)]
    for fitter, per_n in fits.items():
        for n in ns:
            for i, f in enumerate(per_n[str(n)], 1):
                cells = [f"{f['weights'].get(c, 0.0):.3g}" for c in names[1:]]
                out.append(f"| `{fitter}` | {n} | {i} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def blend_trace_table(fits: list[dict], names: list[str]) -> str:
    """격자 탐욕 선택이 무엇을 어떤 순서로 붙였는지 (폴드 5개)."""
    out = ["| 폴드 | 붙인 순서 (구성요소 λ → 학습 NDCG@20) |", "|---|---|"]
    for i, f in enumerate(fits, 1):
        steps = []
        for t in f["trace"]:
            if t["added"] is None:
                steps.append(f"(인기만) {t['ndcg']:.4f}")
            else:
                steps.append(f"+`{t['added']}` λ={t['lam']:g} → {t['ndcg']:.4f}")
        out.append(f"| {i} | " + " → ".join(steps) + " |")
    return "\n".join(out)


def ablation_table(abl: dict, ns: list[int], metric: str) -> str:
    """ablation 은 `--ablation-N` 으로 줄일 수 있으므로 실제로 돈 N 만 그린다."""
    done = [n for n in ns if all(str(n) in per_n for per_n in abl.values())]
    out = ["| 뺀 구성요소 | " + " | ".join(f"N={n}" for n in done) + " |",
           "|---|" + "---|" * len(done)]
    for comp, per_n in abl.items():
        cells = [_fmt_ci(per_n[str(n)][metric]) for n in done]
        out.append(f"| `{comp}` 없이 | " + " | ".join(cells) + " |")
    return "\n".join(out)


# ────────────────────────────────────────────────── 근거: 사례 설명
def _store_row(ds, sid) -> dict:
    return ds.stores.get(sid, {})


def example_store_ids(step1_json: Path) -> list[tuple[str, str]]:
    """Step 1 이 쓴 예시 가게 5곳을 **그대로** 가져온다.

    같은 가게를 두 modality 로 나란히 보게 하려는 것이다 — 다른 가게를 고르면
    '메뉴는 업종을 못 넘었는데 리뷰는 넘는다' 를 비교할 수 없다.
    """
    if not step1_json.exists():
        return []
    blob = json.loads(step1_json.read_text(encoding="utf-8"))
    return [(e["label"], e["store_id"]) for e in blob["examples"]["neighbours"]]


def neighbours_section(ds, pool, space, variant, sim, chosen, *,
                       top_k: int = 5) -> tuple[str, list]:
    """예시 가게의 최근접 이웃 — 리뷰 유사도가 업종을 넘는지 눈으로 본다."""
    packed = space.packed(variant)
    out = ["| 기준 가게 (cat2) | 이웃 | cat2 | sim | 맞은 리뷰 쌍 |",
           "|---|---|---|---|---|"]
    rows_json = []
    for label, sid in chosen:
        i = pool.pos.get(sid)
        if i is None or not np.isfinite(sim[i]).any():
            continue
        s = _store_row(ds, sid)
        row = sim[i].copy()
        row[i] = -np.inf
        order = [int(j) for j in np.argsort(-np.nan_to_num(row, nan=-np.inf))[:top_k]]
        head = f"**{_md(s.get('name', sid))}** ({s.get('cat2') or '—'}, {label})"
        entry = {"label": label, "store_id": sid, "name": s.get("name"),
                 "cat2": s.get("cat2"), "neighbours": []}
        for k, j in enumerate(order):
            t = _store_row(ds, pool.ids[j])
            a, b, c = review_content.best_review_pair(packed, i, j)
            out.append(f"| {head if k == 0 else ''} | "
                       f"{_md(t.get('name', pool.ids[j]))} | "
                       f"{t.get('cat2') or '—'} | {sim[i, j]:.4f} | "
                       f"“{_md(a)}” ↔ “{_md(b)}” ({c:.3f}) |")
            entry["neighbours"].append(
                {"store_id": pool.ids[j], "name": t.get("name"), "cat2": t.get("cat2"),
                 "sim": float(sim[i, j]), "review_a": a, "review_b": b, "cos": c})
        rows_json.append(entry)
    return "\n".join(out), rows_json


def explain_users(ds, pool, space, variant, sim, splits, best_model, ctx, *,
                  n_history: int, seed: int, n_users: int = 5,
                  top_k: int = 10) -> tuple[str, list]:
    """평가 사용자 5명의 방문 이력 → 추천 상위 10곳과 **맞은 리뷰 쌍**.

    사용자 뽑기는 Step 1 과 **같은 난수**를 쓴다(`"step1", "example-users"`). 같은
    사람의 메뉴 추천과 리뷰 추천을 두 문서에서 나란히 볼 수 있게 하려는 것이다.
    """
    packed = space.packed(variant)
    scorer = scorers.get(best_model)
    scorer.prepare(ctx)
    pick = generator(seed, "step1", "example-users").choice(
        len(splits), size=min(n_users, len(splits)), replace=False)

    out: list[str] = []
    rows_json = []
    for u in sorted(int(i) for i in pick):
        sp = splits[u]
        hist = sp.signals(n_history, ctx.history_mode)
        visited = [sid for sid, like in hist if like and sid in pool.pos]
        if not visited:
            continue
        out.append(f"**사용자 `{sp.user_id}`** — 이력 N={n_history}, 방문 {len(visited)}곳")
        out.append("")
        out.append("| 방문한 가게 | cat2 | 리뷰 벡터 수 | 대표 리뷰 조각 |")
        out.append("|---|---|---|---|")
        for sid in visited:
            s = _store_row(ds, sid)
            i = pool.pos[sid]
            picked = space.selection(variant, sid)
            snippet = picked[0].snippet if picked else "—"
            out.append(f"| {_md(s.get('name', sid))} | {s.get('cat2') or '—'} | "
                       f"{int(packed.counts[i])} | “{_md(snippet)}” |")
        out.append("")

        scores = np.asarray(scorer.score(hist, ctx, user_id=sp.user_id), dtype=float)
        seen = np.zeros(len(pool), dtype=bool)
        for sid in sp.seen_stores:
            seen[pool.pos[sid]] = True
        scores = np.where(seen, -np.inf, scores)
        future = {sid for sid, _ in sp.future}
        order = np.argsort(-scores)[:top_k]

        out.append(f"추천 상위 {top_k} (`{best_model}`). '맞은 리뷰 쌍' 은 가장 닮은 "
                   "방문 가게와 후보에서 **코사인이 가장 큰 리뷰 한 쌍**이다.")
        out.append("")
        out.append("| # | 추천 가게 | cat2 | 점수 | 매칭된 방문 가게 | "
                   "방문 가게 리뷰 ↔ 후보 리뷰 (코사인) | 정답? |")
        out.append("|---|---|---|---|---|---|---|")
        user_json = {"user_id": sp.user_id, "visited": visited, "top": []}
        for rank, j in enumerate(order, 1):
            j = int(j)
            s = _store_row(ds, pool.ids[j])
            best_h, best_v = None, -np.inf
            for sid in visited:
                v = sim[pool.pos[sid], j]
                if np.isfinite(v) and v > best_v:
                    best_h, best_v = sid, float(v)
            if best_h is None:
                pair, hname = "—", "—"
                a = b = ""
                c = float("nan")
            else:
                a, b, c = review_content.best_review_pair(packed, pool.pos[best_h], j)
                pair = f"“{_md(a)}” ↔ “{_md(b)}” ({c:.3f})" if a else "—"
                hname = _md(_store_row(ds, best_h).get("name", best_h))
            hit = "**O**" if pool.ids[j] in future else ""
            out.append(f"| {rank} | {_md(s.get('name', pool.ids[j]))} | "
                       f"{s.get('cat2') or '—'} | {scores[j]:.4f} | {hname} | "
                       f"{pair} | {hit} |")
            user_json["top"].append(
                {"rank": rank, "store_id": pool.ids[j], "name": s.get("name"),
                 "cat2": s.get("cat2"), "score": float(scores[j]),
                 "matched_store": best_h, "review_a": a, "review_b": b,
                 "cos": c, "is_future": pool.ids[j] in future})
        out.append("")
        rows_json.append(user_json)
    return "\n".join(out), rows_json


# ────────────────────────────────────────────────── main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.backtest.step2",
        description="PLAN.md 2절 '리뷰 다중 벡터 (Step 2)' 와 구성요소 블렌드를 백테스트한다.")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES))
    ap.add_argument("--out", default="step2-text-reviews")
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--history", default=config.DEFAULT_HISTORY_MODE,
                    choices=list(config.HISTORY_MODES))
    ap.add_argument("--select-metric", default=f"ndcg@{config.NDCG_K}",
                    choices=list(config.SELECT_METRICS),
                    help="하이브리드 λ 를 고르는 잣대 (기본 NDCG@20 — 스와이프는 "
                         "상위 10~20장이 전부다)")
    ap.add_argument("--sweep-N", type=int, default=5, help="스윕을 돌릴 이력 길이")
    ap.add_argument("--ablation-N", default=None,
                    help="ablation 을 돌릴 이력 길이(쉼표 구분). 기본은 --N 전부. "
                         "시간이 없을 때 줄이는 안전장치다")
    ap.add_argument("--content", default=STEP1_CONTENT, help="Step 1 의 메뉴 모델")
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--skip-reviews", action="store_true",
                    help="리뷰 임베딩이 아직 없을 때 — 요약문과 블렌드(리뷰 제외)만 돌린다")
    ap.add_argument("--rebuild-md", action="store_true",
                    help="실험을 다시 돌리지 않고 저장된 json 에서 .md 만 다시 쓴다")
    args = ap.parse_args(argv)

    if args.rebuild_md:
        path = Path(config.RESULTS_DIR) / f"{args.out}.json"
        _write(json.loads(path.read_text(encoding="utf-8")), args.out)
        return 0

    ns = [int(x) for x in args.N.split(",") if x.strip()]
    abl_ns = ([int(x) for x in args.ablation_N.split(",") if x.strip()]
              if args.ablation_N else list(ns))
    metric = args.select_metric
    have_reviews = (not args.skip_reviews) and "reviews" in _available_kinds()
    if args.skip_reviews:
        print("리뷰 임베딩을 건너뛴다 (--skip-reviews)", flush=True)
    elif not have_reviews:
        print("data/vectors/reviews.npy 가 없다 — 요약문과 블렌드만 돌린다", flush=True)

    print("데이터 로드 …", flush=True)
    ds = load_dataset()
    pool = build_pool(ds)
    splits, _ = cache.load_or_build(ds, pool, use_cache=not args.no_cache)
    eval_users = {s.user_id for s in splits}
    attach_crawled_quality(pool, ds, eval_users)
    store_names = {s: (ds.stores.get(s, {}).get("name") or "") for s in pool.ids}
    ctx = ScoreContext(pool=pool, seed=args.seed, history_mode=args.history,
                       extras={"candidates": run.seen_masks(splits, pool),
                               "dataset": ds, "eval_users": eval_users,
                               "store_names": store_names})
    print(f"  후보 풀 {len(pool):,}곳 · 평가 사용자 {len(splits)}명 · "
          f"이력 해석 `{args.history}`", flush=True)

    ref5 = evaluate(scorers.get("popularity"), splits, ctx, args.sweep_N)

    # ── 1) 가게 요약문
    space_t = text_space(ctx)
    sim_t = text_sim(ctx)
    fin_t = sim_t[np.isfinite(sim_t)]
    text_meta = {
        "pool_size": len(pool), "with_text": int(space_t.has_text.sum()),
        "coverage": space_t.coverage,
        "missing": [store_names[sid] or sid
                    for sid in np.asarray(pool.ids)[~space_t.has_text].tolist()],
        "sim_mean": float(fin_t.mean()), "sim_p95": float(np.percentile(fin_t, 95)),
        "sim_p05": float(np.percentile(fin_t, 5)),
        "text_len_mean": float(np.mean([len(t) for t in space_t.texts if t])),
        # 업종을 넘는지 — 리뷰·메뉴와 같은 잣대로 잰다(Step 1 의 26% / 무작위 82%).
        "cross_category": cross_category(pool, sim_t, top_k=5),
    }
    print(f"  요약문: {text_meta['with_text']:,}/{len(pool):,} "
          f"({text_meta['coverage']:.1%})", flush=True)
    text_sweep = sweep([f"text_{a}" for a in TEXT_AGGS], splits, ctx,
                       args.sweep_N, ref5, resamples=args.resamples, seed=args.seed)
    best_text = max(text_sweep, key=lambda r: r[f"ndcg@{config.NDCG_K}"])["model"]
    print(f"  → 요약문 1위: {best_text}", flush=True)

    # ── 2) 리뷰 다중 벡터
    review_meta: dict = {"available": have_reviews}
    review_sweep: list[dict] = []
    best_review = None
    space_r = None
    if have_reviews:
        space_r = review_space(ctx)
        review_meta.update(space_r.usable_stats())
        review_meta["excluded_authors"] = len(eval_users)
        review_meta["excluded_reviews"] = _count_excluded(eval_users)
        review_meta["variants"] = {}
        for vname, variant in review_content.VARIANTS.items():
            mat = review_sim(ctx, vname)
            counts = space_r.counts(variant)
            fin = mat[np.isfinite(mat)]
            review_meta["variants"][vname] = {
                "vectors": int(counts.sum()),
                "stores_with_vectors": int((counts > 0).sum()),
                "sim_mean": float(fin.mean()), "sim_p95": float(np.percentile(fin, 95)),
                "used_reviews": len(space_r.used_review_ids(variant)),
            }
            print(f"  리뷰 {vname}: 벡터 {int(counts.sum()):,}개 · "
                  f"가게 {int((counts > 0).sum()):,}곳", flush=True)
        counts = space_r.counts(review_content.VARIANTS[NULL_VARIANT])
        review_meta["count_pop_corr"] = float(
            np.corrcoef(counts.astype(float), pool.log_review_count)[0, 1])
        names = [f"review_null_{a}" for a in REVIEW_AGGS]
        names += [f"review_{v}_{a}" for v in review_content.VARIANTS
                  for a in REVIEW_AGGS]
        names += [f"review_{v}_single" for v in review_content.VARIANTS]
        review_sweep = sweep(names, splits, ctx, args.sweep_N, ref5,
                             resamples=args.resamples, seed=args.seed)
        best_review = max((r for r in review_sweep
                           if not r["model"].startswith("review_null_")),
                          key=lambda r: r[f"ndcg@{config.NDCG_K}"])["model"]
        print(f"  → 리뷰 1위: {best_review}", flush=True)

    # ── 3) 결선 (모든 N)
    finalists = REFERENCE_MODELS + [BAR_CONTENT, args.content, best_text]
    if best_review:
        finalists.append(best_review)
    raw: dict[str, dict] = {}
    for name in finalists:
        raw[name] = {}
        for n in ns:
            raw[name][n] = evaluate(scorers.get(name), splits, ctx, n)
            print(f"  {name:<26} N={n:<3} "
                  f"AUC(P)={_mean(raw[name][n].p['auc']):.4f}", flush=True)

    # ── 4) 하이브리드 (λ 를 폴드 밖에서 고른다)
    hybrids: list[str] = []
    chosen: dict = {}
    contents = [BAR_CONTENT, best_text] + ([best_review] if best_review else [])
    for content in contents:
        name = hybrid_name(content, metric)
        hybrids.append(name)
        raw[name] = {}
        chosen[content] = {}
        candidates = {f"{lam:g}": (lambda c=content, lam=lam: scorers.get(
            f"hybrid_pop_{c}_l{lam:g}")) for lam in LAMBDAS}
        for n in ns:
            res, folds = cv.outer_cv(candidates, splits, ctx, n,
                                     metric=metric, seed=args.seed)
            raw[name][n] = res
            chosen[content][str(n)] = folds
            print(f"  {name:<34} N={n:<3} λ={folds} "
                  f"AUC(P)={_mean(res.p['auc']):.4f}", flush=True)
    bar_model = hybrid_name(BAR_CONTENT, metric)

    # ── 5) 블렌드
    comp_models = {"popularity": "popularity", BAR_CONTENT: BAR_CONTENT,
                   args.content: args.content, "text": best_text}
    if best_review:
        comp_models["review"] = best_review
    comp_names = list(comp_models)
    banks = {}
    blend_models = {"grid": "blend_grid", "pairwise": "blend_pairwise"}
    fits: dict = {f: {} for f in blend_models}
    for n in ns:
        banks[n] = blend_mod.ComponentBank.build(comp_models, splits, ctx, n)
        for fitter, model_name in blend_models.items():
            res, per_fold = blend_mod.outer_cv_blend(
                banks[n], splits, ctx, fitter=fitter, seed=args.seed, name=model_name)
            raw.setdefault(model_name, {})[n] = res
            fits[fitter][str(n)] = per_fold
            print(f"  {model_name:<26} N={n:<3} "
                  f"AUC(P)={_mean(res.p['auc']):.4f} "
                  f"hit@{config.HIT_K}={_mean(res.p[f'hit@{config.HIT_K}']):.4f}",
                  flush=True)

    # ── 6) ablation — 구성요소를 하나씩 빼고 **다시 학습**한다
    ablation: dict = {f: {} for f in blend_models}
    for fitter, model_name in blend_models.items():
        for comp in comp_names:
            rest = [c for c in comp_names if c != comp]
            ablation[fitter][comp] = {}
            for n in abl_ns:
                res, _ = blend_mod.outer_cv_blend(
                    banks[n], splits, ctx, fitter=fitter, seed=args.seed,
                    name=f"{model_name}_no_{comp}", components=rest)
                ablation[fitter][comp][str(n)] = {
                    m: paired_diff_ci(res.p[m], raw[model_name][n].p[m],
                                      resamples=args.resamples, seed=args.seed)
                    for m in METRICS4}
            shown = ablation[fitter][comp][str(abl_ns[0])][f"ndcg@{config.NDCG_K}"]
            print(f"  ablation {fitter:<9} −{comp:<16} "
                  f"ΔNDCG@{config.NDCG_K}(N={abl_ns[0]})={shown['diff']:+.4f}",
                  flush=True)

    model_names = finalists + hybrids + list(blend_models.values())
    results = run.aggregate(raw, model_names, ns, REFS,
                            resamples=args.resamples, seed=args.seed)
    results_bar = run.aggregate(raw, model_names, ns,
                                {"task_p": bar_model, "task_r": REFS["task_r"]},
                                resamples=args.resamples, seed=args.seed)
    users_meta = run.summarise_users(splits, raw, model_names, ns,
                                     history_mode=args.history)

    # ── 7) 사례
    examples: dict = {}
    if have_reviews and best_review:
        variant = review_content.VARIANTS[_variant_of(best_review)]
        sim_best = review_sim(ctx, variant.name)
        chosen_stores = example_store_ids(
            Path(config.RESULTS_DIR) / "step1-menu.json")
        neigh_md, neigh_json = neighbours_section(ds, pool, space_r, variant,
                                                  sim_best, chosen_stores)
        users_md, users_json = explain_users(ds, pool, space_r, variant, sim_best,
                                             splits, best_review, ctx,
                                             n_history=args.sweep_N, seed=args.seed)
        cross = cross_category(pool, sim_best, top_k=5)
        examples = {"variant": variant.name,
                    "neighbours_md": neigh_md, "neighbours": neigh_json,
                    "users_md": users_md, "users": users_json,
                    "cross_category": cross,
                    "menu_cross_category": _step1_cross()}
        print(f"  사례: 이웃 cat2 교차 {cross['cross_cat2_rate']:.1%} "
              f"(무작위 {cross['random_rate']:.1%})", flush=True)

    blob = {
        "run": args.out,
        "title": "Step 2 — 가게 요약문 · 리뷰 다중 벡터 · 구성요소 블렌드",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": run._git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "references": REFS,
        "baseline_model": REFS["task_p"],
        "history_sizes": ns,
        "intro": [
            "`analysis/PLAN.md` 2절 '리뷰 다중 벡터 (Step 2)' 와 3절의 '구성요소 점수'"
            " 를 구현한 것이다.",
            "가게 요약문(`store_text`) modality 를 새로 넣고, 가게마다 리뷰 ≤ 12개를"
            " 집합으로 들고 메뉴와 같은 MaxSim 으로 재고,",
            "마지막에 인기·협업필터·메뉴·요약문·리뷰 다섯 구성요소를 z 점수로 섞어"
            " 가중치를 바깥 5-fold 로 학습했다.",
            f"기준은 둘이다 — `popularity`(PLAN 5절 1번)와 Step 1.5 가 세운 막대"
            f" `{bar_model}`.",
        ],
        "command": [
            "~/venvs/recsys/bin/python -m analysis.backtest.step2 \\",
            f"    --N {','.join(str(n) for n in ns)} --history {args.history} \\",
            f"    --select-metric {metric} --out {args.out}",
        ],
        "config": {
            "hit_k": config.HIT_K, "recall_k": config.RECALL_K, "ndcg_k": config.NDCG_K,
            "bootstrap_resamples": args.resamples,
            "history_mode": args.history, "select_metric": metric,
            "lambdas": list(LAMBDAS), "blend_grid": list(blend_mod.GRID),
            "blend_l2_grid": list(blend_mod.L2_GRID),
            "blend_negatives": blend_mod.N_NEGATIVES,
            "sweep_n_history": args.sweep_N, "ablation_n_history": abl_ns,
            "max_reviews_per_store": review_content.MAX_REVIEWS,
            "split_cache_fingerprint": cache.fingerprint(),
        },
        "pool": {"size": len(pool), "stores_total": len(ds.stores),
                 "reviews_total": len(ds.reviews)},
        "users": users_meta,
        "store_text": {"meta": text_meta, "sweep": text_sweep, "best": best_text},
        "reviews": {"meta": review_meta, "sweep": review_sweep, "best": best_review},
        "bar_model": bar_model,
        "content_model": args.content,
        "finalists": finalists, "hybrids": hybrids,
        "hybrid": {"models": hybrids, "chosen_per_fold": chosen,
                   "select_metric": metric, "lambdas": list(LAMBDAS)},
        "blend": {"components": comp_names, "component_models": comp_models,
                  "models": blend_models, "fits": fits, "ablation": ablation},
        "examples": examples,
        "models": {m: {"description": scorers.get(m).description} for m in finalists}
        | {h: {"description": f"popularity_z + λ·(`{_content_of(h, metric)}`)_z — "
                              f"λ 는 사용자 5-fold out-of-fold 선택, 기준 `{metric}`"}
           for h in hybrids}
        | {"blend_grid": {"description":
                          "구성요소 블렌드 — λ 를 격자 탐욕 전진 선택(NDCG@20)으로"},
           "blend_pairwise": {"description":
                              "구성요소 블렌드 — λ 를 쌍대 로지스틱(BPR)으로"}},
        "results": results,
        "results_vs_bar": results_bar,
    }
    _write(blob, args.out)
    return 0


def _available_kinds() -> list[str]:
    from analysis import vectors
    return vectors.available()


def _count_excluded(eval_users: set[str]) -> int:
    """가게 표현에서 빠진 평가 사용자 리뷰 수 — 임베딩된 22,686건 기준."""
    from analysis import vectors
    try:
        index = vectors.load_index("reviews")
    except FileNotFoundError:
        return 0
    return sum(1 for r in index if str(r.get("author_id")) in eval_users)


def _variant_of(model: str) -> str:
    return model.split("_")[1]


def _content_of(hybrid: str, metric: str) -> str:
    return hybrid[len("hybrid_pop_"):].rsplit("_cv_", 1)[0]


def _step1_cross() -> dict:
    """Step 1 의 cat2 교차 비율 — 리뷰와 비교하려면 같은 문서의 숫자를 인용해야 한다."""
    path = Path(config.RESULTS_DIR) / "step1-menu.json"
    if not path.exists():
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))
    return blob.get("examples", {}).get("cross_category", {})


# ────────────────────────────────────────────────── 글 (숫자는 blob 에서만 읽는다)
def text_section(blob: dict) -> str:
    m, sweep_rows, best = (blob["store_text"]["meta"], blob["store_text"]["sweep"],
                           blob["store_text"]["best"])
    n5 = blob["config"]["sweep_n_history"]
    missing = ", ".join(m["missing"][:8]) + ("…" if len(m["missing"]) > 8 else "")
    return "\n".join([
        "카카오가 가게마다 붙여 놓은 `ai_title + ai_summary + tags + review_tags` 를 "
        "문장 하나로 이어 붙여 임베딩한 것이다(평균 "
        f"{m['text_len_mean']:.0f}자). 가게당 벡터가 **1개**라 MaxSim 이 없고, 남는 "
        "자유도는 이력 위의 집계뿐이다.",
        "",
        f"- **풀 커버리지 {m['with_text']:,} / {m['pool_size']:,} = "
        f"{m['coverage']:.1%}.** 요약문이 없는 {m['pool_size'] - m['with_text']}곳"
        f"({missing})은 유사도가 **NaN** 이고, Step 1 의 메뉴 NaN 규칙을 그대로 쓴다 — "
        "이력 쪽이 NaN 이면 집계에서 빼고, 후보 쪽이 NaN 이면 그 사용자의 평균 점수"
        "(중립)로 채운다. 0 으로 채우면 '모른다' 가 '싫다' 로 번역된다.",
        f"- 가게-가게 코사인은 평균 **{m['sim_mean']:.3f}** (5분위 {m['sim_p05']:.3f} · "
        f"95분위 {m['sim_p95']:.3f})다. 모든 가게가 서로 그만큼 닮은 것이 바닥값이고 "
        f"{m['sim_p95']:.2f} 가 넘어야 상위 5%다 — 절댓값을 '닮았다' 로 읽지 말 것.",
        "",
        f"**집계 3종** (N={n5}, 기준 `popularity`):",
        "",
        sweep_table(sweep_rows, sort=False),
        "",
        f"결선에는 NDCG@{config.NDCG_K} 가 가장 높은 **`{best}`** 를 올린다.",
    ] + ([
        "",
        f"- 최근접 {m['cross_category']['top_k']}곳 중 cat2 가 다른 비율 "
        f"**{m['cross_category']['cross_cat2_rate']:.1%}** (무작위였다면 "
        f"{m['cross_category']['random_rate']:.1%}). Step 1 의 메뉴는 같은 잣대로 "
        "26.3% 였다 — 요약문도 업종 안쪽을 강하게 선호한다면 '카테고리를 넘는 취향' 은 "
        "아직 어느 표현에서도 관측되지 않은 것이다.",
    ] if m.get("cross_category") else []))


def review_section(blob: dict) -> str:
    meta, rows, best = (blob["reviews"]["meta"], blob["reviews"]["sweep"],
                        blob["reviews"]["best"])
    if not meta.get("available"):
        return ("리뷰 임베딩(`data/vectors/reviews.npy`)이 아직 없어 이 절은 비어 있다. "
                "임베딩이 끝나면 `--out` 을 같게 두고 다시 돌리면 채워진다.")
    n5 = blob["config"]["sweep_n_history"]
    k = blob["config"]["max_reviews_per_store"]
    var = meta["variants"]
    body = [
        f"가게마다 리뷰 **≤ {k}개**를 집합으로 들고, 메뉴와 **같은 코드**"
        "(`analysis.content.menus.similarity_matrix`)로 MaxSim 을 잰다. 리뷰 쪽에서 "
        "새로 정하는 것은 '어떤 12개를 고르나' 뿐이고, 그것이 이 절의 질문이다.",
        "",
        "```",
        "MaxSim(A→B) = mean_{a∈A} max_{b∈B} a·b      (리뷰는 가중치 전부 1)",
        "sim(A, B)   = (MaxSim(A→B) + MaxSim(B→A)) / 2",
        "```",
        "",
        f"- **누수 규칙.** 평가 사용자 {meta['excluded_authors']}명이 쓴 리뷰 "
        f"**{meta['excluded_reviews']:,}건**을 가게 표현에서 전부 뺐다(임베딩된 리뷰 "
        "기준). 이력 구간이든 미래 구간이든 상관없다 — 평가 사용자의 문장이 가게 "
        "표현에 남으면 그 사람의 취향으로 그 사람의 정답을 맞히게 된다. "
        "`tests/test_review_content.py` 가 선택된 review_id 에 평가 사용자의 것이 "
        "하나도 없음을 검증한다.",
        f"- **쓸 수 있는 리뷰의 분포** (후보 풀 {meta['stores']:,}곳): "
        f"0건 **{meta['zero']}곳**, 1~{k - 1}건 **{meta['under_k']:,}곳**, "
        f"{k}건 이상 **{meta['at_least_k']}곳**. 가게당 평균 {meta['mean']:.1f}건 · "
        f"중앙값 {meta['median']:.0f}건 · 최대 {meta['max']}건. "
        f"**0건인 {meta['zero']}곳은 유사도가 NaN** 이고 메뉴와 같은 중립 처리를 받는다. "
        f"'≤ {k}개' 라고 썼지만 실제로 {k}개를 채우는 가게는 "
        f"{meta['at_least_k'] / max(meta['stores'], 1):.0%} 뿐이다 — 이 modality 의 "
        "해상도는 대부분의 가게에서 리뷰 몇 건이다.",
        "",
        "| 변종 | 쓴 리뷰 벡터 | 벡터가 있는 가게 | 유사도 평균 | 95분위 |",
        "|---|---|---|---|---|",
    ]
    for name, v in var.items():
        body.append(f"| `{name}` | {v['vectors']:,} | {v['stores_with_vectors']:,} | "
                    f"{v['sim_mean']:.3f} | {v['sim_p95']:.3f} |")
    body += [
        "",
        f"**대조군을 둘 둔다.** `single` 은 가게 = 리뷰 {k}개의 평균 벡터 1개, "
        "사용자 = 방문 가게 평균 1개, 코사인이다 — 다중 벡터의 이득이 '집합' 때문인지 "
        "본다. `review_null_*` 는 **개수 구조는 그대로 두고 벡터만 난수로 바꾼 것**"
        "이다. MaxSim 의 `max_{b∈B}` 는 B 가 클수록 커지므로 리뷰가 12개인 가게는 "
        "내용과 무관하게 유사도가 높게 나오고, 리뷰 수는 곧 `popularity` 다 — 즉 리뷰 "
        "MaxSim 점수의 일부는 **인기의 다른 이름**일 수 있다. 난수 대조군의 성능이 "
        "그 몫이다."
        + (f" (쓴 리뷰 벡터 수와 log1p(리뷰 수)의 상관 "
           f"**{meta['count_pop_corr']:.2f}**)" if "count_pop_corr" in meta else ""),
        "",
        f"**스윕** (N={n5}, 기준 `popularity`, NDCG@{config.NDCG_K} 내림차순):",
        "",
        sweep_table(rows),
        "",
        f"**변종 × 집계 피벗** (NDCG@{config.NDCG_K}):",
        "",
        review_pivot(rows, f"ndcg@{config.NDCG_K}"),
        "",
        "**같은 것, AUC:**",
        "",
        review_pivot(rows, "auc"),
        "",
        f"결선에는 **`{best}`** 를 올린다.",
    ]
    return "\n".join(body)


def hybrid_lambda_table(chosen: dict, ns: list[int]) -> str:
    """하이브리드의 λ 를 폴드 5개가 각각 무엇으로 골랐는지."""
    out = ["| 콘텐츠 모델 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|" + "---|" * len(ns)]
    for content, per_n in chosen.items():
        cells = [", ".join(per_n[str(n)]) for n in ns]
        out.append(f"| `{content}` | " + " | ".join(cells) + " |")
    return "\n".join(out)


def finalist_section(blob: dict) -> str:
    ns = blob["history_sizes"]
    bar = blob["bar_model"]
    models = blob["finalists"] + blob["hybrids"] + list(blob["blend"]["models"].values())
    body = [
        f"셀은 사용자 평균, 아랫줄은 **막대 `{bar}` 대비** 차이와 paired bootstrap "
        f"{blob['config']['bootstrap_resamples']:,}회 95% CI 다. `popularity` 대비 CI 는 "
        "위의 '과제 P' 표에 있다 — 두 기준을 다 보라고 표를 둘로 나눴다.",
        "",
        "**굵게 표시된 것만 CI 가 0 을 넘겼다.** 부호를 같이 볼 것: 굵은 음수는 "
        "막대에 **졌다**는 뜻이다.",
    ]
    for metric in METRICS4:
        body += ["", f"### {metric} — 막대 대비", "",
                 finalist_table(blob["results"], blob["results_vs_bar"], models, ns,
                                metric, bar)]
    hyb = blob["hybrid"]
    grid = ", ".join(f"{g:g}" for g in hyb["lambdas"])
    body += [
        "", "### 하이브리드의 λ (폴드 밖 선택)", "",
        f"λ 격자 {{{grid}}} 를 사용자 5-fold 로 고른다(`cv.outer_cv`, 기준 "
        f"`{hyb['select_metric']}`). 칸 안의 값은 **폴드 5개가 각각 고른 λ** 다 — "
        "다섯이 같으면 선택이 안정적이라는 뜻이고, 흩어졌으면 λ 가 별로 중요하지 "
        "않다는 뜻이다.",
        "",
        hybrid_lambda_table(hyb["chosen_per_fold"], ns),
    ]
    return "\n".join(body)


def blend_section(blob: dict) -> str:
    b = blob["blend"]
    ns = blob["history_sizes"]
    names = b["components"]
    bar = blob["bar_model"]
    n5 = str(blob["config"]["sweep_n_history"] if blob["config"]["sweep_n_history"] in ns
             else ns[0])
    body = [
        "PLAN 3절: *\"온라인 학습은 1,024차원 벡터 위에서 하지 않는다. 사용자별로 "
        "학습하는 것은 **구성요소 점수의 가중치 몇 개**뿐이다.\"* 그 가중치가 오프라인"
        "에서라도 학습되는지, 어떤 구성요소가 실제로 기여하는지를 여기서 잰다.",
        "",
        "```",
        "score(i) = z_pop(i) + Σ_m λ_m · z_m(i)",
        "```",
        "",
        "- z 는 **그 사용자의 후보**(이미 간 가게를 뺀 나머지) 안에서 낸다. 눈금이 "
        "modality 마다 다르므로(코사인 0.55 대 공방문 0.02) 정규화 없이 더하면 큰 "
        "눈금 하나가 전부를 결정한다.",
        "- **NaN 은 z 정규화 뒤 0** 이다 — 그 사용자 후보의 평균, 즉 '모른다'.",
        "- `popularity` 의 가중치는 1 로 고정한다. λ 는 '인기 위에 취향을 얼마나 "
        "얹을까' 이고, 그래야 `hybrid_pop_*` 와 같은 눈금으로 읽힌다.",
        "",
        "**구성요소**",
        "",
        "| 구성요소 | 쓴 모델 |",
        "|---|---|",
    ]
    for comp, model in b["component_models"].items():
        body.append(f"| `{comp}` | `{model}` |")
    body += [
        "",
        "**(a) 격자 탐욕 전진 선택** — λ ∈ {"
        + ", ".join(f"{g:g}" for g in blob["config"]["blend_grid"]) + "}, 학습 폴드의 "
        f"NDCG@{config.NDCG_K} 가 가장 많이 오르는 (구성요소, λ) 를 하나씩 붙이고 더 "
        "오르지 않으면 멈춘다.",
        "",
        f"**(b) 쌍대 로지스틱 (BPR)** — 학습 폴드의 사용자마다 양성(미래 방문) 하나당 "
        f"씨앗 고정 무작위 음성 {blob['config']['blend_negatives']}곳을 뽑아 "
        "σ(w·(z⁺−z⁻)) 를 최대화한다. 절편 없음, L2 계수는 학습 폴드 안에서 {"
        + ", ".join(f"{a:g}" for a in blob["config"]["blend_l2_grid"]) + "} 중에 고른다. "
        "순위는 w 를 상수배해도 같으므로 표에는 **w_pop 으로 나눈 λ** 를 적는다.",
        "",
        "**폴드별로 학습된 λ**",
        "",
        blend_weight_table(b["fits"], ns, names),
        "",
        f"**격자가 무엇을 어떤 순서로 붙였나** (N={n5})",
        "",
        blend_trace_table(b["fits"]["grid"][n5], names),
        "",
        f"**ablation — 구성요소를 하나씩 빼고 다시 학습했을 때의 변화** (막대가 아니라 "
        "**전체 블렌드** 대비 차이다. 음수면 그 구성요소가 기여했다는 뜻이다.)",
        "",
        f"격자 학습 · NDCG@{config.NDCG_K}:",
        "",
        ablation_table(b["ablation"]["grid"], ns, f"ndcg@{config.NDCG_K}"),
        "",
        f"쌍대 학습 · NDCG@{config.NDCG_K}:",
        "",
        ablation_table(b["ablation"]["pairwise"], ns, f"ndcg@{config.NDCG_K}"),
        "",
        f"쌍대 학습 · AUC:",
        "",
        ablation_table(b["ablation"]["pairwise"], ns, "auc"),
        "",
        f"블렌드가 막대 `{bar}` 를 넘었는지는 위 '결선' 표의 `blend_*` 줄에서 읽는다.",
        "",
        "**PLAN 6절이 요구한 사전값.** *\"w 의 사전값은 오프라인 블렌드(Step 2)의 CV "
        "가중치\"* — 폴드 평균으로 적으면 이렇다(쌍대 학습, w_pop 으로 나눈 λ):",
        "",
        blend_prior_table(b["fits"]["pairwise"], ns, names),
        "",
        "PLAN 6절의 성분 목록과 두 군데가 다르다. (1) **`z_q`(품질)를 넣지 않았다** — "
        "과제 P 에서 `quality` 는 AUC 0.50(무작위)이라 이 블렌드의 목적함수에 기여할 "
        "여지가 없다. 품질은 과제 R 의 가드로 따로 다뤄야 한다. (2) PLAN 은 "
        "`z_text`·`z_rev` 를 **max** 집계로 적었지만, 스윕에서 이긴 집계는 그것과 "
        "다를 수 있다 — 위 '구성요소' 표의 모델 이름이 실제로 쓴 집계다.",
    ]
    return "\n".join(body)


def blend_prior_table(per_n: dict, ns: list[int], names: list[str]) -> str:
    """폴드 평균 λ — Step 4 의 사전값으로 그대로 쓰라고 만든 표."""
    out = ["| N | " + " | ".join(f"λ({n})" for n in names[1:]) + " |",
           "|---|" + "---|" * (len(names) - 1)]
    for n in ns:
        fits = per_n[str(n)]
        cells = []
        for c in names[1:]:
            vals = [f["weights"].get(c, 0.0) for f in fits]
            cells.append(f"{float(np.mean(vals)):.3f}")
        out.append(f"| {n} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def examples_section(blob: dict) -> str:
    ex = blob.get("examples") or {}
    if not ex:
        return "리뷰 임베딩이 없어 사례를 만들지 못했다."
    cross, menu_cross = ex["cross_category"], ex.get("menu_cross_category") or {}
    variant = (blob.get("examples", {}).get("variant")
               or _variant_of(str(blob["reviews"]["best"])))
    single = str(blob["reviews"]["best"]).endswith("_single")
    body = [
        f"리뷰 **MaxSim(`{variant}`)** 유사도로 본 예시 가게 5곳의 이웃 5곳씩. "
        "**Step 1 이 쓴 것과 같은 가게**다 — 같은 가게를 두 modality 로 나란히 보려는 "
        "것이다.",
    ] + ([
        "",
        f"**표를 읽을 때 주의.** 결선에 오른 리뷰 모델은 단일 벡터 대조군"
        f"(`{blob['reviews']['best']}`)이라 '리뷰 쌍' 이라는 것이 없다(가게를 벡터 "
        "하나로 평균냈으므로). 아래 표의 이웃 순서와 '맞은 리뷰 쌍' 은 같은 리뷰 "
        f"집합의 **MaxSim** 으로 계산한 것이다 — 무엇이 무엇과 닮았는지를 문장 "
        "수준에서 보여 줄 수 있는 표현이 그쪽뿐이기 때문이다. 사용자 사례 표의 "
        "**점수와 상위 10곳은 결선 모델의 것**이고, '매칭된 방문 가게' 와 리뷰 쌍만 "
        "MaxSim 으로 고른다.",
    ] if single else []) + [
        "",
        ex["neighbours_md"],
        "",
        f"전역으로도 셌다: 리뷰가 있는 {cross['stores']:,}곳 각각의 최근접 "
        f"{cross['top_k']}곳 중 cat2 가 다른 비율이 **{cross['cross_cat2_rate']:.1%}** "
        f"다(이웃을 무작위로 골랐다면 {cross['random_rate']:.1%}).",
    ]
    if menu_cross:
        d = cross["cross_cat2_rate"] - menu_cross["cross_cat2_rate"]
        verdict = ("리뷰가 메뉴보다 업종을 **더 넘는다**" if d > 0.02 else
                   "리뷰도 메뉴만큼 업종 안에 갇혀 있다" if abs(d) <= 0.02 else
                   "리뷰가 메뉴보다 업종을 **덜 넘는다**")
        body += [
            f"Step 1 의 메뉴 유사도는 같은 지표가 {menu_cross['cross_cat2_rate']:.1%} "
            f"(무작위 {menu_cross['random_rate']:.1%})였다 — 차이 {d:+.1%}. "
            f"**{verdict}.**",
        ]
    body += ["", "### 방문 이력에서 추천까지", "", ex["users_md"]]
    return "\n".join(body)


def _p(results: dict, model: str, n, metric: str) -> dict:
    return results[model][str(n)]["task_p"][metric]


def headline(blob: dict) -> list[str]:
    ns = blob["history_sizes"]
    n5 = str(blob["config"]["sweep_n_history"] if blob["config"]["sweep_n_history"] in ns
             else ns[0])
    res, resb = blob["results"], blob["results_vs_bar"]
    bar, best_text = blob["bar_model"], blob["store_text"]["best"]
    hit, ndcg = f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}"
    out = []

    t = _p(res, best_text, n5, "auc")
    tp = _p(res, best_text, n5, "auc_pm")
    menu = blob["content_model"]
    out.append(
        f"**가게 요약문은 메뉴명보다 인기와 독립적인 신호를 더 갖고 있다** — "
        f"`{best_text}` 의 인기 매칭 AUC(auc_pm) {tp['mean']:.4f} vs `{menu}` "
        f"{_p(res, menu, n5, 'auc_pm')['mean']:.4f} vs `popularity` "
        f"{_p(res, 'popularity', n5, 'auc_pm')['mean']:.4f} (N={n5}). "
        f"전체 AUC 는 {t['mean']:.4f} 로 여전히 `popularity` "
        f"({_p(res, 'popularity', n5, 'auc')['mean']:.4f})에 못 미친다 "
        f"({_verdict(t['vs_baseline'])}).")

    if blob["reviews"]["best"]:
        br = blob["reviews"]["best"]
        r = _p(res, br, n5, "auc")
        head = ("**리뷰 결선에 오른 것은 다중 벡터가 아니라 단일 벡터 대조군 "
                f"`{br}` 다**" if br.endswith("_single")
                else f"**리뷰 다중 벡터 1위는 `{br}`**")
        out.append(
            f"{head} — AUC {r['mean']:.4f}, auc_pm "
            f"{_p(res, br, n5, 'auc_pm')['mean']:.4f}, Hit@{config.HIT_K} "
            f"{_p(res, br, n5, hit)['mean']:.4f}, NDCG@{config.NDCG_K} "
            f"{_p(res, br, n5, ndcg)['mean']:.4f} (N={n5}). "
            f"`popularity` 대비 AUC {_verdict(r['vs_baseline'])}.")

    for model in blob["blend"]["models"].values():
        cells = {m: _p(resb, model, n5, m) for m in METRICS4}
        bits = " · ".join(
            f"{m} {cells[m]['mean']:.4f} ({_fmt_ci(cells[m]['vs_baseline'])})"
            for m in METRICS4)
        n_win = sum(1 for n in ns for m in METRICS4
                    if _p(resb, model, n, m)["vs_baseline"]["significant"]
                    and _p(resb, model, n, m)["vs_baseline"]["diff"] > 0)
        n_loss = sum(1 for n in ns for m in METRICS4
                     if _p(resb, model, n, m)["vs_baseline"]["significant"]
                     and _p(resb, model, n, m)["vs_baseline"]["diff"] < 0)
        out.append(
            f"**`{model}` 대 막대 `{bar}` (N={n5})**: {bits}. 지표 {len(METRICS4)} × "
            f"N {len(ns)} = {len(METRICS4) * len(ns)}칸 중 **{n_win}칸이 유의하게 "
            f"위**, {n_loss}칸이 유의하게 아래다.")

    ex = blob.get("examples") or {}
    if ex:
        cross = ex["cross_category"]
        mc = ex.get("menu_cross_category") or {}
        tail = (f" (메뉴는 {mc['cross_cat2_rate']:.1%})" if mc else "")
        out.append(
            f"**리뷰 유사도의 최근접 5곳 중 cat2 가 다른 비율 "
            f"{cross['cross_cat2_rate']:.1%}**{tail}, 무작위였다면 "
            f"{cross['random_rate']:.1%}. 카테고리를 넘는 취향의 증거로 읽으려면 이 "
            "숫자가 무작위 쪽으로 가야 한다.")
    return out


def learnings(blob: dict) -> str:
    ns = blob["history_sizes"]
    n5 = str(blob["config"]["sweep_n_history"] if blob["config"]["sweep_n_history"] in ns
             else ns[0])
    res, resb = blob["results"], blob["results_vs_bar"]
    bar = blob["bar_model"]
    hit, ndcg = f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}"
    best_text, best_review = blob["store_text"]["best"], blob["reviews"]["best"]
    b = blob["blend"]
    lines = ["### 배운 것", ""]

    d_pm = (_p(res, best_text, n5, "auc_pm")["mean"]
            - _p(res, "popularity", n5, "auc_pm")["mean"])
    d_auc = (_p(res, best_text, n5, "auc")["mean"]
             - _p(res, "popularity", n5, "auc")["mean"])
    lines.append(
        f"- **modality 마다 이기는 지표가 다르다는 Step 1.5 의 관찰이 요약문에서도 "
        f"반복된다.** `{best_text}` 는 auc_pm 에서 `popularity` 를 {d_pm:+.4f} "
        f"앞서지만 전체 AUC 에서는 {d_auc:+.4f} 뒤진다. 인기를 통제하면 콘텐츠가 "
        "이기고, 통제하지 않으면 인기가 이긴다.")

    if best_review:
        rmeta = blob["reviews"]["meta"]
        rows = {r["model"]: r for r in blob["reviews"]["sweep"]}
        variants = list(review_content.VARIANTS)
        by_var = {v: max(rows[f"review_{v}_{a}"][ndcg] for a in REVIEW_AGGS)
                  for v in variants}
        spread_v = max(by_var.values()) - min(by_var.values())
        by_agg = {a: max(rows[f"review_{v}_{a}"][ndcg] for v in variants)
                  for a in REVIEW_AGGS}
        spread_a = max(by_agg.values()) - min(by_agg.values())
        best_single = max(rows[f"review_{v}_single"][ndcg] for v in variants)
        best_multi = max(by_var.values())
        lines.append(
            f"- **어떤 리뷰 12개를 고르나 vs 어떻게 접나.** 선택 변종 사이의 "
            f"NDCG@{config.NDCG_K} 폭은 {spread_v:.4f}, 집계 사이의 폭은 "
            f"{spread_a:.4f} 다 ("
            + ", ".join(f"`{v}` {by_var[v]:.4f}" for v in variants) + "). "
            + ("선택이 집계보다 중요하다" if spread_v > spread_a * 1.5
               else "집계가 선택보다 중요하다" if spread_a > spread_v * 1.5
               else "둘 다 이 정도로는 중요하지 않다 — 어느 쪽으로 바꿔도 폭이 비슷하다")
            + ".")
        multi_pm0 = max(rows[f"review_{v}_{a}"]["auc_pm"]
                        for v in variants for a in REVIEW_AGGS)
        null_auc = max(rows[f"review_null_{a}"]["auc"] for a in REVIEW_AGGS)
        null_ndcg = max(rows[f"review_null_{a}"][ndcg] for a in REVIEW_AGGS)
        best_auc = max(rows[f"review_{v}_{a}"]["auc"]
                       for v in variants for a in REVIEW_AGGS)
        pop_auc = _p(res, "popularity", n5, "auc")["mean"]
        share = (null_auc - 0.5) / max(best_auc - 0.5, 1e-9)
        null_pm = max(rows[f"review_null_{a}"]["auc_pm"] for a in REVIEW_AGGS)
        verdict = (f"**진짜 벡터보다도 높다**(대조군 {null_auc:.4f} > 최고 MaxSim "
                   f"{best_auc:.4f}) — 리뷰 MaxSim 의 AUC 는 내용이 아니라 개수로 "
                   "설명된다" if share >= 1.0 else
                   f"최고 MaxSim {best_auc:.4f} 의 0.5 위쪽 여유 중 **{share:.0%} 를 "
                   "내용 없이 얻는다**")
        lines.append(
            f"- **리뷰 MaxSim 의 AUC 는 '리뷰가 몇 개냐' 다.** 벡터를 난수로 바꾸고 "
            f"개수 구조만 남긴 대조군이 AUC {null_auc:.4f} 를 낸다 — {verdict}. "
            "MaxSim 의 `max_{b∈B}` 가 B 의 크기에 단조증가하는데 리뷰 수가 곧 "
            f"인기이기 때문이다(쓴 벡터 수와 log1p(리뷰 수)의 상관 "
            f"{rmeta.get('count_pop_corr', float('nan')):.2f}, `popularity` 의 AUC 는 "
            f"{pop_auc:.4f}). **인기를 통제하면 이야기가 뒤집힌다**: 같은 대조군의 "
            f"auc_pm 은 {null_pm:.4f}(무작위 이하)이고 진짜 벡터는 {multi_pm0:.4f} 다 "
            "— 리뷰 내용에 신호가 있다는 증거는 AUC 가 아니라 auc_pm 쪽에 있다.")
        single_auc = max(rows[f"review_{v}_single"]["auc"] for v in variants)
        single_pm = max(rows[f"review_{v}_single"]["auc_pm"] for v in variants)
        multi_pm = max(rows[f"review_{v}_{a}"]["auc_pm"]
                       for v in variants for a in REVIEW_AGGS)
        if best_single > best_multi:
            lines.append(
                f"- **PLAN 원칙 1번('가게를 하나의 벡터로 평균내지 않는다')이 리뷰에서는 "
                f"뒤집혔다.** 단일 벡터 대조군이 MaxSim 을 모든 지표에서 이긴다 — "
                f"NDCG@{config.NDCG_K} {best_single:.4f} vs {best_multi:.4f}, "
                f"AUC {single_auc:.4f} vs {best_auc:.4f}, auc_pm {single_pm:.4f} vs "
                f"{multi_pm:.4f}. 리뷰 12개의 평균 벡터는 **개수 편향이 없고**"
                "(정규화된 벡터 1개라 집합 크기가 점수에 안 들어간다) 잡음이 평균으로 "
                "지워진다. late interaction 이 이기려면 집합 안에서 **서로 다른 면**을 "
                "말하는 원소들이어야 하는데, 한 가게의 리뷰 12개는 대체로 같은 말을 "
                "다르게 한 것이다. 메뉴에서는 MaxSim 이 나았다는 Step 1 결과와 반대이고, "
                "그 차이가 바로 '집합이 다양한가' 다.")
        else:
            lines.append(
                f"- **다중 벡터 vs 평균 한 개.** 최고 MaxSim {best_multi:.4f} vs 최고 "
                f"단일 벡터 대조군 {best_single:.4f} (NDCG@{config.NDCG_K}) — "
                "집합으로 두는 것이 낫다.")

    for fitter, model in b["models"].items():
        wins, losses = [], []
        for m in METRICS4:
            for n in ns:
                c = _p(resb, model, n, m)["vs_baseline"]
                if c["significant"]:
                    (wins if c["diff"] > 0 else losses).append(f"{m} N={n}")
        tail = (f" 반대로 **{', '.join(losses)} 에서는 막대에 졌다**(CI)." if losses
                else " 막대에 CI 로 진 칸은 없다.")
        lines.append(
            f"- **블렌드(`{fitter}`)는 막대 `{bar}` 를 {len(wins)}/"
            f"{len(METRICS4) * len(ns)}칸에서 CI 로 넘었다** — "
            + (", ".join(wins) if wins else "넘은 칸이 하나도 없다") + f".{tail} "
            f"N={n5} 기준 AUC {_p(res, model, n5, 'auc')['mean']:.4f} · "
            f"auc_pm {_p(res, model, n5, 'auc_pm')['mean']:.4f} · "
            f"Hit@{config.HIT_K} {_p(res, model, n5, hit)['mean']:.4f} · "
            f"NDCG@{config.NDCG_K} {_p(res, model, n5, ndcg)['mean']:.4f}.")

    # 두 학습법의 차이 자체가 결과다 — 같은 구성요소·같은 폴드인데 성적이 다르다.
    fitters = list(b["models"])
    if len(fitters) == 2:
        a, c = (b["models"][f] for f in fitters)
        d_auc = _p(res, c, n5, "auc")["mean"] - _p(res, a, n5, "auc")["mean"]
        d_ndcg = _p(res, c, n5, ndcg)["mean"] - _p(res, a, n5, ndcg)["mean"]
        lines.append(
            f"- **같은 구성요소인데 λ 를 어떻게 고르느냐로 갈린다.** `{fitters[1]}` − "
            f"`{fitters[0]}` = AUC {d_auc:+.4f} · NDCG@{config.NDCG_K} {d_ndcg:+.4f} "
            f"(N={n5}). 격자 탐욕 선택은 학습 폴드의 NDCG@{config.NDCG_K} 를 직접 "
            "최대화하는데도 쌍대 로지스틱보다 못하다 — 격자가 성기고(5칸), 탐욕이라 "
            "조합을 못 보고, 무엇보다 **NDCG 는 상위 20칸만 보는 지표라 학습 신호가 "
            "얇다**(사용자당 정답 5.9곳, 대부분 20위 밖). 쌍대 손실은 모든 "
            "(양성, 음성) 쌍에서 신호를 받는다.")

    # ablation 에서 실제로 기여한 구성요소만 세어 문장으로 옮긴다. 지표를 하나만
    # 보면(NDCG@20) '아무 것도 기여하지 않는다' 로 읽히는데, 그건 그 지표의 분산이
    # 큰 탓일 수 있다 — 네 지표를 전부 훑는다.
    for fitter in b["models"]:
        bits = []
        for comp, per_n in b["ablation"][fitter].items():
            hit_ms = [m for m in METRICS4
                      for nn in [k for k in per_n]
                      if per_n[nn][m]["significant"] and per_n[nn][m]["diff"] < 0]
            if hit_ms:
                worst = min((per_n[nn][m]["diff"], m, nn)
                            for m in METRICS4 for nn in per_n
                            if per_n[nn][m]["significant"] and per_n[nn][m]["diff"] < 0)
                bits.append(f"`{comp}` ({len(hit_ms)}칸, 최대 {worst[1]} N={worst[2]} "
                            f"{worst[0]:+.4f})")
        lines.append(
            f"- **ablation(`{fitter}`).** 빼면 CI 로 나빠지는(= 기여한) 구성요소: "
            + (", ".join(bits) if bits else "**없다**")
            + f". 지표 {len(METRICS4)}개 × 잰 N 전부를 훑은 것이고, 나머지 칸은 CI 가 "
            "0 을 포함한다 — '기여했다고 말할 수 없다' 이지 '기여가 0 이다' 가 아니다.")

    ex = blob.get("examples") or {}
    if ex:
        cross, mc = ex["cross_category"], ex.get("menu_cross_category") or {}
        if mc:
            d = cross["cross_cat2_rate"] - mc["cross_cat2_rate"]
            lines.append(
                f"- **리뷰가 업종을 더 넘는가.** 최근접 5곳 중 cat2 가 다른 비율은 "
                f"리뷰 {cross['cross_cat2_rate']:.1%}, 메뉴 "
                f"{mc['cross_cat2_rate']:.1%} (무작위 {cross['random_rate']:.1%}). "
                f"차이 {d:+.1%} — "
                + ("리뷰 쪽이 업종 경계를 더 넘지만 무작위와는 여전히 멀다."
                   if d > 0.02 else
                   "두 modality 다 업종 안에 갇혀 있다. 'cat2 를 넘는 취향' 은 아직 "
                   "어느 표현에서도 관측되지 않았다."))

    lines += ["", "### 증명하지 못한 것", ""]
    lines.append("- **CI 가 0 을 포함하는 값은 우위의 근거가 아니다**(PLAN 1절). 위 "
                 "표에서 굵게 표시되지 않은 차이는 전부 여기에 해당한다.")
    for model in list(b["models"].values()):
        lines.append(f"- {significance_line(res, model, ns)}")
    lines += [
        f"- **스윕 1위(`{best_text}`" + (f", `{best_review}`" if best_review else "")
        + ") 는 테스트 사용자 위에서 골랐다.** 5-fold 로 고른 것은 하이브리드의 λ 와 "
        "블렌드의 가중치뿐이다. 스윕에서 고른 modality 변종만큼 낙관 편향이 남아 있다.",
        "- **다중비교 보정을 하지 않았다.** 이 문서에는 모델 × N × 지표로 수백 개의 "
        "칸이 있고, 그 중 몇 칸이 우연히 유의할 수 있다. 개별 칸이 아니라 **여러 칸이 "
        "같은 방향으로 몰려 있는지**만 읽는다.",
        "- **블렌드의 가중치는 사용자별이 아니라 전역이다.** PLAN 3절이 말한 '사용자별 "
        "가중치' 는 여기서 재지 않았다 — 폴드 안의 모든 사용자가 같은 λ 를 쓴다. "
        "사용자별 학습은 Step 4 의 일이고, 이 문서는 그 전제(구성요소 몇 개로 "
        "충분한가)만 확인한다.",
        "- 리뷰 텍스트는 **가게 표현**으로만 썼다. 평가 사용자 본인의 리뷰 원문은 어떤 "
        "모델의 입력도 아니다(PLAN 1절). 제품이 추천 시점에 아는 것은 (가게, 방문) "
        "뿐이라는 계약 그대로다.",
        "- 정답이 카카오 리뷰에서 나왔다는 표본틀 편향은 그대로다. 리뷰 modality 는 "
        "**그 편향과 같은 재료**로 만들어졌다 — 리뷰가 많은 가게가 리뷰 벡터도 많고 "
        "정답이 될 확률도 높다. `auc_pm` 이 그 일부를 통제할 뿐이다.",
    ]
    if blob["reviews"]["meta"].get("available"):
        m = blob["reviews"]["meta"]
        lines.append(
            f"- 후보 풀 {m['stores']:,}곳 중 **{m['zero']}곳은 쓸 수 있는 리뷰가 0건**"
            f"이고 {m['under_k']:,}곳은 12건을 못 채운다. 이 가게들에 대한 리뷰 "
            "modality 의 성능은 이 실험이 재지 못한다.")
    return "\n".join(lines)


def notes(blob: dict) -> list[str]:
    m = blob["reviews"]["meta"]
    out = [
        "**CI 가 0 을 포함하면 우위 미확정이다.** 작은 양수를 근거로 삼지 않는다"
        "(PLAN 1절).",
        f"이력 해석은 **`{blob['history_mode']}`**(방문 = 관심)다 — PLAN 5절 Step 1.5 "
        "결정 1번의 기본값. `--history like_dislike` 로 옛 해석을 재현할 수 있다.",
        f"기준이 **둘**이다. '과제 P' 표의 CI 는 `popularity` 대비, '결선' 표의 CI 는 "
        f"막대 `{blob['bar_model']}` 대비다. 같은 숫자를 두 잣대로 본 것이지 다른 "
        "실험이 아니다.",
        f"막대(`{blob['bar_model']}`)는 이 실행에서 **다시 계산했다** — 같은 seed·같은 "
        "분할이므로 step1_5-visit-cf 의 값과 같아야 한다. 다르면 둘 중 하나가 깨진 것이다.",
        "막대의 이름에 `_cv_ndcg20` 이 붙는 것은 λ 를 폴드 밖에서 고른 모델이기 "
        "때문이다. `analysis.backtest.run --ref` 로 같은 비교를 하려면 λ 가 박힌 이름"
        "(`hybrid_pop_cf_item_sum_l0.5`)을 써야 한다 — `hybrid_pop_cf_item_sum` 은 "
        "λ 없이는 이름 규칙에 걸리지 않아 모델로 풀리지 않는다.",
        "하이브리드와 블렌드의 z 정규화는 **그 사용자의 후보**(이미 간 가게를 뺀 나머지) "
        "안에서 한다. 미래(정답) 정보는 들어가지 않는다.",
        "블렌드의 가중치는 **폴드 밖에서** 학습한다(`blend.outer_cv_blend`). 사용자마다 "
        "자기가 학습에 참여하지 않은 모델의 점수가 남으므로 다른 모델과 그대로 paired "
        "bootstrap 에 넣을 수 있다.",
        "BLAS 스레드 수에 따라 부동소수 합의 순서가 달라져 마지막 자리가 흔들릴 수 있다. "
        "이 실행은 임베딩 작업과 코어를 나눠 쓰느라 1스레드로 돌렸다.",
    ]
    if m.get("available"):
        out.insert(3, f"가게 표현에 쓴 리뷰에서 평가 사용자 {m['excluded_authors']}명의 "
                      f"리뷰 {m['excluded_reviews']:,}건을 **전부** 뺐다. 제외는 "
                      "`ReviewSpace.build` 한 곳에서만 일어나고 테스트가 그것을 "
                      "검증한다.")
    return out


def _prose(blob: dict) -> dict:
    """숫자는 그대로 두고 문장·표만 다시 만든다(`--rebuild-md` 가 이것만 부른다)."""
    blob["headline"] = headline(blob)
    blob["sections_before"] = [
        {"heading": "가게 요약문 — 한 문장짜리 modality", "body": text_section(blob)},
        {"heading": "리뷰 다중 벡터 — 어떤 12개를 고르나", "body": review_section(blob)},
    ]
    blob["sections"] = [
        {"heading": "결선 — 막대(CF 하이브리드) 대비", "body": finalist_section(blob)},
        {"heading": "구성요소 블렌드", "body": blend_section(blob)},
        {"heading": "사례 — 리뷰가 무엇을 맞췄나", "body": examples_section(blob)},
        {"heading": "배운 것 / 증명하지 못한 것", "body": learnings(blob)},
    ]
    blob["notes"] = notes(blob)
    return blob


def _write(blob: dict, out: str) -> None:
    blob = _prose(blob)
    out_dir = Path(config.RESULTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{out}.json").write_text(
        json.dumps(blob, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8")
    (out_dir / f"{out}.md").write_text(report.to_markdown(blob), encoding="utf-8")
    print(f"기록: {out_dir / (out + '.json')}\n      {out_dir / (out + '.md')}",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
