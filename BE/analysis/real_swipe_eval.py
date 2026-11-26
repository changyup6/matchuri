# -*- coding: utf-8 -*-
"""실제 스와이프 기록으로 추천 모델을 잰다 — 가상 사용자(페르소나)가 아니라 앱에 쌓인 좋아요·싫어요로.

지금까지 추천 성능은 페르소나 재생(`reco/eval_swipe.py`)으로만 쟀다. 지어낸 페르소나로 재면 26%, 실제 리뷰어를
페르소나로 쓰면 15% — 재는 방법이 숫자를 바꾼다. 앱에는 이제 실제 스와이프가 1만 건 넘게 쌓였다
(`state/preferences.jsonl`). 그 기록을 서버와 같은 방법으로 처음부터 다시 먹이면서(세션마다 모델 하나,
`recsys.taste.record`), **매 스와이프 직전에** 모델이 그 가게를 추천 풀에서 몇 번째로 봤는지(백분위)를 적는다.

    좋아요를 받은 가게의 백분위 > 싫어요를 받은 가게의 백분위  일 확률 = AUC
    0.5 는 동전 던지기, 1 은 완벽. 성분(인기·평판·공방문·메뉴·요약문·리뷰·음식사진·기피) 하나만 써도 같은 값을 낸다.

재는 점수
  full       모델이 카드를 고를 때 쓰는 기대 점수 m·z(톰프슨 흔들기 전)
  personal   그중 개인화 성분만(서버의 personal_rank 와 같은 식) — 좋아요가 하나 이상 있을 때만 뜻이 있다
  성분 8개    성분 하나로만 줄 세웠을 때
  pooled     실제 사용자 전체에서 배운 고정 가중치(사용자 단위 5겹 — 자기 기록은 학습에 안 들어간다)

한계: 세션을 새로 시작하는 것으로 재생한다(서버의 이전 세션 승계는 뺐다). 지금 추천 풀(1,498곳)과 지금 산출물로
재므로 기록 당시 풀 밖 가게는 빠진다. 누가 무엇을 봤는지(노출)는 추천 정책이 골랐으므로 표본이 무작위가 아니다.

    cd BE/server      # 서버 코드와 서빙 산출물(data/kakao/serving)이 있는 곳
    python ../analysis/real_swipe_eval.py --prefs <preferences.jsonl 사본> --out <결과.json> --prior-exp

`--prior-exp` 는 실제 기록으로 배운 시작값(사용자 단위 5겹 — 자기 기록은 학습에 안 들어간다)으로 한 번 더 재생해 지금
시작값과 견준다. 결과와 결론은 `analysis/backtest/results/step4-real-swipes.md`. 실서비스 기록은 사본으로만 읽는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

import numpy as np

BUCKETS = ((0, 0), (1, 4), (5, 9), (10, 19), (20, 29), (30, 59), (60, 10 ** 9))


def auc(pos, neg):
    """Mann-Whitney — 좋아요 점수가 싫어요 점수보다 클 확률(같으면 반)."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return None
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv))
    ranks[order] = np.arange(1, len(allv) + 1)
    # 같은 값은 평균 순위
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    ranks = (sums / cnt)[inv]
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def pct_rank(v, i):
    return float((np.count_nonzero(v < v[i]) + 0.5 * np.count_nonzero(v == v[i])) / len(v))


