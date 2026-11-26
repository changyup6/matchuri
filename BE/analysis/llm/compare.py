# -*- coding: utf-8 -*-
"""규칙 기반 채점과 LLM 채점을 비교한다.

  python analysis/llm/compare.py data/analysis/llm_exaone3.5_7.8b_n50.jsonl
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

CATS = ["taste", "service", "price", "ambience", "wait"]
CAT_KO = {"taste": "맛·음식", "service": "서비스", "price": "가격", "ambience": "분위기", "wait": "대기·편의"}


def kappa(a: list[int], b: list[int], k: int = 4) -> float:
    """Cohen's weighted kappa (linear weights) — 순서형 라벨용."""
    n = len(a)
    if n == 0:
        return float("nan")
    obs = collections.Counter(zip(a, b))
    ca, cb = collections.Counter(a), collections.Counter(b)
    w = lambda i, j: 1 - abs(i - j) / (k - 1)
    po = sum(w(i, j) * c for (i, j), c in obs.items()) / n
    pe = sum(w(i, j) * ca[i] * cb[j] for i in range(k) for j in range(k)) / (n * n)
    return (po - pe) / (1 - pe) if pe != 1 else float("nan")


def main() -> None:
    path = Path(sys.argv[1])
    rows = [json.loads(l) for l in path.open(encoding="utf-8")]
    n = len(rows)
    print(f"비교 대상: {n}건  ({path.name})\n")

    rg = [r["rule_grade"] for r in rows]
    lg = [r["llm_grade"] for r in rows]
    exact = sum(1 for a, b in zip(rg, lg) if a == b)
    within1 = sum(1 for a, b in zip(rg, lg) if abs(a - b) <= 1)
    print("■ 리뷰 등급(grade) 일치도")
    print(f"  완전일치 {exact}/{n} = {100*exact/n:.1f}%   ±1 이내 {100*within1/n:.1f}%")
    print(f"  weighted kappa = {kappa(rg, lg):.3f}")
    print(f"  평균 등급  규칙 {statistics.mean(rg):.2f}  vs  LLM {statistics.mean(lg):.2f}")

    print("\n■ 혼동행렬 (행=규칙, 열=LLM)")
    print("        LLM0  LLM1  LLM2  LLM3")
    for i in range(4):
        row = [sum(1 for r in rows if r["rule_grade"] == i and r["llm_grade"] == j) for j in range(4)]
        print(f"  규칙{i}  " + "".join(f"{v:5}" + " " for v in row))

    print("\n■ 축별 일치도")
    print(f"  {'축':<10} {'완전일치':>8} {'kappa':>7}  {'평균(규칙)':>10} {'평균(LLM)':>9}")
    for c in CATS:
        a = [r["rule_levels"][c] for r in rows]
        b = [r["llm_levels"][c] for r in rows]
        ex = sum(1 for x, y in zip(a, b) if x == y)
        print(f"  {CAT_KO[c]:<10} {100*ex/n:7.1f}% {kappa(a,b):7.3f}  {statistics.mean(a):10.2f} {statistics.mean(b):9.2f}")

    print("\n■ 등급 분포 비교")
    cr, cl = collections.Counter(rg), collections.Counter(lg)
    for g in range(4):
        print(f"  G{g}: 규칙 {cr[g]:4} ({100*cr[g]/n:5.1f}%)   LLM {cl[g]:4} ({100*cl[g]/n:5.1f}%)")

    dis = [r for r in rows if r["rule_grade"] != r["llm_grade"]]
    print(f"\n■ 불일치 {len(dis)}건 중 격차 큰 순 상위 15")
    dis.sort(key=lambda r: -abs(r["rule_grade"] - r["llm_grade"]))
    for r in dis[:15]:
        ev = "; ".join(f"{k}:{v}" for k, v in r["llm_evidence"].items())
        print(f"  규칙 G{r['rule_grade']} vs LLM G{r['llm_grade']} | {r['llm_levels']}")
        print(f"    본문: {r['content'][:100]}")
        print(f"    LLM근거: {ev[:110]}")

    el = [r["elapsed"] for r in rows if r.get("elapsed")]
    if el:
        print(f"\n■ 속도: 평균 {statistics.mean(el):.2f}s/건, 중앙값 {statistics.median(el):.2f}s")
        print(f"  1,200건 환산 예상 {statistics.mean(el)*1200/60:.0f}분")


if __name__ == "__main__":
    main()
