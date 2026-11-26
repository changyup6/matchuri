# -*- coding: utf-8 -*-
"""취향-긍정 상관 분석 결과 시각화.

  python analysis/make_taste_figures.py [--dark]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "analysis" / "figures"


def theme(dark: bool) -> dict:
    if dark:
        return dict(bg="#201C18", ink="#EDE7DD", ink2="#C9C0B3", muted="#93897A",
                    grid="#453D33", taste="#8FADD9", rep="#E58A7E",
                    good="#9BBE98", suffix="_dark")
    return dict(bg="#F1EEE8", ink="#2A2521", ink2="#514A42", muted="#857A6C",
                grid="#DED7C9", taste="#34517D", rep="#B23A2E",
                good="#4F6F4E", suffix="")


def setup(t):
    plt.rcParams.update({
        "font.family": "Malgun Gothic", "axes.unicode_minus": False,
        "figure.facecolor": t["bg"], "axes.facecolor": t["bg"],
        "savefig.facecolor": t["bg"], "text.color": t["ink"],
        "axes.labelcolor": t["ink2"], "xtick.color": t["ink2"],
        "ytick.color": t["ink2"], "axes.edgecolor": t["grid"],
        "grid.color": t["grid"], "font.size": 11,
    })


def strip(ax, keep=("left", "bottom")):
    for s in ("top", "right", "left", "bottom"):
        ax.spines[s].set_visible(s in keep)
    ax.tick_params(length=0)


# 실측값 (full_sweep.py 결과)
TH = ["3곳+", "5곳+", "8곳+", "10곳+", "15곳+", "20곳+", "30곳+"]
COVER = [43.0, 23.0, 9.6, 6.2, 2.2, 0.9, 0.7]
TASTE = [61.0, 61.3, 64.0, 64.2, 73.2, 83.5, 86.2]
TASTE_LO = [59.7, 59.4, 61.0, 60.6, 67.3, 75.1, 77.0]
TASTE_HI = [62.4, 63.2, 66.8, 67.7, 78.3, 89.4, 92.1]
REP = [61.4, 62.5, 61.7, 63.4, 69.1, 68.0, 75.0]
REP_LO = [60.0, 60.7, 58.8, 59.7, 63.1, 58.4, 64.5]
REP_HI = [62.8, 64.4, 64.6, 66.9, 74.5, 76.2, 83.2]


def fig_threshold(t):
    x = np.arange(len(TH))
    fig, ax = plt.subplots(figsize=(9.2, 5.0))
    ax.fill_between(x, TASTE_LO, TASTE_HI, color=t["taste"], alpha=0.15, zorder=2)
    ax.fill_between(x, REP_LO, REP_HI, color=t["rep"], alpha=0.12, zorder=2)
    ax.plot(x, TASTE, "o-", color=t["taste"], lw=2.4, ms=8,
            label="취향 가중 (겹침+선호일치)", zorder=4)
    ax.plot(x, REP, "s--", color=t["rep"], lw=2.0, ms=7,
            label="가게 평판 (같은 표본)", zorder=3)
    # 손익분기
    ax.axvline(2, color=t["muted"], ls=":", lw=1.4, zorder=1)
    ax.text(2.05, 55.5, "손익분기 8곳", fontsize=9.5, color=t["muted"])
    for i, (v, c) in enumerate(zip(TASTE, COVER)):
        ax.text(x[i], v + 2.2, f"{v:.1f}%", ha="center", fontsize=9,
                color=t["taste"], fontweight="bold")
        ax.text(x[i], 54.0, f"{c:.1f}%", ha="center", fontsize=8.5,
                color=t["muted"])
    ax.text(-0.55, 54.0, "적용범위", fontsize=8.5, color=t["muted"], ha="right")
    ax.set_xticks(x, TH)
    ax.set_xlabel("두 사람이 공유한 가게 수 (임계값)")
    ax.set_ylabel("선호/비선호 예측 정확도 (%)")
    ax.set_ylim(52, 95)
    ax.grid(axis="y", alpha=0.45, zorder=0)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("겹침이 클수록 취향이 평판을 이긴다  ·  단, 적용 범위는 급감",
                 loc="left", fontsize=13, fontweight="bold", pad=14)
    strip(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"t1_threshold{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_overlap_vs_agree(t):
    """겹침은 취향 정보를 담지 않는다."""
    bands = ["1곳", "2곳", "3-4곳", "5-9곳", "10-19곳", "20-49곳"]
    rate = [58.0, 57.9, 57.7, 57.3, 57.8, 61.8]
    opp = [42, 18, 13, 10, 6, 1]      # 취향 상반 쌍 비율
    x = np.arange(len(bands))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11.4, 4.3))

    a1.plot(x, rate, "o-", color=t["rep"], lw=2.4, ms=8, zorder=3)
    a1.axhline(58.0, color=t["muted"], ls=":", lw=1.2)
    a1.set_xticks(x, bands, fontsize=9)
    a1.set_ylim(50, 70)
    a1.set_ylabel("선호 동일률 (%)")
    a1.grid(axis="y", alpha=0.45, zorder=0)
    a1.set_title("겹침이 늘어도 선호 동일률은 평평",
                 loc="left", fontsize=12, fontweight="bold", pad=12)
    strip(a1)

    a2.bar(x, opp, 0.6, color=t["taste"], zorder=3)
    for i, v in enumerate(opp):
        a2.text(x[i], v + 1, f"{v}%", ha="center", fontsize=9.5, color=t["ink2"])
    a2.set_xticks(x, bands, fontsize=9)
    a2.set_ylabel("취향 상반 쌍 비율 (%)")
    a2.set_ylim(0, 48)
    a2.grid(axis="y", alpha=0.45, zorder=0)
    a2.set_title("다만 '정반대' 쌍은 걸러진다",
                 loc="left", fontsize=12, fontweight="bold", pad=12)
    strip(a2)

    fig.tight_layout()
    fig.savefig(OUT / f"t2_overlap{t['suffix']}.png", dpi=200)
    plt.close(fig)


def fig_methods(t):
    """확장 시도 결과 — 전부 실패."""
    names = ["2-hop 전파", "상반 이웃 반전", "신뢰도 가중",
             "명확한 태도만", "하이브리드 폴백", "최종 조합"]
    gain = [+2.7, +1.5, -1.4, +1.4, +0.1, +0.07]
    cover = [2.3, 2.3, 80.0, 1.3, 100.0, 100.0]
    y = np.arange(len(names))
    colors = [t["good"] if g > 1 else (t["rep"] if g < 0 else t["muted"])
              for g in gain]
    fig, ax = plt.subplots(figsize=(9.2, 4.2))
    ax.barh(y, gain, 0.6, color=colors, zorder=3)
    ax.axvline(0, color=t["ink2"], lw=1.2, zorder=4)
    for i, (g, c) in enumerate(zip(gain, cover)):
        off = 0.12 if g >= 0 else -0.12
        ha = "left" if g >= 0 else "right"
        ax.text(g + off, y[i], f"{g:+.1f}%p  (적용 {c:.0f}%)",
                va="center", ha=ha, fontsize=9.5, color=t["ink2"])
    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.set_xlabel("평판 대비 정확도 개선 (%p)")
    ax.set_xlim(-3.5, 6.5)
    ax.grid(axis="x", alpha=0.45, zorder=0)
    ax.set_title("확장 시도 — 적용 범위를 넓히면 개선이 사라진다",
                 loc="left", fontsize=13, fontweight="bold", pad=14)
    strip(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"t3_methods{t['suffix']}.png", dpi=200)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dark", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    t = theme(a.dark)
    setup(t)
    fig_threshold(t)
    fig_overlap_vs_agree(t)
    fig_methods(t)
    print(f"{'다크' if a.dark else '라이트'} 3종 -> {OUT}")


if __name__ == "__main__":
    main()
