# -*- coding: utf-8 -*-
"""병렬 실행 + 이어하기 지원 버전.

Ollama 서버가 OLLAMA_NUM_PARALLEL=N (+ flash-attn, q8_0 KV캐시) 로 떠 있어야
실제 처리량이 오른다. --out 파일에 이미 있는 review_id 는 건너뛴다.

  python analysis/llm/run_llm_parallel.py --workers 2 --out data/analysis/llm_exaone3.5_7.8b_full.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_llm import CATS, apply_guards, call, normalize

ROOT = HERE.parents[1]


def process_one(model, r):
    t1 = time.time()
    try:
        raw, usage = call(model, r["content"])
        res = normalize(raw)
        fail = False
    except Exception as exc:
        res, usage, fail = normalize(None), {}, True
        print(f"  [{r['review_id']}] 실패: {exc!r}", flush=True)
    res, guards_fired = apply_guards(res)
    levels = {c: res[c]["lv"] for c in CATS}
    return {
        "review_id": r["review_id"], "source": r["source"],
        "store_name": r["store_name"], "hub": r["hub"], "category": r["category"],
        "rating": r["rating"], "content": r["content"], "char_len": r["char_len"],
        "rule_grade": r["grade"], "rule_levels": {c: r[f"lv_{c}"] for c in CATS},
        "llm_levels": levels, "llm_grade": max(levels.values()),
        "llm_breadth": sum(1 for v in levels.values() if v >= 1),
        "llm_depth_sum": sum(levels.values()),
        "llm_evidence": {c: res[c]["ev"] for c in CATS if res[c]["ev"]},
        "guards_fired": guards_fired, "elapsed": round(time.time()-t1, 2),
        "usage": usage, "fail": fail,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="exaone3.5:7.8b")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    src = ROOT / "data" / "analysis" / "reviews_scored.jsonl"
    rows = [json.loads(l) for l in src.open(encoding="utf-8")]

    out_path = Path(args.out)
    done_ids = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            line = line.strip()
            if line:
                done_ids.add(json.loads(line)["review_id"])
    todo = [r for r in rows if r["review_id"] not in done_ids]
    print(f"이미 완료 {len(done_ids)}건, 남은 {len(todo)}건, workers={args.workers}", flush=True)

    lock = threading.Lock()
    done, fails, t0 = 0, 0, time.time()
    with out_path.open("a", encoding="utf-8") as f, cf.ThreadPoolExecutor(args.workers) as ex:
        for rec in ex.map(lambda r: process_one(args.model, r), todo):
            with lock:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                done += 1
                fails += rec["fail"]
                if done % 20 == 0 or done == len(todo):
                    el = time.time() - t0
                    print(f"  {done}/{len(todo)}  {el:.0f}s  ({el/done:.2f}s/건, "
                          f"{done/el:.3f}건/s, 남은 예상 {(len(todo)-done)*el/done/60:.1f}분)", flush=True)

    print(f"\n완료: {done}건 (실패 {fails}) -> {out_path}")


if __name__ == "__main__":
    main()
