# -*- coding: utf-8 -*-
"""표현력 채점 결과 시각화 — matplotlib.

  python analysis/make_figures.py            # 라이트 모드
  python analysis/make_figures.py --dark     # 다크 모드
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "analysis" / "figures"
CATS = ["taste", "service", "price", "ambience", "wait"]
CAT_KO = {"taste": "맛·음식", "service": "서비스", "price": "가격",
          "ambience": "분위기", "wait": "대기·편의"}
GRADE_KO = ["G0 무내용", "G1 단순평가", "G2 속성언급", "G3 구체묘사"]


def theme(dark: bool) -> dict:
    if dark:
        return dict(bg="#201C18", surface="#29241F", ink="#EDE7DD",
                    ink2="#C9C0B3", muted="#93897A", grid="#453D33",
                    rule="#8FADD9", llm="#E58A7E", agree="#9BBE98", suffix="_dark")
    return dict(bg="#F1EEE8", surface="#FAF8F3", ink="#2A2521",
                ink2="#514A42", muted="#857A6C", grid="#DED7C9",
                rule="#34517D", llm="#B23A2E", agree="#4F6F4E", suffix="")


def setup(t: dict) -> None:
    plt.rcParams.update({
        "font.family": "Malgun Gothic",
        "axes.unicode_minus": False,
        "figure.facecolor": t["bg"],
        "axes.facecolor": t["bg"],
        "savefig.facecolor": t["bg"],
        "text.color": t["ink"],
        "axes.labelcolor": t["ink2"],
        "xtick.color": t["ink2"],
        "ytick.color": t["ink2"],
        "axes.edgecolor": t["grid"],
        "grid.color": t["grid"],
        "font.size": 11,
    })


def strip_axes(ax, keep=("left", "bottom")) -> None:
    for s in ("top", "right", "left", "bottom"):
        ax.spines[s].set_visible(s in keep)
    ax.tick_params(length=0)


def weighted_kappa(a: list[int], b: list[int], k: int = 4) -> float:
    n = len(a)
    obs = collections.Counter(zip(a, b))
    ca, cb = collections.Counter(a), collections.Counter(b)

    def w(i, j):
        return 1 - abs(i - j) / (k - 1)

    po = sum(w(i, j) * c for (i, j), c in obs.items()) / n
    pe = sum(w(i, j) * ca[i] * cb[j] for i in range(k) for j in range(k)) / (n * n)
    return (po - pe) / (1 - pe)


# ───────────────────────────────────────────────────────────── figures
def fig_grade_dist(rows, t) -> None:
    N = len(rows)
    rule = collections.Counter(r["rule_grade"] for r in rows)
    llm = collections.Counter(r["llm_grade"] for r in rows)
    x = np.arange(4)
    w = 0.38

    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    b1 = ax.bar(x - w / 2, [rule[g] for g in range(4)], w, label="규칙 기반",
                color=t["rule"], zorder=3)
    b2 = ax.bar(x + w / 2, [llm[g] for g in range(4)], w, label="LLM (EXAONE)",
                color=t["llm"], zorder=3)
    for bars, cnt in ((b1, rule), (b2, llm)):
        for i, bar in enumerate(bars):
            v = cnt[i]
            ax.text(bar.get_x() + bar.get_width() / 2, v + 12,
                    f"{v}\n{100 * v / N:.1f}%", ha="center", va="bottom",
                    fontsize=9, color=t["ink2"], linespacing=1.4)

    ax.set_xticks(x, GRADE_KO)
    ax.set_ylabel("리뷰 수")
    ax.set_ylim(0, 640)
    ax.grid(axis="y", alpha=0.5, zorder=0)
    ax.legend(frameon=False, loc="upper right")
    ax.set_title("표현력 등급 분포  ·  N=1,200", loc="left",
                 fontsize=13, fontweight="bold", pad=14)
    strip_axes(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"01_grade_dist{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_confusion(rows, t) -> None:
    cm = np.zeros((4, 4), dtype=int)
    for r in rows:
        cm[r["rule_grade"], r["llm_grade"]] += 1

    cmap = LinearSegmentedColormap.from_list("c", [t["bg"], t["rule"]])
    fig, ax = plt.subplots(figsize=(6.4, 5.4))
    ax.imshow(cm, cmap=cmap, vmin=0, vmax=cm.max())

    for i in range(4):
        for j in range(4):
            v = int(cm[i, j])
            frac = v / cm.max()
            ax.text(j, i, str(v), ha="center", va="center",
                    fontsize=13, fontweight="bold",
                    color=t["bg"] if frac > 0.5 else t["ink"])
            if i == j:
                ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False,
                                           edgecolor=t["agree"], lw=2.5, zorder=5))

    ax.set_xticks(range(4), [f"G{i}" for i in range(4)])
    ax.set_yticks(range(4), [f"G{i}" for i in range(4)])
    ax.set_xlabel("LLM 등급", labelpad=8)
    ax.set_ylabel("규칙 기반 등급", labelpad=8)
    diag = int(np.trace(cm))
    ax.set_title(f"혼동행렬  ·  대각선 일치 {diag}건 ({100 * diag / len(rows):.1f}%)",
                 loc="left", fontsize=13, fontweight="bold", pad=14)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(OUT / f"02_confusion{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_axis_l3(rows, t) -> None:
    """축별 L3(구체묘사) 도달 건수 — 다축 학습 표본 진단."""
    rule3 = [sum(1 for r in rows if r["rule_levels"][c] == 3) for c in CATS]
    llm3 = [sum(1 for r in rows if r["llm_levels"][c] == 3) for c in CATS]
    both3 = [sum(1 for r in rows if r["rule_levels"][c] == 3 and r["llm_levels"][c] == 3)
             for c in CATS]

    y = np.arange(len(CATS))
    h = 0.26
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    ax.barh(y + h, rule3, h, label="규칙 기반", color=t["rule"], zorder=3)
    ax.barh(y, llm3, h, label="LLM", color=t["llm"], zorder=3)
    ax.barh(y - h, both3, h, label="양쪽 합의", color=t["agree"], zorder=3)

    for arr, off in ((rule3, h), (llm3, 0), (both3, -h)):
        for i, v in enumerate(arr):
            ax.text(v + 3, y[i] + off, str(v), va="center", fontsize=9.5,
                    color=t["ink2"])

    ax.set_yticks(y, [CAT_KO[c] for c in CATS])
    ax.invert_yaxis()
    ax.set_xlabel("L3(구체묘사) 도달 리뷰 수")
    ax.set_xlim(0, 205)
    ax.grid(axis="x", alpha=0.5, zorder=0)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("축별 구체묘사(L3) 표본 수  ·  맛 외 축은 학습 표본이 사실상 없다",
                 loc="left", fontsize=13, fontweight="bold", pad=14)
    strip_axes(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"03_axis_l3{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_axis_agreement(rows, t) -> None:
    N = len(rows)
    rm = [sum(1 for r in rows if r["rule_levels"][c] >= 1) / N for c in CATS]
    lm = [sum(1 for r in rows if r["llm_levels"][c] >= 1) / N for c in CATS]
    ks = [weighted_kappa([r["rule_levels"][c] for r in rows],
                         [r["llm_levels"][c] for r in rows]) for c in CATS]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.4, 4.2),
                                   gridspec_kw={"width_ratios": [1.5, 1]})
    y = np.arange(len(CATS))
    h = 0.36
    ax1.barh(y + h / 2, [v * 100 for v in rm], h, label="규칙 기반",
             color=t["rule"], zorder=3)
    ax1.barh(y - h / 2, [v * 100 for v in lm], h, label="LLM",
             color=t["llm"], zorder=3)
    for arr, off in ((rm, h / 2), (lm, -h / 2)):
        for i, v in enumerate(arr):
            ax1.text(v * 100 + 1.2, y[i] + off, f"{v * 100:.1f}%", va="center",
                     fontsize=9.5, color=t["ink2"])
    ax1.set_yticks(y, [CAT_KO[c] for c in CATS])
    ax1.invert_yaxis()
    ax1.set_xlabel("언급률 (%)")
    ax1.set_xlim(0, 106)
    ax1.grid(axis="x", alpha=0.5, zorder=0)
    ax1.legend(frameon=False, loc="lower right")
    ax1.set_title("축별 언급률", loc="left", fontsize=12.5, fontweight="bold", pad=12)
    strip_axes(ax1)

    # kappa 는 단일 지표이므로 중립색 한 가지로 그린다.
    # (규칙=파랑 / LLM=빨강 은 왼쪽 차트에서 이미 '주체'를 뜻하므로 여기 재사용 금지)
    ax2.barh(y, ks, 0.5, color=t["muted"], zorder=3)
    for i, k in enumerate(ks):
        ax2.text(k + 0.012, y[i], f"{k:.2f}", va="center", fontsize=10,
                 color=t["ink2"], fontweight="bold")
    ax2.set_yticks(y, [])
    ax2.invert_yaxis()
    ax2.set_xlabel("weighted kappa")
    ax2.set_xlim(0, 0.78)
    ax2.axvline(0.6, color=t["muted"], ls=":", lw=1.2, zorder=2)
    ax2.text(0.607, -0.62, "0.6 (실질적 일치)", fontsize=8.5, color=t["muted"])
    ax2.grid(axis="x", alpha=0.5, zorder=0)
    ax2.set_title("축별 채점자 간 일치도", loc="left", fontsize=12.5,
                  fontweight="bold", pad=12)
    strip_axes(ax2)

    fig.tight_layout()
    fig.savefig(OUT / f"04_axis_agreement{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_length(rows, t) -> None:
    def bucket(L):
        return ("0-30" if L < 30 else "30-60" if L < 60 else
                "60-100" if L < 100 else "100-200" if L < 200 else "200+")

    order = ["0-30", "30-60", "60-100", "100-200", "200+"]
    bk = collections.defaultdict(list)
    for r in rows:
        bk[bucket(r["char_len"])].append(r)

    rm = [statistics.mean(r["rule_grade"] for r in bk[b]) for b in order]
    lm = [statistics.mean(r["llm_grade"] for r in bk[b]) for b in order]
    ns = [len(bk[b]) for b in order]

    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    x = np.arange(len(order))
    ax.plot(x, rm, "o-", color=t["rule"], lw=2.2, ms=8, label="규칙 기반", zorder=3)
    ax.plot(x, lm, "o-", color=t["llm"], lw=2.2, ms=8, label="LLM", zorder=3)
    ax.fill_between(x, rm, lm, color=t["llm"], alpha=0.10, zorder=2)

    for i in range(len(order)):
        ax.text(x[i], max(rm[i], lm[i]) + 0.10, f"+{lm[i] - rm[i]:.2f}",
                ha="center", fontsize=9, color=t["muted"])

    ax.set_xticks(x, [f"{b}자\n(n={n})" for b, n in zip(order, ns)])
    ax.set_ylabel("평균 등급 (0~3)")
    ax.set_ylim(0.6, 3.0)
    ax.grid(axis="y", alpha=0.5, zorder=0)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("리뷰 길이 구간별 평균 등급  ·  두 채점자 모두 길이와 함께 오른다",
                 loc="left", fontsize=13, fontweight="bold", pad=14)
    strip_axes(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"05_length{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_usable(rows, t) -> None:
    N = len(rows)
    both_hi = sum(1 for r in rows if r["rule_grade"] >= 2 and r["llm_grade"] >= 2)
    only_rule = sum(1 for r in rows if r["rule_grade"] >= 2 and r["llm_grade"] < 2)
    only_llm = sum(1 for r in rows if r["llm_grade"] >= 2 and r["rule_grade"] < 2)
    neither = N - both_hi - only_rule - only_llm

    labels = ["양쪽 합의 (고품질)", "규칙만 인정", "LLM만 인정", "양쪽 모두 저품질"]
    vals = [both_hi, only_rule, only_llm, neither]
    colors = [t["agree"], t["rule"], t["llm"], t["grid"]]

    fig, ax = plt.subplots(figsize=(9.2, 2.7))
    left = 0
    for v, c in zip(vals, colors):
        ax.barh(0, v, left=left, color=c, height=0.55, zorder=3)
        if v > 60:
            ax.text(left + v / 2, 0, f"{v}\n{100 * v / N:.1f}%", ha="center",
                    va="center", fontsize=10.5, fontweight="bold",
                    color=t["bg"] if c != t["grid"] else t["ink"])
        left += v

    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colors]
    ax.legend(handles, labels, frameon=False, ncol=4, loc="upper center",
              bbox_to_anchor=(0.5, -0.06), fontsize=9.5)
    ax.set_xlim(0, N)
    ax.set_yticks([])
    ax.set_xticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(
        f"학습에 쓸 수 있는 리뷰 규모  ·  양쪽 합의 {both_hi}건 ({100 * both_hi / N:.1f}%)",
        loc="left", fontsize=13, fontweight="bold", pad=14)
    fig.tight_layout()
    fig.savefig(OUT / f"06_usable{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_rating(rows, t) -> None:
    by = collections.defaultdict(list)
    for r in rows:
        if r["rating"]:
            by[round(r["rating"])].append(r)
    ks = sorted(k for k in by if len(by[k]) >= 20)

    rm = [statistics.mean(r["rule_grade"] for r in by[k]) for k in ks]
    lm = [statistics.mean(r["llm_grade"] for r in by[k]) for k in ks]
    ns = [len(by[k]) for k in ks]

    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    x = np.arange(len(ks))
    ax.plot(x, rm, "o-", color=t["rule"], lw=2.2, ms=8, label="규칙 기반", zorder=3)
    ax.plot(x, lm, "o-", color=t["llm"], lw=2.2, ms=8, label="LLM", zorder=3)
    ax.set_xticks(x, [f"★{k}\n(n={n})" for k, n in zip(ks, ns)])
    ax.set_ylabel("평균 등급 (0~3)")
    ax.set_ylim(0, 3)
    ax.grid(axis="y", alpha=0.5, zorder=0)
    ax.legend(frameon=False, loc="upper right")
    ax.set_title("별점별 평균 표현력 등급  ·  거의 평평 = 감성과 표현력은 독립 축",
                 loc="left", fontsize=13, fontweight="bold", pad=14)
    strip_axes(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"07_rating{t['suffix']}.png", dpi=200)
    plt.close(fig)


def _pearson(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def fig_scatter_length(rows, t) -> None:
    """길이 vs 표현력 산점도 — 상관은 있지만 예측은 못 한다."""
    xs = [r["char_len"] for r in rows]
    ys = [sum(r["llm_levels"].values()) for r in rows]
    r = _pearson(xs, ys)
    rng = np.random.default_rng(7)
    yj = [y + rng.uniform(-0.28, 0.28) for y in ys]  # 정수 겹침 완화

    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.scatter(xs, yj, s=13, color=t["llm"], alpha=0.28, edgecolors="none", zorder=3)
    # 구간 평균 추세선
    edges = [0, 30, 60, 100, 150, 200, 300, 850]
    cx, cy = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        grp = [y for x, y in zip(xs, ys) if lo <= x < hi]
        if grp:
            cx.append((lo + min(hi, max(xs))) / 2)
            cy.append(statistics.mean(grp))
    ax.plot(cx, cy, "o-", color=t["ink"], lw=2.2, ms=7, zorder=5, label="구간 평균")
    ax.set_xlim(0, 500)
    ax.set_ylim(-0.5, 10.5)
    ax.set_xlabel("리뷰 길이 (자)")
    ax.set_ylabel("표현력 점수 (depth_sum, 0~15)")
    ax.grid(alpha=0.4, zorder=0)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title(f"길이 vs 표현력  ·  r = {r:.2f} (설명력 {r*r*100:.0f}%)  ·  길지만 밋밋한 리뷰가 흩어져 있다",
                 loc="left", fontsize=12.5, fontweight="bold", pad=14)
    strip_axes(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"08_scatter_length{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_rating_box(rows, t) -> None:
    """별점 vs 표현력 분포 — 상관 없음."""
    rated = [r for r in rows if r["rating"]]
    rr = _pearson([r["rating"] for r in rated],
                  [sum(r["llm_levels"].values()) for r in rated])
    by = collections.defaultdict(list)
    for r in rated:
        by[round(r["rating"])].append(sum(r["llm_levels"].values()))
    ks = sorted(k for k in by if len(by[k]) >= 20)
    data = [by[k] for k in ks]

    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    bp = ax.boxplot(data, positions=range(len(ks)), widths=0.55,
                    patch_artist=True, showfliers=False,
                    medianprops=dict(color=t["ink"], lw=2),
                    whiskerprops=dict(color=t["muted"]),
                    capprops=dict(color=t["muted"]))
    for patch in bp["boxes"]:
        patch.set_facecolor(t["llm"])
        patch.set_alpha(0.35)
        patch.set_edgecolor(t["llm"])
    means = [statistics.mean(d) for d in data]
    ax.plot(range(len(ks)), means, "D-", color=t["rule"], lw=2, ms=7,
            zorder=5, label="평균")
    ax.set_xticks(range(len(ks)), [f"★{k}\n(n={len(by[k])})" for k in ks])
    ax.set_ylabel("표현력 점수 (depth_sum)")
    ax.set_ylim(-0.5, 10.5)
    ax.grid(axis="y", alpha=0.4, zorder=0)
    ax.legend(frameon=False, loc="upper right")
    ax.set_title(f"별점 vs 표현력  ·  r = {rr:.2f} (사실상 0)  ·  별점이 표현력을 설명하지 않는다",
                 loc="left", fontsize=12.5, fontweight="bold", pad=14)
    strip_axes(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"09_rating_box{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_low_reasons(rows, t) -> None:
    """표현력 낮은 리뷰(G<=1)는 왜 낮은가."""
    import re
    low = [r for r in rows if r["llm_grade"] <= 1]
    reasons = collections.Counter()
    for r in low:
        c = r["content"]
        lv = r["llm_levels"]
        if len(c) < 15:
            reasons["너무 짧음 (<15자)"] += 1
        elif lv["taste"] == 1 and sum(lv.values()) == 1:
            reasons["\"맛있어요\"류 단순평가"] += 1
        elif re.search(r"또 (가|올|방문)|재방문|강추|추천|최고|맛집이", c):
            reasons["총평·재방문 의사만"] += 1
        else:
            reasons["맥락·인사·정보 (평가 없음)"] += 1

    order = ["\"맛있어요\"류 단순평가", "너무 짧음 (<15자)",
             "맥락·인사·정보 (평가 없음)", "총평·재방문 의사만"]
    vals = [reasons[k] for k in order]
    y = np.arange(len(order))

    fig, ax = plt.subplots(figsize=(8.8, 3.8))
    ax.barh(y, vals, 0.62, color=t["llm"], zorder=3)
    for i, v in enumerate(vals):
        ax.text(v + 2, y[i], f"{v}건 · {100*v/len(low):.0f}%",
                va="center", fontsize=10.5, color=t["ink2"])
    ax.set_yticks(y, order)
    ax.invert_yaxis()
    ax.set_xlim(0, max(vals) * 1.25)
    ax.set_xlabel("리뷰 수")
    ax.grid(axis="x", alpha=0.4, zorder=0)
    ax.set_title(f"표현력 낮은 리뷰는 왜 낮은가  ·  G1 이하 {len(low)}건 (전체의 {100*len(low)/len(rows):.0f}%)",
                 loc="left", fontsize=12.5, fontweight="bold", pad=14)
    strip_axes(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"10_low_reasons{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_platform(rows, t) -> None:
    """플랫폼별 표현력 등급 분포 — 차이 거의 없음."""
    N = {src: sum(1 for r in rows if r["source"] == src) for src in ("kakao", "naver")}
    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    srcs = [("kakao", "카카오"), ("naver", "네이버")]
    y = np.arange(len(srcs))
    grade_colors = [t["grid"], t["muted"], t["rule"], t["agree"]]
    for i, (src, ko) in enumerate(srcs):
        sel = [r for r in rows if r["source"] == src]
        left = 0
        for g in range(4):
            v = sum(1 for r in sel if r["llm_grade"] == g)
            ax.barh(i, v, left=left, height=0.5, color=grade_colors[g], zorder=3)
            if v > 45:
                ax.text(left + v / 2, i, f"{100*v/len(sel):.0f}%", ha="center",
                        va="center", fontsize=10, fontweight="bold",
                        color=t["bg"] if g >= 2 else t["ink"])
            left += v
        mean_g = statistics.mean(r["llm_grade"] for r in sel)
        ax.text(left + 12, i, f"평균 {mean_g:.2f}", va="center",
                fontsize=10.5, color=t["ink2"], fontweight="bold")
    ax.set_yticks(y, [f"{ko}\n(n={N[src]})" for src, ko in srcs])
    ax.set_xlim(0, 720)
    ax.set_xticks([])
    handles = [plt.Rectangle((0, 0), 1, 1, color=grade_colors[g]) for g in range(4)]
    ax.legend(handles, ["G0 무내용", "G1 단순평가", "G2 속성언급", "G3 구체묘사"],
              frameon=False, ncol=4, loc="upper center",
              bbox_to_anchor=(0.5, -0.04), fontsize=9.5)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title("플랫폼별 표현력 등급 분포  ·  카카오·네이버 사실상 동일",
                 loc="left", fontsize=12.5, fontweight="bold", pad=14)
    fig.tight_layout()
    fig.savefig(OUT / f"11_platform{t['suffix']}.png", dpi=200)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dark", action="store_true")
    args = ap.parse_args()

    src = ROOT / "data" / "analysis" / "llm_exaone3.5_7.8b_full.jsonl"
    rows = [json.loads(l) for l in src.open(encoding="utf-8")]
    OUT.mkdir(parents=True, exist_ok=True)

    t = theme(args.dark)
    setup(t)
    fig_grade_dist(rows, t)
    fig_confusion(rows, t)
    fig_axis_l3(rows, t)
    fig_axis_agreement(rows, t)
    fig_length(rows, t)
    fig_usable(rows, t)
    fig_rating(rows, t)
    fig_scatter_length(rows, t)
    fig_rating_box(rows, t)
    fig_low_reasons(rows, t)
    fig_platform(rows, t)
    print(f"{'다크' if args.dark else '라이트'} 모드 그래프 11종 -> {OUT}")


if __name__ == "__main__":
    main()
