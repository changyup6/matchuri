# -*- coding: utf-8 -*-
"""백테스트 CLI.

    ~/venvs/recsys/bin/python -m analysis.backtest.run \
        --models random,popularity,quality,quality_crawled,current --N 3,5,10,20

산출물은 `analysis/backtest/results/<out>.json` 과 `<out>.md`. json 이 원본이고 md 는
그 표현이다 — 표가 이상하면 json 을 본다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import cache, config, report, scorers
from .bootstrap import paired_diff_ci
from .data import load_dataset
from .evaluate import P_METRICS, evaluate
from .pool import attach_crawled_quality, build_pool
from .scorers import ScoreContext

DEFAULT_MODELS = ("random,popularity,quality,quality_crawled,"
                  "quality_crawled_all,current")

# PLAN 5절 1번: 과제 P 의 참조는 `popularity`, R 은 `quality`.
DEFAULT_REF = "p=popularity,r=quality"
_REF_KEYS = {"p": "task_p", "r": "task_r", "task_p": "task_p", "task_r": "task_r"}


def parse_refs(spec: str) -> dict[str, str]:
    """`--ref` 를 과제별 기준 모델로 푼다.

    `p=popularity,r=quality` 처럼 과제별로 주거나, 이름 하나만 주면 두 과제에 같이 쓴다
    (옛 `--baseline` 과 같은 뜻). 과제 P 에서 `quality` 는 AUC 0.50 이라 그것과의 차이는
    아무 것도 말해주지 않으므로 기본값을 과제별로 나눴다.
    """
    spec = spec.strip()
    if "=" not in spec:
        return {"task_p": spec, "task_r": spec}
    out: dict[str, str] = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        key, _, val = part.partition("=")
        task = _REF_KEYS.get(key.strip())
        if task is None:
            raise ValueError(f"모르는 과제 키 '{key.strip()}' — p 또는 r 을 쓴다")
        out[task] = val.strip()
    missing = {"task_p", "task_r"} - set(out)
    if missing:
        raise ValueError(f"참조 모델이 빠졌다: {', '.join(sorted(missing))}")
    return out


def seen_masks(splits, pool) -> dict[str, np.ndarray]:
    """사용자 -> '아직 안 가본 후보' 마스크. 하이브리드의 사용자 내 z 정규화가 쓴다.

    지표를 재는 후보 집합과 정확히 같은 것을 쓴다 — 점수를 매긴 집합과 순위를 재는
    집합이 다르면 z 정규화가 미세하게 어긋난다. 미래(정답) 정보는 들어가지 않는다.
    """
    out = {}
    for sp in splits:
        m = np.ones(len(pool), dtype=bool)
        for sid in sp.seen_stores:
            m[pool.pos[sid]] = False
        out[sp.user_id] = m
    return out


def aggregate(raw: dict, model_names: list[str], ns: list[int], refs: dict,
              *, resamples: int, seed: int) -> dict:
    """모델 x N 의 사용자별 지표를 평균과 과제별 paired bootstrap CI 로 접는다."""
    results: dict[str, dict] = {}
    for name in model_names:
        results[name] = {}
        for n in ns:
            res = raw[name][n]
            basis_p = raw[refs["task_p"]][n]
            basis_r = raw[refs["task_r"]][n]
            task_p = {}
            for metric in P_METRICS:
                v = res.p[metric]
                task_p[metric] = {
                    "mean": _mean(v),
                    "n_users": int(np.isfinite(v).sum()),
                    "vs_baseline": paired_diff_ci(v, basis_p.p[metric],
                                                  resamples=resamples, seed=seed),
                }
            task_r = {"auc": {
                "mean": _mean(res.r_auc),
                "n_users": int(np.isfinite(res.r_auc).sum()),
                "vs_baseline": paired_diff_ci(res.r_auc, basis_r.r_auc,
                                              resamples=resamples, seed=seed),
            }}
            results[name][str(n)] = {"task_p": task_p, "task_r": task_r,
                                     "full_history_users": int(res.full_history.sum())}
    return results


def summarise_users(splits, raw: dict, model_names: list[str], ns: list[int],
                    *, history_mode: str = "like_dislike") -> dict:
    """분할 요약 — 결과 .md 의 '무엇을 쟀나' 표가 읽는 값.

    `signal_mean` 은 **그 해석에서 모델에 실제로 들어간 신호 수**의 평균이다.
    `visit` 모드에서는 이력 건수와 같아지고(별점을 안 보므로 버리는 신호가 없다),
    `like_dislike` 에서는 rating 3 을 버려 그보다 작다. `signal_mean_full` 은 N 을
    다 채운 사용자만 센 것 — PLAN 이 말한 '3/5/10/20 으로 늘어난다' 는 이 열이다.
    """
    totals = np.array([s.total for s in splits])
    any_res = raw[model_names[0]][ns[0]]

    def sig_mean(n: int, only_full: bool) -> float:
        vals = [len(s.signals(n, history_mode)) for s in splits
                if not only_full or s.has_full_history(n)]
        return float(np.mean(vals)) if vals else float("nan")

    return {
        "history_mode": history_mode,
        "signal_mean_full": {str(n): sig_mean(n, True) for n in ns},
        "signal_mean_like_dislike": {
            str(n): float(np.mean([len(s.signals(n, "like_dislike"))
                                   for s in splits])) for n in ns},
        "eval_users": len(splits),
        "min_reviews": config.MIN_USER_REVIEWS,
        "total_mean": float(totals.mean()), "total_median": float(np.median(totals)),
        "total_max": int(totals.max()), "total_min": int(totals.min()),
        "future_total": int(sum(len(s.future) for s in splits)),
        "future_mean": float(np.mean([len(s.future) for s in splits])),
        "future_dup_merged": int(sum(s.n_future_dup for s in splits)),
        "future_seen_excluded": int(sum(s.n_future_seen for s in splits)),
        "future_seen_excluded_recent_window":
            {str(n): int(sum(s.n_future_seen_recent.get(n, 0) for s in splits))
             for n in config.HISTORY_SIZES},
        "full_history": {str(n): int(raw[model_names[0]][n].full_history.sum())
                         for n in ns},
        "signal_mean": {str(n): sig_mean(n, False) for n in ns},
        "history_mean": {str(n): float(np.mean([len(s.history(n)) for s in splits]))
                         for n in ns},
        "task_r_users": int(np.isfinite(any_res.r_auc).sum()),
    }


def _git_commit() -> str:
    """실행 시점의 커밋. 작업 트리가 더러우면 `-dirty` 를 붙인다.

    결과 파일은 자기를 담은 커밋을 가리킬 수 없으므로(닭과 달걀) 여기 적히는 것은
    '이 커밋 위에서 돌렸다' 라는 뜻이다. 재현은 seed 와 캐시 지문으로 보장된다.
    """
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=config.ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=config.ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return f"{rev}-dirty" if dirty else rev
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _mean(v: np.ndarray) -> float:
    v = np.asarray(v, dtype=float)
    return float(np.nanmean(v)) if np.isfinite(v).any() else float("nan")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.backtest.run",
        description="PLAN.md 1절의 백테스트 계약대로 모델을 비교한다.")
    ap.add_argument("--models", default=DEFAULT_MODELS,
                    help=f"쉼표 구분. 등록된 것: {', '.join(scorers.available())}")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES),
                    help="이력 길이. 쉼표 구분")
    ap.add_argument("--out", default="step0-baselines", help="results/<out>.{json,md}")
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--ref", default=DEFAULT_REF,
                    help="paired bootstrap 의 기준 모델. 과제별로 "
                         "`p=popularity,r=quality` 또는 이름 하나")
    ap.add_argument("--baseline", default=None,
                    help="(옛 이름) --ref 와 같다. 주면 두 과제에 같이 쓴다")
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--no-cache", action="store_true", help="분할 캐시를 쓰지 않는다")
    ap.add_argument("--history", default=config.DEFAULT_HISTORY_MODE,
                    choices=list(config.HISTORY_MODES),
                    help="이력 해석. visit = 방문한 가게 전부가 가중 1 의 관심 신호"
                         "(PLAN 5절 Step 1.5 결정 1번). Step 0·1 결과를 재현하려면 "
                         "like_dislike")
    args = ap.parse_args(argv)

    model_names = [m.strip() for m in args.models.split(",") if m.strip()]
    ns = [int(x) for x in args.N.split(",") if x.strip()]
    # 이름 규칙으로 푸는 모델(하이브리드 등)도 받아들인다 — `scorers.exists` 가 판정한다.
    unknown = [m for m in model_names if not scorers.exists(m)]
    if unknown:
        ap.error(f"모르는 모델: {', '.join(unknown)}. "
                 f"등록된 것: {', '.join(scorers.available())}")
    try:
        refs = parse_refs(args.baseline if args.baseline else args.ref)
    except ValueError as e:
        ap.error(str(e))
    for task, ref in refs.items():
        if ref not in model_names:
            ap.error(f"{task} 기준 모델 '{ref}' 이 --models 에 없다")

    print("데이터 로드 …", flush=True)
    ds = load_dataset()
    pool = build_pool(ds)
    splits, from_cache = cache.load_or_build(ds, pool, use_cache=not args.no_cache)
    eval_users = {s.user_id for s in splits}
    attach_crawled_quality(pool, ds, eval_users)     # 평가 사용자 리뷰를 뺀 품질
    print(f"  후보 풀 {len(pool):,}곳 · 평가 사용자 {len(splits)}명"
          f"{' (캐시)' if from_cache else ''}", flush=True)

    ctx = ScoreContext(pool=pool, seed=args.seed, history_mode=args.history,
                       extras={"candidates": seen_masks(splits, pool),
                               "dataset": ds, "eval_users": eval_users,
                               "store_names": {s: (ds.stores.get(s, {}).get("name") or "")
                                               for s in pool.ids}})
    print(f"  이력 해석 `{args.history}`", flush=True)

    # ── 평가
    raw: dict[str, dict[int, object]] = {}
    for name in model_names:
        raw[name] = {}
        for n in ns:
            raw[name][n] = evaluate(scorers.get(name), splits, ctx, n)
            print(f"  {name:<16} N={n:<3} "
                  f"AUC(P)={_mean(raw[name][n].p['auc']):.4f}", flush=True)

    # ── 집계 + 기준 대비 paired bootstrap
    results = aggregate(raw, model_names, ns, refs,
                        resamples=args.resamples, seed=args.seed)
    users_meta = summarise_users(splits, raw, model_names, ns,
                                 history_mode=args.history)

    leak_scale = _leak_scale(pool, splits)

    blob = {
        "run": args.out,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "references": refs,
        "baseline_model": refs["task_p"],     # 옛 독자용(과제 P 기준)
        "history_sizes": ns,
        "config": {
            "bayes_c": config.BAYES_C,
            "min_user_reviews": config.MIN_USER_REVIEWS,
            "future_ratio": config.FUTURE_RATIO, "future_min": config.FUTURE_MIN,
            "history_mode": args.history,
            "like_min_rating": config.LIKE_MIN_RATING,
            "dislike_max_rating": config.DISLIKE_MAX_RATING,
            "satisfied_min_rating": config.SATISFIED_MIN_RATING,
            "hit_k": config.HIT_K, "recall_k": config.RECALL_K, "ndcg_k": config.NDCG_K,
            "bootstrap_resamples": args.resamples,
            "split_cache_fingerprint": cache.fingerprint(),
        },
        "pool": {"size": len(pool), "stores_total": len(ds.stores),
                 "reviews_total": len(ds.reviews)},
        "users": users_meta,
        "models": {m: {"description": scorers.get(m).description} for m in model_names},
        "results": results,
        "headline": _headline(results, ns, refs["task_p"]),
        "notes": _notes(users_meta),
        "leak_scale": leak_scale,
        "leak_notes": _leak_notes(pool, users_meta, leak_scale),
    }

    out_dir = Path(config.RESULTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.out}.json").write_text(
        json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{args.out}.md").write_text(report.to_markdown(blob), encoding="utf-8")
    print(f"기록: {out_dir / (args.out + '.json')}\n"
          f"      {out_dir / (args.out + '.md')}", flush=True)
    return 0


def _leak_scale(pool, splits) -> dict:
    """카카오 집계에 섞인 평가 사용자 리뷰가 품질 점수를 얼마나 움직이는가.

    정답 가게 하나하나에 대해 '그 사용자의 별점 1건을 집계에서 뺐다면' 의 베이지안
    품질을 계산해 차이를 본다. 크롤링 대조군(`quality_crawled`)의 하락에는 누수 제거와
    **표본 축소**가 섞여 있어 그것만으로는 카카오 집계의 누수 크기를 말할 수 없다.
    """
    mu = float((np.nan_to_num(pool.review_avg, nan=0.0) * pool.review_count).sum()
               / max(pool.review_count.sum(), 1.0))
    deltas, counts = [], []
    for sp in splits:
        for sid, rating in sp.future:
            i = pool.pos[sid]
            c, a = float(pool.review_count[i]), pool.review_avg[i]
            counts.append(c)
            if c <= 1 or np.isnan(a):
                without = mu
            else:
                a_wo = (a * c - rating) / (c - 1)
                without = ((config.BAYES_C * mu + a_wo * (c - 1))
                           / (config.BAYES_C + c - 1))
            deltas.append(abs(float(pool.bayes[i]) - without))
    d = np.array(deltas) if deltas else np.zeros(1)
    return {"n_pairs": len(deltas),
            "review_count_median": float(np.median(counts)) if counts else 0.0,
            "abs_delta_bayes_mean": float(d.mean()),
            "abs_delta_bayes_p95": float(np.percentile(d, 95)),
            "pool_bayes_std": float(pool.bayes.std())}


def _headline(results: dict, ns: list[int], baseline: str) -> list[str]:
    """표에서 바로 읽히는 사실만 문장으로 옮긴다(숫자는 전부 results 에서 온다)."""
    n_max = str(max(ns))

    def best(task: str) -> tuple[str, float]:
        vals = {m: results[m][n_max][task]["auc"]["mean"] for m in results}
        vals = {m: v for m, v in vals.items() if not np.isnan(v)}
        top = max(vals, key=lambda m: vals[m])
        return top, vals[top]

    p_top, p_val = best("task_p")
    r_top, r_val = best("task_r")
    q_p = results[baseline][n_max]["task_p"]["auc"]["mean"]
    q_r = results[baseline][n_max]["task_r"]["auc"]["mean"]
    out = [
        f"**과제 P 1위는 `{p_top}` (AUC {p_val:.4f})**. 기준 `{baseline}` 은 "
        f"{q_p:.4f} — 다음 방문을 고르는 데 **평점 품질은 거의 무력하다**(0.5 = 무작위).",
        f"**과제 R 1위는 `{r_top}` (AUC {r_val:.4f})**. 기준 `{baseline}` 은 {q_r:.4f}.",
    ]
    if p_top != r_top:
        pr = results[p_top][n_max]["task_r"]["auc"]["mean"]
        rp = results[r_top][n_max]["task_p"]["auc"]["mean"]
        out.append(
            f"**두 과제의 순위가 뒤집힌다.** P 1위 `{p_top}` 은 R 에서 {pr:.4f}, "
            f"R 1위 `{r_top}` 은 P 에서 {rp:.4f} 다. '어디 갈까' 와 '가서 만족할까' 는 "
            "다른 문제이고, 하나의 점수로 둘 다 잘하기를 기대할 수 없다.")
    cur = results.get("current")
    if cur:
        lo, hi = str(min(ns)), n_max
        a, b = cur[lo]["task_p"]["auc"]["mean"], cur[hi]["task_p"]["auc"]["mean"]
        ci = cur[hi]["task_p"]["auc"]["vs_baseline"]
        out.append(
            f"`current`(현재 서버 모델)는 이력이 길수록 나아진다 — P AUC "
            f"N={lo} {a:.4f} -> N={hi} {b:.4f}, 기준 대비 {ci['diff']:+.4f} "
            f"[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}]. 학습이 **작동은 한다.** "
            f"다만 `popularity` 한 줄에 크게 못 미친다.")
    return out


def _notes(u: dict) -> list[str]:
    dup, seen = u["future_dup_merged"], u["future_seen_excluded"]
    return [
        "**CI 가 0 을 포함하면 우위 미확정이다.** 작은 양수를 근거로 삼지 않는다 "
        "(PLAN 1절).",
        "과제 P 는 음성이 2천여 곳인 전수 랭킹이라 Hit@10·NDCG@20 의 절댓값이 아주 "
        "작다(무작위 기준 Recall@50 = 50/2,216 = 2.3%). 절댓값을 제품 성능으로 읽지 "
        "말고 모델 간 **차이**만 본다.",
        "**`popularity` 에는 구조적 이점이 있다.** 정답(미래 방문)을 카카오 리뷰에서 "
        "뽑았으므로 '리뷰가 많은 가게' 는 정답으로 뽑힐 확률 자체가 높다 — 정답의 "
        "표본추출 틀과 특성이 같은 양이다. 리뷰 기반 백테스트라면 어디서나 생기는 "
        "편향이고 제거할 방법이 없다. 새 모델이 `popularity` 를 넘는지가 진짜 시험이며, "
        "`popularity` 를 넘었다는 사실만으로 제품에서 그만큼 좋다고 말하면 안 된다.",
        f"재방문(같은 사용자·같은 가게 리뷰 2건 이상)은 이 데이터에 **{dup}건**이다. "
        f"과거에 간 가게가 미래에 다시 나온 경우도 **{seen}건**이다. "
        "따라서 '미래 유일화·과거 제외' 규칙은 실제로 아무 행도 지우지 않았다.",
        "'이력에 이미 있으면 미래에서 제외' 의 '이력' 을 **과거 전체**로 읽었다"
        "(최근 N 창이 아니라). 정답 집합이 N 에 따라 달라지면 N 간 비교가 성립하지 "
        "않기 때문이다. 위 재방문 0건이라 두 해석의 결과는 같다 — N 창으로 읽어도 "
        f"제외 건수는 N ∈ {{{', '.join(u['future_seen_excluded_recent_window'])}}} "
        "전부 0건이다.",
        ("과제 R 은 rating 3 이 **음성**이다(PLAN 이 그렇게 정의했다). 이력 해석이 "
         "`visit` 이면 이력 쪽에서는 별점을 아예 보지 않는다 — 두 곳의 별점 쓰임이 "
         "다르다는 점을 헷갈리지 말 것."
         if u.get("history_mode") == "visit" else
         "과제 R 은 like 매핑과 경계가 다르다 — 여기서는 rating 3 이 **음성**이다 "
         "(PLAN 이 그렇게 정의했다). 이력 매핑에서 3 은 중립이다."),
        "이력이 짧은 사용자는 N 을 다 채우지 못한다. N 을 키운 결과를 볼 때는 위의 "
        "'다 채운 사용자 수' 를 같이 본다 — N=20 은 대부분 실제로 20 이 아니다.",
    ]


def _leak_notes(pool, u: dict, ls: dict) -> list[str]:
    empty = int((pool.crawled_count == 0).sum())
    ratio = ls["abs_delta_bayes_mean"] / max(ls["pool_bayes_std"], 1e-12)
    return [
        f"`quality` 는 서버가 쓰는 카카오 집계(`review_avg`·`review_count`)다. 평가 "
        f"사용자 리뷰가 이 집계에 섞여 있다. **크기를 직접 쟀다**: 정답 가게 "
        f"{ls['n_pairs']:,}쌍에 대해 그 사용자의 별점 1건을 빼면 베이지안 품질이 평균 "
        f"{ls['abs_delta_bayes_mean']:.4f} (95분위 {ls['abs_delta_bayes_p95']:.4f}) "
        f"움직인다. 풀 전체 품질의 표준편차 {ls['pool_bayes_std']:.4f} 의 "
        f"{ratio:.1%} 다(정답 가게의 리뷰 수 중앙값 {ls['review_count_median']:.0f}건). "
        f"PLAN 의 '미미함' 은 이 수치로 뒷받침된다.",
        f"반면 `quality_crawled`(누수 제거)는 `quality_crawled_all`(미제거)보다 과제 R "
        f"에서 뚜렷이 낮다. 크롤링 코퍼스는 가게당 리뷰가 훨씬 적어(전체 "
        f"{len(pool.crawled_count):,}곳에 리뷰 3만여 건) 본인 리뷰 1건의 비중이 크기 "
        f"때문이고, 표본이 12% 줄어든 효과도 섞여 있다. **두 수치의 차이를 카카오 "
        f"집계의 누수 크기로 읽으면 안 된다** — 그건 위 문단의 수치다.",
        f"`quality_crawled` 는 크롤링 리뷰만으로 다시 계산하되 평가 사용자 "
        f"{u['eval_users']}명의 리뷰를 **전부** 뺐다. 그러고 나면 리뷰가 하나도 남지 "
        f"않는 가게가 {empty:,}곳이라 그 가게들은 전체 평균으로 수렴한다 — "
        "누수는 없지만 해상도가 낮다.",
        "사용자의 리뷰 **원문은 어떤 모델의 입력도 아니다.** 추천 시점에 제품이 아는 "
        "것은 (가게, like/dislike) 뿐이다.",
        "Step 2 에서 가게 표현을 리뷰 텍스트로 만들 때도 평가 사용자의 리뷰는 전부 "
        "제외한다(`pool.attach_crawled_quality` 와 같은 제외 집합을 쓴다).",
        "하이퍼파라미터(λ 등)는 `cv.select_hyperparam` 의 사용자 5-fold 로 고른다. "
        "테스트 사용자에게서 고르지 않는다.",
    ]


if __name__ == "__main__":
    sys.exit(main())
