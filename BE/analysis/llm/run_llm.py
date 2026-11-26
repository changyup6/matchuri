# -*- coding: utf-8 -*-
"""로컬 LLM(Ollama)으로 리뷰 표현력을 채점한다.

  python analysis/llm/run_llm.py --limit 50            # 샘플 실행
  python analysis/llm/run_llm.py                        # 전량
  python analysis/llm/run_llm.py --model qwen3:4b       # 모델 교체

루브릭 전체를 system 메시지에 고정해 Ollama 의 프리픽스 KV 캐시를 재사용한다.
user 메시지는 리뷰 원문만 담는다.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
API = "http://127.0.0.1:11434/api/chat"
CATS = ["taste", "service", "price", "ambience", "wait"]

SYSTEM = (HERE / "prompt_system.md").read_text(encoding="utf-8")
SCHEMA = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))


def call(model: str, review: str, timeout: int = 180) -> dict | None:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"다음 리뷰를 채점하라.\n\n<리뷰>\n{review}\n</리뷰>"},
        ],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "num_ctx": 3072, "num_predict": 256, "seed": 7},
    }
    r = requests.post(API, json=body, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    usage = {"prompt_tokens": j.get("prompt_eval_count"), "out_tokens": j.get("eval_count")}
    try:
        return json.loads(j["message"]["content"]), usage
    except Exception:
        return None, usage


EMPTY_EV = {"없음", "없다", "없어요", "none", "n/a", "na", "-", "해당없음", "해당 없음"}


def normalize(raw: dict | None) -> dict:
    """스키마를 벗어난 응답도 안전하게 정리한다."""
    out = {}
    for c in CATS:
        node = (raw or {}).get(c) or {}
        lv = node.get("lv")
        lv = lv if isinstance(lv, int) and 0 <= lv <= 3 else 0
        ev = str(node.get("ev") or "")[:120]
        # "없음"처럼 근거 자체가 무근거라고 말하는데 lv>=1 인 스키마 위반을 정정한다
        if ev.strip().lower().rstrip(".,!? ") in EMPTY_EV:
            lv, ev = 0, ""
        out[c] = {"lv": lv, "ev": ev}
    return out


def _ev_key(s: str) -> str:
    return s.strip().rstrip(".,!?~ ")[:30]


# service 는 "대기·혼잡=서비스" 로 넘겨짚는 고질적 오류가 프롬프트 지시로도 안 고쳐졌다
# (동일 seed 재확인 결과 4/52 건 재현, A/B 전후 무변화 — 2026-08-24 검증).
# 모델 출력을 신뢰하는 대신, 결정론적 후처리로 service 근거가 다른 축 근거의
# 부분/전체 문자열이면 "독립적인 서비스 근거가 없다"고 보고 service 를 0으로 되돌린다.
GUARD_PAIRS = [("service", "wait"), ("service", "ambience")]

# 위 문자열-겹침 가드로 못 잡는 변종: 같은 리뷰 안에서 서로 다른 방문의 대기시간을
# 각각 인용해 문자열은 다르지만, 의미상 둘 다 "대기" 얘기일 뿐 직원 행동 얘기가 아닌 경우.
# 예) service 근거="30분 넘게...웨이팅 하고" / wait 근거="웨이팅 20분정도만" — 문자열은 다르나 둘 다 대기시간.
WAIT_ONLY_MARKERS = ["웨이팅", "대기", "줄서", "줄이", "줄을", "기다려", "기다리", "기다렸"]
SERVICE_ACTION_MARKERS = [
    "직원", "사장", "알바", "서빙", "종업원", "스태프", "이모님", "점원",
    "친절", "불친절", "무뚝뚝", "상냥", "싹싹", "정중", "무례",
    "응대", "안내", "챙겨", "챙기", "케어", "리필", "구워주", "잘라주",
    "설명해", "요청", "눈치", "배려", "무시", "퉁명", "방치",
]


def apply_guards(res: dict) -> tuple[dict, list[str]]:
    fired = []

    for c in CATS:
        ev = res[c]["ev"].strip().lower().rstrip(".,!? ")
        if res[c]["lv"] >= 1 and ev in EMPTY_EV:
            fired.append(f"{c}<-empty_ev:{res[c]['lv']}->0")
            res[c] = {"lv": 0, "ev": ""}

    for a, b in GUARD_PAIRS:
        ea, eb = _ev_key(res[a]["ev"]), _ev_key(res[b]["ev"])
        if res[a]["lv"] >= 1 and ea and eb and (ea in eb or eb in ea or (len(ea) >= 10 and ea[:15] == eb[:15])):
            fired.append(f"{a}<-{b}:{res[a]['lv']}->0")
            res[a] = {"lv": 0, "ev": ""}

    sv = res["service"]["ev"]
    if res["service"]["lv"] >= 1 and sv:
        if any(w in sv for w in WAIT_ONLY_MARKERS) and not any(w in sv for w in SERVICE_ACTION_MARKERS):
            fired.append(f"service<-wait_semantic:{res['service']['lv']}->0")
            res["service"] = {"lv": 0, "ev": ""}

    return res, fired


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="exaone3.5:7.8b")
    ap.add_argument("--limit", type=int, default=0, help="0이면 전량")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stratify", action="store_true", help="규칙 등급별 균등 표집")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = ROOT / "data" / "analysis" / "reviews_scored.jsonl"
    rows = [json.loads(l) for l in src.open(encoding="utf-8")]
    if args.limit:
        random.seed(args.seed)
        if args.stratify:
            # 규칙 기반 등급으로 층화 — G3 가 과소표집되면 프롬프트 검증이 약해진다
            by = {}
            for r in rows:
                by.setdefault(r["grade"], []).append(r)
            per = max(1, args.limit // len(by))
            picked = []
            for g in sorted(by):
                picked += random.sample(by[g], min(per, len(by[g])))
            rest = [r for r in rows if r not in picked]
            picked += random.sample(rest, max(0, min(args.limit - len(picked), len(rest))))
            rows = picked
        else:
            rows = random.sample(rows, min(args.limit, len(rows)))

    out_path = Path(args.out) if args.out else (
        ROOT / "data" / "analysis" / (f"llm_{args.model.replace(':','_')}"
                                      + (f"_n{args.limit}" if args.limit else "") + ".jsonl"))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done, t0, fails = 0, time.time(), 0
    with out_path.open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            t1 = time.time()
            try:
                raw, usage = call(args.model, r["content"])
                res = normalize(raw)
            except Exception as exc:
                fails += 1
                print(f"  [{i}] 실패: {exc!r}", flush=True)
                res, usage = normalize(None), {}
            res, guards_fired = apply_guards(res)
            levels = {c: res[c]["lv"] for c in CATS}
            rec = {
                "review_id": r["review_id"], "source": r["source"],
                "store_name": r["store_name"], "hub": r["hub"], "category": r["category"],
                "rating": r["rating"], "content": r["content"], "char_len": r["char_len"],
                "rule_grade": r["grade"],
                "rule_levels": {c: r[f"lv_{c}"] for c in CATS},
                "llm_levels": levels,
                "llm_grade": max(levels.values()),
                "llm_breadth": sum(1 for v in levels.values() if v >= 1),
                "llm_depth_sum": sum(levels.values()),
                "llm_evidence": {c: res[c]["ev"] for c in CATS if res[c]["ev"]},
                "guards_fired": guards_fired,
                "elapsed": round(time.time() - t1, 2),
                "usage": usage,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if guards_fired:
                print(f"  [{i}] guard 발동: {guards_fired}", flush=True)
            if done == 1:
                print(f"  [토큰] 프롬프트 {usage.get('prompt_tokens')} / 출력 {usage.get('out_tokens')}  (num_ctx=3072)", flush=True)
            if done % 10 == 0 or done == len(rows):
                el = time.time() - t0
                print(f"  {done}/{len(rows)}  {el:.0f}s  ({el/done:.2f}s/건, 남은 예상 {(len(rows)-done)*el/done/60:.1f}분)", flush=True)

    print(f"\n완료: {done}건 (실패 {fails}) -> {out_path}")


if __name__ == "__main__":
    main()
