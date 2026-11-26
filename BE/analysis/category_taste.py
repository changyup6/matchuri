# -*- coding: utf-8 -*-
"""카테고리 수준에서 개인 취향이 존재하는가.

앞선 실패
  - 가게 축 MF: b_i(가게 평판)가 취향 항을 다 흡수 -> 취향 기여 0
  - 카테고리 코사인 유사도: 축이 적어 ±1 극단화, 상관 오히려 악화

이번에 다른 점
  '아이템'을 가게가 아니라 카테고리로 두고 MF 를 학습한다.
  밀도가 0.09% -> 수십 % 로 오르므로 b_c(카테고리 평균)와 p_u.q_c(개인 취향)를
  분리해 추정할 수 있다. 핵심 질문은 하나:

    "이 사람이 어떤 카테고리를 남들보다 더 좋아하는가"가 실재하는가?
    = p_u.q_c 가 b_c 대비 held-out 예측을 개선하는가?

  개선되면 카테고리 수준 취향은 실재 -> 접합률 상승이 의미를 갖는다.
  개선 안 되면 카테고리는 취향 축으로 부적합 -> 이 방향 종료.

  python analysis/category_taste.py
"""
from __future__ import annotations

import collections
import json
import random
import statistics
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"
SEED = 7
K = 8
EPOCHS = 40
LR = 0.02
REG = 0.05


def load():
    cat = {}
    for l in (DATA / "store_meta.jsonl").open(encoding="utf-8"):
        m = json.loads(l)
        c = m.get("cat3") or m.get("cat2")
        if c:
            cat[m["store_id"]] = c
    rows = []
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star") and r["store_id"] in cat:
            rows.append((r["user_hash"], cat[r["store_id"]], r["star"],
                         r["store_id"]))
    return rows


def fit(train, uidx, cidx, interaction=True, k=K, epochs=EPOCHS,
        lr=LR, reg=REG, seed=SEED):
    rng = np.random.default_rng(seed)
    mu = float(np.mean([st for _, _, st in train]))
    bu = np.zeros(len(uidx))
    bc = np.zeros(len(cidx))
    P = rng.normal(0, 0.05, (len(uidx), k))
    Q = rng.normal(0, 0.05, (len(cidx), k))
    data = [(uidx[u], cidx[c], st) for u, c, st in train]
    order = list(range(len(data)))
    for ep in range(epochs):
        rng.shuffle(order)
        cur = lr * (0.95 ** ep)
        for t in order:
            ui, ci, st = data[t]
            pred = mu + bu[ui] + bc[ci]
            if interaction:
                pred += P[ui] @ Q[ci]
            e = st - pred
            bu[ui] += cur * (e - reg * bu[ui])
            bc[ci] += cur * (e - reg * bc[ci])
            if interaction:
                pu = P[ui].copy()
                P[ui] += cur * (e * Q[ci] - reg * P[ui])
                Q[ci] += cur * (e * pu - reg * Q[ci])
    return mu, bu, bc, P, Q


def predict_all(test, uidx, cidx, mu, bu, bc, P, Q, interaction=True,
                use_P=None):
    out = []
    for u, c, st in test:
        ui, ci = uidx.get(u), cidx.get(c)
        p = mu
        if ui is not None:
            p += bu[ui]
        if ci is not None:
            p += bc[ci]
        if interaction and ui is not None and ci is not None:
            v = P if use_P is None else use_P
            p += v[ui] @ Q[ci]
        out.append(min(5.0, max(1.0, p)))
    return out


def mae(pred, act):
    return statistics.mean(abs(p - a) for p, a in zip(pred, act))


