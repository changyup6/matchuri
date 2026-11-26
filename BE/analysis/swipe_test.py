# -*- coding: utf-8 -*-
"""배포 중인 스와이프 추천(api.swipe)의 효과 측정.

reco_proto 시뮬(전국 replay)의 한계 — 후보 풀이 유저가 이미 고른 가게뿐이라
'카테고리 회피'를 측정 못 함 — 를 정확히 보완한다: 여기서는 풀이 역삼동 전체
2,045곳이고, 취향이 뚜렷한 가상 페르소나가 스와이프한다.

  - api.swipe 를 직접 임포트해 실제 서빙 코드 경로로 테스트한다 (HTTP 없음)
  - 페르소나: P(선호) = sigmoid(b + cat성향 + 가격성향 + q*품질z)
  - 같은 (run, 가게) 는 정책과 무관하게 같은 라벨 -> 정책 간 공정 비교
  - 비교: hybrid(실서빙) vs quality-only vs random vs oracle(P순 정렬 상한)

  python analysis/swipe_test.py
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
from recsys.swipe import Feedback

OUT = ROOT / "data" / "analysis"

N_RUNS = 20
N_SWIPES = 80
SEED = 11


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


# 페르소나: 취향의 '축'이 서로 다르다. 개인화가 이겨야 하는 곳과
# 이길 수 없는 곳(미식가=품질 그 자체, 무작위=신호 없음)을 함께 둔다.
PERSONAS = {
    "한식러": {"cat": {"한식": 1.6, "분식": 0.8, "카페": -1.6, "술집": -1.2,
                       "양식": -0.8}, "price": {}, "q": 0.6, "b": -0.2},
    "카페인간": {"cat": {"카페": 1.8, "간식": 1.0, "한식": -1.2, "중식": -1.0,
                         "일식": -0.6}, "price": {}, "q": 0.6, "b": -0.2},
    "가성비": {"cat": {}, "price": {0: 1.4, 1: 0.7, 3: -1.4}, "q": 0.4, "b": 0.0},
    "미식가": {"cat": {}, "price": {}, "q": 2.2, "b": -0.5},
    "무작위": {"cat": {}, "price": {}, "q": 0.0, "b": 0.0},
}


def store_attrs():
    """풀의 가게 속성(카테고리·가격버킷·품질z)을 한 번만 계산."""
    p = sw.pool()
    attrs = {}
    for i, sid in enumerate(p["ids"]):
        attrs[sid] = {"cat": p["cat"].get(sid),
                      "bucket": sw._price_bucket(sid),
                      "qz": float(p["qz"][i])}
    return attrs


def like_prob(persona, a):
    z = (persona["b"] + persona["cat"].get(a["cat"], 0.0)
         + persona["price"].get(a["bucket"], 0.0) + persona["q"] * a["qz"])
    return float(sigmoid(z))


def label_fn(run_seed, probs):
    """같은 (run, 가게) -> 같은 라벨. 정책이 달라도 세계는 같다."""
    def f(sid):
        return random.Random(f"{run_seed}:{sid}").random() < probs[sid]
    return f


def run_hybrid(probs, lab, attrs):
    resp = sw.next_card(None)
    sid_sess = resp["session_id"]
    out = []
    for _ in range(N_SWIPES):
        store = resp["card"]["store_id"]
        lk = lab(store)
        out.append((store, lk))
        resp = sw.feedback(Feedback(session_id=sid_sess, store_id=store, like=lk))
    # 학습된 카테고리 가중치(패널과 동일한 값)를 회수
    learn = resp["learn"]
    return out, {c["name"]: c["w"] for c in learn["cats"]}


def run_static(order, lab):
    return [(s, lab(s)) for s in order[:N_SWIPES]]


def main() -> None:
    print("풀/속성 준비...", flush=True)
    p = sw.pool()
    attrs = store_attrs()
    ids = p["ids"]
    qz_order = [ids[int(i)] for i in np.argsort(-p["qz"])]
    print(f"  후보 {len(ids):,}곳", flush=True)

    results = {}          # (persona, policy) -> N_RUNS x N_SWIPES 라벨 행렬
    learned = collections.defaultdict(list)
    expo = collections.defaultdict(list)   # (persona, policy) -> 선호카테고리 노출률(초반, 후반)

    for pname, persona in PERSONAS.items():
        probs = {s: like_prob(persona, a) for s, a in attrs.items()}
        pos_cats = {c for c, w in persona["cat"].items() if w > 0}
        base = statistics.mean(probs.values())
        print(f"\n[{pname}] 기본 선호율 {base:.3f}", flush=True)

        for pol in ["hybrid", "quality", "random", "oracle"]:
            mat = np.zeros((N_RUNS, N_SWIPES))
            for r in range(N_RUNS):
                lab = label_fn(SEED * 100 + r, probs)
                if pol == "hybrid":
                    seq, w = run_hybrid(probs, lab, attrs)
                    learned[pname].append(w)
                elif pol == "quality":
                    seq = run_static(qz_order, lab)
                elif pol == "random":
                    rng = random.Random(SEED * 100 + r)
                    order = ids[:]
                    rng.shuffle(order)
                    seq = run_static(order, lab)
                else:  # oracle: 실제 확률 순 (달성 가능한 상한)
                    order = sorted(ids, key=lambda s: -probs[s])
                    seq = run_static(order, lab)
                mat[r] = [1.0 if lk else 0.0 for _, lk in seq]
                if pos_cats:
                    cats = [attrs[s]["cat"] for s, _ in seq]
                    expo[(pname, pol)].append((
                        sum(1 for c in cats[:15] if c in pos_cats) / 15,
                        sum(1 for c in cats[-30:] if c in pos_cats) / 30))
            results[(pname, pol)] = mat
            cum = mat.mean(axis=0).cumsum() / np.arange(1, N_SWIPES + 1)
            print(f"  {pol:<8} @10 {cum[9]:.3f}  @30 {cum[29]:.3f}  "
                  f"@60 {cum[59]:.3f}  @80 {cum[79]:.3f}", flush=True)

    # ── 요약 저장 ─────────────────────────────────────────
    out = {"n_runs": N_RUNS, "n_swipes": N_SWIPES, "personas": {}}
    for pname in PERSONAS:
        d = {}
        for pol in ["hybrid", "quality", "random", "oracle"]:
            mat = results[(pname, pol)]
            cum = mat.mean(axis=0).cumsum() / np.arange(1, N_SWIPES + 1)
            # run별 @30~@80 구간 평균으로 쌍대 부트스트랩
            d[pol] = {"cum": cum.tolist(),
                      "per_run_late": mat[:, 30:].mean(axis=1).tolist()}
        # hybrid - quality 후반 개선의 95% CI (쌍대)
        diff = np.array(d["hybrid"]["per_run_late"]) - np.array(d["quality"]["per_run_late"])
        rng = np.random.default_rng(SEED)
        boots = [float(np.mean(rng.choice(diff, len(diff)))) for _ in range(2000)]
        d["uplift_late"] = {"mean": float(diff.mean()),
                           "ci": [float(np.percentile(boots, 2.5)),
                                  float(np.percentile(boots, 97.5))]}
        if (pname, "hybrid") in expo:
            e_h = expo[(pname, "hybrid")]
            e_q = expo[(pname, "quality")]
            d["exposure"] = {
                "hybrid_early": statistics.mean(x[0] for x in e_h),
                "hybrid_late": statistics.mean(x[1] for x in e_h),
                "quality_early": statistics.mean(x[0] for x in e_q),
                "quality_late": statistics.mean(x[1] for x in e_q)}
        # 학습된 가중치 평균 (hybrid)
        ws = learned[pname]
        if ws:
            keys = ws[0].keys()
            d["learned_cats"] = {k: round(statistics.mean(w[k] for w in ws), 2)
                                 for k in keys}
        out["personas"][pname] = d

    with (OUT / "swipe_test_results.json").open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\n-> {OUT / 'swipe_test_results.json'}")


if __name__ == "__main__":
    main()