def replay(prefs, max_n, prior=None):
    """prior: {user_id: 시작 가중치(8,)} — 주면 세션의 m·prior_m 을 그것으로 바꿔 시작한다(분산은 그대로)."""
    from app import loader
    from recsys import content, taste
    art = content.load()
    if art is None:
        raise SystemExit("산출물(serving)이 없다 — 팀 서버 코드 폴더의 data/kakao/serving 을 확인할 것")
    comps = list(content.COMPONENTS)
    personal = [content.IDX[c] for c in ("cf", "menu", "text", "review", "food", "neg") if c in content.IDX]
    sessions, out = {}, []
    skipped = collections.Counter()
    t0 = time.time()
    for k, r in enumerate(prefs):
        if r.get("event") in ("session_create", "session_start"):
            continue
        pref = r.get("prefer")
        if pref not in ("like", "dislike") or r.get("card_id") is None:
            skipped["no_pref"] += 1
            continue
        key = r.get("session_id") if r.get("event") in ("preference", "exposure") else r.get("user_id")
        sid = loader.to_store_id(r["card_id"])
        if sid is None or sid not in art.pos:
            skipped["out_of_pool"] += 1
            continue
        sess = sessions.get(key)
        if sess is None:
            sess = sessions[key] = taste._new_session()
            if prior is not None and r.get("user_id") in prior:
                sess["m"] = np.array(prior[r["user_id"]], dtype=float)
                sess["prior_m"] = sess["m"].copy()
        i = art.pos[sid]
        if sess["n"] < max_n and sid not in sess["like_ids"] and sid not in sess["dislike_ids"]:
            z = taste.components(sess, art)
            m = sess["m"]
            rec = {"user": r.get("user_id"), "key": key, "n": int(sess["n"]), "likes": int(sess["likes"]),
                   "like": pref == "like", "full": pct_rank(m @ z, i),
                   "personal": pct_rank(m[personal] @ z[personal], i) if sess["like_ids"] else None,
                   "z": [pct_rank(z[j], i) for j in range(len(comps))],
                   "zraw": [float(z[j, i]) for j in range(len(comps))]}
            out.append(rec)
        else:
            skipped["repeat_or_long"] += 1
        taste.record(sess, sid, pref == "like")
        if k % 1000 == 0:
            print("  %d/%d 줄 · 잰 스와이프 %d · %.0f초" % (k, len(prefs), len(out), time.time() - t0), flush=True)
    return out, comps, dict(skipped), len(sessions)


def pooled_auc(recs, comps, folds=5, steps=400, lr=0.5):
    """성분 백분위 8개로 좋아요를 맞히는 고정 가중치 — 사용자 단위 겹(자기 기록은 학습에서 뺀다)."""
    X = np.array([[1.0] + [v - 0.5 for v in r["z"]] for r in recs])
    y = np.array([1.0 if r["like"] else 0.0 for r in recs])
    users = sorted({r["user"] for r in recs})
    fold_of = {u: i % folds for i, u in enumerate(users)}
    f = np.array([fold_of[r["user"]] for r in recs])
    s = np.zeros(len(recs))
    W = []
    for k in range(folds):
        tr = f != k
        w = np.zeros(X.shape[1])
        for _ in range(steps):
            p = 1 / (1 + np.exp(-(X[tr] @ w)))
            w -= lr * (X[tr].T @ (p - y[tr]) / tr.sum() + 1e-3 * np.r_[0, w[1:]])
        s[~tr] = X[~tr] @ w
        W.append(w)
    return s, np.mean(W, axis=0)


def real_prior(recs, comps, folds=5, steps=600, lr=0.3, scale=1.0):
    """스와이프한 가게의 원래 성분값 z 로 좋아요를 맞히는 가중치(점별 로지스틱) — 사용자 겹마다 따로.

    모델의 시작값 m 과 같은 자리(같은 z 눈금)의 값이다. 돌려주는 것: {user: 그 사용자를 뺀 겹에서 배운 가중치}, 평균."""
    Z = np.array([r["zraw"] for r in recs])
    X = np.c_[np.ones(len(recs)), Z]
    y = np.array([1.0 if r["like"] else 0.0 for r in recs])
    users = sorted({r["user"] for r in recs})
    fold_of = {u: i % folds for i, u in enumerate(users)}
    f = np.array([fold_of[r["user"]] for r in recs])
    W = {}
    for k in range(folds):
        tr = f != k
        w = np.zeros(X.shape[1])
        for _ in range(steps):
            p = 1 / (1 + np.exp(-(X[tr] @ w)))
            w -= lr * (X[tr].T @ (p - y[tr]) / tr.sum() + 1e-3 * np.r_[0, w[1:]])
        W[k] = w[1:] * scale
    return {u: W[fold_of[u]].tolist() for u in users}, np.mean([W[k] for k in W], axis=0)


