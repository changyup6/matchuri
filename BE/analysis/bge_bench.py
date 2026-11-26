# -*- coding: utf-8 -*-
"""가게 2,045곳을 bge-m3 로 임베딩해 LSA 와 같은 자리(rocchio)에서 재대결.

질문: method_compare 에서 rocchio(LSA)가 진 것이 '방법' 탓인가 '임베딩 품질' 탓인가.
매운맛러(취향이 텍스트에만 존재)가 판가름 페르소나다.

  python analysis/bge_bench.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import analysis.method_compare as M
import analysis.swipe_test as T
from recsys import swipe as sw

OUT = ROOT / "data" / "analysis"
OLLAMA = "http://localhost:11434/api/embed"
N_RUNS = 20


def embed_batch(texts: list[str]) -> np.ndarray:
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": "bge-m3", "input": texts}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.load(r)
    return np.array(d["embeddings"], dtype=np.float32)


def embed_stores(ids, prof) -> np.ndarray:
    cache = OUT / "yeoksam_bge.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if list(z["ids"]) == ids:
            print("  bge 캐시 사용")
            return z["E"]
    E = np.zeros((len(ids), 1024), dtype=np.float32)
    t0 = time.time()
    B = 32
    for i in range(0, len(ids), B):
        E[i:i+B] = embed_batch([prof[s] for s in ids[i:i+B]])
        if (i // B) % 8 == 0:
            el = time.time() - t0
            done = min(i + B, len(ids))
            print(f"  임베딩 {done}/{len(ids)} · {el:.0f}s · "
                  f"남은 {(len(ids)-done)*el/max(done,1):.0f}s", flush=True)
    E /= (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    np.savez_compressed(cache, ids=np.array(ids, dtype=object), E=E)
    print(f"  완료 {time.time()-t0:.0f}s -> {cache.name}")
    return E


def main() -> None:
    p = sw.pool()
    ids = p["ids"]
    qz = np.asarray(p["qz"], dtype=float)
    cat = [p["cat"].get(s) for s in ids]
    attrs = T.store_attrs()

    print("가게 프로파일/임베딩...", flush=True)
    prof = M.build_profiles(ids)
    E_bge = embed_stores(ids, prof)
    z = np.load(OUT / "yeoksam_lsa.npz", allow_pickle=True)
    E_lsa = z["E"]

    # 즉석 검증: 임베딩 이웃이 말이 되는가
    name = {s: (sw.svc.get_store(s) or {}).get("name") for s in ids}
    sim = E_bge @ E_bge.T
    np.fill_diagonal(sim, -1)
    for probe_kw in ["마라", "순대", "파스타"]:
        pi = next((i for i, s in enumerate(ids)
                   if probe_kw in (name[s] or "")), None)
        if pi is not None:
            nb = np.argsort(-sim[pi])[:3]
            print(f"  '{name[ids[pi]]}' 이웃: " +
                  ", ".join(f"{name[ids[j]]}({sim[pi, j]:.2f})" for j in nb),
                  flush=True)

    spicy = {sid: sum(prof[sid].count(t) for t in M.SPICY_TOKENS) >= 2
             for sid in ids}
    personas = dict(T.PERSONAS)
    personas["매운맛러"] = {"cat": {}, "price": {}, "q": 0.5, "b": -0.8,
                            "spicy": 1.9}

    def like_prob(per, sid):
        a = attrs[sid]
        v = (per["b"] + per["cat"].get(a["cat"], 0.0)
             + per["price"].get(a["bucket"], 0.0) + per["q"] * a["qz"]
             + per.get("spicy", 0.0) * (1.0 if spicy[sid] else 0.0))
        return float(1 / (1 + np.exp(-v)))

    pols = {
        "logistic": M.Logistic(p["X"]),
        "rocchio_lsa": M.Rocchio(E_lsa),
        "rocchio_bge": M.Rocchio(E_bge),
        "ens_bge": M.Ensemble(M.Logistic(p["X"]), M.Rocchio(E_bge)),
        "quality": M.Base(),
    }

    res = {}
    rng = np.random.default_rng(7)
    for pname, per in personas.items():
        probs = {s: like_prob(per, s) for s in ids}
        print(f"\n[{pname}]", flush=True)
        lates = {}
        for polname, pol in pols.items():
            mat = np.zeros((N_RUNS, 80))
            for r in range(N_RUNS):
                mat[r] = [1.0 if x else 0.0 for x in
                          M.simulate(pol, ids, cat, qz, probs, T.SEED * 100 + r)]
            cum = mat.mean(0).cumsum() / np.arange(1, 81)
            lates[polname] = mat[:, 30:].mean(axis=1)
            print(f"  {polname:<12} @10 {cum[9]:.3f}  @30 {cum[29]:.3f}  "
                  f"후반 {lates[polname].mean():.3f}", flush=True)
        d = {}
        for polname in pols:
            if polname == "quality":
                continue
            diff = lates[polname] - lates["quality"]
            boots = [float(np.mean(rng.choice(diff, len(diff))))
                     for _ in range(2000)]
            d[polname] = {"late": float(lates[polname].mean()),
                          "uplift": float(diff.mean()),
                          "ci": [float(np.percentile(boots, 2.5)),
                                 float(np.percentile(boots, 97.5))]}
        d["quality"] = {"late": float(lates["quality"].mean())}
        res[pname] = d

    with (OUT / "bge_bench.json").open("w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print(f"\n-> {OUT / 'bge_bench.json'}")


if __name__ == "__main__":
    main()
