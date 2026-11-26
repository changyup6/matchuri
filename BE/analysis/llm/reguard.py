# -*- coding: utf-8 -*-
"""이미 생성된 llm_*.jsonl 결과에 최신 apply_guards() 를 재적용한다.

LLM 을 다시 호출하지 않고, 저장된 llm_levels/llm_evidence 만 가지고
가드 로직을 다시 돌려 grade/breadth/depth_sum 을 갱신한다.
run_llm.py 의 가드를 고친 뒤, 이미 뽑아둔 결과에 소급 적용할 때 쓴다.

  python analysis/llm/reguard.py data/analysis/llm_exaone3.5_7.8b_full.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_llm import CATS, apply_guards


def main() -> None:
    src = Path(sys.argv[1])
    out = src.with_name(src.stem + "_reguarded.jsonl")

    n, changed = 0, 0
    with src.open(encoding="utf-8") as fin, out.open("w", encoding="utf-8") as fout:
        for line in fin:
            r = json.loads(line)
            res = {c: {"lv": r["llm_levels"][c], "ev": r["llm_evidence"].get(c, "")} for c in CATS}
            res, fired = apply_guards(res)
            if fired:
                changed += 1
                r["guards_fired"] = list(set(r.get("guards_fired", []) + fired))
                r["llm_levels"] = {c: res[c]["lv"] for c in CATS}
                r["llm_evidence"] = {c: res[c]["ev"] for c in CATS if res[c]["ev"]}
                r["llm_grade"] = max(r["llm_levels"].values())
                r["llm_breadth"] = sum(1 for v in r["llm_levels"].values() if v >= 1)
                r["llm_depth_sum"] = sum(r["llm_levels"].values())
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1

    print(f"{n}건 재검사, {changed}건 추가 교정 -> {out}")


if __name__ == "__main__":
    main()
