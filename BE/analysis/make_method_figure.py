# -*- coding: utf-8 -*-
"""method_compare 결과 -> 방법별 후반 개선폭 비교 그림 (라이트/다크).

  python analysis/make_method_figure.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "data" / "analysis" / "method_compare_results.json"
FIG = ROOT / "data" / "analysis" / "figures"

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

METHODS = ["logistic", "rocchio", "mf", "knn", "ensemble"]
LAB = {"logistic": "로지스틱(서빙 중)", "rocchio": "텍스트 Rocchio",
       "mf": "MF 전이", "knn": "아이템 kNN", "ensemble": "앙상블(로지+텍스트)"}
COL = {"logistic": "#d62728", "rocchio": "#1f77b4", "mf": "#2ca02c",
       "knn": "#9467bd", "ensemble": "#e377c2"}


def draw(dark: bool) -> None:
    d = json.loads(RES.read_text(encoding="utf-8"))
    fg = "#e8e8e8" if dark else "#222222"
    bg = "#1e1e1e" if dark else "#ffffff"
    grid = "#3a3a3a" if dark else "#dddddd"

    personas = list(d["personas"])
    n, m = len(personas), len(METHODS)
    fig, ax = plt.subplots(figsize=(11.5, 5.2), dpi=150)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    width = 0.8 / m
    xs = np.arange(n)
    for k, meth in enumerate(METHODS):
        vals, err = [], [[], []]
        for pname in personas:
            u = d["personas"][pname][meth]["uplift"]
            vals.append(u["mean"])
            err[0].append(u["mean"] - u["ci"][0])
            err[1].append(u["ci"][1] - u["mean"])
        ax.bar(xs + (k - m / 2 + 0.5) * width, vals, width * 0.92,
               yerr=err, error_kw={"ecolor": fg, "lw": 0.9, "capsize": 2},
               color=COL[meth], label=LAB[meth])

    # 오라클 여유(상한-품질)를 회색 점으로 표시
    for i, pname in enumerate(personas):
        head = (d["personas"][pname]["oracle"]["late_mean"]
                - d["personas"][pname]["quality"]["late_mean"])
        ax.plot([i - 0.4, i + 0.4], [head, head], color=grid, lw=1.2, ls=":")

    ax.axhline(0, color=fg, lw=0.8)
    ax.set_xticks(xs)
    ax.set_xticklabels(personas, color=fg, fontsize=11)
    ax.set_ylabel("후반(31~80장) 선호율 — 품질정렬 대비", color=fg)
    ax.set_title("개인화 방법 정면 비교 — 페르소나 6종 × 20회 · 95% CI · "
                 "점선 = 오라클 여유(상한-품질)", color=fg, fontsize=12, pad=10)
    ax.tick_params(colors=fg)
    for sp in ax.spines.values():
        sp.set_color(grid)
    ax.grid(axis="y", color=grid, lw=0.5, alpha=0.6)
    ax.legend(loc="upper left", framealpha=0, labelcolor=fg, fontsize=9, ncol=2)
    fig.tight_layout()
    out = FIG / f"method_compare{'_dark' if dark else ''}.png"
    fig.savefig(out, facecolor=bg)
    plt.close(fig)
    print(f"-> {out}")


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    draw(False)
    draw(True)
