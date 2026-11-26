# -*- coding: utf-8 -*-
"""스와이프 개인화 방법 비교 — 같은 테스트베드에서 정면 대결.

방법 (개인 점수 산출기만 다르고 나머지는 전부 동일):
  logistic   카테고리·가격 온라인 로지스틱 + 완화 Thompson (현재 서빙 중)
  rocchio    가게 텍스트(리뷰·메뉴·태그) LSA-128 임베딩 + Rocchio 유저벡터
  mf         전국 FunkSVD 가게벡터 전이 + 유저 fold-in (커버 1,051/2,801곳)
  knn        역삼 리뷰 작성자 겹침 기반 아이템-아이템 협업 (쌍 70,598개)
  ensemble   logistic + rocchio 개인점수 평균

공통 스캐폴딩: total = w_q(n)·품질z + (1-w_q)·z(개인점수), 같은 카테고리 연속
3장 금지, 같은 (run,가게)->같은 라벨(CRN). 즉 순수하게 '개인화 표현력'만 비교.

페르소나에 '매운맛러'를 추가: 취향이 텍스트에만 있고 카테고리·가격 피처로는
표현 불가 -> logistic 의 홈그라운드 밖에서 각 방법의 한계를 드러낸다.

  python analysis/method_compare.py
"""
from __future__ import annotations

import collections
import json
import random
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from recsys import swipe as sw
import analysis.swipe_test as T

DATA = ROOT / "data" / "yeoksam"
OUT = ROOT / "data" / "analysis"

N_RUNS = 20
N_SWIPES = 80
SEED = 11
LSA_DIM = 128

SPICY_TOKENS = ["매운", "매콤", "마라", "불닭", "짬뽕", "떡볶이", "김치찌",
                "매워", "얼큰", "불맛", "청양", "짜글"]


