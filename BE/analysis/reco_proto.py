# -*- coding: utf-8 -*-
"""스와이프 추천 프로토타입 + 오프라인 시뮬레이터.

전국 user_graph 의 헤비 리뷰어를 '가상 스와이프 유저'로 쓴다:
  - 그 유저가 실제 평가한 가게들 = 후보 풀
  - 별점 >= 4  -> 선호(오른쪽), 아니면 비선호(왼쪽)
  - 정책이 다음 카드를 고르면 실제 라벨을 공개(replay 평가)

정책
  random      무작위
  quality     베이지안 평균(모집단 리뷰) 정렬 — 우리 실험의 최강 무개인화 베이스라인
  logistic    온라인 베이지안 로지스틱 + Thompson (카테고리·품질 피처)
  mf          모집단 FunkSVD 가게 벡터 고정 + 유저 벡터 fold-in(리지)
  hybrid      quality -> logistic+mf 로 스와이프 수에 따라 가중 이동 + 다양성 제약

주의: 전국 데이터는 카테고리 커버리지가 리뷰 기준 17.7%뿐이다(수집 대상 3.6천 곳만
메타 보유). 실서비스(역삼동)는 100% 라 logistic/hybrid 는 여기 수치가 하한선이다.

  python analysis/reco_proto.py
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"
OUT = ROOT / "data" / "analysis"

SEED = 7
N_SIM_USERS = 250        # 가상 유저 수
POOL_CAP = 200           # 유저당 후보 카드 수
N_SWIPES = 100           # 유저당 스와이프 수
LIKE_THR = 4             # star >= 4 -> 선호
K = 16                   # MF 잠재차원
MF_EPOCHS = 15
BAYES_C = 10.0

TOP_CATS = ["한식", "일식", "카페", "양식", "중식", "술집", "간식", "분식"]


# ────────────────────────────────────────────────────── 데이터
def load():
    meta = {}
    for l in (DATA / "store_meta.jsonl").open(encoding="utf-8"):
        m = json.loads(l)
        meta[m["store_id"]] = m.get("cat2")

    by_user = collections.defaultdict(lambda: collections.defaultdict(list))
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            by_user[r["user_hash"]][r["store_id"]].append(r["star"])
    # 같은 가게 중복 리뷰는 평균 1개로
    rated = {u: {s: statistics.mean(v) for s, v in d.items()}
             for u, d in by_user.items()}
    return meta, rated


def split_users(rated):
    """시뮬 유저: 60가게+ 이고 선호율이 0.25~0.75 (라벨이 갈리는 유저만)."""
    rng = random.Random(SEED)
    cands = []
    for u, d in rated.items():
        if len(d) < 60:
            continue
        lr = sum(1 for st in d.values() if st >= LIKE_THR) / len(d)
        if 0.25 <= lr <= 0.75:
            cands.append(u)
    cands.sort()
    rng.shuffle(cands)
    sim = set(cands[:N_SIM_USERS])
    pop = {u for u in rated if u not in sim}
    return sim, pop


# ────────────────────────────────────────────────────── 모집단 통계
def build_priors(rated, pop_users):
    cnt = collections.Counter()
    tot = collections.Counter()
    for u in pop_users:
        for s, st in rated[u].items():
            cnt[s] += 1
            tot[s] += st
    mu = sum(tot.values()) / sum(cnt.values())
    bayes = {s: (BAYES_C * mu + tot[s]) / (BAYES_C + cnt[s]) for s in cnt}
    vals = np.array(list(bayes.values()))
    m, sd = vals.mean(), vals.std() or 1.0
    qz = {s: (v - m) / sd for s, v in bayes.items()}
    return qz, cnt, mu


def fit_mf(rated, pop_users):
    """taste_proof 와 같은 FunkSVD. 모집단만으로 학습해 가게 벡터를 얻는다.

    분할이 SEED 로 결정적이라 결과를 캐시해도 안전하다(7분 -> 즉시).
    """
    cache = OUT / "mf_cache.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        sids = [str(s) for s in z["sids"]]
        print(f"    캐시 사용: {cache.name} ({len(sids):,}곳)")
        return {s: i for i, s in enumerate(sids)}, z["Q"]
    train = [(u, s, st) for u in sorted(pop_users)
             for s, st in sorted(rated[u].items())]
    uidx = {u: i for i, u in enumerate(sorted({t[0] for t in train}))}
    sidx = {s: i for i, s in enumerate(sorted({t[1] for t in train}))}
    rng = np.random.default_rng(SEED)
    mu = statistics.mean(t[2] for t in train)
    bu = np.zeros(len(uidx))
    bi = np.zeros(len(sidx))
    P = rng.normal(0, 0.05, (len(uidx), K))
    Q = rng.normal(0, 0.05, (len(sidx), K))
    data = [(uidx[u], sidx[s], st) for u, s, st in train]
    order = np.arange(len(data))
    lr0, reg = 0.01, 0.06
    for ep in range(MF_EPOCHS):
        rng.shuffle(order)
        lr = lr0 * (0.95 ** ep)
        for t in order:
            ui, si, st = data[t]
            e = st - (mu + bu[ui] + bi[si] + P[ui] @ Q[si])
            bu[ui] += lr * (e - reg * bu[ui])
            bi[si] += lr * (e - reg * bi[si])
            pu = P[ui].copy()
            P[ui] += lr * (e * Q[si] - reg * P[ui])
            Q[si] += lr * (e * pu - reg * Q[si])
        print(f"    MF epoch {ep+1}/{MF_EPOCHS}", flush=True)
    np.savez_compressed(cache, sids=np.array(sorted(sidx), dtype=object), Q=Q)
    return sidx, Q


# ────────────────────────────────────────────────────── 피처
def featurize(pool, meta, qz, cnt):
    """가게 -> 피처행렬. [bias, 품질z, log리뷰수, cat 원핫(8+기타+미상)]"""
    d = 3 + len(TOP_CATS) + 2
    X = np.zeros((len(pool), d))
    for i, s in enumerate(pool):
        X[i, 0] = 1.0
        X[i, 1] = qz.get(s, 0.0)
        X[i, 2] = np.log1p(cnt.get(s, 0)) / 6.0
        c = meta.get(s)
        if c is None:
            X[i, 3 + len(TOP_CATS) + 1] = 1.0        # 미상
        elif c in TOP_CATS:
            X[i, 3 + TOP_CATS.index(c)] = 1.0
        else:
            X[i, 3 + len(TOP_CATS)] = 1.0            # 기타
    return X


# ────────────────────────────────────────────────────── 정책
class Random_:
    name = "random"

    def __init__(self, ctx):
        self.rng = random.Random(ctx["seed"])

    def pick(self, rem):
        return self.rng.choice(sorted(rem))

    def update(self, s, like):
        pass


class Quality:
    name = "quality"

    def __init__(self, ctx):
        self.qz = ctx["qz"]

    def pick(self, rem):
        return max(sorted(rem), key=lambda s: self.qz.get(s, 0.0))

    def update(self, s, like):
        pass


class LogisticTS:
    """대각 라플라스 근사 온라인 베이지안 로지스틱 + Thompson."""
    name = "logistic"

    def __init__(self, ctx):
        self.X, self.ids = ctx["X"], ctx["ids"]
        self.pos = {s: i for i, s in enumerate(self.ids)}
        d = self.X.shape[1]
        self.m = np.zeros(d)
        self.v = np.full(d, 1.0)
        self.rng = np.random.default_rng(ctx["seed"])

    def pick(self, rem):
        w = self.rng.normal(self.m, np.sqrt(self.v))
        rem_s = sorted(rem)
        idx = [self.pos[s] for s in rem_s]
        sc = self.X[idx] @ w
        return rem_s[int(np.argmax(sc))]

    def update(self, s, like):
        x = self.X[self.pos[s]]
        p = 1 / (1 + np.exp(-x @ self.m))
        g = (p - (1.0 if like else 0.0)) * x
        self.v = 1 / (1 / self.v + p * (1 - p) * x * x)
        self.m -= self.v * g


class MFFoldIn:
    """가게 벡터 고정, 유저 벡터만 스와이프로 리지 추정. 콜드는 품질로."""
    name = "mf"

    def __init__(self, ctx):
        self.qz, self.sidx, self.Q = ctx["qz"], ctx["sidx"], ctx["Q"]
        self.cnt = ctx["cnt"]
        self.obs = []
        self.p = np.zeros(K)

    def _q(self, s):
        i = self.sidx.get(s)
        return self.Q[i] if (i is not None and self.cnt.get(s, 0) >= 3) else None

    def pick(self, rem):
        def sc(s):
            q = self._q(s)
            return 0.6 * self.qz.get(s, 0.0) + (self.p @ q if q is not None else 0.0)
        return max(sorted(rem), key=sc)

    def update(self, s, like):
        q = self._q(s)
        if q is None:
            return
        self.obs.append((self.sidx[s], 1.0 if like else -1.0))
        Qs = self.Q[[i for i, _ in self.obs]]
        y = np.array([v for _, v in self.obs])
        self.p = np.linalg.solve(Qs.T @ Qs + 0.5 * np.eye(K), Qs.T @ y)


class HybridTS:
    """품질 -> 개인화로 가중 이동 + Thompson + cat2 연속 3장 금지."""
    name = "hybrid"

    def __init__(self, ctx):
        self.lg = LogisticTS(ctx)
        self.mf = MFFoldIn(ctx)
        self.qz, self.meta = ctx["qz"], ctx["meta"]
        self.n = 0
        self.last_cats = []

    def pick(self, rem):
        rem_s = sorted(rem)
        w_q = 1 / (1 + self.n / 15)
        w_m = 0.8 * min(1.0, max(0.0, (self.n - 15) / 30))
        w = self.lg.rng.normal(self.lg.m, np.sqrt(self.lg.v))
        idx = [self.lg.pos[s] for s in rem_s]
        lg_sc = self.lg.X[idx] @ w
        scored = []
        for j, s in enumerate(rem_s):
            q = self.mf._q(s)
            mf_sc = self.mf.p @ q if q is not None else 0.0
            scored.append((w_q * self.qz.get(s, 0.0)
                           + (1 - w_q) * lg_sc[j] + w_m * mf_sc, s))
        scored.sort(key=lambda x: (-x[0], x[1]))
        ban = (len(self.last_cats) >= 2
               and self.last_cats[-1] is not None
               and self.last_cats[-1] == self.last_cats[-2])
        for _, s in scored:
            if ban and self.meta.get(s) == self.last_cats[-1]:
                continue
            return s
        return scored[0][1]

    def update(self, s, like):
        self.n += 1
        self.last_cats.append(self.meta.get(s))
        self.lg.update(s, like)
        self.mf.update(s, like)


POLICIES = [Random_, Quality, LogisticTS, MFFoldIn, HybridTS]


# ────────────────────────────────────────────────────── 시뮬레이션
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warm", action="store_true",
                    help="후보 풀을 모집단 리뷰 3+ 가게로 제한 "
                         "(아이템에 신호가 있는 실서비스 조건의 근사)")
    args = ap.parse_args()
    tag = "_warm" if args.warm else ""

    print("데이터 로딩...", flush=True)
    meta, rated = load()
    sim, pop = split_users(rated)
    print(f"  유저 {len(rated):,} -> 시뮬 {len(sim)} · 모집단 {len(pop):,}"
          + (" · warm 풀" if args.warm else ""))

    print("모집단 품질 사전확률...", flush=True)
    qz, cnt, mu = build_priors(rated, pop)
    print(f"  가게 {len(qz):,}곳 · 전역평균 {mu:.2f}")

    print("모집단 FunkSVD 학습...", flush=True)
    sidx, Q = fit_mf(rated, pop)

    curves = {P.name: np.zeros(N_SWIPES) for P in POLICIES}
    curves["oracle"] = np.zeros(N_SWIPES)
    counts = np.zeros(N_SWIPES)
    pool_rates = []

    n_used = 0
    for ui, u in enumerate(sorted(sim), 1):
        rng = random.Random(SEED * 1000 + ui)
        pool = sorted(rated[u])
        if args.warm:
            pool = [s for s in pool if cnt.get(s, 0) >= 3]
            if len(pool) < 40:        # 풀이 너무 작으면 곡선이 노이즈가 된다
                continue
        n_used += 1
        rng.shuffle(pool)
        pool = pool[:POOL_CAP]
        labels = {s: rated[u][s] >= LIKE_THR for s in pool}
        n_like = sum(labels.values())
        pool_rates.append(n_like / len(pool))
        steps = min(N_SWIPES, len(pool))
        counts[:steps] += 1

        # oracle: 선호를 전부 먼저 보여주는 상한
        for t in range(steps):
            curves["oracle"][t] += 1.0 if t < n_like else 0.0

        X = featurize(pool, meta, qz, cnt)
        ctx = {"qz": qz, "cnt": cnt, "meta": meta, "sidx": sidx, "Q": Q,
               "X": X, "ids": pool, "seed": SEED * 1000 + ui}
        for P in POLICIES:
            pol = P(ctx)
            rem = set(pool)
            for t in range(steps):
                s = pol.pick(rem)
                rem.discard(s)
                like = labels[s]
                pol.update(s, like)
                curves[P.name][t] += 1.0 if like else 0.0
        if ui % 25 == 0:
            print(f"  {ui}/{len(sim)} 유저", flush=True)

    # 스와이프 시점별 평균 적중률 -> 누적 평균
    res = {}
    for name, arr in curves.items():
        rate = arr / np.maximum(counts, 1)
        cum = np.cumsum(rate) / np.arange(1, N_SWIPES + 1)
        res[name] = {"per_swipe": rate.tolist(), "cum": cum.tolist()}

    base = statistics.mean(pool_rates)
    print(f"\n유저 {n_used}명 · 풀 평균 선호율(=random 기대) {base:.3f}\n")
    print(f"{'정책':<10} {'@10':>7} {'@30':>7} {'@60':>7} {'@100':>7}")
    order = ["oracle", "hybrid", "logistic", "mf", "quality", "random"]
    for name in order:
        c = res[name]["cum"]
        print(f"{name:<10} {c[9]:>7.3f} {c[29]:>7.3f} {c[59]:>7.3f} {c[99]:>7.3f}")

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"reco_proto_results{tag}.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"pool_like_rate": base, "n_sim_users": n_used,
                   "curves": res}, f, ensure_ascii=False)
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
