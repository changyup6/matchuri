# -*- coding: utf-8 -*-
"""성향 벡터 기반 — 같은 가게 안에서 취향 유사도가 평가 일치를 설명하는가.

가게 단위 유사도는 밀도 0.09% 라 노이즈였다(r=-0.04).
카테고리 단위로 축을 올려 유사도를 안정화한 뒤 같은 분석을 반복한다.

성향 벡터
  축   : cat2(중분류) 또는 cat3(소분류)
  값   : 그 카테고리에서의 평균별점 - 본인 전체 평균
         (개인이 후한지/박한지를 제거해 '상대적 선호'만 남김)

누수 차단
  평가 대상 가게 s 는 벡터 계산에서 제외한다(leave-one-out).
  나아가 s 가 속한 카테고리의 기여도 뺄 수 있어야 정확하지만, 그러면 표본이
  급감하므로 기본은 s 만 제외하고 --strict 로 카테고리 통째 제외도 지원한다.

  python analysis/taste_vector_corr.py --axis cat2
  python analysis/taste_vector_corr.py --axis cat3 --strict
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"

MAX_STORE_USERS = 400
MIN_AXIS_OVERLAP = 3      # 벡터 비교 시 최소 공통 축 수


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else None


def cosine(va: dict, vb: dict) -> tuple[float, int] | tuple[None, int]:
    common = set(va) & set(vb)
    if len(common) < MIN_AXIS_OVERLAP:
        return None, len(common)
    na = math.sqrt(sum(va[k] ** 2 for k in common))
    nb = math.sqrt(sum(vb[k] ** 2 for k in common))
    if na == 0 or nb == 0:
        return None, len(common)
    dot = sum(va[k] * vb[k] for k in common)
    return dot / (na * nb), len(common)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", choices=["cat2", "cat3"], default="cat2")
    ap.add_argument("--strict", action="store_true",
                    help="평가 가게가 속한 카테고리 전체를 벡터에서 제외")
    args = ap.parse_args()

    # 가게 -> 카테고리
    cat: dict[str, str] = {}
    for line in (DATA / "store_meta.jsonl").open(encoding="utf-8"):
        m = json.loads(line)
        v = m.get(args.axis)
        if v:
            cat[m["store_id"]] = v
    print(f"카테고리 보유 가게 {len(cat):,}곳 · 축 종류 {len(set(cat.values()))}종")

    # 리뷰
    us: dict[str, dict[str, int]] = collections.defaultdict(dict)
    store_users: dict[str, set[str]] = collections.defaultdict(set)
    for line in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("star"):
            us[r["user_hash"]][r["store_id"]] = r["star"]
            store_users[r["store_id"]].add(r["user_hash"])

    umean = {u: statistics.mean(sm.values()) for u, sm in us.items()}
    # 유저별 카테고리 -> [별점]
    ucat: dict[str, dict[str, list[int]]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for u, sm in us.items():
        for s, st in sm.items():
            c = cat.get(s)
            if c:
                ucat[u][c].append(st)
    cov = [len(v) for v in ucat.values()]
    if not cov:
        print("카테고리 매칭된 리뷰 없음 — 수집 대기 필요")
        return
    print(f"유저당 보유 축 수: 중앙 {statistics.median(cov):.0f} · 평균 {statistics.mean(cov):.1f}")

    def vector(u: str, drop_store: str, drop_cat: str | None) -> dict[str, float]:
        """u 의 성향 벡터. drop_store 는 제외, strict 면 drop_cat 통째 제외."""
        out = {}
        for c, _ in ucat[u].items():
            if drop_cat and c == drop_cat:
                continue
            vals = [st for s, st in us[u].items()
                    if cat.get(s) == c and s != drop_store]
            if vals:
                out[c] = statistics.mean(vals) - umean[u]
        return out

    # ── 같은 가게 안에서 쌍 만들기 ─────────────────────────────
    recs = []
    for s, users in store_users.items():
        if len(users) < 2 or len(users) > MAX_STORE_USERS:
            continue
        c_s = cat.get(s)
        if not c_s:
            continue
        ul = sorted(users)
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                dc = c_s if args.strict else None
                va = vector(a, s, dc)
                vb = vector(b, s, dc)
                sim, k = cosine(va, vb)
                if sim is None:
                    continue
                diff = abs(us[a][s] - us[b][s])
                adj = abs((us[a][s] - umean[a]) - (us[b][s] - umean[b]))
                recs.append((sim, diff, adj, k))

    print(f"\n분석 대상 (가게, 쌍) {len(recs):,}건  "
          f"[axis={args.axis}{' strict' if args.strict else ''}]")
    if len(recs) < 100:
        print("표본 부족")
        return

    sims = [r[0] for r in recs]
    diffs = [r[1] for r in recs]
    adjs = [r[2] for r in recs]
    print(f"  유사도 범위 {min(sims):+.3f} ~ {max(sims):+.3f} · 평균 {statistics.mean(sims):+.3f}")

    print("\n■ 상관계수 (음수여야 가설 지지)")
    print(f"  성향유사도 vs 별점차이(원본)     r = {pearson(sims, diffs):+.4f}")
    print(f"  성향유사도 vs 별점차이(편향보정)  r = {pearson(sims, adjs):+.4f}")

    order = sorted(recs, key=lambda r: r[0])
    n = len(order)
    print(f"\n■ 성향유사도 구간별 같은 가게 별점차이  (n={n:,})")
    print(f"    {'구간':>10} {'n':>7} {'유사도':>8} {'별점차이':>9} {'보정차이':>9}")
    prev = None
    for lo, hi, label in [(0, .20, '하위20%'), (.20, .40, '20-40%'), (.40, .60, '40-60%'),
                          (.60, .80, '60-80%'), (.80, .95, '80-95%'), (.95, 1.0, '상위5%')]:
        seg = order[int(n * lo):int(n * hi)]
        if not seg:
            continue
        ms = statistics.mean(r[0] for r in seg)
        md = statistics.mean(r[1] for r in seg)
        ma = statistics.mean(r[2] for r in seg)
        arrow = "" if prev is None else (" ↓" if md < prev else " ↑")
        prev = md
        print(f"    {label:>10} {len(seg):7,} {ms:+8.3f} {md:9.3f}{arrow} {ma:9.3f}")

    # 무작위 베이스라인
    random.seed(3)
    ulist = list(us)
    rnd = []
    for _ in range(50000):
        a, b = random.sample(ulist, 2)
        common = set(us[a]) & set(us[b])
        if common:
            s = random.choice(list(common))
            rnd.append(abs(us[a][s] - us[b][s]))
    print(f"\n■ 무작위 쌍 베이스라인 별점차이 = {statistics.mean(rnd):.3f}")

    print("\n■ 공통 축 수를 통제해도 관계가 남는가")
    byk = collections.defaultdict(list)
    for sim, d, adj, k in recs:
        band = '3-4개' if k < 5 else '5-9개' if k < 10 else '10개+'
        byk[band].append((sim, d))
    for band in ['3-4개', '5-9개', '10개+']:
        g = byk[band]
        if len(g) < 30:
            continue
        r = pearson([x[0] for x in g], [x[1] for x in g])
        print(f"  공통축 {band:>6}: n={len(g):7,}  r={r:+.4f}  "
              f"평균차이 {statistics.mean(x[1] for x in g):.3f}")


if __name__ == "__main__":
    main()
