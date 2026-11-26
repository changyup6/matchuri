# -*- coding: utf-8 -*-
"""방법별 학습곡선: 스와이프 횟수에 따른 적중률(선호율) 변화.

method_compare 의 누적곡선에서 시점별 적중률을 복원해 이동평균(15장)으로 그린다.
'몇 장째부터 얼마나 좋아지는가'가 방법별로 보인다.

  python analysis/make_learning_curves.py
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

ORDER = ["oracle", "quality", "logistic", "rocchio", "mf", "knn", "ensemble"]
LAB = {"oracle": "오라클(상한)", "quality": "품질 정렬",
       "logistic": "로지스틱(서빙 중)", "rocchio": "텍스트 Rocchio",
       "mf": "MF 전이", "knn": "아이템 kNN", "ensemble": "앙상블"}
COL = {"oracle": "#9e9e9e", "quality": "#ff7f0e", "logistic": "#d62728",
       "rocchio": "#1f77b4", "mf": "#2ca02c", "knn": "#9467bd",
       "ensemble": "#e377c2"}
STY = {"oracle": ":", "quality": "--"}
WIN = 15


def per_swipe(cum):
    """누적평균 -> 시점별 적중률 복원 후 이동평균."""
    cum = np.asarray(cum)
    t = np.arange(1, len(cum) + 1)
    rate = np.empty_like(cum)
    rate[0] = cum[0]
    rate[1:] = cum[1:] * t[1:] - cum[:-1] * t[:-1]
    out = np.empty_like(rate)
    for i in range(len(rate)):
        out[i] = rate[max(0, i - WIN + 1):i + 1].mean()
    return out


def draw(dark: bool) -> None:
    d = json.loads(RES.read_text(encoding="utf-8"))
    fg = "#e8e8e8" if dark else "#222222"
    bg = "#1e1e1e" if dark else "#ffffff"
    grid = "#3a3a3a" if dark else "#dddddd"

    personas = list(d["personas"])
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 7.2), dpi=150, sharex=True)
    fig.patch.set_facecolor(bg)
    x = np.arange(1, d["n_swipes"] + 1)
    for ax, pname in zip(axes.flat, personas):
        ax.set_facecolor(bg)
        for pol in ORDER:
            ax.plot(x, per_swipe(d["personas"][pname][pol]["cum"]),
                    color=COL[pol], ls=STY.get(pol, "-"),
                    lw=2.2 if pol == "logistic" else 1.4, label=LAB[pol],
                    alpha=1.0 if pol in ("logistic", "quality", "oracle") else 0.85)
        ax.set_title(pname, color=fg, fontsize=11)
        ax.tick_params(colors=fg, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(grid)
        ax.grid(color=grid, lw=0.4, alpha=0.6)
        ax.set_ylim(0.28, 1.02)
    for ax in axes[1]:
        ax.set_xlabel("스와이프 수", color=fg, fontsize=9)
    for ax in axes[:, 0]:
        ax.set_ylabel(f"적중률(최근 {WIN}장)", color=fg, fontsize=9)
    axes[0, 0].legend(loc="lower left", framealpha=0, labelcolor=fg,
                      fontsize=7.5, ncol=2)
    fig.suptitle("스와이프 횟수별 적중률 학습곡선 — 방법 7종 × 페르소나 6종 "
                 f"(20회 반복 평균, {WIN}장 이동평균)",
                 color=fg, fontsize=12.5, y=0.995)
    fig.tight_layout()
    out = FIG / f"learning_curves{'_dark' if dark else ''}.png"
    fig.savefig(out, facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out}")


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    draw(False)
    draw(True)
