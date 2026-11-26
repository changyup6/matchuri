# -*- coding: utf-8 -*-
"""취향 -> 긍정 평가 상관의 3단 증명.

  ① 존재       취향 모델(행렬분해)이 취향 없는 베이스라인을 held-out 에서 이기는가
  ② 귀속       유저 취향 벡터를 서로 뒤섞으면(순열) 그 개선이 사라지는가
  ③ 용량-반응  취향 학습량을 늘릴수록 예측이 좋아지는 단조 곡선이 나오는가

모델: FunkSVD  r_hat = mu + b_u + b_i + p_u . q_i
  - (mu + b_u + b_i)   = 취향과 무관한 부분(전체 수준 + 개인 후함 + 가게 수준)
  - p_u . q_i          = 상호작용 = '취향'. 증명 대상은 이 항의 기여분.

분할: 유저별 시간순 과거 80% -> train, 미래 20% -> test (누수 차단)

  python analysis/taste_proof.py
"""
from __future__ import annotations

import collections
import json
import math
import random
import statistics
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"

K = 16          # 잠재 취향 차원
LR = 0.01
REG = 0.06
EPOCHS = 30
SEED = 7


# ────────────────────────────────────────────────────────── 데이터
def load_split(mode="random"):
    by_user: dict[str, list[tuple[str, str, int]]] = collections.defaultdict(list)
    for line in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("star") and r.get("date"):
            by_user[r["user_hash"]].append((r["date"], r["store_id"], r["star"]))
    rng = random.Random(SEED)
    train, test = [], []
    for u, rows in by_user.items():
        if mode == "time":
            rows.sort()
        else:
            rng.shuffle(rows)
        k = max(1, int(len(rows) * 0.8))
        for d, s, st in rows[:k]:
            train.append((u, s, st))
        for d, s, st in rows[k:]:
            test.append((u, s, st))
    return train, test


def index(train):
    uids = sorted({u for u, _, _ in train})
    sids = sorted({s for _, s, _ in train})
    uidx = {u: i for i, u in enumerate(uids)}
    sidx = {s: i for i, s in enumerate(sids)}
    return uidx, sidx


# ────────────────────────────────────────────────────────── 모델
def fit(train, uidx, sidx, k=K, epochs=EPOCHS, lr=LR, reg=REG, seed=SEED,
        interaction=True):
    rng = np.random.default_rng(seed)
    nu, ni = len(uidx), len(sidx)
    mu = float(np.mean([st for _, _, st in train]))
    bu = np.zeros(nu)
    bi = np.zeros(ni)
    P = rng.normal(0, 0.05, (nu, k))
    Q = rng.normal(0, 0.05, (ni, k))

    data = [(uidx[u], sidx[s], st) for u, s, st in train]
    order = list(range(len(data)))
    for ep in range(epochs):
        rng.shuffle(order)
        cur_lr = lr * (0.95 ** ep)
        for t in order:
            ui, si, st = data[t]
            pred = mu + bu[ui] + bi[si]
            if interaction:
                pred += P[ui] @ Q[si]
            e = st - pred
            bu[ui] += cur_lr * (e - reg * bu[ui])
            bi[si] += cur_lr * (e - reg * bi[si])
            if interaction:
                pu = P[ui].copy()
                P[ui] += cur_lr * (e * Q[si] - reg * P[ui])
                Q[si] += cur_lr * (e * pu - reg * Q[si])
    return mu, bu, bi, P, Q


def predict(u, s, uidx, sidx, mu, bu, bi, P, Q, use_P=None):
    ui = uidx.get(u)
    si = sidx.get(s)
    pred = mu
    if ui is not None:
        pred += bu[ui]
    if si is not None:
        pred += bi[si]
    if ui is not None and si is not None:
        vec = P if use_P is None else use_P
        pred += vec[ui] @ Q[si]
    return min(5.0, max(1.0, pred))


# ────────────────────────────────────────────────────────── 평가
def mae(preds, actuals):
    return statistics.mean(abs(p - a) for p, a in zip(preds, actuals))


