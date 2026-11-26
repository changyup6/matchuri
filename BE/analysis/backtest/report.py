# -*- coding: utf-8 -*-
"""결과를 사람이 읽는 표(.md)로 옮긴다. 숫자의 출처는 언제나 같이 저장되는 .json 이다.

참조 모델(paired bootstrap 의 기준)은 **과제마다 다르다.** PLAN 5절의 결정대로 과제 P
는 `popularity`, 과제 R 은 `quality` 를 기준으로 쓴다 — P 에서 quality 는 0.50 이라
그것과의 차이는 아무 것도 말해주지 않는다. blob 의 `references` 가 과제별 기준을 담고,
옛 결과(step0)처럼 `references` 가 없는 blob 은 `baseline_model` 하나를 양쪽에 쓴다.

`sections` 에 `{"heading", "body"}` 를 넣으면 표 뒤에 그대로 붙는다. 단계마다 다른
증거(사례 설명·범용 메뉴 목록 등)를 report.py 를 고치지 않고 실을 자리다.
"""
from __future__ import annotations

import math

from . import config
from .evaluate import P_METRICS

CI_MARK = {True: "**", False: ""}     # CI 가 0 을 넘긴 값만 굵게


def references(blob: dict) -> dict[str, str]:
    """과제 -> 기준 모델. 옛 blob 은 `baseline_model` 하나를 양쪽에 쓴다."""
    ref = blob.get("references")
    if ref:
        return dict(ref)
    base = blob.get("baseline_model", config.BASELINE_MODEL)
    return {"task_p": base, "task_r": base}


def _fmt(v: float, nd: int = 4) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{nd}f}"


def _cell(entry: dict, baseline_name: str, model: str) -> str:
    v = _fmt(entry["mean"])
    ci = entry.get("vs_baseline")
    if model == baseline_name or not ci or math.isnan(ci.get("diff", float("nan"))):
        return v
    mark = CI_MARK[bool(ci["significant"])]
    return (f"{v} <br><sub>{mark}{ci['diff']:+.4f}{mark} "
            f"[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}]</sub>")


