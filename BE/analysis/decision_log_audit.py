"""운영 기록의 검증 가능성 점검. 원본/사용자 식별자를 출력하거나 변경하지 않는다.

현재 카탈로그 이후 처음 평가한 프로필만 재생한다. 기록이 사람/테스트인지 식별되지
않고 노출 확률도 없으므로 실제 사용자 만족도나 반사실 성과로 해석하지 않는다.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys

BE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE / "server"))


def run(directory):
    from app import loader
    from recsys import content, decision, taste
    loader.load_all()
    art = content.load()
    histories = defaultdict(list)
    first_seen = {}
    bad = 0
    with (directory / "preferences.jsonl").open() as handle:
        for line in handle:
            try:
                row = json.loads(line)
                at = datetime.fromisoformat(row["at"])
                if at.tzinfo is None:
                    raise ValueError("timezone missing")
                uid = row["user_id"]
                first_seen[uid] = min(first_seen.get(uid, at), at)
                sid = loader.to_store_id(int(row["card_id"]))
                label = row["prefer"]
                if sid not in art.pos or label not in ("like", "dislike"):
                    bad += 1
                    continue
                histories[uid].append((at, sid, label == "like"))
            except (ValueError, KeyError, TypeError):
                bad += 1
    cutoff = datetime.fromisoformat("2026-09-11T16:00:00+09:00")
    eligible, ready, future_overlap, reasons = 0, 0, 0, defaultdict(int)
    first_times = []
    for uid, history in histories.items():
        # 파일 순서가 실제 학습 순서다. 재정렬하거나 중복 평가를 삭제하지 않는다.
        if first_seen[uid] < cutoff or len({r[1] for r in history}) < 30:
            continue
        eligible += 1
        sess = taste._new_session()
        first = None
        for n, (_, sid, label) in enumerate(history[:30], 1):
            taste.record(sess, sid, label)
            result = decision.evaluate(sess)
            if first is None and result["ready"]:
                first = (n, set(result["store_ids"]))
        reasons[result["status"]] += 1
        if first:
            ready += 1
            first_times.append(first[0])
            future_overlap += len(first[1] & {r[1] for r in history[30:]})
    exposure_keys = set()
    exposures = 0
    with (directory / "exposures.jsonl").open() as handle:
        for line in handle:
            try:
                exposure_keys.update(json.loads(line))
                exposures += 1
            except (ValueError, TypeError):
                pass
    return {"valid_records_current_pool": sum(map(len, histories.values())),
            "profiles_current_pool": len(histories), "invalid_or_out_of_pool_records": bad,
            "eligibility_cutoff": cutoff.isoformat(), "eligible_profiles": eligible,
            "first_30_recorded_feedback_events": {"ready": ready, "first_times": first_times,
                                                 "last_status_counts": dict(reasons)},
            "proposed_stores_with_later_recorded_feedback": future_overlap,
            "exposure_records": exposures,
            "has_logged_propensity": any(k in exposure_keys for k in ("propensity", "probability", "selection_probability")),
            "limitations": ["Profiles may be test scripts; not verified human users.",
                            "No historical filter/location reconstruction; unfiltered diagnostic only.",
                            "No randomized logging probabilities: unbiased policy comparison is unavailable.",
                            "Sparse future feedback is not counterfactual ground truth for the four proposed stores."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.state_dir), ensure_ascii=False, indent=2))
