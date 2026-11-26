# -*- coding: utf-8 -*-
"""규칙 vs LLM 불일치 건을 사람이 판정하고, 어느 쪽이 맞았는지 집계한다.

  # 1) 불일치 건을 판정용 템플릿으로 추출
  python analysis/llm/audit.py extract data/analysis/llm_*.jsonl --out data/analysis/audit_todo.jsonl

  # 2) 템플릿의 gold_levels 를 손으로 채운 뒤 채점
  python analysis/llm/audit.py score data/analysis/audit_todo.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

CATS = ["taste", "service", "price", "ambience", "wait"]
CAT_KO = {"taste": "맛·음식", "service": "서비스", "price": "가격", "ambience": "분위기", "wait": "대기·편의"}


def load(pattern: str) -> list[dict]:
    rows = []
    for p in glob.glob(pattern):
        rows += [json.loads(l) for l in Path(p).open(encoding="utf-8")]
    return rows


def cmd_extract(args) -> None:
    rows = load(args.src)
    dis = [r for r in rows if r["rule_grade"] != r["llm_grade"]
           or any(r["rule_levels"][c] != r["llm_levels"][c] for c in CATS)]
    dis.sort(key=lambda r: -abs(r["rule_grade"] - r["llm_grade"]))
    if args.limit:
        dis = dis[:args.limit]
    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for r in dis:
            f.write(json.dumps({
                "review_id": r["review_id"], "content": r["content"],
                "rule_levels": r["rule_levels"], "llm_levels": r["llm_levels"],
                "llm_evidence": r.get("llm_evidence", {}),
                "gold_levels": None,          # ← 사람이 채울 칸 (dict 로 교체)
                "note": "",
            }, ensure_ascii=False) + "\n")
    print(f"불일치 {len(dis)}건 추출 -> {out}")
    print("각 줄의 gold_levels 를 {\"taste\":2,\"service\":0,...} 형태로 채운 뒤 score 실행")


def cmd_score(args) -> None:
    rows = [json.loads(l) for l in Path(args.src).open(encoding="utf-8")]
    done = [r for r in rows if isinstance(r.get("gold_levels"), dict)]
    if not done:
        print("gold_levels 가 채워진 줄이 없다.")
        return

    print(f"판정 완료 {len(done)} / 전체 {len(rows)}\n")
    tot = {"rule": 0, "llm": 0, "both": 0, "neither": 0}
    per_cat = {c: {"rule": 0, "llm": 0, "n": 0} for c in CATS}

    for r in done:
        g = r["gold_levels"]
        for c in CATS:
            if c not in g:
                continue
            per_cat[c]["n"] += 1
            rok = r["rule_levels"][c] == g[c]
            lok = r["llm_levels"][c] == g[c]
            per_cat[c]["rule"] += rok
            per_cat[c]["llm"] += lok
            if rok and lok:   tot["both"] += 1
            elif rok:         tot["rule"] += 1
            elif lok:         tot["llm"] += 1
            else:             tot["neither"] += 1

    n = sum(tot.values())
    print("■ 축 단위 판정 결과 (정답 대비)")
    print(f"  둘 다 정답   {tot['both']:4} ({100*tot['both']/n:5.1f}%)")
    print(f"  규칙만 정답  {tot['rule']:4} ({100*tot['rule']/n:5.1f}%)")
    print(f"  LLM만 정답   {tot['llm']:4} ({100*tot['llm']/n:5.1f}%)")
    print(f"  둘 다 오답   {tot['neither']:4} ({100*tot['neither']/n:5.1f}%)")

    print("\n■ 축별 정확도")
    print(f"  {'축':<10} {'n':>4} {'규칙':>7} {'LLM':>7}")
    for c in CATS:
        d = per_cat[c]
        if d["n"]:
            print(f"  {CAT_KO[c]:<10} {d['n']:4} {100*d['rule']/d['n']:6.1f}% {100*d['llm']/d['n']:6.1f}%")

    ra = (tot["both"] + tot["rule"]) / n
    la = (tot["both"] + tot["llm"]) / n
    print(f"\n■ 종합 정확도 (불일치 구간 한정)  규칙 {100*ra:.1f}%  vs  LLM {100*la:.1f}%")
    print("  ※ 불일치 건만 본 것이므로 전체 정확도가 아니다. 일치 구간은 양쪽 모두 맞다고 가정할 때")
    print("     전체 정확도 = (일치건수 + 해당 방식 정답수) / 전체건수 로 환산해야 한다.")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract"); e.add_argument("src"); e.add_argument("--out", required=True); e.add_argument("--limit", type=int, default=0); e.set_defaults(fn=cmd_extract)
    s = sub.add_parser("score"); s.add_argument("src"); s.set_defaults(fn=cmd_score)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
