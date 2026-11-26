"""Read-only catalog audit; export aggregates and a design diagram, never raw pairs/users.

Run from BE: python analysis/decision_design_audit.py --output-dir /tmp/decision-audit
The distribution describes the catalog, not human judgments of similarity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch
import numpy as np

BE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE / "server"))
from recsys import content, store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    art = content.load()
    if art is None:
        raise RuntimeError("Serving artifacts unavailable")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    font = BE.parent / "FE/web/src/fonts/Paperlogy-4Regular.ttf"
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=str(font)).get_name(),
                         "axes.unicode_minus": False, "font.size": 11})
    n = len(art.ids)
    a, b = np.triu_indices(n, k=1)
    cats = np.array([(store.get_store(sid) or {}).get("cat3") or "" for sid in art.ids])
    known_cats = (cats[a] != "") & (cats[b] != "")
    same_cat = known_cats & (cats[a] == cats[b])
    report = {"scope": "all unordered distinct pairs in current serving catalog; no user filters",
              "not_human_similarity_labels": True, "stores": n, "pairs": len(a),
              "cat3_missing_stores": int((cats == "").sum()),
              "both_cat3_known_pairs": int(known_cats.sum()),
              "same_nonempty_cat3_pairs": int(same_cat.sum()), "modalities": {}}
    bins = np.linspace(-1, 1, 101)
    total, count = np.zeros(len(a)), np.zeros(len(a), dtype=int)
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), facecolor="#f7f9fc")
    labels = {"menu": "메뉴", "text": "요약문", "review": "리뷰", "food": "음식 사진"}
    colors = ["#2f6ac2", "#d96b36", "#329183", "#9861b2"]
    for (name, label), color in zip(labels.items(), colors):
        values = np.asarray(getattr(art, "sim_" + name)[a, b], dtype=float)
        finite = np.isfinite(values)
        v = values[finite]
        total += np.where(finite, values, 0)
        count += finite
        def summary(mask):
            subset = values[finite & mask]
            return {"n": len(subset), "median": float(np.median(subset)) if len(subset) else None,
                    "ge_0_8_fraction": float(np.mean(subset >= .8)) if len(subset) else None}
        report["modalities"][name] = {
            "known_pairs": len(v), "missing_pair_fraction": float(1-finite.mean()),
            "quantiles": {str(q): float(np.quantile(v, q)) for q in (.1, .5, .9, .95, .99)},
            "ge_0_8_fraction": float(np.mean(v >= .8)),
            "same_cat3": summary(same_cat), "different_cat3": summary(known_cats & ~same_cat)}
        hist, _ = np.histogram(v, bins=bins)
        axes[0].plot(bins[1:], hist.cumsum()/len(v)*100, label=label, color=color, linewidth=2)
    mean = total / np.maximum(count, 1)
    enough = count >= 2
    report["mean_rule"] = {"at_least_two_known_pairs": int(enough.sum()),
                           "mean_ge_0_8_pairs": int((enough & (mean >= .8)).sum()),
                           "fewer_than_two_known_pairs": int((~enough).sum())}
    inputs = [BE / "server/recsys/content.py", BE / "server/recsys/decision.py",
              BE / "data/kakao/stores.jsonl", BE / "data/kakao/serving/manifest.json"]
    report["sha256"] = {str(p.relative_to(BE.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in inputs}
    (args.output_dir / "design-data-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    axes[0].axvline(.8, color="#34445a", linestyle="--", alpha=.5)
    axes[0].set(xlim=(0, 1), ylim=(0, 101), xlabel="원시 유사도 (음수 구간은 표시 생략)",
                ylabel="이 값 이하인 관측 쌍 (%)", title="성분별 유사도의 눈금이 다릅니다")
    axes[0].legend(frameon=False)
    missing = [report["modalities"][k]["missing_pair_fraction"]*100 for k in labels]
    axes[1].barh(list(labels.values()), missing, color=colors, height=.55)
    for i, val in enumerate(missing):
        axes[1].text(val+1, i, f"{val:.1f}%", va="center")
    axes[1].set(xlim=(0, 100), xlabel="전체 가게 쌍 중 성분 결측 비율 (%)", title="결측을 평균 점수로만 덮으면 근거량이 사라집니다")
    fig.suptitle(f"실제 서빙 데이터 점검 · {n:,}곳 / {len(a):,}쌍 전수", fontsize=18, x=.06, ha="left")
    fig.text(.06, .045, "카탈로그 분포의 실측입니다. 사람의 유사성 판단·추천 만족도·새 임계값의 근거를 직접 증명하지 않습니다.", fontsize=10)
    fig.subplots_adjust(left=.07, right=.96, bottom=.2, top=.80, wspace=.30)
    fig.savefig(args.output_dir / "design-data-audit.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(13, 8), facecolor="#f7f9fc")
    ax.set(xlim=(0, 13), ylim=(0, 8))
    ax.axis("off")
    ax.text(.35, 7.6, "4곳 제안의 설계 기준", fontsize=24, color="#172640")
    ax.text(.35, 7.13, "설계 검토안 · 아래의 보정·불확실성·조합 판단은 아직 구현/실증되지 않았습니다", fontsize=12, color="#a04935")

    def box(x, y, w, h, title, body, color="#e8eef7"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.12", linewidth=0, facecolor=color))
        ax.text(x+.15, y+h-.35, title, fontsize=15, color="#172640")
        ax.text(x+.15, y+h-.78, body, fontsize=11, color="#455268", va="top", linespacing=1.6)

    box(.4, 5.45, 12.1, 1.15, "기존 추천과 스와이프는 계속", "노출·응답 이력 + 현재 위치·필터 + 데이터 결측/버전 → 매 응답 뒤 제안 가능성 평가")
    box(.4, 3.3, 3.75, 1.6, "① 취향을 얼마나 아는가", "여러 취향과 이번 선택 맥락\n개인화의 추가 예측력\n현재 후보 판단의 불확실성")
    box(4.55, 3.3, 3.75, 1.6, "② 각 가게가 맞는가", "현재 맥락에서 선택 가능한가\n보정된 적합도 + 근거량\n낮은 적합도를 다양성으로 상쇄 금지")
    box(8.7, 3.3, 3.75, 1.6, "③ 함께 고를 가치가 있는가", "메뉴·가격·분위기 등 차이\n내 취향 안에서 의미 있는 대안\n4곳 조합 전체의 중복/선택 가치")
    for x in (2.25, 6.4, 10.55):
        ax.annotate("", (x, 5.0), (x, 5.4), arrowprops={"arrowstyle": "->", "color": "#68788e"})
        ax.annotate("", (x, 2.85), (x, 3.2), arrowprops={"arrowstyle": "->", "color": "#68788e"})
    box(.4, 1.5, 12.1, 1.25, "세 근거를 함께 확인해 ‘지금 제안할 만한 4곳’인지 판단", "충족 → 4곳 선택 권유     /     근거 부족·적합한 조합 부족 → 기존 스와이프 계속\n20~30회는 경험 목표. 횟수만으로 조건을 낮추지 않음.", "#deeee9")
    ax.text(.5, .86, "최종 후보의 과거 좋아요·싫어요·노출 여부는 자격 조건이 아닙니다. 반응은 취향 학습에 사용합니다.", fontsize=11)
    ax.text(.5, .39, "실증 순서: 측정 정의 → 로그·평가 데이터 → 점수 보정 → 조합/제안 정책 비교 → 독립 사용자 검증", fontsize=11, color="#455268")
    fig.tight_layout()
    fig.savefig(args.output_dir / "decision-design-review.png", dpi=150)
    plt.close(fig)
    print(json.dumps({k: v for k, v in report.items() if k != "sha256"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
