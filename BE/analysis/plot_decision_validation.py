"""검증 산출물을 요약 JSON·한국어 그래프·PDF로 만든다. 실험을 다시 실행하지 않는다."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

BE = Path(__file__).resolve().parents[1]
FONT = BE.parent / "FE/web/src/fonts/Paperlogy-4Regular.ttf"
font_manager.fontManager.addfont(str(FONT))
plt.rcParams.update({"font.family": font_manager.FontProperties(fname=str(FONT)).get_name(),
                     "font.size": 11, "axes.unicode_minus": False, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.titleweight": "normal",
                     "axes.labelcolor": "#455268", "text.color": "#172640",
                     "axes.edgecolor": "#d6dce5", "savefig.facecolor": "#f7f9fc"})
FLOWS = {"app_10_plus_50": "현재 앱: 10+50장 선조회", "batch_10": "가정: 10장씩 갱신",
         "batch_5": "가정: 5장씩 갱신", "one_at_a_time": "이전 방식: 한 장씩 갱신"}
FAMILIES = {"korean": "한식", "japanese_chinese": "일식·중식", "western": "양식",
            "coffee_sweets": "카페·간식", "broad": "넓은 식사 취향", "budget_meal": "1만원 이하 식사"}
COLORS = {"app_10_plus_50": "#e05a36", "batch_10": "#3367bd", "batch_5": "#28877b",
          "one_at_a_time": "#969aaa"}


def aggregate(rows, version):
    hits = [r for r in rows if r["first_at"][version] is not None]
    total = len(rows)
    curve = [sum(r["first_at"][version] <= n for r in hits) for n in range(1, 31)]
    return {"n": total, "at20": curve[19], "at25": curve[24], "at30": len(hits), "curve": curve,
            "all4_fit": sum(r["first_quality"][version]["fit_count"] == 4 for r in hits),
            "exact3_fit": sum(r["first_quality"][version]["fit_count"] == 3 for r in hits),
            "atmost2_fit": sum(r["first_quality"][version]["fit_count"] <= 2 for r in hits),
            "none_fit": sum(r["first_quality"][version]["fit_count"] == 0 for r in hits),
            "median_first_if_triggered": float(np.median([r["first_at"][version] for r in hits])) if hits else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    positives = [r for r in data["runs"] if r["family"] in FAMILIES]
    versions = list(data["configs"])
    totals = {f: {v: aggregate([r for r in positives if r["flow"] == f], v) for v in versions}
              for f in FLOWS if any(r["flow"] == f for r in positives)}
    latencies = {}
    for key in data["runs"][0]["timings"]:
        values = [t for r in data["runs"] if r["flow"] == "app_10_plus_50" for t in r["timings"][key]]
        latencies[key] = {"n": len(values), **{f"p{p}": round(float(np.percentile(values, p)), 2)
                                               for p in (50, 95, 99)}} if values else {"n": 0}
    compact = {k: v for k, v in data.items() if k != "runs"}
    compact.update(positive_aggregates=totals, current_app_latency_ms=latencies,
                   rule_violations=sum(r["rule_violations"] for r in data["runs"]))
    (out / "validation-results.json").write_text(json.dumps(compact, ensure_ascii=False, indent=2)+"\n")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor="#f7f9fc")
    fig.suptitle("20~30회 선택 제안 · 별도 검증용 합성 반응 실험", x=.06, y=.97, ha="left", fontsize=21)
    fig.text(.06, .925, f"9종 반응 × {data['seeds_per_family']}개 seed × 4개 수신 흐름 = {len(data['runs']):,}회 실행  |  매 실행 30회까지 관찰", color="#65748a")
    ax = axes[0, 0]
    ax.axvspan(20, 30, color="#ecf1f7", zorder=0)
    for flow, name in FLOWS.items():
        row = totals[flow]["revised"]
        ax.plot(range(1, 31), np.array(row["curve"])/row["n"]*100,
                label=name, color=COLORS[flow], linewidth=2.5)
    ax.set(xlim=(1, 30), ylim=(0, 100), title="① 조건이 충족되어 처음 제안한 비율",
           xlabel="누적 스와이프", ylabel="전체 취향 시나리오 중 (%)")
    ax.legend(fontsize=9, loc="upper left", frameon=False)
    ax.grid(axis="y", alpha=.15)

    ax = axes[0, 1]
    selected = [totals["app_10_plus_50"][v] for v in versions]
    base = np.zeros(len(versions))
    for key, label, color in [("all4_fit", "4곳 모두 설정 취향에 맞음", "#28877b"),
                              ("exact3_fit", "3곳 맞음", "#7badba"),
                              ("atmost2_fit", "0~2곳 맞음", "#e99470")]:
        values = np.array([r[key]/r["n"]*100 for r in selected])
        ax.barh(["기존 임계값", "보수적 임계값"], values, left=base, label=label, color=color, height=.5)
        for i, value in enumerate(values):
            if value >= 3:
                ax.text(base[i]+value/2, i, f"{value:.1f}", ha="center", va="center", fontsize=10, color="white")
        base += values
    ax.barh(["기존 임계값", "보수적 임계값"], 100-base, left=base, color="#e6eaf0", label="30회까지 제안 없음", height=.5)
    ax.set(xlim=(0, 100), title="② 최초 4곳의 품질까지 확인 · 현재 앱 흐름", xlabel="전체 취향 시나리오 중 (%)")
    ax.legend(fontsize=9, loc="lower center", bbox_to_anchor=(.5, -.39), frameon=False, ncol=2)

    ax = axes[1, 0]
    groups = [r for r in data["summary"] if r["family"] == "random" and r["version"] == "revised"]
    order = list(FLOWS)
    for i, flow in enumerate(order):
        row = next(r for r in groups if r["flow"] == flow)
        rate = row["at30"]/row["n"]*100
        lo, hi = np.array(row["trigger_ci95"])*100
        ax.bar(i, rate, color=COLORS[flow], width=.55)
        ax.errorbar(i, rate, yerr=[[rate-lo], [hi-rate]], color="#172640", capsize=5)
        ax.text(i, hi+3, f"{row['at30']}/{row['n']}", ha="center", fontsize=11)
    ax.set(ylim=(0, 100), title="③ 무작위 반응에도 제안했는가?", ylabel="30회 이내 제안 비율 (%)")
    ax.set_xticks(range(4), ["현재 앱", "10장씩", "5장씩", "1장씩"])
    ax.text(0, -.22, "오차 막대: 독립 합성 실행의 Wilson 95% 구간", transform=ax.transAxes, fontsize=9, color="#65748a")
    ax.grid(axis="y", alpha=.15)

    ax = axes[1, 1]
    labels, ready, fit = [], [], []
    for family, label in FAMILIES.items():
        r = next(r for r in data["summary"] if r["flow"] == "app_10_plus_50" and r["family"] == family and r["version"] == "revised")
        labels.append(label); ready.append(r["at30"]/r["n"]*100); fit.append(r["all4_fit_at30"]/r["n"]*100)
    y = np.arange(len(labels))
    ax.barh(y+.16, ready, height=.29, color="#e05a36", label="30회 이내 제안")
    ax.barh(y-.16, fit, height=.29, color="#28877b", label="제안 + 4곳 모두 적합")
    ax.set_yticks(y, labels)
    ax.set(xlim=(0, 100), title="④ 취향별 편차 · 현재 앱 흐름", xlabel="각 취향 32회 실행 중 (%)")
    ax.legend(fontsize=9, frameon=False, loc="lower right")
    ax.grid(axis="x", alpha=.15)
    fig.text(.06, .027, "판독 주의: 설정된 합성 취향에 대한 진단입니다. 실제 사용자 만족도·개인화 완료 확률이 아닙니다.\n최종 후보는 좋아요·싫어요·서빙 여부와 무관하게 같은 적합도/다양성 기준으로 평가했습니다.", fontsize=10, color="#65748a")
    fig.subplots_adjust(left=.08, right=.97, top=.86, bottom=.14, hspace=.65, wspace=.32)
    fig.savefig(out / "validation-overview.png", dpi=160)
    fig.savefig(out / "validation-overview.pdf")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(14, 6.5), facecolor="#f7f9fc")
    ax.axis("off")
    ax.set(xlim=(0, 100), ylim=(0, 100))
    ax.text(0, 96, "이전 20회 테스트는 무엇을 놓쳤나", fontsize=22)
    rows = [(71, "이전 테스트", "1장 추천 → 반응 → 즉시 새 추천", "매번 갱신된 모델로 노출 · 4개 seed · 최초 제안에서 중단", "#e99470"),
            (43, "현재 앱 재현", "시작 시 10장 + 50장 → 그 큐에서 스와이프", "첫 30장 노출은 대부분 초기 모델 · 최종 4장만 최신 취향으로 평가", "#e05a36"),
            (15, "이번 검증", "실제 라우터/HTTP 대조 → 고정 반응표 → 30회 관찰", "미제안 포함 · 최초 4곳 품질 · 무작위 대조 · 별도 seed · 95% 구간", "#28877b")]
    for y, label, title, detail, color in rows:
        ax.text(0, y+6, label, fontsize=13, color=color)
        ax.text(20, y+6, title, fontsize=16)
        ax.text(20, y-4, detail, fontsize=12, color="#65748a")
        ax.axhline(y-12, color="#dce3ed", linewidth=1)
    fig.savefig(out / "validation-method.png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(json.dumps(totals["app_10_plus_50"], ensure_ascii=False))


if __name__ == "__main__":
    main()
