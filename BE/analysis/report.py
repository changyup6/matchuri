# -*- coding: utf-8 -*-
"""표현력 채점 결과 요약 통계."""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rubric import CATEGORIES, CATEGORY_KO

ROOT = Path(__file__).resolve().parents[1]
GRADE_KO = {0: "0 무내용", 1: "1 단순평가", 2: "2 속성언급", 3: "3 구체묘사"}


def pct(n, d):
    return f"{100*n/d:5.1f}%" if d else "  -  "


def main() -> None:
    rows = [json.loads(l) for l in (ROOT / "data" / "analysis" / "reviews_scored.jsonl").open(encoding="utf-8")]
    ko = [r for r in rows if r["lang"] == "ko"]
    N = len(rows)

    print(f"전체 {N}건 (한국어 {len(ko)} / 외국어 {sum(1 for r in rows if r['lang']=='foreign')} / 텍스트없음 {sum(1 for r in rows if r['lang']=='non_text')})")

    print("\n■ 표현력 등급 분포 (전체 / 카카오 / 네이버)")
    gk = collections.Counter(r["grade"] for r in rows if r["source"] == "kakao")
    gn = collections.Counter(r["grade"] for r in rows if r["source"] == "naver")
    ga = collections.Counter(r["grade"] for r in rows)
    print(f"  {'등급':<10} {'전체':>13} {'카카오':>13} {'네이버':>13}")
    for g in range(4):
        print(f"  {GRADE_KO[g]:<10} {ga[g]:5}건 {pct(ga[g],N)} {gk[g]:5}건 {pct(gk[g],600)} {gn[g]:5}건 {pct(gn[g],600)}")

    print("\n■ 카테고리별 언급률 / 레벨 분포")
    print(f"  {'카테고리':<12} {'언급률':>7} {'L1':>7} {'L2':>7} {'L3':>7}  {'평균레벨':>7}")
    for c in CATEGORIES:
        lv = [r[f"lv_{c}"] for r in rows]
        cnt = collections.Counter(lv)
        mentioned = N - cnt[0]
        print(f"  {CATEGORY_KO[c]:<12} {pct(mentioned,N)} {pct(cnt[1],N)} {pct(cnt[2],N)} {pct(cnt[3],N)}  {statistics.mean(lv):7.2f}")

    print("\n■ 묘사 폭 (breadth: 언급한 카테고리 수)")
    b = collections.Counter(r["breadth"] for r in rows)
    for k in sorted(b):
        print(f"  {k}개 축: {b[k]:5}건 {pct(b[k],N)}")

    print("\n■ 길이 vs 표현력")
    print(f"  {'등급':<10} {'n':>5} {'평균 글자수':>11} {'중앙값':>7}")
    for g in range(4):
        sel = [r["char_len"] for r in rows if r["grade"] == g]
        if sel:
            print(f"  {GRADE_KO[g]:<10} {len(sel):5} {statistics.mean(sel):11.0f} {statistics.median(sel):7.0f}")

    print("\n■ 별점 vs 표현력 (별점 있는 건만)")
    print(f"  {'등급':<10} {'n':>5} {'평균 별점':>9}")
    for g in range(4):
        sel = [r["rating"] for r in rows if r["grade"] == g and r["rating"]]
        if sel:
            print(f"  {GRADE_KO[g]:<10} {len(sel):5} {statistics.mean(sel):9.2f}")

    print("\n■ 음식 카테고리별 평균 depth_sum")
    for cat, grp in sorted(collections.Counter(r["category"] for r in rows).items()):
        sel = [r["depth_sum"] for r in rows if r["category"] == cat]
        print(f"  {cat}: {statistics.mean(sel):.2f}  (n={len(sel)})")

    print("\n■ 거점별 평균 depth_sum")
    for hub in ["강남", "홍대", "성수", "이태원", "여의도", "잠실"]:
        sel = [r["depth_sum"] for r in rows if r["hub"] == hub]
        print(f"  {hub}: {statistics.mean(sel):.2f}")

    print("\n■ 학습에 쓸 만한 리뷰 (grade>=2) 비율")
    good = sum(1 for r in rows if r["grade"] >= 2)
    print(f"  {good}건 / {N}건 = {pct(good,N)}")
    g3 = [r for r in rows if r["grade"] == 3]
    print(f"  이 중 grade 3 은 {len(g3)}건, 평균 {statistics.mean([r['char_len'] for r in g3]):.0f}자")


if __name__ == "__main__":
    main()