# ────────────────────────────────────────────────── 가게 텍스트 -> LSA
def build_profiles(ids: list[str]) -> dict[str, str]:
    tags = {}
    for l in (DATA / "store_info.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        tags[r["store_id"]] = " ".join(r.get("tags") or [])
    menus = collections.defaultdict(list)
    for l in (DATA / "menus.jsonl").open(encoding="utf-8"):
        m = json.loads(l)
        menus[m["store_id"]].append(
            (m.get("menu_name") or "") + " " + (m.get("description") or ""))
    revs = collections.defaultdict(list)
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        revs[r["store_id"]].append(
            (r.get("content") or "") + " " + (r.get("strength_tags") or ""))
    prof = {}
    for sid in ids:
        s = sw.pool()["cat"].get(sid) or ""
        txt = " ".join([s, tags.get(sid, ""),
                        " ".join(menus[sid][:40]),
                        " ".join(revs[sid])])
        prof[sid] = txt[:2500]
    return prof


def lsa_embed(ids, prof):
    """char 2·3그램 TF-IDF -> LSA. sklearn 없이 X·X^T 고유분해로 얻는다."""
    cache = OUT / "yeoksam_lsa.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if list(z["ids"]) == ids:
            print("  LSA 캐시 사용")
            return z["E"]

    def grams(t):
        t = "".join(t.split())
        return [t[i:i+2] for i in range(len(t)-1)] + \
               [t[i:i+3] for i in range(len(t)-2)]

    df = collections.Counter()
    docs = []
    for sid in ids:
        g = collections.Counter(grams(prof[sid]))
        docs.append(g)
        df.update(g.keys())
    n = len(ids)
    vocab = [w for w, c in df.items() if 3 <= c <= n * 0.5]
    vocab = sorted(vocab, key=lambda w: -df[w])[:20000]
    vidx = {w: i for i, w in enumerate(vocab)}
    idf = np.log(n / np.array([df[w] for w in vocab]))
    print(f"  어휘 {len(vocab):,} · 문서 {n:,}")

    X = np.zeros((n, len(vocab)), dtype=np.float32)
    for i, g in enumerate(docs):
        tot = sum(g.values()) or 1
        for w, c in g.items():
            j = vidx.get(w)
            if j is not None:
                X[i, j] = c / tot * idf[j]
    X /= (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)

    G = X @ X.T                       # 2045x2045 — 고유분해 가능 크기
    vals, vecs = np.linalg.eigh(G.astype(np.float64))
    top = np.argsort(-vals)[:LSA_DIM]
    E = vecs[:, top] * np.sqrt(np.maximum(vals[top], 1e-9))
    E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    np.savez_compressed(cache, ids=np.array(ids, dtype=object),
                        E=E.astype(np.float32))
    return E.astype(np.float32)


# ────────────────────────────────────────────────── 아이템 kNN (작성자 겹침)
def build_knn(ids):
    by = collections.defaultdict(set)
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("author_id"):
            by[r["author_id"]].add(r["store_id"])
    idset = set(ids)
    n_auth = collections.Counter()
    co = collections.defaultdict(collections.Counter)
    for stores in by.values():
        st = sorted(stores & idset)
        if len(st) < 2 or len(st) > 40:
            continue
        for s in st:
            n_auth[s] += 1
        for i, a in enumerate(st):
            for b in st[i+1:]:
                co[a][b] += 1
                co[b][a] += 1
    sim = {}
    for s, nb in co.items():
        rows = [(t, c / np.sqrt(n_auth[s] * n_auth[t])) for t, c in nb.items()]
        rows.sort(key=lambda x: -x[1])
        sim[s] = rows[:50]
    print(f"  kNN: 이웃 보유 {len(sim):,}곳")
    return sim


# ────────────────────────────────────────────────── 정책 (개인 점수 산출기)
class Base:
    def reset(self, rng): pass
    def update(self, i, sid, like): pass
    def personal(self):  # 풀 전체 개인 점수 (없으면 None -> 품질만)
        return None


class Logistic(Base):
    """서빙 중인 것과 같은 수식 (sigma x0.4 Thompson)."""
    def __init__(self, X):
        self.X = X
    def reset(self, rng):
        d = self.X.shape[1]
        self.m = np.zeros(d)
        self.v = np.full(d, 1.0)
        self.rng = rng
    def personal(self):
        w = self.rng.normal(self.m, 0.4 * np.sqrt(self.v))
        return self.X @ w
    def update(self, i, sid, like):
        x = self.X[i]
        p = 1 / (1 + np.exp(-x @ self.m))
        g = (p - (1.0 if like else 0.0)) * x
        self.v = 1 / (1 / self.v + p * (1 - p) * x * x)
        self.m -= self.v * g


class Rocchio(Base):
    def __init__(self, E):
        self.E = E
    def reset(self, rng):
        self.pos, self.neg = [], []
    def personal(self):
        if not self.pos and not self.neg:
            return None
        u = np.zeros(self.E.shape[1])
        if self.pos:
            u += np.mean(self.E[self.pos], axis=0)
        if self.neg:
            u -= 0.7 * np.mean(self.E[self.neg], axis=0)
        return self.E @ u
    def update(self, i, sid, like):
        (self.pos if like else self.neg).append(i)


class MFTransfer(Base):
    def __init__(self, ids):
        z = np.load(OUT / "mf_cache.npz", allow_pickle=True)
        qmap = {str(s): i for i, s in enumerate(z["sids"])}
        self.Q = np.zeros((len(ids), z["Q"].shape[1]))
        self.has = np.zeros(len(ids), dtype=bool)
        for i, sid in enumerate(ids):
            j = qmap.get(sid)
            if j is not None:
                self.Q[i] = z["Q"][j]
                self.has[i] = True
        print(f"  MF 전이: 벡터 보유 {int(self.has.sum()):,}/{len(ids):,}곳")
    def reset(self, rng):
        self.obs = []
        self.p = np.zeros(self.Q.shape[1])
    def personal(self):
        if not self.obs:
            return None
        sc = self.Q @ self.p
        sc[~self.has] = 0.0
        return sc
    def update(self, i, sid, like):
        if not self.has[i]:
            return
        self.obs.append((i, 1.0 if like else -1.0))
        Qs = self.Q[[j for j, _ in self.obs]]
        y = np.array([v for _, v in self.obs])
        k = self.Q.shape[1]
        self.p = np.linalg.solve(Qs.T @ Qs + 0.5 * np.eye(k), Qs.T @ y)


class ItemKNN(Base):
    def __init__(self, ids, sim):
        self.pos_of = {s: i for i, s in enumerate(ids)}
        self.sim = sim
        self.n = len(ids)
    def reset(self, rng):
        self.acc = np.zeros(self.n)
        self.any = False
    def personal(self):
        return self.acc if self.any else None
    def update(self, i, sid, like):
        w = 1.0 if like else -0.6
        for t, s in self.sim.get(sid, []):
            j = self.pos_of.get(t)
            if j is not None:
                self.acc[j] += w * s
                self.any = True


class Ensemble(Base):
    def __init__(self, a, b):
        self.a, self.b = a, b
    def reset(self, rng):
        self.a.reset(rng)
        self.b.reset(rng)
    def personal(self):
        pa, pb = self.a.personal(), self.b.personal()
        z = lambda v: (v - v.mean()) / (v.std() + 1e-9)
        if pa is None and pb is None:
            return None
        if pa is None:
            return pb
        if pb is None:
            return pa
        return z(pa) + z(pb)
    def update(self, i, sid, like):
        self.a.update(i, sid, like)
        self.b.update(i, sid, like)


# ────────────────────────────────────────────────── 시뮬레이션 공통부
def simulate(policy, ids, cat, qz, probs, run_seed):
    rng = np.random.default_rng(run_seed)
    policy.reset(rng)
    lab = T.label_fn(run_seed, probs)
    shown = np.zeros(len(ids), dtype=bool)
    last = []
    out = []
    for _ in range(N_SWIPES):
        n = len(out)
        w_q = 1 / (1 + n / 15)
        per = policy.personal()
        if per is None:
            total = qz.copy()
        else:
            total = w_q * qz + (1 - w_q) * (per - per.mean()) / (per.std() + 1e-9)
        total[shown] = -1e9
        order = np.argsort(-total)
        ban = len(last) >= 2 and last[-1] is not None and last[-1] == last[-2]
        pick = None
        for i in order[:200]:
            if ban and cat[int(i)] == last[-1]:
                continue
            pick = int(i)
            break
        if pick is None:
            pick = int(order[0])
        sid = ids[pick]
        shown[pick] = True
        lk = lab(sid)
        out.append(lk)
        last.append(cat[pick])
        policy.update(pick, sid, lk)
    return out


def main() -> None:
    p = sw.pool()
    ids = p["ids"]
    qz = np.asarray(p["qz"], dtype=float)
    cat = [p["cat"].get(s) for s in ids]
    attrs = T.store_attrs()

    print("텍스트 프로파일/LSA...", flush=True)
    prof = build_profiles(ids)
    E = lsa_embed(ids, prof)
    print("kNN 그래프...", flush=True)
    sim = build_knn(ids)

    spicy = {sid: sum(prof[sid].count(t) for t in SPICY_TOKENS) >= 2
             for sid in ids}
    print(f"  매운 가게: {sum(spicy.values()):,}곳", flush=True)

    personas = dict(T.PERSONAS)
    personas["매운맛러"] = {"cat": {}, "price": {}, "q": 0.5, "b": -0.8,
                            "spicy": 1.9}

    def like_prob(persona, sid):
        a = attrs[sid]
        z = (persona["b"] + persona["cat"].get(a["cat"], 0.0)
             + persona["price"].get(a["bucket"], 0.0)
             + persona["q"] * a["qz"]
             + persona.get("spicy", 0.0) * (1.0 if spicy[sid] else 0.0))
        return float(1 / (1 + np.exp(-z)))

    def make_policies():
        lg = Logistic(p["X"])
        rc = Rocchio(E)
        return {
            "logistic": lg, "rocchio": rc,
            "mf": MFTransfer(ids), "knn": ItemKNN(ids, sim),
            "ensemble": Ensemble(Logistic(p["X"]), Rocchio(E)),
            "quality": Base(),
        }

    pols = make_policies()
    results = {}
    for pname, persona in personas.items():
        probs = {s: like_prob(persona, s) for s in ids}
        base = statistics.mean(probs.values())
        print(f"\n[{pname}] 기본 선호율 {base:.3f}", flush=True)
        oracle = np.zeros((N_RUNS, N_SWIPES))
        order_o = np.argsort([-probs[s] for s in ids])
        for r in range(N_RUNS):
            lab = T.label_fn(SEED * 100 + r, probs)
            oracle[r] = [1.0 if lab(ids[int(i)]) else 0.0
                         for i in order_o[:N_SWIPES]]
        results[(pname, "oracle")] = oracle

        for polname, pol in pols.items():
            mat = np.zeros((N_RUNS, N_SWIPES))
            for r in range(N_RUNS):
                mat[r] = [1.0 if x else 0.0 for x in
                          simulate(pol, ids, cat, qz, probs, SEED * 100 + r)]
            results[(pname, polname)] = mat
            cum = mat.mean(0).cumsum() / np.arange(1, N_SWIPES + 1)
            print(f"  {polname:<9} @10 {cum[9]:.3f}  @30 {cum[29]:.3f}  "
                  f"후반 {mat[:, 30:].mean():.3f}", flush=True)

    # 저장: 후반 run별 평균 (CI 계산용) + 누적곡선
    out = {"n_runs": N_RUNS, "n_swipes": N_SWIPES, "personas": {}}
    rng = np.random.default_rng(SEED)
    for pname in personas:
        d = {}
        qlate = results[(pname, "quality")][:, 30:].mean(axis=1)
        for polname in ["oracle", "logistic", "rocchio", "mf", "knn",
                        "ensemble", "quality"]:
            mat = results[(pname, polname)]
            cum = mat.mean(0).cumsum() / np.arange(1, N_SWIPES + 1)
            late = mat[:, 30:].mean(axis=1)
            e = {"cum": cum.tolist(), "late_mean": float(late.mean())}
            if polname not in ("quality", "oracle"):
                diff = late - qlate
                boots = [float(np.mean(rng.choice(diff, len(diff))))
                         for _ in range(2000)]
                e["uplift"] = {"mean": float(diff.mean()),
                               "ci": [float(np.percentile(boots, 2.5)),
                                      float(np.percentile(boots, 97.5))]}
            d[polname] = e
        out["personas"][pname] = d
    with (OUT / "method_compare_results.json").open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\n-> {OUT / 'method_compare_results.json'}")


if __name__ == "__main__":
    main()
