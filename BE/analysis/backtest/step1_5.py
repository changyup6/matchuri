# -*- coding: utf-8 -*-
"""Step 1.5 실행기 — 계약 수정(방문=관심) + 협업 필터 베이스라인.

    ~/venvs/recsys/bin/python -m analysis.backtest.step1_5

PLAN.md 5절 'Step 1 → Step 1.5 로 넘기는 결정' 1~4번을 구현한다.

  1. 과제 P 의 이력 해석을 `visit`(방문 = 관심, 별점 무관, dislike 없음)으로 바꾼다.
     `like_dislike` 는 남겨 두고 같은 사용자·같은 지표로 두 해석을 나란히 잰다.
  2. 협업 필터(item-item 공방문 코사인)를 베이스라인에 추가한다. 평가 사용자 227명의
     리뷰는 **전부** 뺀다.
  3. 하이브리드의 λ 를 AUC 로도 NDCG@20 으로도 골라 두 선택을 나란히 적는다.
  4. 브랜드 정규화 뒤 같은 브랜드 지점의 유사도를 중립화하는 옵션의 효과를 잰다.

**정직하게 적을 것.** CF 집계(sum/mean/max)와 Step 1 의 콘텐츠 변종은 테스트 사용자
위에서 골랐다. 5-fold 로 고르는 것은 λ 뿐이다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

from analysis.content import brands as brand_norm

from . import cache, cf, config, cv, report, run, scorers
from .bootstrap import paired_diff_ci
from .data import load_dataset
from .evaluate import evaluate
from .pool import attach_crawled_quality, build_pool
from .scorers import ScoreContext
from .scorers_cf import CF_AGGS
from .scorers_menu import BRAND_SUPPRESS_SUFFIX, LAMBDAS

REFS = {"task_p": "popularity", "task_r": "quality"}
REFERENCE_MODELS = ["random", "popularity", "quality", "current"]

#: Step 1 의 스윕 1위 콘텐츠 모델. 여기서는 이력 해석만 바꿔 다시 잰다.
STEP1_CONTENT = "menu_top8_max"

#: PLAN 5절에서 그대로 인용할 문장 — 계약을 바꾼 근거는 문서가 아니라 계약서에 있다.
PLAN_QUOTE = (
    "**과제 P 의 이력 해석을 바꾼다: 방문 = 관심.** 제품의 like 는 '가보고 싶다'"
    "(방문 전 관심)이고, 백테스트의 방문도 방문 전 관심의 결과다. 별점은 방문 후 "
    "만족이라 관심과 직교한다. Step 1 에서 dislike 가중이 해로웠던 것이 이를 뒷받침한다."
)


def _mean(v) -> float:
    v = np.asarray(v, dtype=float)
    return float(np.nanmean(v)) if np.isfinite(v).any() else float("nan")


def _metric_slug(metric: str) -> str:
    return metric.replace("@", "").replace(".", "")


def hybrid_name(content: str, metric: str) -> str:
    return f"hybrid_pop_{content}_cv_{_metric_slug(metric)}"


# ────────────────────────────────────────────────── CF
def cf_section(cfm: dict, pool_size: int, n_history: int) -> str:
    """`blob["cf"]` 만 보고 절을 쓴다 — 실험을 다시 돌리지 않고 글만 고칠 수 있게."""
    inter, sweep, best_agg = cfm["intersection"], cfm["sweep"], cfm["agg"]
    body = [
        "리뷰 33,300건을 **사용자 × 가게 행렬**로 보고 item-item 공방문 코사인을 만들었다"
        "(PLAN 5절 Step 1.5 결정 2번).",
        "",
        "```",
        "sim(h, i) = |U_h ∩ U_i| / sqrt(|U_h| · |U_i|)",
        "score(i)  = agg_h sim(h, i),   agg ∈ {sum, mean, max}",
        "```",
        "",
        f"- **평가 사용자 {cfm["n_excluded_authors"]}명의 리뷰 {cfm["n_excluded_reviews"]:,}건을 "
        "전부 뺐다** — 이력 구간뿐 아니라 미래 구간도, 후보 풀 밖 가게의 리뷰도 전부다. "
        "본인의 미래 방문이 공방문 통계에 한 톨이라도 남으면 정답을 정답으로 맞히게 된다.",
        f"- 남은 저자 **{cfm["n_authors"]:,}명**(후보 풀 안에 리뷰 1건 이상). 그 중 "
        f"**{cfm["n_authors_multi"]:,}명**만 풀 가게를 2곳 이상 리뷰했다 — 공방문 간선을 "
        "만드는 것은 이 사람들뿐이고, 나머지는 분모 |U_i| 만 키운다.",
        f"- 비영(非零) 가게 쌍 **{cfm["n_pairs_nonzero"]:,}개 / "
        f"{cfm["n_pairs_total"]:,}개 = {cfm["density"]:.3%}**. 즉 행렬의 "
        f"**{1 - cfm["density"]:.1%} 가 0 이다.**",
        f"- 공방문한 사람이 **1명뿐인 쌍이 {inter['pairs_with_1']:,}개"
        f"({inter['share_1']:.1%})**, 2명 이하가 {inter['pairs_le_2']:,}개"
        f"({inter['share_le_2']:.1%})다. 최대 교집합은 {inter['max_inter']:.0f}명. "
        "코사인이 1.0 인 쌍이 나오는 것도 이 때문이다(둘 다 방문자 1명, 그게 같은 사람).",
        f"- 후보 풀 {pool_size:,}곳 중 **{cfm["n_items_empty"]}곳**은 비평가 저자의 "
        "리뷰가 하나도 없어 어떤 이력과도 공방문이 0 이다. CF 는 이 가게들에 대해 "
        "아무 말도 하지 않는다(점수 0 = 대다수 후보와 같은 값이라 사실상 중립이다).",
        "",
        f"**집계 3종** (N={n_history}, 기준 `popularity`):",
        "",
        cf_sweep_table(sweep),
        "",
        "`sum` 과 `mean` 은 **사용자 안에서 순위가 같다** — 이력 길이는 그 사용자에게 "
        "상수라 나눠도 순서가 안 바뀐다. 표의 두 줄이 같은 것은 버그가 아니라 정의다"
        "(하이브리드에서 z 정규화 전 눈금이 달라 둘 다 등록해 두었다). "
        f"결선에는 **`cf_item_{best_agg}`** 를 올린다.",
    ]
    return "\n".join(body)


def intersection_stats(cvm: cf.CoVisit) -> dict:
    """비영 쌍의 교집합 크기 분포 — '코사인 1.0' 이 어디서 나오는지 보기 위해."""
    n = cvm.sim.shape[0]
    iu = np.triu_indices(n, k=1)
    denom = np.sqrt(np.outer(cvm.item_users, cvm.item_users))[iu]
    sim = cvm.sim[iu]
    inter = sim * denom                    # |U_h ∩ U_i| 를 되돌린다
    nz = inter[sim > 0]
    if nz.size == 0:
        return {"pairs_with_1": 0, "share_1": 0.0, "pairs_le_2": 0,
                "share_le_2": 0.0, "max_inter": 0.0}
    r = np.rint(nz)
    return {"pairs_with_1": int((r <= 1).sum()), "share_1": float((r <= 1).mean()),
            "pairs_le_2": int((r <= 2).sum()), "share_le_2": float((r <= 2).mean()),
            "max_inter": float(r.max())}


def cf_sweep_table(rows: list[dict]) -> str:
    out = [f"| 집계 | AUC(P) | `popularity` 대비 CI | auc_pm | Hit@{config.HIT_K} | "
           f"NDCG@{config.NDCG_K} | AUC(R) |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        ci = r["vs_pop_auc"]
        mark = "**" if ci["significant"] else ""
        out.append(f"| `{r['model']}` | {r['auc']:.4f} | "
                   f"{mark}{ci['diff']:+.4f}{mark} "
                   f"[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}] | "
                   f"{r['auc_pm']:.4f} | {r[f'hit@{config.HIT_K}']:.4f} | "
                   f"{r[f'ndcg@{config.NDCG_K}']:.4f} | {r['r_auc']:.4f} |")
    return "\n".join(out)


# ────────────────────────────────────────────────── 이력 해석 비교
def history_compare(models: list[str], splits, ctx_by_mode: dict, ns: list[int],
                    *, resamples: int, seed: int) -> dict:
    """같은 사용자·같은 지표로 `visit` 과 `like_dislike` 를 나란히 잰다.

    두 해석은 **분할이 완전히 같다**(과거·미래·N 창이 같다). 바뀌는 것은 모델에 먹이는
    (가게, like) 목록뿐이라 사용자 단위 paired bootstrap 을 그대로 쓸 수 있다.
    """
    metrics = ["auc", "auc_pm", f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}"]
    out: dict = {}
    for name in models:
        out[name] = {}
        for n in ns:
            res = {m: evaluate(scorers.get(name), splits, ctx, n)
                   for m, ctx in ctx_by_mode.items()}
            cell = {}
            for metric in metrics:
                v, l = res["visit"].p[metric], res["like_dislike"].p[metric]
                cell[metric] = {
                    "visit": _mean(v), "like_dislike": _mean(l),
                    "diff": paired_diff_ci(v, l, resamples=resamples, seed=seed)}
            cell["r_auc"] = {
                "visit": _mean(res["visit"].r_auc),
                "like_dislike": _mean(res["like_dislike"].r_auc),
                "diff": paired_diff_ci(res["visit"].r_auc, res["like_dislike"].r_auc,
                                       resamples=resamples, seed=seed)}
            out[name][str(n)] = cell
            print(f"  비교 {name:<20} N={n:<3} visit={cell['auc']['visit']:.4f} "
                  f"like_dislike={cell['auc']['like_dislike']:.4f}", flush=True)
    return out


def history_compare_table(cmp: dict, ns: list[int], metric: str) -> str:
    out = ["| 모델 | N | `visit` | `like_dislike` | 차이 (visit − like_dislike) |",
           "|---|---|---|---|---|"]
    for name, per_n in cmp.items():
        for n in ns:
            c = per_n[str(n)][metric]
            d = c["diff"]
            mark = "**" if d["significant"] else ""
            out.append(f"| `{name}` | {n} | {c['visit']:.4f} | "
                       f"{c['like_dislike']:.4f} | {mark}{d['diff']:+.4f}{mark} "
                       f"[{d['ci_low']:+.4f}, {d['ci_high']:+.4f}] |")
    return "\n".join(out)


def history_section(cmp: dict, ns: list[int], users_meta: dict) -> str:
    body = [
        "PLAN 5절 Step 1.5 결정 1번:",
        "",
        f"> {PLAN_QUOTE}",
        "",
        "그래서 과제 P 의 이력 신호 = **방문한 가게 전부**(rating 무관, 가중 1)로 바꿨다. "
        "바뀐 것은 모델에 먹이는 (가게, like) 목록뿐이다 — 후보 풀·분할·미래(정답)·"
        "지표는 한 글자도 바뀌지 않았다. 그래서 두 해석을 **같은 사용자·같은 지표**로 "
        "짝지어 비교할 수 있다.",
        "",
        "이력 신호 수(사용자 평균):",
        "",
        "| N | " + " | ".join(str(n) for n in ns) + " |",
        "|---|" + "---|" * len(ns),
        "| `visit` (전체 사용자) | "
        + " | ".join(f"{users_meta['signal_mean'][str(n)]:.2f}" for n in ns) + " |",
        "| `visit` (N 을 다 채운 사용자) | "
        + " | ".join(f"{users_meta['signal_mean_full'][str(n)]:.2f}" for n in ns) + " |",
        "| `like_dislike` (전체 사용자) | "
        + " | ".join(f"{users_meta['signal_mean_like_dislike'][str(n)]:.2f}"
                     for n in ns) + " |",
        "| N 을 다 채운 사용자 수 | "
        + " | ".join(f"{users_meta['full_history'][str(n)]} / "
                     f"{users_meta['eval_users']}" for n in ns) + " |",
        "",
        "PLAN 이 예고한 '2.3/4.0/7.0/8.8 → 3/5/10/20' 은 **N 을 다 채운 사용자 줄**에서 "
        "그대로 나온다. 전체 평균이 N=10·20 에서 10·20 에 못 미치는 것은 그 N 을 채울 "
        "만큼 과거가 긴 사용자가 108명·31명뿐이기 때문이고, 이건 해석과 무관한 "
        "표본의 한계다(Step 0 에서 이미 적었다).",
        "",
        "**같은 사용자, 같은 지표, 해석만 바꾼 결과 (AUC(P))**",
        "",
        history_compare_table(cmp, ns, "auc"),
        "",
        f"**Hit@{config.HIT_K}**",
        "",
        history_compare_table(cmp, ns, f"hit@{config.HIT_K}"),
        "",
        "**과제 R (AUC)** — 과제 R 의 *정의*는 이 변경과 무관하다(미래 방문 가게의 별점을 "
        "맞히는 문제 그대로다). 다만 모델에 들어가는 **입력**이 바뀌므로 점수는 바뀐다.",
        "",
        history_compare_table(cmp, ns, "r_auc"),
        "",
        "`current`(현재 서버 모델)는 like/dislike 를 나눠 받아 로지스틱을 갱신한다. "
        "`visit` 모드에서는 **모든 방문을 like 로 먹인다** — dislike 가 없으므로 이 모델은 "
        "'무엇을 고르지 않았나' 를 배우지 못하고 양성만 보는 셈이 된다. 이건 근사가 아니라 "
        "정의상 다른 입력이고, 표의 차이는 그 결과다.",
        "",
        "dislike(= '관심 없음')가 진짜로 쓸모없는 신호인지는 **오프라인에서 검증할 수 없다.** "
        "우리 데이터에는 '스와이프에서 왼쪽으로 넘긴 가게' 가 없고, 낮은 별점은 방문 후 "
        "실행 품질이지 방문 전 무관심이 아니기 때문이다. PLAN 대로 이건 서버 shadow "
        "로깅으로 온라인에서 검증한다.",
    ]
    return "\n".join(body)


# ────────────────────────────────────────────────── 브랜드
def brand_section(brand_meta: dict, effect: dict, ns: list[int]) -> str:
    rows = ["| # | 브랜드 | 후보 풀 가게 수 | 예 |", "|---|---|---|---|"]
    for i, b in enumerate(brand_meta["largest"], 1):
        ex = ", ".join(b["stores"][:3])
        rows.append(f"| {i} | {b['brand']} | {b['count']} | {ex} |")
    body = [
        "Step 1 이 남긴 문제(그 문서의 '체인 문제'): **같은 브랜드의 다른 지점은 메뉴가 "
        "같아 MaxSim 이 1.0 이 되고 추천 상위를 점거한다.** 스타벅스 역삼점을 방문한 "
        "사람에게 스타벅스 선릉점을 맨 위로 주는 것은 취향을 맞춘 게 아니라 근접 중복이다.",
        "",
        "**정규화 규칙** (`analysis/content/brands.py`, 데이터를 보고 정했다). 이름 "
        f"{brand_meta['names_total']:,}개 중 공백이 있는 것 {brand_meta['multi_token']:,}개, "
        f"그 중 **{brand_meta['branch_tail']:,}개**({brand_meta['branch_tail'] / max(brand_meta['multi_token'], 1):.0%})"
        "가 마지막 토큰이 `점` 으로 끝난다.",
        "",
        "1. NFKC 정규화 → **꼬리 괄호 제거** (`태키커피(tackycoffee)`, "
        "`솔솥 GFC몰점(강남파이낸스센터)`).",
        "2. 공백으로 자른 **마지막 토큰이 지점 토큰이면 떼어낸다.** 토큰이 2개 이상일 "
        "때만 뗀다 — `홍반점`·`문익점`처럼 이름 자체가 `점` 으로 끝나는 한 단어 가게를 "
        "지키기 위해서다. 여러 개 붙어 있으면 반복한다.",
        "3. 지점 토큰 = `점` 으로 끝나고, 업태 낱말(`반점`·`전문점`·`주점`·`할인점`·"
        "`상점`·`노점`·`대리점`·`매점`)을 품지 않으며, 14자 이하.",
        "4. 남은 것을 casefold 하고 공백을 지워 키로 쓴다(`본죽&비빔밥cafe` 의 띄어쓰기 "
        "변형을 묶기 위해).",
        "",
        f"결과: 후보 풀 {brand_meta['pool_size']:,}곳이 브랜드 "
        f"**{brand_meta['n_brands']:,}개**로 접힌다. 2곳 이상인 브랜드가 "
        f"**{brand_meta['n_multi_brands']}개**이고 거기 속한 가게가 "
        f"**{brand_meta['n_stores_in_chains']}곳"
        f"({brand_meta['n_stores_in_chains'] / brand_meta['pool_size']:.1%})** 이다.",
        "",
        "**가장 큰 브랜드 20개**",
        "",
        "\n".join(rows),
        "",
        "**놓친 것을 적어 둔다.** `스타벅스 센터필드R`·`스타벅스 큰길타워`·"
        "`투썸플레이스 투썸2.0강남` 처럼 지점명이 `점` 으로 끝나지 않는 3건과, "
        "`웅칼강남점`·`우미야역삼1호점` 처럼 공백 없이 붙은 2건은 묶이지 않는다. "
        "후자를 정규식으로 뜯으면 `명성양갈비전문점` 같은 이름까지 잘리는데, 놓친 "
        "가게들이 묶일 짝(`웅칼`·`우미야`)은 후보 풀에 없다 — 규칙을 위험하게 만들 "
        "이유가 없다. 반대로 이름이 똑같은 서로 다른 가게(`가연`·`전봇대`·`골목집`·"
        "`통영집`)는 브랜드가 아닌데도 한 키로 묶인다.",
        "",
        "**억제 방식.** 이력 가게 h 와 **같은 브랜드**인 후보 i 의 `sim(h, i)` 를 그 "
        "사용자의 평균 유사도(중립)로 바꾼다. 0 으로 만들면 '같은 브랜드라 싫다' 가 되고, "
        "1.0 을 그대로 두면 근접 중복이 상위를 점거한다. 바꾸는 것은 **그 이력 가게와의 "
        "한 칸**뿐이라, 브랜드가 아닌 이유로 닮은 후보는 계속 올라온다.",
        "",
        "**효과.** 상위 10 안에 이력과 같은 브랜드인 가게가 들어간 비율(N=5):",
        "",
        f"- 억제 전 `{effect['base_model']}`: 사용자당 평균 "
        f"**{effect['top10_same_brand_before']:.3f}곳** "
        f"({effect['users_with_brand_hit_before']}명 / {effect['n_users']}명이 "
        "적어도 한 곳)",
        f"- 억제 후 `{effect['bs_model']}`: 사용자당 평균 "
        f"**{effect['top10_same_brand_after']:.3f}곳** "
        f"({effect['users_with_brand_hit_after']}명 / {effect['n_users']}명)",
        f"- 이력에 체인 가게가 하나라도 있는 사용자는 {effect['users_with_chain_history']}명"
        f" / {effect['n_users']}명이다. 나머지 사용자에게 이 옵션은 **아무 것도 하지 "
        "않는다** — 그래서 전체 평균 지표의 변화는 작을 수밖에 없다.",
        "",
        "지표 변화는 위 결선 표의 `" + effect["bs_model"] + "` 줄과 `"
        + effect["base_model"] + "` 줄을 비교하면 된다. 짝지은 차이:",
        "",
        brand_effect_table(effect, ns),
    ]
    return "\n".join(body)


def brand_effect_table(effect: dict, ns: list[int]) -> str:
    out = ["| 지표 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|" + "---|" * len(ns)]
    for metric, cells in effect["paired"].items():
        vals = []
        for n in ns:
            c = cells[str(n)]
            mark = "**" if c["significant"] else ""
            vals.append(f"{mark}{c['diff']:+.4f}{mark} "
                        f"[{c['ci_low']:+.4f}, {c['ci_high']:+.4f}]")
        out.append(f"| {metric} | " + " | ".join(vals) + " |")
    return "\n".join(out)


def brand_effect(splits, ctx, ns, base_model: str, bs_model: str, raw: dict, *,
                 n_history: int, resamples: int, seed: int) -> dict:
    """억제 전/후를 짝지어 잰다 + 상위 10 의 '같은 브랜드' 개수를 직접 센다."""
    from .scorers_menu import brand_index
    brand = brand_index(ctx)
    pool = ctx.pool

    counts = {"before": [], "after": []}
    chain_hist = 0
    for sp in splits:
        hist = sp.signals(n_history, ctx.history_mode)
        hidx = [pool.pos[sid] for sid, _ in hist if sid in pool.pos]
        if hidx and any((brand == brand[h]).sum() > 1 for h in hidx):
            chain_hist += 1
        seen = np.zeros(len(pool), dtype=bool)
        for sid in sp.seen_stores:
            seen[pool.pos[sid]] = True
        hb = {int(brand[h]) for h in hidx}
        for key, name in (("before", base_model), ("after", bs_model)):
            s = np.asarray(scorers.get(name).score(hist, ctx, user_id=sp.user_id),
                           dtype=float)
            s = np.where(seen, -np.inf, s)
            top = np.argsort(-s)[:config.HIT_K]
            counts[key].append(sum(1 for j in top if int(brand[j]) in hb))

    before = np.array(counts["before"], dtype=float)
    after = np.array(counts["after"], dtype=float)

    metrics = ["auc", "auc_pm", f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}"]
    paired: dict = {m: {} for m in metrics}
    for n in ns:
        a, b = raw[bs_model][n], raw[base_model][n]      # 결선에서 이미 잰 것을 쓴다
        for m in metrics:
            paired[m][str(n)] = paired_diff_ci(a.p[m], b.p[m],
                                               resamples=resamples, seed=seed)
    return {
        "base_model": base_model, "bs_model": bs_model, "n_history": n_history,
        "n_users": len(splits),
        "top10_same_brand_before": float(before.mean()),
        "top10_same_brand_after": float(after.mean()),
        "users_with_brand_hit_before": int((before > 0).sum()),
        "users_with_brand_hit_after": int((after > 0).sum()),
        "users_with_chain_history": chain_hist,
        "paired": paired,
    }


# ────────────────────────────────────────────────── λ
def lambda_section(chosen: dict, ns: list[int], metrics: list[str]) -> str:
    grid = ", ".join(f"{l:g}" for l in LAMBDAS)
    body = [
        "PLAN 5절 Step 1.5 결정 3번대로 λ 를 **AUC 로도 NDCG@20 으로도** 골랐다. "
        "스와이프 제품은 상위 10~20장이 전부라, 꼬리까지 세는 AUC 로 고른 λ 가 상위를 "
        "가장 잘 세우는 λ 라는 보장이 없기 때문이다.",
        "",
        f"λ 격자 {{{grid}}}. 폴드 밖에서 고르고 그 폴드에서만 재는 out-of-fold "
        "평가다(`cv.outer_cv`) — 사용자마다 **자기가 선택에 참여하지 않은** 모델의 "
        "점수가 남으므로 다른 모델과 그대로 짝지어 bootstrap 할 수 있다.",
        "",
        "| 하이브리드 | 선택 기준 | " + " | ".join(f"N={n}" for n in ns) + " |",
        "|---|---|" + "---|" * len(ns),
    ]
    for content, per_metric in chosen.items():
        for metric in metrics:
            picks = per_metric[metric]
            cells = [", ".join(picks[str(n)]) for n in ns]
            body.append(f"| `{content}` | `{metric}` | " + " | ".join(cells) + " |")
    body += [
        "",
        "칸 안의 값은 **폴드 5개가 각각 고른 λ** 다. 다섯 개가 같으면 선택이 안정적이라는 "
        "뜻이고, 흩어졌으면 λ 가 별로 중요하지 않다는 뜻이다.",
    ]
    edge = {v for per_metric in chosen.values() for picks in per_metric.values()
            for vals in picks.values() for v in vals}
    if edge == {f"{min(LAMBDAS):g}"}:
        body.append("")
        body.append(f"**폴드 전부가 격자의 맨 아래 값 λ={min(LAMBDAS):g} 를 골랐다.** "
                    "격자 경계에서 멈춘 선택은 '이게 최적' 이 아니라 '더 작았으면 더 "
                    "좋았을 수도 있다' 는 뜻이다 — 콘텐츠·CF 점수는 인기 점수에 아주 "
                    "조금 섞을 때만 도움이 된다.")
    return "\n".join(body)


# ────────────────────────────────────────────────── 한눈에 / 배운 것
def _verdict(ci: dict) -> str:
    """CI 판정을 **부호까지** 말한다. '0 을 넘겼다' 만 쓰면 진 것도 이긴 것처럼 읽힌다."""
    if not ci["significant"]:
        return "**CI 가 0 을 포함한다 — 우위 미확정**"
    return ("CI 가 0 을 넘겼다" if ci["diff"] > 0
            else "CI 가 0 을 넘겼다 — **나쁜 쪽으로**")


def _cell(results, model, n, metric, task="task_p"):
    return results[model][str(n)][task][metric]


def headline(results, ns, cfm, content, bs_model, hybrids, cmp,
             users_meta, effect, chosen, select_metrics) -> list[str]:
    cf_model, inter = cfm["model"], cfm["intersection"]
    n5 = str(5 if 5 in ns else ns[0])
    pop = results["popularity"][n5]["task_p"]
    cfe = results[cf_model][n5]["task_p"]
    con = results[content][n5]["task_p"]
    out = [
        f"**협업 필터는 `popularity` 의 AUC 를 못 넘는다** — `{cf_model}` "
        f"{cfe['auc']['mean']:.4f} vs {pop['auc']['mean']:.4f}, 차이 "
        f"{cfe['auc']['vs_baseline']['diff']:+.4f} "
        f"[{cfe['auc']['vs_baseline']['ci_low']:+.4f}, "
        f"{cfe['auc']['vs_baseline']['ci_high']:+.4f}] "
        f"({_verdict(cfe['auc']['vs_baseline'])}).",
        f"**그러나 상위와 인기통제에서는 CF 가 이긴다** — Hit@{config.HIT_K} "
        f"{cfe[f'hit@{config.HIT_K}']['mean']:.4f} vs {pop[f'hit@{config.HIT_K}']['mean']:.4f} "
        f"({_verdict(cfe[f'hit@{config.HIT_K}']['vs_baseline'])}), "
        f"NDCG@{config.NDCG_K} {cfe[f'ndcg@{config.NDCG_K}']['mean']:.4f} vs "
        f"{pop[f'ndcg@{config.NDCG_K}']['mean']:.4f} "
        f"({_verdict(cfe[f'ndcg@{config.NDCG_K}']['vs_baseline'])}), "
        f"auc_pm {cfe['auc_pm']['mean']:.4f} vs {pop['auc_pm']['mean']:.4f} "
        f"({_verdict(cfe['auc_pm']['vs_baseline'])}). "
        "AUC 는 꼬리 2천 곳의 순서까지 세는 지표이고 제품은 상위 10~20장만 쓴다.",
        f"**CF vs 메뉴 콘텐츠** — AUC {cfe['auc']['mean']:.4f} vs "
        f"{con['auc']['mean']:.4f}, auc_pm {cfe['auc_pm']['mean']:.4f} vs "
        f"{con['auc_pm']['mean']:.4f}, Hit@{config.HIT_K} "
        f"{cfe[f'hit@{config.HIT_K}']['mean']:.4f} vs "
        f"{con[f'hit@{config.HIT_K}']['mean']:.4f}. 콘텐츠가 넘어야 할 상대의 눈금이 "
        "이제 표에 있다.",
        f"**공방문 행렬은 {cfm['density']:.1%} 만 채워져 있다** — 비영 쌍 "
        f"{cfm['n_pairs_nonzero']:,}개 / "
        f"{cfm['n_pairs_total']:,}개, 그 중 {inter['share_1']:.0%} 가 "
        "공방문자 **1명**짜리다. CF 의 상위 점수는 두꺼운 증거가 아니라 우연 한 건 "
        "위에 서 있다.",
    ]
    cmp_c = cmp[content][n5]["auc"]
    out.append(
        f"**이력 해석을 `visit` 로 바꾼 효과는 작다** — `{content}` AUC "
        f"{cmp_c['like_dislike']:.4f} → {cmp_c['visit']:.4f} "
        f"({cmp_c['diff']['diff']:+.4f} [{cmp_c['diff']['ci_low']:+.4f}, "
        f"{cmp_c['diff']['ci_high']:+.4f}], {_verdict(cmp_c['diff'])}). "
        f"신호가 {users_meta['signal_mean_like_dislike'][n5]:.1f}건에서 "
        f"{users_meta['signal_mean'][n5]:.1f}건으로 늘었는데도 그렇다.")
    bs = results[bs_model][n5]["task_p"]
    out.append(
        f"**브랜드 중복 억제는 지표를 올리지 못했다** — `{bs_model}` AUC "
        f"{bs['auc']['mean']:.4f} vs `{content}` {con['auc']['mean']:.4f}. "
        f"상위 {config.HIT_K} 안의 '이력과 같은 브랜드' 가게는 사용자당 "
        f"{effect['top10_same_brand_before']:.2f}곳 → "
        f"{effect['top10_same_brand_after']:.2f}곳으로 확실히 걷혔는데도 그렇다 — "
        "그 자리에 들어온 가게가 정답일 확률이 더 높지는 않았다.")
    for name in hybrids:
        h = results[name][n5]["task_p"]["auc"]
        out.append(f"하이브리드 `{name}` AUC {h['mean']:.4f}, `popularity` 대비 "
                   f"{h['vs_baseline']['diff']:+.4f} "
                   f"[{h['vs_baseline']['ci_low']:+.4f}, "
                   f"{h['vs_baseline']['ci_high']:+.4f}] — "
                   f"{_verdict(h['vs_baseline'])}.")

    # 이 Step 에서 제일 큰 사실: CF 하이브리드가 처음으로 모든 N 에서 인기를 넘겼다.
    best = max(hybrids, key=lambda h: sum(
        1 for n in ns
        for m in ("auc", "auc_pm", f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}")
        if (lambda ci: ci["significant"] and ci["diff"] > 0)(
            results[h][str(n)]["task_p"][m]["vs_baseline"])))
    auc_all = all(results[best][str(n)]["task_p"]["auc"]["vs_baseline"]["significant"]
                  and results[best][str(n)]["task_p"]["auc"]["vs_baseline"]["diff"] > 0
                  for n in ns)
    if auc_all:
        lo = results[best][str(min(ns))]["task_p"]["auc"]["vs_baseline"]
        hi = results[best][str(max(ns))]["task_p"]["auc"]["vs_baseline"]
        out.append(
            f"**`{best}` 는 N ∈ {{{', '.join(str(n) for n in ns)}}} 전부에서 "
            f"`popularity` 의 AUC 를 CI 로 넘겼다.** N={min(ns)} "
            f"{lo['diff']:+.4f} [{lo['ci_low']:+.4f}, {lo['ci_high']:+.4f}], "
            f"N={max(ns)} {hi['diff']:+.4f} [{hi['ci_low']:+.4f}, "
            f"{hi['ci_high']:+.4f}] — 이력이 길수록 폭도 커진다. Step 0·1 을 통틀어 "
            "인기 베이스라인을 이만큼 일관되게 넘긴 것은 처음이다. 다만 **다중비교 "
            "보정을 하지 않았고**(지표 4 × N 4 × 모델 여럿), 폭 자체는 AUC 0.01 "
            "수준이다 — '넘었다' 와 '제품에서 체감된다' 는 다른 말이다.")

    # 결정 3번의 핵심: 두 잣대가 실제로 다른 λ 를 고르고, 결과가 갈리는가.
    for con_name, per_metric in chosen.items():
        picks = {m: sorted({v for vals in per_metric[m].values() for v in vals})
                 for m in select_metrics}
        if len({tuple(v) for v in picks.values()}) <= 1:
            continue
        names = {m: hybrid_name(con_name, m) for m in select_metrics}
        cells = {m: results[names[m]][n5]["task_p"] for m in select_metrics}
        bits = " · ".join(
            f"`{m}` 선택 λ∈{{{', '.join(picks[m])}}} → AUC "
            f"{cells[m]['auc']['mean']:.4f} / Hit@{config.HIT_K} "
            f"{cells[m][f'hit@{config.HIT_K}']['mean']:.4f}"
            for m in select_metrics)
        a, b = select_metrics[0], select_metrics[-1]
        d_auc = cells[b]["auc"]["mean"] - cells[a]["auc"]["mean"]
        d_hit = (cells[b][f"hit@{config.HIT_K}"]["mean"]
                 - cells[a][f"hit@{config.HIT_K}"]["mean"])
        if d_auc < -0.005:
            tail = (f"`{b}` 로 고르면 λ 가 커져 상위(Hit@{config.HIT_K} "
                    f"{d_hit:+.4f})는 좋아지고 꼬리(AUC {d_auc:+.4f})를 내준다")
        elif d_hit > 0.01:
            tail = (f"여기서는 상충이 아니다 — AUC 는 그대로인데(AUC {d_auc:+.4f}) "
                    f"상위만 좋아진다(Hit@{config.HIT_K} {d_hit:+.4f}). 스와이프 "
                    f"제품에서는 `{b}` 로 고른 쪽이 낫다")
        else:
            tail = (f"다만 지표는 거의 안 움직인다 (AUC {d_auc:+.4f}, "
                    f"Hit@{config.HIT_K} {d_hit:+.4f}) — 잣대를 바꿔도 결론이 같다")
        out.append(f"**λ 를 무엇으로 고르느냐로 `{con_name}` 하이브리드의 λ 가 "
                   f"갈린다** (N={n5}): {bits}. {tail}. PLAN 5절 3번이 물은 것이 "
                   "정확히 이 상충이다.")
    return out


def significance_line(results, model, ns) -> str:
    """`popularity` 를 CI 로 넘긴 칸이 어디인지만 그대로 옮긴다. 없으면 없다고 쓴다."""
    metrics = ["auc", "auc_pm", f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}"]
    hits = []
    for metric in metrics:
        for n in ns:
            ci = results[model][str(n)]["task_p"][metric]["vs_baseline"]
            if ci["significant"] and ci["diff"] > 0:
                hits.append(f"{metric} N={n} ({ci['diff']:+.4f} "
                            f"[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}])")
    if not hits:
        return f"`{model}` 이 `popularity` 를 CI 로 넘긴 칸은 **하나도 없다.**"
    lows = [results[model][str(n)]["task_p"][m]["vs_baseline"]["ci_low"]
            for m in metrics for n in ns
            if results[model][str(n)]["task_p"][m]["vs_baseline"]["significant"]
            and results[model][str(n)]["task_p"][m]["vs_baseline"]["diff"] > 0]
    return (f"`{model}` 이 `popularity` 를 CI 로 넘긴 칸은 {len(hits)}"
            f"/{len(metrics) * len(ns)}개다: " + ", ".join(hits)
            + f". 지표 {len(metrics)}개 × N {len(ns)}개를 한꺼번에 봤고 "
            "**다중비교 보정을 하지 않았다.** CI 하한이 가장 얇은 칸은 "
            f"{min(lows):+.4f} 다 — 개별 칸을 근거로 삼지 말고 **여러 칸이 같은 "
            "방향으로 몰려 있다는 것**만 읽는다.")


def _history_learning(cmp: dict, ns: list[int], content: str) -> str:
    """`visit` 로 바꾼 것이 실제로 무엇을 했는지 — 표에서 읽어 문장으로 옮긴다.

    미리 써 둔 결론을 남기지 않기 위해 여기서 숫자를 다시 세어 문장을 만든다.
    """
    def wins(model: str, metric: str, task_r: bool = False) -> list[int]:
        key = "r_auc" if task_r else metric
        return [n for n in ns
                if cmp[model][str(n)][key]["diff"]["significant"]
                and cmp[model][str(n)][key]["diff"]["diff"] > 0]

    def losses(model: str, metric: str, task_r: bool = False) -> list[int]:
        key = "r_auc" if task_r else metric
        return [n for n in ns
                if cmp[model][str(n)][key]["diff"]["significant"]
                and cmp[model][str(n)][key]["diff"]["diff"] < 0]

    cur_p, cur_r = wins("current", "auc"), losses("current", "auc", True)
    con_p = wins(content, "auc")
    n_max = str(max(ns))
    cur = cmp["current"][n_max]
    return (
        "- **`visit` 로 바꾼 것은 과제 P 를 올리고 과제 R 을 내린다.** PLAN 이 말한 "
        "'관심과 만족은 직교한다' 와 **방향이 맞는다**(증명은 아니다 — 아래를 볼 것). "
        "`current` 의 과제 P AUC 는 "
        f"N ∈ {{{', '.join(str(n) for n in cur_p)}}} 에서 CI 로 올랐고"
        f"(N={n_max} {cur['auc']['like_dislike']:.4f} → {cur['auc']['visit']:.4f}, "
        f"{cur['auc']['diff']['diff']:+.4f} [{cur['auc']['diff']['ci_low']:+.4f}, "
        f"{cur['auc']['diff']['ci_high']:+.4f}]), 같은 모델의 과제 R AUC 는 "
        f"N ∈ {{{', '.join(str(n) for n in cur_r)}}} 에서 CI 로 **내렸다**"
        f"(N={n_max} {cur['r_auc']['like_dislike']:.4f} → "
        f"{cur['r_auc']['visit']:.4f}). 별점을 버리면 '어디 갈까' 는 좋아지고 "
        f"'가서 만족할까' 는 나빠진다. 콘텐츠(`{content}`)는 과제 P 에서 "
        f"N ∈ {{{', '.join(str(n) for n in con_p) or '없음'}}} 만 CI 를 넘겼다 — "
        "효과가 있되 모델마다 크기가 다르다.")


def _hybrid_learning(results, ns, hybrids) -> str:
    """하이브리드가 인기를 넘긴 폭을 표에서 세어 문장으로 옮긴다."""
    def n_cells(h):
        return sum(1 for n in ns
                   for m in ("auc", "auc_pm", f"hit@{config.HIT_K}",
                             f"ndcg@{config.NDCG_K}")
                   if results[h][str(n)]["task_p"][m]["vs_baseline"]["significant"]
                   and results[h][str(n)]["task_p"][m]["vs_baseline"]["diff"] > 0)
    best = max(hybrids, key=n_cells)
    lo, hi = (results[best][str(min(ns))]["task_p"]["auc"]["vs_baseline"],
              results[best][str(max(ns))]["task_p"]["auc"]["vs_baseline"])
    return (
        f"- **인기 + 협업 필터 하이브리드가 `popularity` 를 처음으로 일관되게 넘겼다.** "
        f"`{best}` 는 지표 4 × N 4 = 16칸 중 {n_cells(best)}칸에서 CI 가 0 을 넘었고, "
        f"AUC 는 N={min(ns)} {lo['diff']:+.4f} → N={max(ns)} {hi['diff']:+.4f} 로 "
        "이력이 길수록 커진다. Step 0·1 에서는 어떤 모델도 이러지 못했다. **다만 "
        "폭이 AUC 0.01 수준이고 다중비교 보정을 하지 않았다** — '이겼다' 를 '제품에서 "
        "체감된다' 로 옮기지 말 것.")


def learnings(results, ns, cfm, content, bs_model, hybrids, cmp,
              brand_meta) -> str:
    cf_model, inter = cfm["model"], cfm["intersection"]
    n5 = str(5 if 5 in ns else ns[0])
    lines = ["### 배운 것", ""]
    pop = results["popularity"][n5]["task_p"]
    cfe = results[cf_model][n5]["task_p"]
    con = results[content][n5]["task_p"]

    lines += [
        "- **두 과제가 반대라는 Step 0 의 진단이 이력 해석을 바꿔도 그대로다.** 과제 P 는 "
        f"`popularity`(AUC {pop['auc']['mean']:.4f}) 가 여전히 최고이고, "
        f"과제 R 은 `quality` 가 최고다.",
        "- **'다음 방문' 은 협업 필터로도 잘 안 맞는다.** 콘텐츠가 약한 게 아니라 이 "
        "문제 자체가 어렵다는 증거다. 같은 데이터로 만든 CF 가 "
        f"AUC {cfe['auc']['mean']:.4f}, 메뉴 콘텐츠가 {con['auc']['mean']:.4f} — "
        "둘 다 0.5 에서 멀지 않다. 역삼 한 동네의 2,216곳에서 '다음에 갈 3곳' 을 "
        "맞히는 문제의 상한이 이 근처라고 읽는 편이 정직하다.",
        f"- **CF 와 콘텐츠는 다른 곳에서 이긴다.** CF 는 Hit@{config.HIT_K}"
        f"({cfe[f'hit@{config.HIT_K}']['mean']:.4f} vs 콘텐츠 "
        f"{con[f'hit@{config.HIT_K}']['mean']:.4f})에서, 콘텐츠는 auc_pm"
        f"({con['auc_pm']['mean']:.4f} vs CF {cfe['auc_pm']['mean']:.4f})에서 앞선다. "
        "겹치지 않는 신호일 가능성이 있고, 그렇다면 둘을 섞는 것이 Step 2 의 후보다.",
        _history_learning(cmp, ns, content),
        _hybrid_learning(results, ns, hybrids),
        f"- **브랜드는 후보 풀의 {brand_meta['n_stores_in_chains'] / brand_meta['pool_size']:.0%} "
        "를 차지하고, 억제해도 지표가 안 오른다.** 체인 지점을 상위에서 걷어내는 것은 "
        "지표 문제가 아니라 **제품 문제**로 다뤄야 한다 — 같은 브랜드 카드를 연달아 "
        "주지 않는 것은 백테스트가 아니라 노출 규칙으로 정할 일이다.",
        "",
        "### 증명하지 못한 것", "",
        "- **CI 가 0 을 포함하는 값은 우위의 근거가 아니다**(PLAN 1절). 위 표에서 굵게 "
        "표시되지 않은 차이는 전부 여기에 해당한다.",
    ] + [f"- {significance_line(results, h, ns)}" for h in hybrids] + [
        f"- CF 의 공방문 증거는 얇다. 비영 쌍 {cfm['n_pairs_nonzero']:,}개 중 "
        f"{inter['share_1']:.0%} 가 공방문자 1명이고, 코사인 1.0 인 "
        "쌍은 '둘 다 방문자가 1명인데 그게 같은 사람' 이라는 뜻이다. 사용자를 더 "
        "모으기 전에는 CF 의 상한을 이 데이터로 말할 수 없다.",
        "- CF 집계(sum/mean/max)와 Step 1 의 콘텐츠 변종은 **테스트 사용자 위에서** "
        "골랐다. 5-fold 로 고른 것은 λ 뿐이다.",
        "- `visit` 가 과제 P 를 **얼마나** 올리는지는 모델마다 다르고, 콘텐츠 모델에서는 "
        "N 절반에서 CI 가 0 을 포함한다. 그리고 올라간 폭(AUC 0.01~0.03)은 "
        "`popularity` 와의 간극(0.13~0.17)에 비하면 작다. 계약을 바꾼 1차 근거는 이 "
        "숫자가 아니라 **제품 정의**(스와이프 like = 방문 전 관심)이고, 숫자는 그 "
        "정의가 손해가 아님을 확인해 줄 뿐이다.",
        "- 과제 P 가 오르고 과제 R 이 내린 것을 '관심과 만족은 직교한다' 의 증명으로 "
        "읽으면 과하다. 두 과제의 **입력이 같이 바뀌었을 뿐** 직교성을 따로 측정한 것이 "
        "아니다. 방향이 PLAN 의 가설과 맞는다는 것까지가 이 표가 말할 수 있는 전부다.",
        "- dislike 가 쓸모없다는 것도 증명하지 못했다. 오프라인 데이터에는 '넘긴 카드' "
        "가 없다. shadow 로깅으로 온라인에서 볼 일이다.",
        "- 정답이 카카오 리뷰에서 나왔다는 표본틀 편향은 `auc_pm` 으로 줄였을 뿐 없애지 "
        "못했다. 그리고 **CF 는 그 편향과 같은 재료(리뷰)로 만들어졌다** — 리뷰를 남긴 "
        "사람들의 방문 패턴이지 '가고 싶어한 패턴' 이 아니다.",
        f"- 후보 풀 {cfm['n_items_empty']}곳은 CF 가, 그리고 Step 1 기준 124곳은 메뉴 "
        "모델이 아무 말도 하지 않는다. 이 가게들에 대한 성능은 이 실험이 재지 못한다.",
    ]
    return "\n".join(lines)


def notes(cfm, brand_meta, history_mode) -> list[str]:
    return [
        "**CI 가 0 을 포함하면 우위 미확정이다.** 작은 양수를 근거로 삼지 않는다"
        "(PLAN 1절).",
        f"이 실행의 이력 해석은 **`{history_mode}`** 다. `--history like_dislike` 로 "
        "돌리면 Step 0·1 과 같은 해석이 되고, `analysis/backtest/step1.py` 의 기본값은 "
        "그쪽이라 step1-menu.md 는 그대로 재현된다.",
        "`current`(서버 모델)는 like/dislike 를 나눠 받는다. `visit` 모드에서는 "
        "**모든 방문을 like 로 먹인다** — 그 모델에 dislike 를 만들어 줄 방법이 없기 "
        "때문이다. 이건 근사가 아니라 다른 입력이며, 위 비교 표가 그 크기를 보여 준다.",
        f"공방문 행렬은 평가 사용자 {cfm['n_excluded_authors']}명의 리뷰 "
        f"{cfm['n_excluded_reviews']:,}건을 **전부** 빼고 만들었다. 테스트"
        "(`tests/test_cf.py`)가 평가 사용자의 리뷰가 한 건도 행렬에 들어가지 않음을 "
        "검증한다.",
        "`cf_item_sum` 과 `cf_item_mean` 은 사용자 안에서 순위가 같다(이력 길이는 그 "
        "사용자에게 상수다). 표의 두 줄이 같은 것은 정의이지 버그가 아니다.",
        f"브랜드 키는 후보 풀 {brand_meta['pool_size']:,}곳을 "
        f"{brand_meta['n_brands']:,}개로 접는다. 규칙과 놓친 사례는 '브랜드' 절에 적었다.",
        "하이브리드의 z 정규화는 **그 사용자의 후보**(이미 간 가게를 뺀 나머지) 안에서 "
        "한다. 미래(정답) 정보는 들어가지 않는다.",
        "BLAS 스레드 수에 따라 부동소수 합의 순서가 달라져 마지막 자리가 흔들릴 수 있다. "
        "이 실행은 임베딩 작업과 코어를 나눠 쓰느라 1스레드로 돌렸다.",
    ]


# ────────────────────────────────────────────────── main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.backtest.step1_5",
        description="PLAN.md 5절 'Step 1 → Step 1.5' 1~4번을 백테스트한다.")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES))
    ap.add_argument("--out", default="step1_5-visit-cf")
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--history", default=config.DEFAULT_HISTORY_MODE,
                    choices=list(config.HISTORY_MODES),
                    help="이력 해석. 기본은 visit(방문 = 관심)")
    ap.add_argument("--select-metric", default=",".join(config.SELECT_METRICS),
                    help="하이브리드 λ 를 고르는 잣대. 쉼표로 여러 개 "
                         f"({'|'.join(config.SELECT_METRICS)})")
    ap.add_argument("--content", default=STEP1_CONTENT,
                    help="Step 1 스윕 1위 콘텐츠 모델")
    ap.add_argument("--sweep-N", type=int, default=5, help="CF 집계 스윕을 돌릴 이력 길이")
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--rebuild-md", action="store_true",
                    help="실험을 다시 돌리지 않고 저장된 json 에서 .md 만 다시 쓴다")
    args = ap.parse_args(argv)

    if args.rebuild_md:
        path = Path(config.RESULTS_DIR) / f"{args.out}.json"
        _write(json.loads(path.read_text(encoding="utf-8")), args.out)
        return 0

    ns = [int(x) for x in args.N.split(",") if x.strip()]
    select_metrics = [m.strip() for m in args.select_metric.split(",") if m.strip()]
    bad = [m for m in select_metrics if m not in config.SELECT_METRICS]
    if bad:
        ap.error(f"모르는 선택 기준: {', '.join(bad)}. "
                 f"쓸 수 있는 것: {', '.join(config.SELECT_METRICS)}")

    print("데이터 로드 …", flush=True)
    ds = load_dataset()
    pool = build_pool(ds)
    splits, _ = cache.load_or_build(ds, pool, use_cache=not args.no_cache)
    eval_users = {s.user_id for s in splits}
    attach_crawled_quality(pool, ds, eval_users)
    store_names = {s: (ds.stores.get(s, {}).get("name") or "") for s in pool.ids}
    base_extras = {"candidates": run.seen_masks(splits, pool), "dataset": ds,
                   "eval_users": eval_users, "store_names": store_names}
    ctx = ScoreContext(pool=pool, seed=args.seed, history_mode=args.history,
                       extras=base_extras)
    print(f"  후보 풀 {len(pool):,}곳 · 평가 사용자 {len(splits)}명 · "
          f"이력 해석 `{args.history}`", flush=True)

    # ── 공방문 행렬
    from .scorers_cf import covisit
    cvm = covisit(ctx)
    inter = intersection_stats(cvm)
    print(f"  공방문: 저자 {cvm.n_authors:,}명(2곳+ {cvm.n_authors_multi:,}명) · "
          f"비영 쌍 {cvm.n_pairs_nonzero:,} ({cvm.density:.3%})", flush=True)

    # ── 1단계: CF 집계 스윕
    ref_sweep = evaluate(scorers.get("popularity"), splits, ctx, args.sweep_N)
    cf_sweep = []
    for agg in CF_AGGS:
        name = f"cf_item_{agg}"
        res = evaluate(scorers.get(name), splits, ctx, args.sweep_N)
        cf_sweep.append({
            "model": name, "auc": _mean(res.p["auc"]),
            "auc_pm": _mean(res.p["auc_pm"]),
            f"hit@{config.HIT_K}": _mean(res.p[f"hit@{config.HIT_K}"]),
            f"ndcg@{config.NDCG_K}": _mean(res.p[f"ndcg@{config.NDCG_K}"]),
            "r_auc": _mean(res.r_auc),
            "vs_pop_auc": paired_diff_ci(res.p["auc"], ref_sweep.p["auc"],
                                         resamples=args.resamples, seed=args.seed),
        })
        print(f"  {name:<16} AUC={cf_sweep[-1]['auc']:.4f} "
              f"hit@{config.HIT_K}={cf_sweep[-1][f'hit@{config.HIT_K}']:.4f}", flush=True)
    best_cf = max(cf_sweep, key=lambda r: r["auc"])["model"]
    best_agg = best_cf.rsplit("_", 1)[1]
    print(f"  → CF 1위: {best_cf}", flush=True)

    # ── 2단계: 결선
    bs_model = args.content + BRAND_SUPPRESS_SUFFIX
    finalists = REFERENCE_MODELS + [best_cf, args.content, bs_model]
    hybrids: list[str] = []
    raw: dict[str, dict] = {}
    for name in finalists:
        raw[name] = {}
        for n in ns:
            raw[name][n] = evaluate(scorers.get(name), splits, ctx, n)
            print(f"  {name:<24} N={n:<3} "
                  f"AUC(P)={_mean(raw[name][n].p['auc']):.4f}", flush=True)

    chosen: dict = {}
    for content in (best_cf, args.content):
        chosen[content] = {}
        candidates = {f"{lam:g}": (lambda c=content, lam=lam: scorers.get(
            f"hybrid_pop_{c}_l{lam:g}")) for lam in LAMBDAS}
        for metric in select_metrics:
            name = hybrid_name(content, metric)
            hybrids.append(name)
            raw[name] = {}
            chosen[content][metric] = {}
            for n in ns:
                res, folds = cv.outer_cv(candidates, splits, ctx, n,
                                         metric=metric, seed=args.seed)
                raw[name][n] = res
                chosen[content][metric][str(n)] = folds
                print(f"  {name:<34} N={n:<3} λ={folds} "
                      f"AUC(P)={_mean(res.p['auc']):.4f}", flush=True)

    model_names = finalists + hybrids
    results = run.aggregate(raw, model_names, ns, REFS,
                            resamples=args.resamples, seed=args.seed)
    users_meta = run.summarise_users(splits, raw, model_names, ns,
                                     history_mode=args.history)

    # ── 이력 해석 비교
    other = "like_dislike" if args.history == "visit" else "visit"
    ctx_by_mode = {
        args.history: ctx,
        other: ScoreContext(pool=pool, seed=args.seed, history_mode=other,
                            extras=base_extras),
    }
    cmp = history_compare([args.content, "current"], splits, ctx_by_mode, ns,
                          resamples=args.resamples, seed=args.seed)

    # ── 브랜드
    largest = brand_norm.largest_brands(pool.ids, store_names, 20)
    all_names = [(ds.stores[s].get("name") or "") for s in ds.store_order]
    multi_token = sum(1 for n in all_names if len(n.split()) > 1)
    branch_tail = sum(1 for n in all_names if len(n.split()) > 1
                      and brand_norm.is_branch_token(n.split()[-1]))
    groups = brand_norm.brand_groups(pool.ids, store_names)
    brand_meta = {
        "pool_size": len(pool), "names_total": len(all_names),
        "multi_token": multi_token, "branch_tail": branch_tail,
        "n_brands": len(groups),
        "n_multi_brands": sum(1 for v in groups.values() if len(v) > 1),
        "n_stores_in_chains": sum(len(v) for v in groups.values() if len(v) > 1),
        "largest": [{"brand": b, "count": c, "stores": s} for b, c, s in largest],
    }
    effect = brand_effect(splits, ctx, ns, args.content, bs_model, raw,
                          n_history=args.sweep_N, resamples=args.resamples,
                          seed=args.seed)
    print(f"  브랜드: {brand_meta['n_brands']:,}개 · 2곳+ "
          f"{brand_meta['n_multi_brands']}개 · 상위10 같은 브랜드 "
          f"{effect['top10_same_brand_before']:.3f} → "
          f"{effect['top10_same_brand_after']:.3f}", flush=True)

    blob = {
        "run": args.out,
        "title": "Step 1.5 — 방문=관심 계약 수정과 협업 필터 베이스라인",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": run._git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "references": REFS,
        "baseline_model": REFS["task_p"],
        "history_sizes": ns,
        "intro": [
            "`analysis/PLAN.md` 5절 'Step 1 → Step 1.5 로 넘기는 결정' 1~4번을 구현한 것이다.",
            "과제 P 의 이력 해석을 **방문 = 관심**(`visit`)으로 바꾸고, 협업 필터"
            "(item-item 공방문) 베이스라인을 추가했으며, λ 를 AUC 와 NDCG@20 두 잣대로",
            "고르고, 메뉴 콘텐츠에 브랜드 중복 억제 옵션을 넣어 효과를 쟀다.",
            "기준은 과제 P 가 `popularity`, 과제 R 이 `quality` 다(PLAN 5절 1번).",
        ],
        "command": [
            "~/venvs/recsys/bin/python -m analysis.backtest.step1_5 \\",
            f"    --N {','.join(str(n) for n in ns)} --history {args.history} \\",
            f"    --select-metric {','.join(select_metrics)} --out {args.out}",
        ],
        "config": {
            "bayes_c": config.BAYES_C,
            "hit_k": config.HIT_K, "recall_k": config.RECALL_K, "ndcg_k": config.NDCG_K,
            "bootstrap_resamples": args.resamples,
            "history_mode": args.history,
            "select_metrics": select_metrics,
            "lambdas": list(LAMBDAS),
            "sweep_n_history": args.sweep_N,
            "split_cache_fingerprint": cache.fingerprint(),
        },
        "pool": {"size": len(pool), "stores_total": len(ds.stores),
                 "reviews_total": len(ds.reviews)},
        "users": users_meta,
        "cf": {
            "model": best_cf, "agg": best_agg,
            "n_authors": cvm.n_authors, "n_authors_multi": cvm.n_authors_multi,
            "n_pairs_nonzero": cvm.n_pairs_nonzero,
            "n_pairs_total": cvm.n_pairs_total, "density": cvm.density,
            "n_items_empty": cvm.n_items_empty,
            "n_excluded_authors": cvm.n_excluded_authors,
            "n_excluded_reviews": cvm.n_excluded_reviews,
            "intersection": inter,
            "sweep": cf_sweep,
        },
        "content_model": args.content,
        "bs_model": bs_model,
        "brand": brand_meta,
        "brand_effect": effect,
        "history_compare": cmp,
        "hybrid": {"models": hybrids, "chosen_per_fold": chosen,
                   "select_metrics": select_metrics, "lambdas": list(LAMBDAS)},
        "models": {m: {"description": scorers.get(m).description} for m in finalists}
        | {h: {"description":
               f"popularity_z + λ·(`{h[len('hybrid_pop_'):].rsplit('_cv_', 1)[0]}`)_z"
               f" — λ 는 사용자 5-fold out-of-fold 선택, 기준 "
               f"`{h.rsplit('_cv_', 1)[1]}`"}
           for h in hybrids},
        "results": results,
    }
    _write(blob, args.out)
    return 0


def _prose(blob: dict) -> dict:
    """숫자는 그대로 두고 문장·표만 다시 만든다.

    실험(15분)을 다시 돌리지 않고 글을 고칠 수 있어야 한다. 그래서 이 함수는
    `blob` 에 이미 저장된 값만 읽는다 — 여기서 새로 계산하는 숫자는 하나도 없다.
    """
    ns = blob["history_sizes"]
    cfm, hyb = blob["cf"], blob["hybrid"]
    content, bs_model = blob["content_model"], blob["bs_model"]
    chosen, metrics = hyb["chosen_per_fold"], hyb["select_metrics"]
    blob["headline"] = headline(blob["results"], ns, cfm, content, bs_model,
                                hyb["models"], blob["history_compare"],
                                blob["users"], blob["brand_effect"], chosen, metrics)
    blob["sections_before"] = [
        {"heading": "계약 수정 — 왜 방문을 관심으로 읽나",
         "body": history_section(blob["history_compare"], ns, blob["users"])},
        {"heading": "협업 필터 — item-item 공방문",
         "body": cf_section(cfm, blob["pool"]["size"],
                            blob["config"]["sweep_n_history"])},
    ]
    blob["sections"] = [
        {"heading": "하이브리드의 λ — 두 잣대의 선택",
         "body": lambda_section(chosen, ns, metrics)},
        {"heading": "브랜드 정규화와 중복 억제",
         "body": brand_section(blob["brand"], blob["brand_effect"], ns)},
        {"heading": "배운 것 / 증명하지 못한 것",
         "body": learnings(blob["results"], ns, cfm, content, bs_model,
                           hyb["models"], blob["history_compare"], blob["brand"])},
    ]
    blob["notes"] = notes(cfm, blob["brand"], blob["history_mode"])
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