def eval_model(test, uidx, sidx, mu, bu, bi, P, Q, use_P=None,
               interaction=True):
    preds, acts = [], []
    for u, s, st in test:
        ui, si = uidx.get(u), sidx.get(s)
        pred = mu
        if ui is not None:
            pred += bu[ui]
        if si is not None:
            pred += bi[si]
        if interaction and ui is not None and si is not None:
            vec = P if use_P is None else use_P
            pred += vec[ui] @ Q[si]
        preds.append(min(5.0, max(1.0, pred)))
        acts.append(st)
    return preds, acts


def per_user_auc(test, uidx, sidx, mu, bu, bi, P, Q, umean_tr,
                 use_P=None, interaction=True):
    """유저별: 긍정(별점>=본인 train 평균) 가게를 부정 가게보다 높게 랭크하는가."""
    by_u = collections.defaultdict(list)
    for u, s, st in test:
        by_u[u].append((s, st))
    aucs = []
    for u, rows in by_u.items():
        if u not in uidx or u not in umean_tr:
            continue
        thr = umean_tr[u]
        pos, neg = [], []
        for s, st in rows:
            si = sidx.get(s)
            score = 0.0 if si is None else bi[si]
            if interaction and si is not None:
                vec = P if use_P is None else use_P
                score += vec[uidx[u]] @ Q[si]
            (pos if st >= thr else neg).append(score)
        if len(pos) < 1 or len(neg) < 1:
            continue
        wins = sum(1 for p in pos for n in neg if p > n) \
             + 0.5 * sum(1 for p in pos for n in neg if p == n)
        aucs.append(wins / (len(pos) * len(neg)))
    return aucs