def main() -> None:
    rows = load()
    print(f"카테고리 매칭 리뷰 {len(rows):,}건 · "
          f"카테고리 {len({c for _, c, _, _ in rows})}종 · "
          f"유저 {len({u for u, _, _, _ in rows}):,}명")

    # 유저-카테고리 단위로 집계(같은 카테고리 여러 번 방문 -> 개별 평점 유지)
    rng = random.Random(SEED)
    data = [(u, c, st) for u, c, st, _ in rows]
    rng.shuffle(data)
    k = int(len(data) * 0.8)
    train, test = data[:k], data[k:]

    uidx = {u: i for i, u in enumerate(sorted({u for u, _, _ in train}))}
    cidx = {c: i for i, c in enumerate(sorted({c for _, c, _ in train}))}
    test = [(u, c, st) for u, c, st in test if u in uidx and c in cidx]
    print(f"train {len(train):,} · test {len(test):,} · "
          f"밀도 {100*len(train)/(len(uidx)*len(cidx)):.1f}%")

    acts = [st for _, _, st in test]
    gmu = statistics.mean(st for _, _, st in train)
    umean = collections.defaultdict(list)
    for u, c, st in train:
        umean[u].append(st)
    um = {u: statistics.mean(v) for u, v in umean.items()}

    print("\n■ held-out MAE")
    print(f"  전역평균                 {mae([gmu]*len(test), acts):.4f}")
    print(f"  유저평균                 {mae([um.get(u,gmu) for u,_,_ in test], acts):.4f}")

    mu0, bu0, bc0, P0, Q0 = fit(train, uidx, cidx, interaction=False)
    p0 = predict_all(test, uidx, cidx, mu0, bu0, bc0, P0, Q0,
                     interaction=False)
    b_bias = mae(p0, acts)
    print(f"  bias(mu+bu+bc)           {b_bias:.4f}   <- 취향 없음")

    mu1, bu1, bc1, P1, Q1 = fit(train, uidx, cidx, interaction=True)
    p1 = predict_all(test, uidx, cidx, mu1, bu1, bc1, P1, Q1)
    m_full = mae(p1, acts)
    print(f"  + 카테고리 취향(p_u.q_c)  {m_full:.4f}   (개선 {b_bias-m_full:+.4f})")

    # 유저별 개선 + bootstrap
    eb = collections.defaultdict(list)
    ef = collections.defaultdict(list)
    for (u, c, st), a, b in zip(test, p0, p1):
        eb[u].append(abs(a - st))
        ef[u].append(abs(b - st))
    diffs = [statistics.mean(eb[u]) - statistics.mean(ef[u])
             for u in eb if len(eb[u]) >= 3]
    nrng = np.random.default_rng(SEED)
    boots = [float(np.mean(nrng.choice(diffs, len(diffs), replace=True)))
             for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    print(f"  유저별 개선 {statistics.mean(diffs):+.4f} "
          f"[95% CI {lo:+.4f}, {hi:+.4f}] · 개선유저 "
          f"{100*sum(1 for d in diffs if d>0)/len(diffs):.0f}% (n={len(diffs)})")

    # 순열 검정
    print("\n■ 귀속 — 취향 벡터 순열 (5회)")
    pm = []
    for i in range(5):
        prng = np.random.default_rng(200 + i)
        Pp = P1[prng.permutation(len(uidx))]
        pm.append(mae(predict_all(test, uidx, cidx, mu1, bu1, bc1, P1, Q1,
                                  use_P=Pp), acts))
    print(f"  순열 MAE {statistics.mean(pm):.4f} "
          f"(정상 {m_full:.4f} / bias {b_bias:.4f})")
    if m_full < b_bias:
        recover = (statistics.mean(pm) - m_full) / (b_bias - m_full)
        print(f"  순열 시 개선분의 {100*recover:.0f}% 소멸 "
              f"-> 그만큼이 '개인 취향' 귀속")

    print("\n■ 카테고리 취향의 실제 크기 (개인별 편차)")
    dev = collections.defaultdict(list)
    for u, c, st in train:
        dev[(u, c)].append(st)
    spread = []
    byc = collections.defaultdict(list)
    for (u, c), v in dev.items():
        byc[c].append(statistics.mean(v))
    for c, means in byc.items():
        if len(means) >= 30:
            spread.append((statistics.pstdev(means), c, len(means)))
    spread.sort(reverse=True)
    print("  카테고리별 '사람마다 다른 정도'(유저 평균평점의 표준편차) 상위 8")
    for sd, c, n in spread[:8]:
        print(f"    {c:<16} sd={sd:.3f} (n={n})")
    print("  하위 3(=누구나 비슷하게 평가)")
    for sd, c, n in spread[-3:]:
        print(f"    {c:<16} sd={sd:.3f} (n={n})")


if __name__ == "__main__":
    main()
