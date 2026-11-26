# -*- coding: utf-8 -*-
"""카카오/네이버 리뷰 전체에 표현력 채점을 적용하고 결과를 저장한다."""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rubric import CATEGORIES, CATEGORY_KO
from scorer import score_review

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "analysis"

GRADE_KO = {0: "0_무내용", 1: "1_단순평가", 2: "2_속성언급", 3: "3_구체묘사"}
_HAN = re.compile(r"[가-힣]")


def language_flag(text: str) -> str:
    """한글 비중으로 한국어 리뷰 여부를 판정한다 (외국어 리뷰는 사전 적용 불가)."""
    letters = re.findall(r"[A-Za-z가-힣]", text or "")
    if not letters:
        return "non_text"          # 이모지·기호만
    ko = sum(1 for c in letters if _HAN.match(c))
    return "ko" if ko / len(letters) >= 0.3 else "foreign"


def main() -> None:
    rows = []
    for src in ("kakao", "naver"):
        for line in (ROOT / "data" / src / "reviews.jsonl").open(encoding="utf-8"):
            r = json.loads(line)
            s = score_review(r["content"])
            rec = {
                "source": src,
                "review_id": r["review_id"],
                "store_id": r["store_id"],
                "store_name": r["store_name"],
                "hub": r["hub"],
                "category": r["category"],
                "rating": r["rating"],
                "content": r["content"],
                "char_len": len(r["content"]),
                "lang": language_flag(r["content"]),
                "grade": s["grade"],
                "grade_label": GRADE_KO[s["grade"]],
                "breadth": s["breadth"],
                "depth_sum": s["depth_sum"],
                "top_category": s["top_category"] or "",
            }
            for c in CATEGORIES:
                rec[f"lv_{c}"] = s["levels"][c]
            rec["evidence_attrs"] = "; ".join(
                f"{c}:{'/'.join(s['attrs'][c])}" for c in CATEGORIES if s["attrs"][c])
            rec["evidence_signals"] = "; ".join(
                f"{c}:{'/'.join(s['signals'][c])}" for c in CATEGORIES if s["signals"][c])
            rows.append(rec)

    OUT.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys())
    with (OUT / "reviews_scored.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with (OUT / "reviews_scored.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)}건 채점 완료 -> {OUT}")


if __name__ == "__main__":
    main()
