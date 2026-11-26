# -*- coding: utf-8 -*-
"""swipe_test 결과 -> 페르소나별 학습곡선 (라이트/다크).

  python analysis/make_swipe_figure.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "data" / "analysis" / "swipe_test_results.json"
FIG = ROOT / "data" / "analysis" / "figures"

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

POLS = ["oracle", "hybrid", "quality", "random"]
LAB = {"oracle": "오라클(상한)", "hybrid": "hybrid(서빙 중)",
       "quality": "품질 정렬", "random": "무작위"}
COL = {"oracle": "#9e9e9e", "hybrid": "#d62728",
       "quality": "#ff7f0e", "random": "#bdbdbd"}
STY = {"oracle": ":", "random": "--"}


def draw(dark: bool) -> None:
    d = json.loads(RES.read_text(encoding="utf-8"))
    fg = "#e8e8e8" if dark else "#222222"
    bg = "#1e1e1e" if dark else "#ffffff"
    grid = "#3a3a3a" if dark else "#dddddd"

    names = list(d["personas"])
    fig, axes = plt.subplots(1, len(names), figsize=(15, 3.4), dpi=150,
                             sharey=True)
    fig.patch.set_facecolor(bg)
    x = range(1, d["n_swipes"] + 1)
    for ax, pname in zip(axes, names):
        ax.set_facecolor(bg)
        for pol in POLS:
            ax.plot(x, d["personas"][pname][pol]["cum"], color=COL[pol],
                    ls=STY.get(pol, "-"), lw=2 if pol == "hybrid" else 1.4,
                    label=LAB[pol])
        u = d["personas"][pname]["uplift_late"]
        ax.set_title(f"{pname}\n후반 {u['mean']:+.1%} "
                     f"[{u['ci'][0]:+.1%},{u['ci'][1]:+.1%}]",
                     color=fg, fontsize=10)
        ax.tick_params(colors=fg, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(grid)
        ax.grid(color=grid, lw=0.4, alpha=0.6)
        ax.set_xlabel("스와이프", color=fg, fontsize=9)
    axes[0].set_ylabel("누적 선호율", color=fg, fontsize=9)
    axes[0].legend(loc="lower left", framealpha=0, labelcolor=fg, fontsize=7.5)
    fig.suptitle("서빙 중인 스와이프 추천의 효과 — 페르소나 5종 × 20회 반복 "
                 "(후반 = 31~80장에서 hybrid-품질 차이, 95% CI)",
                 color=fg, fontsize=11, y=1.04)
    fig.tight_layout()
    out = FIG / f"swipe_test{'_dark' if dark else ''}.png"
    fig.savefig(out, facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out}")


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    draw(False)
    draw(True)
