# -*- coding: utf-8 -*-
"""선호/비선호(이진) 추론이 접합도에 따라 얼마나 정확해지는가.

별점을 정확히 맞히는 건 어렵지만(MAE ~0.8), 실무에서 필요한 건
"이 사람이 이 집을 좋아할까 아닐까"뿐이다. 접합도 50곳+ 쌍의 평균 별점차이가
0.496 이므로, 이진 판정에서는 훨씬 잘 맞을 수 있다.

정의
  선호   = 별점 >= 본인 평균   (개인 후함/박함 보정)
  비선호 = 별점 <  본인 평균

측정
  같은 가게에서 A 의 선호/비선호로 B 를 예측할 때의 일치율.
  우연 일치율(chance) 과 비교하고 Cohen's kappa 도 낸다.

  python analysis/binary_pref.py
"""
from __future__ import annotations

import collections
import json
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"
MAX_STORE_USERS = 400


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (c - m) / d, (c + m) / d


def main() -> None:
    us: dict[str, dict[str, int]] = collections.defaultdict(dict)
    su: dict[str, set[str]] = collections.defaultdict(set)
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            us[r["user_hash"]][r["store_id"]] = r["star"]
            su[r["store_id"]].add(r["user_hash"])
    umean = {u: statistics.mean(v.values()) for u, v in us.items()}

    def pref(u: str, s: str) -> int:
        return 1 if us[u][s] >= umean[u] else 0

    # ── 쌍 수집 ────────────────────────────────────────────────
    recs = []      # (n_shared_excl, pa, pb, diff)
    for s, U in su.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                sa, sb = set(us[a]), set(us[b])
                k = len((sa & sb) - {s})
                recs.append((k, pref(a, s), pref(b, s),
                             abs(us[a][s] - us[b][s])))
    print(f"유저 {len(us):,}명 · 분석 쌍 {len(recs):,}건")

    pos_rate = statistics.mean([r[1] for r in recs] + [r[2] for r in recs])
    print(f"전체 '선호' 비율 {100*pos_rate:.1f}%")

    def report(rows, label):
        n = len(rows)
        if n < 20:
            return None
        agree = sum(1 for _, pa, pb, _ in rows if pa == pb)
        acc = agree / n
        # 우연 일치율: 각 변의 주변확률로 계산
        pa_r = statistics.mean(r[1] for r in rows)
        pb_r = statistics.mean(r[2] for r in rows)
        chance = pa_r * pb_r + (1 - pa_r) * (1 - pb_r)
        kappa = (acc - chance) / (1 - chance) if chance < 1 else 0.0
        lo, hi = wilson(agree, n)
        return dict(label=label, n=n, acc=acc, chance=chance, kappa=kappa,
                    lo=lo, hi=hi, diff=statistics.mean(r[3] for r in rows))

    print("\n■ 접합도별 선호/비선호 일치율")
    print(f"  {'공유':>8} {'n':>9} {'일치율':>8} {'우연':>7} {'kappa':>8} "
          f"{'95%CI':>18} {'별점차':>7}")
    bands = [(1, 1), (2, 2), (3, 4), (5, 9), (10, 19), (20, 49), (50, 10**9)]
    for lo_b, hi_b in bands:
        rows = [r for r in recs if lo_b <= r[0] <= hi_b]
        d = report(rows, "")
        if not d:
            continue
        lab = f"{lo_b}곳" if lo_b == hi_b else (
            f"{lo_b}+곳" if hi_b > 1000 else f"{lo_b}-{hi_b}곳")
        print(f"  {lab:>8} {d['n']:9,} {100*d['acc']:7.1f}% "
              f"{100*d['chance']:6.1f}% {d['kappa']:+8.4f} "
              f"[{100*d['lo']:5.1f},{100*d['hi']:5.1f}] {d['diff']:7.3f}")

    print("\n■ 누적 — 공유 N곳 이상")
    print(f"  {'기준':>8} {'n':>9} {'비율':>7} {'일치율':>8} {'kappa':>8}")
    for th in [1, 2, 3, 5, 10, 20, 30, 50]:
        rows = [r for r in recs if r[0] >= th]
        d = report(rows, "")
        if not d:
            continue
        print(f"  {th:>6}곳+ {d['n']:9,} {100*d['n']/len(recs):6.1f}% "
              f"{100*d['acc']:7.1f}% {d['kappa']:+8.4f}")

    # ── 무작위 베이스라인(실측) ────────────────────────────────
    rng = random.Random(3)
    ul = list(us)
    rnd_agree = rnd_n = 0
    for _ in range(60000):
        a, b = rng.sample(ul, 2)
        c = set(us[a]) & set(us[b])
        if not c:
            continue
        s = rng.choice(list(c))
        rnd_n += 1
        if pref(a, s) == pref(b, s):
            rnd_agree += 1
    print(f"\n■ 무작위 쌍 실측 일치율 {100*rnd_agree/rnd_n:.1f}% (n={rnd_n:,})")

    # ── 실용 관점: 강한 선호/비선호만 볼 때 ────────────────────
    print("\n■ 확실한 신호만 (양쪽 모두 본인평균 ±0.5 이상 벗어난 경우)")
    strong = []
    for s, U in su.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul2 = sorted(U)
        for i in range(len(ul2)):
            for j in range(i + 1, len(ul2)):
                a, b = ul2[i], ul2[j]
                da = us[a][s] - umean[a]
                db = us[b][s] - umean[b]
                if abs(da) < 0.5 or abs(db) < 0.5:
                    continue
                k = len((set(us[a]) & set(us[b])) - {s})
                strong.append((k, 1 if da >= 0 else 0, 1 if db >= 0 else 0,
                               abs(us[a][s] - us[b][s])))
    print(f"  대상 {len(strong):,}건 (전체의 {100*len(strong)/len(recs):.0f}%)")
    print(f"  {'기준':>8} {'n':>9} {'일치율':>8} {'kappa':>8}")
    for th in [1, 5, 10, 20, 50]:
        rows = [r for r in strong if r[0] >= th]
        d = report(rows, "")
        if not d:
            continue
        print(f"  {th:>6}곳+ {d['n']:9,} {100*d['acc']:7.1f}% {d['kappa']:+8.4f}")


if __name__ == "__main__":
    main()