def main() -> None:
    random.seed(SEED)
    train, test_all = load_split(mode="random")
    uidx, sidx = index(train)
    # 취향 항이 실제로 계산되는 표본으로 평가: train 에 있고 리뷰어 3명+ 인 가게
    tr_scount = collections.Counter(s for _, s, _ in train)
    test = [(u, s, st) for u, s, st in test_all
            if s in sidx and tr_scount[s] >= 3]
    print(f"train {len(train):,} · test(전체 {len(test_all):,} -> warm3+ {len(test):,}) · "
          f"유저 {len(uidx):,} · 가게 {len(sidx):,}")
    print(f"  평가 표본: train 존재 & 리뷰어 3명+ 가게로 한정(콜드 제거)")

    tr_by_u = collections.defaultdict(list)
    for u, s, st in train:
        tr_by_u[u].append(st)
    umean_tr = {u: statistics.mean(v) for u, v in tr_by_u.items()}
    gmu = statistics.mean(st for _, _, st in train)

    # ── 베이스라인 ──────────────────────────────────────────
    print("\n■ ① 존재 — held-out MAE")
    b_glob = mae([gmu] * len(test), [st for _, _, st in test])
    b_user = mae([umean_tr.get(u, gmu) for u, _, _ in test],
                 [st for _, _, st in test])
    print(f"  전역평균                MAE {b_glob:.4f}")
    print(f"  유저평균                MAE {b_user:.4f}")

    # bias-only 모델 (취향 항 없음)
    mu0, bu0, bi0, P0, Q0 = fit(train, uidx, sidx, interaction=False,
                                epochs=EPOCHS)
    p0, a0 = eval_model(test, uidx, sidx, mu0, bu0, bi0, P0, Q0,
                        interaction=False)
    b_bias = mae(p0, a0)
    print(f"  bias 모델(mu+bu+bi)     MAE {b_bias:.4f}   <- 취향 없는 최강 베이스라인")

    # full MF
    mu1, bu1, bi1, P1, Q1 = fit(train, uidx, sidx, interaction=True,
                                epochs=EPOCHS)
    p1, a1 = eval_model(test, uidx, sidx, mu1, bu1, bi1, P1, Q1)
    m_full = mae(p1, a1)
    print(f"  취향 모델(+ p_u.q_i)    MAE {m_full:.4f}   (개선 {b_bias-m_full:+.4f})")

    # 유저별 쌍대 bootstrap
    err_bias = collections.defaultdict(list)
    err_full = collections.defaultdict(list)
    for (u, s, st), pb, pf in zip(test, p0, p1):
        err_bias[u].append(abs(pb - st))
        err_full[u].append(abs(pf - st))
    diffs = [statistics.mean(err_bias[u]) - statistics.mean(err_full[u])
             for u in err_bias if u in err_full and len(err_bias[u]) >= 3]
    rng = np.random.default_rng(SEED)
    boots = [float(np.mean(rng.choice(diffs, len(diffs), replace=True)))
             for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    frac_pos = sum(1 for d in diffs if d > 0) / len(diffs)
    print(f"  유저별 개선 평균 {statistics.mean(diffs):+.4f} "
          f"[95% CI {lo:+.4f}, {hi:+.4f}] · 개선된 유저 {100*frac_pos:.0f}% "
          f"(n={len(diffs)})")

    # 랭킹 AUC
    auc_bias = per_user_auc(test, uidx, sidx, mu0, bu0, bi0, P0, Q0,
                            umean_tr, interaction=False)
    auc_full = per_user_auc(test, uidx, sidx, mu1, bu1, bi1, P1, Q1,
                            umean_tr, interaction=True)
    print(f"\n  긍정/부정 랭킹 AUC (유저별 평균, 0.5=무작위)")
    print(f"    bias 모델   {statistics.mean(auc_bias):.4f} (n={len(auc_bias)})")
    print(f"    취향 모델   {statistics.mean(auc_full):.4f} (n={len(auc_full)})")

    # ── ② 귀속: 순열 검정 ───────────────────────────────────
    print("\n■ ② 귀속 — 취향 벡터 순열 검정 (5회)")
    perm_maes, perm_aucs = [], []
    for i in range(5):
        prng = np.random.default_rng(100 + i)
        perm = prng.permutation(len(uidx))
        P_perm = P1[perm]
        pp, aa = eval_model(test, uidx, sidx, mu1, bu1, bi1, P1, Q1,
                            use_P=P_perm)
        perm_maes.append(mae(pp, aa))
        pa = per_user_auc(test, uidx, sidx, mu1, bu1, bi1, P1, Q1,
                          umean_tr, use_P=P_perm)
        perm_aucs.append(statistics.mean(pa))
    print(f"  순열 MAE  {statistics.mean(perm_maes):.4f} "
          f"(정상 {m_full:.4f} / bias {b_bias:.4f})")
    print(f"  순열 AUC  {statistics.mean(perm_aucs):.4f} "
          f"(정상 {statistics.mean(auc_full):.4f} / bias {statistics.mean(auc_bias):.4f})")
    print("  해석: 순열값이 bias 수준으로 후퇴하면 개선분은 '개인 취향' 귀속")

    # ── ③ 용량-반응 ────────────────────────────────────────
    print("\n■ ③ 용량-반응 — 취향 학습량별 test MAE (동일 test 셋)")
    caps = [10, 25, 50, 100, 200]
    for cap in caps:
        tr_cap = []
        cnt = collections.Counter()
        # 유저별 test 직전(최신) cap 건만 사용
        by_u = collections.defaultdict(list)
        for u, s, st in train:
            by_u[u].append((s, st))
        for u, rows in by_u.items():
            for s, st in rows[-cap:]:
                tr_cap.append((u, s, st))
        uidx_c, sidx_c = index(tr_cap)
        mu_c, bu_c, bi_c, P_c, Q_c = fit(tr_cap, uidx_c, sidx_c,
                                         epochs=20)
        pc, ac = eval_model(test, uidx_c, sidx_c, mu_c, bu_c, bi_c,
                            P_c, Q_c)
        auc_c = per_user_auc(test, uidx_c, sidx_c, mu_c, bu_c, bi_c,
                             P_c, Q_c, umean_tr)
        print(f"  취향 {cap:>3}건/유저: MAE {mae(pc, ac):.4f} · "
              f"AUC {statistics.mean(auc_c):.4f} · train {len(tr_cap):,}건")

    # 저장
    out = {
        "baseline_global": b_glob, "baseline_user": b_user,
        "baseline_bias": b_bias, "mf_full": m_full,
        "improvement": b_bias - m_full,
        "improved_user_frac": frac_pos,
        "ci95": [float(lo), float(hi)],
        "auc_bias": statistics.mean(auc_bias),
        "auc_full": statistics.mean(auc_full),
        "auc_perm": statistics.mean(perm_aucs),
        "mae_perm": statistics.mean(perm_maes),
    }
    (DATA / "taste_proof.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    print(f"\n저장 -> {DATA/'taste_proof.json'}")


if __name__ == "__main__":
    main()
