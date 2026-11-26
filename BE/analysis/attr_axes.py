# -*- coding: utf-8 -*-
"""속성 축: '이름 있는' 취향 축 8개를 임베딩 프로브로 계산해 logistic 피처에 추가.

  축 점수(가게) = z( cos(가게벡터, 긍정프로브) - cos(가게벡터, 부정프로브) )

PCA 24축 실험의 교훈(무지성 고차원 -> 차원의 저주)을 반영해, 사람이 이름 붙인
소수 축만 쓴다. LLM 호출 없음 — 프로브 문장 몇 개만 bge-m3 로 임베딩.

검증 순서
  ① 맵기 축이 실제 매운 가게(토큰 기준)를 가려내는가 (AUC)
  ② 축별 상위 가게가 상식적인가 (눈 검증)
  ③ 벤치: logistic(19) vs logistic+축(27) — 매운맛러가 드디어 품질을 이기는가

  python analysis/attr_axes.py
"""
from __future__ import annotations

import json
import sys
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

AXES = {
    "맵기": ("마라탕 불닭 짬뽕 떡볶이처럼 얼큰하고 화끈하게 매운 음식",
             "맵지 않고 순하고 담백한 음식"),
    "달콤": ("케이크 빙수 마카롱 달달한 디저트와 달콤한 음료",
             "달지 않은 식사 위주 음식"),
    "분위기": ("인테리어가 예쁘고 조용해서 데이트하기 좋은 분위기 좋은 가게",
               "분위기보다 음식 위주의 평범한 식당"),
    "푸짐": ("양이 많고 푸짐해서 배부르게 먹는 가성비 식당",
             "양이 적고 아담하게 나오는 곳"),
    "술": ("소주 맥주 와인 하이볼과 안주를 곁들여 술 마시기 좋은 곳",
           "술 없이 식사만 하는 곳"),
    "건강": ("신선한 재료로 만든 샐러드 포케 같은 건강하고 가벼운 음식",
             "기름지고 자극적인 음식"),
    "고기": ("삼겹살 소고기 갈비 구이처럼 육즙 가득한 고기 요리",
             "고기가 없는 음식"),
    "국물": ("뜨끈한 국물의 탕 찌개 국밥 해장 요리",
             "국물이 없는 마른 음식"),
}


def embed(texts, retries=4):
    # Ollama 가 모델 스왑 타이밍에 일시적 400 을 뱉는다(실측) -> 재시도
    import time as _t
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(
                OLLAMA,
                data=json.dumps({"model": "bge-m3", "input": texts}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                E = np.array(json.load(r)["embeddings"], dtype=np.float32)
            return E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
        except Exception as exc:
            last = exc
            _t.sleep(2.0 * (a + 1))
    raise RuntimeError(f"embed 실패 (재시도 {retries}회): {last!r}")


def auc(score, flag):
    pos = score[flag]
    neg = score[~flag]
    wins = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return wins / (len(pos) * len(neg))


def main() -> None:
    p = sw.pool()
    ids = p["ids"]
    qz = np.asarray(p["qz"], dtype=float)
    cat = [p["cat"].get(s) for s in ids]
    attrs = T.store_attrs()
    E = np.load(OUT / "yeoksam_bge.npz", allow_pickle=True)["E"].astype(np.float32)
    name = {s: (sw.svc.get_store(s) or {}).get("name") for s in ids}

    # ── 축 계산 ───────────────────────────────────────────
    names = list(AXES)
    probes = embed([t for pair in AXES.values() for t in pair])
    A = np.zeros((len(ids), len(names)), dtype=float)
    for k in range(len(names)):
        pos, neg = probes[2 * k], probes[2 * k + 1]
        raw = E @ pos - E @ neg
        A[:, k] = (raw - raw.mean()) / (raw.std() + 1e-9)
    np.savez_compressed(OUT / "store_axes.npz",
                        ids=np.array(ids, dtype=object),
                        names=np.array(names, dtype=object), A=A)

    # ── ① 맵기 AUC ── ② 축별 상위 ────────────────────────
    prof = M.build_profiles(ids)
    spicy_flag = np.array([sum(prof[s].count(t) for t in M.SPICY_TOKENS) >= 2
                           for s in ids])
    print(f"맵기 축 AUC(토큰 기준 매운 가게 {spicy_flag.sum()}곳 판별): "
          f"{auc(A[:, 0], spicy_flag):.3f}")
    for k, nm in enumerate(names):
        top = np.argsort(-A[:, k])[:4]
        print(f"  {nm:<3} 상위: " + " · ".join(name[ids[i]] or "?" for i in top))

    # ── ③ 벤치 ───────────────────────────────────────────
    Xplus = np.hstack([p["X"], A * 0.8])
    print(f"\n피처 {p['X'].shape[1]} -> {Xplus.shape[1]}")
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
    print(f"{'페르소나':<6} {'방법':<10} {'@10':>6} {'@30':>6} {'후반':>6}   vs품질 CI")
    for pname, per in personas.items():
        probs = {s: lp(per, s) for s in ids}
        lates = {}
        for polname, pol in [("logi19", M.Logistic(p["X"])),
                             ("logi+축", M.Logistic(Xplus)),
                             ("quality", M.Base())]:
            mat = np.zeros((20, 80))
            for r in range(20):
                mat[r] = [1.0 if x else 0.0 for x in
                          M.simulate(pol, ids, cat, qz, probs, T.SEED * 100 + r)]
            cum = mat.mean(0).cumsum() / np.arange(1, 81)
            lates[polname] = mat[:, 30:].mean(axis=1)
            line = f"{pname:<6} {polname:<10} {cum[9]:6.3f} {cum[29]:6.3f} {lates[polname].mean():6.3f}"
            if polname != "quality":
                pass
            print(line, flush=True)
        d = {}
        for polname in ("logi19", "logi+축"):
            diff = lates[polname] - lates["quality"]
            boots = [float(np.mean(rng.choice(diff, len(diff))))
                     for _ in range(2000)]
            lo, hi = np.percentile(boots, [2.5, 97.5])
            sig = "*" if lo > 0 else ("x" if hi < 0 else " ")
            print(f"       {polname:<10} vs품질 {diff.mean():+.3f} "
                  f"[{lo:+.3f},{hi:+.3f}] {sig}")
            d[polname] = {"late": float(lates[polname].mean()),
                          "uplift": float(diff.mean()),
                          "ci": [float(lo), float(hi)]}
        d["quality"] = {"late": float(lates["quality"].mean())}
        res[pname] = d
        print()
    with (OUT / "attr_axes_bench.json").open("w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print(f"-> {OUT / 'attr_axes_bench.json'}")


if __name__ == "__main__":
    main()
