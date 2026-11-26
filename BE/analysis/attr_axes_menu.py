# -*- coding: utf-8 -*-
"""속성 축 v2 — 메뉴 단위 계산.

v1(가게 문서 임베딩 기반)은 맵기 AUC 0.681 로 약해서 벤치에서 죽었다.
원인: 문서 평균에 신호가 희석됨. 수정: 축 점수를 메뉴명 임베딩에서 계산하고
가게 점수 = 그 가게 메뉴들의 상위 3개 평균. "마라탕"이란 메뉴명은 희석되지 않는다.

  python analysis/attr_axes_menu.py
"""
from __future__ import annotations

import collections
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
from analysis.attr_axes import AXES, embed, auc
from recsys import swipe as sw

DATA = ROOT / "data" / "yeoksam"
OUT = ROOT / "data" / "analysis"


def menu_embeddings():
    """고유 메뉴명 전체 임베딩 (캐시)."""
    names = set()
    by_store = collections.defaultdict(list)
    for l in (DATA / "menus.jsonl").open(encoding="utf-8"):
        m = json.loads(l)
        n = (m.get("menu_name") or "").strip()
        if n and len(n) <= 30:
            names.add(n)
            by_store[m["store_id"]].append(n)
    names = sorted(names)
    cache = OUT / "menu_emb_all.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if list(z["names"]) == names:
            print(f"  메뉴 임베딩 캐시 사용 ({len(names):,}개)")
            return names, z["E"], by_store
    print(f"  고유 메뉴명 {len(names):,}개 임베딩...", flush=True)
    E = np.zeros((len(names), 1024), dtype=np.float32)
    t0 = time.time()
    B = 256
    for i in range(0, len(names), B):
        E[i:i+B] = embed(names[i:i+B])
        if (i // B) % 10 == 0:
            done = min(i + B, len(names))
            el = time.time() - t0
            print(f"    {done}/{len(names)} · {el:.0f}s · "
                  f"남은 {(len(names)-done)*el/max(done,1):.0f}s", flush=True)
    np.savez_compressed(cache, names=np.array(names, dtype=object), E=E)
    print(f"  완료 {time.time()-t0:.0f}s")
    return names, E, by_store


def main() -> None:
    p = sw.pool()
    ids = p["ids"]
    qz = np.asarray(p["qz"], dtype=float)
    cat = [p["cat"].get(s) for s in ids]
    attrs = T.store_attrs()

    mnames, ME, by_store = menu_embeddings()
    midx = {n: i for i, n in enumerate(mnames)}
    axis_names = list(AXES)
    probes = embed([t for pair in AXES.values() for t in pair])

    # 메뉴별 축 점수 -> 가게 = 상위 3개 평균 (메뉴 없으면 0 = 평균)
    A = np.zeros((len(ids), len(axis_names)))
    menu_ax = np.zeros((len(mnames), len(axis_names)), dtype=np.float32)
    for k in range(len(axis_names)):
        menu_ax[:, k] = ME @ probes[2 * k] - ME @ probes[2 * k + 1]
    for i, sid in enumerate(ids):
        rows = [midx[n] for n in by_store.get(sid, []) if n in midx]
        if not rows:
            continue
        sc = menu_ax[rows]
        top3 = np.sort(sc, axis=0)[::-1][:3]
        A[i] = top3.mean(axis=0)
    A = (A - A.mean(0)) / (A.std(0) + 1e-9)
    np.savez_compressed(OUT / "store_axes_menu.npz",
                        ids=np.array(ids, dtype=object),
                        names=np.array(axis_names, dtype=object), A=A)

    # 검증
    prof = M.build_profiles(ids)
    spicy_flag = np.array([sum(prof[s].count(t) for t in M.SPICY_TOKENS) >= 2
                           for s in ids])
    print(f"\n맵기 축(메뉴 기반) AUC: {auc(A[:, 0], spicy_flag):.3f}  "
          f"(v1 문서 기반은 0.681)")
    name = {s: (sw.svc.get_store(s) or {}).get("name") for s in ids}
    for k, nm in enumerate(axis_names):
        top = np.argsort(-A[:, k])[:4]
        print(f"  {nm:<3} 상위: " + " · ".join(name[ids[i]] or "?" for i in top))

    # 벤치
    Xplus = np.hstack([p["X"], A * 0.8])
    spicy = {s: bool(f) for s, f in zip(ids, spicy_flag)}
    personas = dict(T.PERSONAS)
    personas["매운맛러"] = {"cat": {}, "price": {}, "q": 0.5, "b": -0.8,
                            "spicy": 1.9}

    def lp(per, sid):
        a = attrs[sid]
        v = (per["b"] + per["cat"].get(a["cat"], 0.0)
             + per["price"].get(a["bucket"], 0.0) + per["q"] * a["qz"]
             + per.get("spicy", 0.0) * (1.0 if spicy[sid] else 0.0))
        return float(1 / (1 + np.exp(-v)))

    rng = np.random.default_rng(7)
    res = {}
    print()
    for pname, per in personas.items():
        probs = {s: lp(per, s) for s in ids}
        lates = {}
        for polname, pol in [("logi19", M.Logistic(p["X"])),
                             ("logi+메뉴축", M.Logistic(Xplus)),
                             ("quality", M.Base())]:
            mat = np.zeros((20, 80))
            for r in range(20):
                mat[r] = [1.0 if x else 0.0 for x in
                          M.simulate(pol, ids, cat, qz, probs, T.SEED * 100 + r)]
            lates[polname] = mat[:, 30:].mean(axis=1)
        line = [f"[{pname}] 품질 {lates['quality'].mean():.3f}"]
        d = {"quality": {"late": float(lates["quality"].mean())}}
        for polname in ("logi19", "logi+메뉴축"):
            diff = lates[polname] - lates["quality"]
            boots = [float(np.mean(rng.choice(diff, len(diff))))
                     for _ in range(2000)]
            lo, hi = np.percentile(boots, [2.5, 97.5])
            sig = "*" if lo > 0 else ("x" if hi < 0 else " ")
            line.append(f"{polname} {lates[polname].mean():.3f} "
                        f"({diff.mean():+.3f} [{lo:+.3f},{hi:+.3f}]{sig})")
            d[polname] = {"late": float(lates[polname].mean()),
                          "uplift": float(diff.mean()),
                          "ci": [float(lo), float(hi)]}
        print("  ".join(line), flush=True)
        res[pname] = d
    with (OUT / "attr_axes_menu_bench.json").open("w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print(f"\n-> {OUT / 'attr_axes_menu_bench.json'}")


if __name__ == "__main__":
    main()
