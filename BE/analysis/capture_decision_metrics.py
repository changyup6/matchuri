"""30 synthetic responses through the actual HTTP API, exported as JSONL and a measured plot.

No live users/state are read. This validates exports, not recommendation efficacy.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ["OPENBLAS_NUM_THREADS"] = "1"
BE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE / "server"))

import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace-output", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("validation", Path(__file__).with_name("decision_validation.py"))
    validation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validation)
    with tempfile.TemporaryDirectory(prefix="decision-metrics-") as temp:
        os.environ["MATCHURI_STATE"] = temp
        from fastapi.testclient import TestClient
        import main as api
        from recsys import policy
        client = TestClient(api.app)
        family, seed = "korean", 2000
        uid = f"validation-{family}-{seed}"
        sess = policy.session_for(uid)
        for name in ("legacy", "taste"):
            sess[name]["rng"] = np.random.default_rng(seed)
        queue = []
        for size in (10, 50):
            response = client.get(f"/cards?user_id={uid}&limit={size}")
            response.raise_for_status()
            queue.extend(response.json())
        frames, timings = [], []
        small_example = None
        for n, card in enumerate(queue[:30], 1):
            sid = card["store"]["id"]
            like = validation.uniform(family, seed, sid) < validation.utility(family, sid)
            started = time.perf_counter()
            response = client.post("/preferences?include_decision=true&include_decision_metrics=true", json={
                "user_id": uid, "card_id": card["card_id"], "prefer": "like" if like else "dislike"})
            response.raise_for_status()
            timings.append((time.perf_counter()-started)*1000)
            data = response.json()
            frame = data["decision_metrics"]
            assert frame["feedback_count"] == n
            assert frame["decision"]["ready"] == data["decision"]["ready"]
            assert set(frame["decision"]["store_ids"]) == {c["store"]["id"] for c in data["decision"]["cards"]}
            for pair in frame["pairs"]:
                assert pair["passes_current_rule"] == (not pair["blocking_reasons"])
            frames.append(frame)
            if small_example is None and frame["decision"]["ready"]:
                sample_response = client.get(f"/users/{uid}/decision/metrics", params={
                    "candidate_limit": 4, "snapshot_id": frame["snapshot_id"]})
                sample_response.raise_for_status()
                small_example = sample_response.json()
        args.trace_output.parent.mkdir(parents=True, exist_ok=True)
        args.trace_output.write_text("".join(json.dumps(f, ensure_ascii=False, allow_nan=False)+"\n" for f in frames))
        # Full-pool pagination against the real endpoint at one frozen feedback state.
        all_candidates, offset, snapshot_id = [], 0, frames[-1]["snapshot_id"]
        while True:
            response = client.get(f"/users/{uid}/decision/metrics", params={
                "candidate_limit": 200, "candidate_offset": offset, "snapshot_id": snapshot_id})
            response.raise_for_status()
            page = response.json()
            all_candidates.extend(page["candidates"])
            offset = page["next_offset"]
            if offset is None:
                break
        assert len(all_candidates) == frames[-1]["candidate_total"]
        assert len({c["store_id"] for c in all_candidates}) == len(all_candidates)
        from export_decision_metrics import write_export
        full = {**frames[-1], "candidates": all_candidates, "candidate_limit": len(all_candidates), "next_offset": None}
        write_export(full, args.trace_output.parent / "metrics-full-export")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    example = next((f for f in frames if f["decision"]["ready"]), frames[-1])
    (args.output_dir / "metrics-export-example.json").write_text(json.dumps(small_example or example, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    check = {"synthetic_example_not_efficacy": True, "family": family, "seed": seed,
             "feedback_count": len(frames), "http_decision_parity": True,
             "full_pool_paginated_count": len(all_candidates),
             "example_feedback_count": example["feedback_count"],
             "example_pair_count": len(example["pairs"]),
             "candidate_records_across_30": sum(len(f["candidates"]) for f in frames),
             "http_in_process_with_write_decision_and_export_ms": {
                 "p50": float(np.percentile(timings, 50)), "p95": float(np.percentile(timings, 95))},
             "provenance": example["provenance"]}
    (args.output_dir / "metrics-export-check.json").write_text(json.dumps(check, ensure_ascii=False, indent=2)+"\n")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font = BE.parent / "FE/web/src/fonts/Paperlogy-4Regular.ttf"
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=str(font)).get_name(), "axes.unicode_minus": False})
    fig, axes = plt.subplots(1, 3, figsize=(17, 6), facecolor="#f7f9fc")
    fig.suptitle("판단 수치 내보내기 · 실제 API 실행", x=.055, ha="left", fontsize=22)
    fig.text(.055, .88, "실제 서빙 벡터 + 합성 반응 30회 / 현재 앱 10+50장 흐름 · 추천 성공률을 증명하는 실험은 아닙니다", fontsize=11)
    for key, label, color in [("validation_auc", "답변 전 순위 AUC", "#e47840"), ("stability", "순위 유지율", "#3379ae")]:
        values = [f["personalization"][key] if f["personalization"][key] is not None else np.nan for f in frames]
        axes[0].plot(range(1, 31), values, label=label, color=color, linewidth=2)
    axes[0].set(xlabel="스와이프", ylim=(0, 1.05), title="① 개인화 근거의 변화 · 미측정은 빈 구간")
    axes[0].legend(fontsize=9, frameon=False)
    chosen = example["decision"]["store_ids"] or [c["store_id"] for c in example["candidates"][:4]]
    candidates = [next(c for c in example["candidates"] if c["store_id"] == sid) for sid in chosen]
    x = np.arange(len(candidates))
    axes[1].bar(x-.17, [c["personal_score"] for c in candidates], .34, label="개인화 기여", color="#3379ae")
    axes[1].bar(x+.17, [c["conservative_score"] for c in candidates], .34, label="불확실성 보정 후", color="#329183")
    axes[1].axhline(example["fit"]["threshold"], color="#b06450", linestyle="--", label="현재 적합도 기준")
    axes[1].set(xticks=x, xticklabels=[f"후보 {i+1}" for i in x], title=f"② {example['feedback_count']}회째 후보별 적합도 분해")
    axes[1].legend(fontsize=9, frameon=False)
    pairs = example["pairs"][:6]
    matrix = np.array([[p["similarities"][k] if p["similarities"][k] is not None else np.nan for k in ("menu", "text", "review", "food")] for p in pairs])
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad("#cccccc")
    axes[2].imshow(matrix, vmin=0, vmax=1, cmap=cmap, aspect="auto")
    axes[2].set(xticks=range(4), xticklabels=["메뉴", "요약문", "리뷰", "사진"], yticks=range(len(pairs)),
                yticklabels=[f"후보 {chosen.index(p['store_ids'][0])+1} ↔ {chosen.index(p['store_ids'][1])+1}" for p in pairs],
                title="③ 가게 쌍의 원시 유사도 · 성분별 유지")
    for (i, j), value in np.ndenumerate(matrix):
        axes[2].text(j, i, f"{value:.3f}" if np.isfinite(value) else "결측", ha="center", va="center",
                     color="white" if value > .6 else "#172640")
    fig.text(.055, .06, "JSON / CSV: 개인화 근거·성분 가중치/분산·후보별 점수/기여·가게 쌍 유사도·결측·통과/보류 사유·설정/버전\nAUC·유지율·모델 표준편차는 보정된 만족 확률이나 개인화 완료 확률이 아닙니다.", fontsize=11)
    fig.subplots_adjust(left=.055, right=.98, top=.75, bottom=.23, wspace=.35)
    fig.savefig(args.output_dir / "metrics-export-overview.png", dpi=140)
    plt.close(fig)
    # The accompanying explanation uses the same exported numbers; no inferred taste labels.
    fig, ax = plt.subplots(figsize=(13, 8), facecolor="#f7f9fc")
    ax.axis("off")
    ax.text(.02, .97, "숫자와 함께 읽어야 하는 의미", fontsize=24, va="top", color="#172640")
    ax.text(.02, .89, f"실제 API의 {example['feedback_count']}회째 응답 · 합성 반응 사례 / 사용자 성능 검증 아님", fontsize=12, color="#65748a")
    p = example["personalization"]
    first = candidates[0]
    pair = example["pairs"][0]["similarities"]
    blocks = [
        ("① 개인화 근거", f"구분력 {p['validation_auc']:.3f} · 순위 유지율 {p['stability']:.0%}",
         f"긍정 {p['positive_predictions']}건 × 부정 {p['negative_predictions']}건 = {p['positive_predictions']*p['negative_predictions']}개 비교쌍에서, 응답 전 긍정 순위가 더 높았던 정도입니다.\n"
         f"유지율은 최근 비교에서 상위20곳 중 최소{round(p['stability']*20)}곳이 겹쳤다는 뜻입니다.\n"
         "두 값은 반응 구분과 최근 변화의 단서입니다. 표본은 작고, 개인화 완료 확률은 아닙니다."),
        ("② 후보의 취향 적합도", f"차감 점수 {first['conservative_score']:.3f} · 기준 {example['fit']['threshold']:.3f}",
         f"개인화 점수 {first['personal_score']:.3f}에서 모델 추정 폭 {first['model_sd']:.3f}의 절반을 차감했습니다.\n"
         f"미평가 풀의 상대 기준보다 {first['fit_margin']:.3f} 높아 현재 적합도 규칙을 통과했습니다.\n"
         "같은 기준 풀에서 높게 평가된 후보라는 뜻입니다. 절대 만족도나 수락 확률로 읽으면 안 됩니다."),
        ("③ 가게끼리 얼마나 닮았는가", f"메뉴 {pair['menu']:.3f} · 요약 {pair['text']:.3f} · 리뷰 {pair['review']:.3f} · 사진 {pair['food']:.3f}",
         "선택 후보1과2의 고차원 표현에서 계산한 성분별 유사도입니다.\n"
         "같은 성분 안에서 클수록 가깝습니다. 메뉴와 사진은 분포가 달라 크기를 직접 비교할 수 없습니다.\n"
         "이 값으로 어떤 취향을 공유하는지 이름 붙이거나, 사람이 충분히 다른 대안으로 느낀다고 단정하지 않습니다."),
    ]
    for y, (label, value, explanation) in zip((.79, .54, .29), blocks):
        ax.text(.02, y, label, fontsize=15, color="#286d65", va="top")
        ax.text(.29, y, value, fontsize=16, color="#172640", va="top")
        ax.text(.29, y-.065, explanation, fontsize=11, color="#455268", va="top", linespacing=1.65)
    ax.text(.02, .025, "export 동봉: 현재 값의 한국어 해설 + 조건별 판단 이유 + 지표별 의미·단위·읽는 법·해석 한계·결측 설명", fontsize=11)
    fig.tight_layout()
    fig.savefig(args.output_dir / "metrics-reading-guide.png", dpi=150)
    plt.close(fig)
    print(json.dumps(check, ensure_ascii=False))


if __name__ == "__main__":
    main()
