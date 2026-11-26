# -*- coding: utf-8 -*-
"""reco_proto 결과 -> 학습곡선 그림 (라이트/다크).

  python analysis/make_reco_figure.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "data" / "analysis" / "figures"
VARIANTS = {          # 파일 접미사 -> 그림 제목 조각
    "": "전체 풀",
    "_warm": "warm 풀(모집단 리뷰 3+ 가게만)",
}

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

LABELS = {
    "oracle": "오라클(상한)",
    "hybrid": "하이브리드(품질→개인화+TS)",
    "logistic": "로지스틱+Thompson",
    "mf": "MF fold-in",
    "quality": "품질 정렬(베이스라인)",
    "random": "무작위",
}
COLORS = {
    "oracle": "#9e9e9e",
    "hybrid": "#d62728",
    "logistic": "#1f77b4",
    "mf": "#2ca02c",
    "quality": "#ff7f0e",
    "random": "#bdbdbd",
}
STYLE = {"oracle": ":", "random": "--"}


def draw(tag: str, dark: bool) -> None:
    res = ROOT / "data" / "analysis" / f"reco_proto_results{tag}.json"
    d = json.loads(res.read_text(encoding="utf-8"))
    fg = "#e8e8e8" if dark else "#222222"
    bg = "#1e1e1e" if dark else "#ffffff"
    grid = "#3a3a3a" if dark else "#dddddd"

    fig, ax = plt.subplots(figsize=(9, 5.4), dpi=150)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    x = range(1, 101)
    for name in ["oracle", "hybrid", "logistic", "mf", "quality", "random"]:
        ax.plot(x, d["curves"][name]["cum"], color=COLORS[name],
                ls=STYLE.get(name, "-"), lw=2 if name == "hybrid" else 1.6,
                label=LABELS[name])

    ax.axhline(d["pool_like_rate"], color=grid, lw=0.8)
    ax.set_xlabel("스와이프 수", color=fg)
    ax.set_ylabel("누적 선호(오른쪽 스와이프) 비율", color=fg)
    ax.set_title(f"스와이프 추천 정책 비교 — {VARIANTS[tag]} · "
                 f"가상 유저 {d['n_sim_users']}명 replay",
                 color=fg, fontsize=12, pad=10)
    ax.tick_params(colors=fg)
    for sp in ax.spines.values():
        sp.set_color(grid)
    ax.grid(color=grid, lw=0.5, alpha=0.6)
    ax.legend(loc="lower right", framealpha=0, labelcolor=fg, fontsize=9)
    fig.tight_layout()
    out = FIG / f"reco_proto_curve{tag}{'_dark' if dark else ''}.png"
    fig.savefig(out, facecolor=bg)
    plt.close(fig)
    print(f"-> {out}")


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    for tag in VARIANTS:
        p = ROOT / "data" / "analysis" / f"reco_proto_results{tag}.json"
        if p.exists():
            draw(tag, False)
            draw(tag, True)
