# -*- coding: utf-8 -*-
"""메뉴 임베딩 지도 — 역삼동 인기 메뉴 100개를 bge-m3 로 벡터화해 2D 로 투영.

  - 선정: 카테고리 10종 x 각 판매 빈도 상위 10개 (중복 제거)
  - 임베딩: Ollama bge-m3 (1024차원)
  - 투영: t-SNE (sklearn 없음 -> numpy 직접 구현, n=100 이라 정확식으로 충분)

  python analysis/menu_map.py
"""
from __future__ import annotations

import collections
import json
import urllib.request
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "yeoksam"
OUT = ROOT / "data" / "analysis"
FIG = OUT / "figures"

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

CATS = ["한식", "일식", "중식", "양식", "카페", "분식", "간식", "술집",
        "치킨", "샐러드"]
OLLAMA = "http://localhost:11434/api/embed"


# ─────────────────────────────────────────────── 선정
def pick_menus() -> list[tuple[str, str, int]]:
    cat = {json.loads(l)["store_id"]: json.loads(l)["cat2"]
           for l in (DATA / "store_stats.jsonl").open(encoding="utf-8")}
    cnt = collections.defaultdict(collections.Counter)
    for l in (DATA / "menus.jsonl").open(encoding="utf-8"):
        m = json.loads(l)
        c = cat.get(m["store_id"])
        n = (m.get("menu_name") or "").strip()
        if c and n and 2 <= len(n) <= 14:
            cnt[c][n] += 1
    picked, seen = [], set()
    for c in CATS:
        got = 0
        for n, k in cnt[c].most_common(60):
            if n in seen or got >= 10:
                continue
            seen.add(n)
            picked.append((c, n, k))
            got += 1
    return picked


# ─────────────────────────────────────────────── 임베딩
def embed(texts: list[str]) -> np.ndarray:
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": "bge-m3", "input": texts}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    E = np.array(d["embeddings"], dtype=np.float32)
    return E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)


# ─────────────────────────────────────────────── t-SNE (정확식)
def tsne(X, perplexity=12, iters=800, lr=60, seed=7):
    n = len(X)
    D = ((X[:, None, :] - X[None, :, :]) ** 2).sum(-1)

    P = np.zeros((n, n))
    logU = np.log(perplexity)
    for i in range(n):
        d = np.delete(D[i], i)
        lo, hi, beta = 1e-12, 1e12, 1.0
        for _ in range(60):
            p = np.exp(-d * beta)
            s = p.sum() + 1e-12
            H = np.log(s) + beta * (d * p).sum() / s
            if H > logU:
                lo = beta
                beta = beta * 2 if hi > 1e11 else (beta + hi) / 2
            else:
                hi = beta
                beta = (beta + lo) / 2
            if abs(H - logU) < 1e-5:
                break
        p = np.exp(-np.delete(D[i], i) * beta)
        p /= p.sum() + 1e-12
        P[i, np.arange(n) != i] = p
    P = (P + P.T) / (2 * n)
    P = np.maximum(P, 1e-12)

    rng = np.random.default_rng(seed)
    # PCA 초기화
    Xc = X - X.mean(0)
    _, _, Vt = np.linalg.svd(Xc, full_matrices=False)
    Y = (Xc @ Vt[:2].T) * 0.01 + rng.normal(0, 1e-4, (n, 2))
    dY = np.zeros_like(Y)

    for it in range(iters):
        ex = 12.0 if it < 150 else 1.0
        mom = 0.5 if it < 250 else 0.8
        num = 1 / (1 + ((Y[:, None, :] - Y[None, :, :]) ** 2).sum(-1))
        np.fill_diagonal(num, 0)
        Q = np.maximum(num / num.sum(), 1e-12)
        PQ = (P * ex - Q) * num
        grad = 4 * ((PQ.sum(1)[:, None] * Y) - PQ @ Y)
        dY = mom * dY - lr * grad
        Y = Y + dY
        Y -= Y.mean(0)
    return Y


# ─────────────────────────────────────────────── 그림
def draw(rows, Y, dark: bool) -> None:
    fg = "#e8e8e8" if dark else "#1c1c1c"
    bg = "#16181d" if dark else "#ffffff"
    grid = "#2c313c" if dark else "#e3e3e3"
    palette = plt.cm.tab10(np.linspace(0, 1, 10))

    fig, ax = plt.subplots(figsize=(14, 10), dpi=150)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    for ci, c in enumerate(CATS):
        idx = [i for i, (cc, _, _) in enumerate(rows) if cc == c]
        ax.scatter(Y[idx, 0], Y[idx, 1], s=42, color=palette[ci],
                   label=c, zorder=3, edgecolors=bg, linewidths=0.6)
        for i in idx:
            ax.annotate(rows[i][1], (Y[i, 0], Y[i, 1]),
                        xytext=(4, 3), textcoords="offset points",
                        fontsize=8, color=fg, zorder=4)
    ax.set_title("역삼동 인기 메뉴 100개의 임베딩 지도 — bge-m3(1024차원) → t-SNE\n"
                 "색 = 파는 가게의 카테고리 · 위치 = 의미 유사도 (가까울수록 비슷한 음식)",
                 color=fg, fontsize=13, pad=12)
    ax.tick_params(colors=bg)          # 좌표축 숫자는 의미 없어 숨김
    for sp in ax.spines.values():
        sp.set_color(grid)
    ax.grid(color=grid, lw=0.4, alpha=0.5)
    ax.legend(loc="upper right", framealpha=0, labelcolor=fg, fontsize=10,
              ncol=2)
    fig.tight_layout()
    out = FIG / f"menu_map{'_dark' if dark else ''}.png"
    fig.savefig(out, facecolor=bg)
    plt.close(fig)
    print(f"-> {out}")


def main() -> None:
    rows = pick_menus()
    print(f"메뉴 {len(rows)}개 선정")
    E = embed([n for _, n, _ in rows])
    print(f"임베딩 {E.shape}")
    np.savez_compressed(OUT / "menu100_emb.npz",
                        names=np.array([n for _, n, _ in rows], dtype=object),
                        cats=np.array([c for c, _, _ in rows], dtype=object),
                        E=E)
    Y = tsne(E)
    FIG.mkdir(parents=True, exist_ok=True)
    draw(rows, Y, False)
    draw(rows, Y, True)

    # 임베딩이 실제로 의미를 잡는지 즉석 검증: 최근접 이웃 몇 개
    sim = E @ E.T
    np.fill_diagonal(sim, -1)
    names = [n for _, n, _ in rows]
    for probe in ["김치찌개", "아메리카노", "마라탕", "모둠회"]:
        if probe in names:
            i = names.index(probe)
            nb = np.argsort(-sim[i])[:3]
            print(f"  {probe} 이웃: " + ", ".join(
                f"{names[j]}({sim[i, j]:.2f})" for j in nb))


if __name__ == "__main__":
    main()