def _table(blob: dict, task: str, metric: str) -> str:
    models = list(blob["results"])
    ns = blob["history_sizes"]
    base = references(blob)[task]
    head = "| 모델 | " + " | ".join(f"N={n}" for n in ns) + " |"
    sep = "|---|" + "---|" * len(ns)
    rows = [head, sep]
    for m in models:
        cells = []
        for n in ns:
            e = blob["results"][m][str(n)][task].get(metric)
            cells.append(_cell(e, base, m) if e else "—")
        rows.append(f"| `{m}` | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def to_markdown(blob: dict) -> str:
    u = blob["users"]
    p = blob["pool"]
    ref = references(blob)
    ns = blob["history_sizes"]
    L: list[str] = []
    a = L.append

    a(f"# {blob.get('title', 'Step 0 — 백테스트 하네스와 베이스라인')} ({blob['run']})")
    a("")
    a(f"생성 {blob['generated_at']} · seed `{blob['seed']}` · 커밋 `{blob['git_commit']}`")
    a("")
    for line in blob.get("intro", [
            "`analysis/PLAN.md` 1절 '백테스트 계약' 의 구현이다. 이후 모든 실험(메뉴 MaxSim,",
            "리뷰, 사진, 하이브리드)은 이 표의 `quality` 열과 비교된다."]):
        a(line)
    a("")
    a("```bash")
    for line in blob.get("command", [
            f"~/venvs/recsys/bin/python -m analysis.backtest.run "
            f"--models {','.join(blob['results'])} \\",
            f"    --N {','.join(str(n) for n in ns)} --out {blob['run']}"]):
        a(line)
    a("```")
    a("")

    a("## 한눈에")
    a("")
    for line in blob.get("headline", []):
        a(f"- {line}")
    a("")

    for sec in blob.get("sections_before", []):
        a(f"## {sec['heading']}")
        a("")
        a(sec["body"])
        a("")

    a("## 무엇을 쟀나")
    a("")
    a(f"- **후보 풀 {p['size']:,}곳** — 전체 {p['stores_total']:,}곳 중 카카오 리뷰 "
      f"{config.MIN_POOL_REVIEWS}+ 이고 사진 {config.MIN_POOL_PHOTOS}+ "
      f"(`swipe._build_pool` 과 같은 규칙). README 의 2,216 과 일치하고 "
      "`swipe.py` 주석의 2,045 는 옛 수치다.")
    a(f"- **평가 사용자 {u['eval_users']}명** — 후보 풀 안 리뷰 T ≥ "
      f"{config.MIN_USER_REVIEWS}. T 평균 {u['total_mean']:.1f} · 중앙값 "
      f"{u['total_median']:.0f} · 최대 {u['total_max']}.")
    a(f"- **미래(정답) F = max({config.FUTURE_MIN}, ceil({config.FUTURE_RATIO}·T))** — "
      f"N 과 무관하게 고정. 사용자당 평균 {u['future_mean']:.1f}곳, 합 {u['future_total']:,}곳.")
    a("- **이력 N** — 미래 직전의 최근 N 건(중첩). N 을 다 채운 사용자 수:")
    a("")
    a("| N | " + " | ".join(str(n) for n in ns) + " |")
    a("|---|" + "---|" * len(ns))
    a("| 이력 N 을 다 채운 사용자 | "
      + " | ".join(f"{u['full_history'][str(n)]} / {u['eval_users']}" for n in ns) + " |")
    a("| 모델에 실제로 들어간 신호(평균) | "
      + " | ".join(f"{u['signal_mean'][str(n)]:.1f}" for n in ns) + " |")
    mode = blob.get("history_mode", u.get("history_mode", "like_dislike"))
    if u.get("signal_mean_full"):
        a("| 같은 것, N 을 다 채운 사용자만 | "
          + " | ".join(f"{u['signal_mean_full'][str(n)]:.1f}" for n in ns) + " |")
    if mode == "visit" and u.get("signal_mean_like_dislike"):
        a("| (참고) `like_dislike` 해석이었다면 | "
          + " | ".join(f"{u['signal_mean_like_dislike'][str(n)]:.1f}" for n in ns) + " |")
    a("")
    if mode == "visit":
        a("이력 해석은 **`visit`(방문 = 관심)** 이다 — 이력의 모든 방문이 가중 1 의 "
          "양성 신호이고 dislike 가 없다(PLAN 5절 Step 1.5 결정 1번). 별점을 보지 "
          "않으므로 버리는 신호가 없고, N 을 다 채운 사용자에게는 신호 수가 곧 N 이다. "
          "N 을 못 채운 사용자 때문에 전체 평균은 N 보다 작다.")
    else:
        a(f"이력 해석은 **`like_dislike`** 다. 신호가 N 보다 적은 것은 rating 3(중립)을 "
          f"넣지 않기 때문이다 — like는 rating ≥ {config.LIKE_MIN_RATING}, dislike 는 "
          f"≤ {config.DISLIKE_MAX_RATING}.")
    a("")

    a("## 과제 P — 다음 방문 예측 (주 지표)")
    a("")
    a("양성 = 미래 방문 가게, 음성 = 후보 풀에서 이미 간 가게를 뺀 **나머지 전부**"
      "(샘플링 없이 전수 랭킹).")
    a("")
    a(f"셀은 `사용자 평균` / 아랫줄은 `{ref['task_p']}` 대비 차이와 paired bootstrap "
      f"{config.BOOTSTRAP_RESAMPLES:,}회 95% CI. **굵게** 표시된 것만 CI 가 0 을 넘겼다.")
    a("")
    a("`auc_pm` 은 **인기 매칭 AUC** — 양성마다 같은 리뷰수 십분위의 음성만 상대로 잰다. "
      "인기를 통제한 뒤에도 남는 신호가 있는지를 보는 지표다(PLAN 5절 2번).")
    a("")
    for metric in P_METRICS:
        a(f"### {metric}")
        a("")
        a(_table(blob, "task_p", metric))
        a("")

    a("## 과제 R — 방문 가게 중 만족 예측 (보조)")
    a("")
    a(f"미래 방문 가게만 놓고 rating ≥ {config.SATISFIED_MIN_RATING} 를 양성, "
      f"≤ {config.SATISFIED_MIN_RATING - 1} 를 음성으로 AUC. 양쪽 클래스가 다 있는 "
      f"**{blob['users']['task_r_users']}명**만 센다. 기준은 `{ref['task_r']}`.")
    a("")
    a(_table(blob, "task_r", "auc"))
    a("")

    for sec in blob.get("sections", []):
        a(f"## {sec['heading']}")
        a("")
        a(sec["body"])
        a("")

    a("## 읽는 법과 주의")
    a("")
    for line in blob["notes"]:
        a(f"- {line}")
    a("")

    if blob.get("leak_notes"):
        a("## 누수 규칙")
        a("")
        for line in blob["leak_notes"]:
            a(f"- {line}")
        a("")

    a("## 모델")
    a("")
    a("| 이름 | 무엇 |")
    a("|---|---|")
    for name, meta in blob["models"].items():
        a(f"| `{name}` | {meta['description']} |")
    a("")
    return "\n".join(L) + "\n"