def summarize(recs, comps):
    rows = {}
    s_pool, w_pool = pooled_auc(recs, comps)
    for r, sp in zip(recs, s_pool):
        r["pooled"] = float(sp)
    names = ["full", "personal", "pooled"] + comps
    for lo, hi in BUCKETS:
        b = [r for r in recs if lo <= r["n"] <= hi]
        if not b:
            continue
        row = {"swipes": len(b), "like_rate": round(sum(r["like"] for r in b) / len(b), 3),
               "users": len({r["user"] for r in b})}
        for nm in names:
            if nm in comps:
                j = comps.index(nm)
                pos = [r["z"][j] for r in b if r["like"]]
                neg = [r["z"][j] for r in b if not r["like"]]
            else:
                pos = [r[nm] for r in b if r["like"] and r[nm] is not None]
                neg = [r[nm] for r in b if not r["like"] and r[nm] is not None]
            a = auc(pos, neg)
            row[nm] = None if a is None else round(a, 3)
        rows["%d-%s" % (lo, "" if hi > 10 ** 8 else hi)] = row
    allrow = {}
    for nm in names:
        if nm in comps:
            j = comps.index(nm)
            allrow[nm] = auc([r["z"][j] for r in recs if r["like"]], [r["z"][j] for r in recs if not r["like"]])
        else:
            allrow[nm] = auc([r[nm] for r in recs if r["like"] and r[nm] is not None],
                             [r[nm] for r in recs if not r["like"] and r[nm] is not None])
    # 사용자마다 따로 잰 AUC 의 중앙값 — 한 사람이 수천 번 스와이프한 기록이 전체를 좌우하지 않게
    per_user = collections.defaultdict(list)
    for r in recs:
        per_user[r["user"]].append(r)
    macro = {}
    for nm in ("full", "personal", "pooled"):
        vals = []
        for u, rs in per_user.items():
            pos = [r[nm] for r in rs if r["like"] and r[nm] is not None]
            neg = [r[nm] for r in rs if not r["like"] and r[nm] is not None]
            if len(pos) >= 3 and len(neg) >= 3:
                vals.append(auc(pos, neg))
        macro[nm] = {"users": len(vals), "median": round(float(np.median(vals)), 3) if vals else None}
    return {"by_bucket": rows, "all": {k: (None if v is None else round(v, 3)) for k, v in allrow.items()},
            "per_user_median": macro,
            "pooled_weights": {nm: round(float(w), 3) for nm, w in zip(["bias"] + comps, w_pool)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-n", type=int, default=100, help="세션마다 앞 몇 장까지 잴지(학습은 끝까지 한다)")
    ap.add_argument("--prior-exp", action="store_true",
                    help="실제 기록으로 배운 시작값(사용자 겹 밖)으로 다시 재생해 지금 시작값과 견준다")
    a = ap.parse_args()
    sys.path.insert(0, os.getcwd())
    prefs = [json.loads(l) for l in open(a.prefs, encoding="utf-8") if l.strip()]
    t0 = time.time()
    recs, comps, skipped, n_sess = replay(prefs, a.max_n)
    res = summarize(recs, comps)
    res.update({"measured": len(recs), "users": len({r["user"] for r in recs}), "sessions": n_sess,
                "skipped": skipped, "seconds": round(time.time() - t0), "components": comps})
    if a.prior_exp:
        from recsys import content
        art = content.load()
        now = art.prior_mean()
        per_user, mean_w = real_prior(recs, comps)
        res["real_prior_mean"] = {c: round(float(v), 3) for c, v in zip(comps, mean_w)}
        res["current_prior"] = {c: round(float(v), 3) for c, v in zip(comps, now)}
        for name, pri in (("real", per_user),
                          ("blend", {u: (0.5 * np.array(w) + 0.5 * now).tolist() for u, w in per_user.items()})):
            r2, _, _, _ = replay(prefs, a.max_n, prior=pri)
            s2 = summarize(r2, comps)
            res["prior_" + name] = {"all": s2["all"], "per_user_median": s2["per_user_median"],
                                    "by_bucket": {k: {"swipes": v["swipes"], "full": v["full"], "personal": v["personal"]}
                                                  for k, v in s2["by_bucket"].items()}}
        res["seconds"] = round(time.time() - t0)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
