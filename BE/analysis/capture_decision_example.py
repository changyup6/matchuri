"""고정된 검증 사례를 실제 HTTP API로 재생하여 반환된 네 카드를 캡처한다.

검증용 뷰어이며 모바일 앱 구현은 아니다. 원본 응답/사용자 식별자를 저장하지 않는다.
"""
import argparse
from html import escape
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np

BE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE / "server"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    example = next(r for r in data["runs"] if r["flow"] == "app_10_plus_50"
                   and r["family"] not in ("random", "all_like", "all_dislike")
                   and r["first_at"]["revised"] is not None
                   and r["first_quality"]["revised"]["fit_count"] == 4)
    spec = importlib.util.spec_from_file_location("decision_validation", Path(__file__).with_name("decision_validation.py"))
    validation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validation)
    with tempfile.TemporaryDirectory(prefix="decision-example-") as temp:
        os.environ["MATCHURI_STATE"] = temp
        from fastapi.testclient import TestClient
        import main as api
        from recsys import policy
        client = TestClient(api.app)
        family, seed = example["family"], example["seed"]
        uid = f"validation-{family}-{seed}"
        sess = policy.session_for(uid)
        for name in ("legacy", "taste"):
            sess[name]["rng"] = np.random.default_rng(seed)
        queue = []
        for size in [10, 50]:
            response = client.get(f"/cards?user_id={uid}&limit={size}")
            response.raise_for_status()
            queue.extend(response.json())
        expected_n = example["first_at"]["revised"]
        for n, card in enumerate(queue[:expected_n], 1):
            sid = card["store"]["id"]
            like = validation.uniform(family, seed, sid) < validation.utility(family, sid)
            response = client.post("/preferences?include_decision=true", json={
                "user_id": uid, "card_id": card["card_id"], "prefer": "like" if like else "dislike"})
            response.raise_for_status()
            result = response.json()["decision"]
            assert result["ready"] == (n == expected_n), "HTTP replay differs from held-out protocol"
        assert len(result["cards"]) == 4
        cards_html = []
        for i, card in enumerate(result["cards"], 1):
            s = card["store"]
            source = card.get("image") or ""
            if not source.startswith(("https://", "http://")):
                source = ""
            cards_html.append(f'''<article><div class="photo"><span>{escape(s['category_group'])}</span>
              <img src="{escape(source, quote=True)}" onerror="this.style.display='none'" /></div>
              <div class="content"><small>선택 후보 {i} · 카드 {card['card_id']}</small>
              <h2>{escape(s['name'])}</h2><p>{escape(s['category'])}</p>
              <div class="tags">{''.join('<b>'+escape(t)+'</b>' for t in s['tags'][:3])}</div>
              <p class="reason">{escape(card['recommendation_reason'])}</p></div></article>''')
        html = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><style>
          @font-face{{font-family:paper;src:url('{(BE.parent/'FE/web/src/fonts/Paperlogy-4Regular.ttf').as_uri()}')}}
          *{{box-sizing:border-box}} body{{margin:0;background:#f6f8fc;color:#172640;font-family:paper,sans-serif;padding:38px 44px}}
          .eyebrow{{color:#bb5c29;font-size:14px}}h1{{font-size:30px;margin:16px 0 10px}}.sub{{color:#65748a;margin-bottom:28px}}
          .metrics{{display:flex;gap:20px;background:white;padding:18px 22px;border-radius:14px;margin-bottom:24px}}
          .metrics div{{flex:1;color:#65748a;font-size:13px}}.metrics strong{{display:block;color:#172640;font-size:23px;margin-top:8px}}
          .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}}article{{background:white;border-radius:16px;overflow:hidden;border:1px solid #e0e7f0}}
          .photo{{height:195px;background:#e7edf4;position:relative;display:flex;align-items:center;justify-content:center;color:#7b8ca6}}
          img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}}.content{{padding:20px}}small{{font-size:11px;color:#7b8ca6}}
          h2{{font-size:19px;line-height:1.4;height:55px;margin:12px 0 5px}}p{{font-size:13px;color:#65748a;line-height:1.6}}
          .tags{{display:flex;gap:5px;flex-wrap:wrap;min-height:52px}}b{{font-weight:400;font-size:11px;padding:6px;background:#eff4fa;border-radius:5px;height:25px}}
          .reason{{padding-top:12px;border-top:1px solid #edf1f6;color:#356b61}}footer{{font-size:12px;color:#65748a;margin-top:25px;line-height:1.7}}
          </style><body><div class="eyebrow">검증용 뷰어 · 합성 반응 / 실제 HTTP 응답</div>
          <h1>{escape(result['message'])}</h1><div class="sub">최종 후보의 과거 선호 여부와 관계없이, 현재 취향 적합도와 다양성을 검사한 결과입니다.</div>
          <div class="metrics"><div>최초 제안 시점<strong>{expected_n}회 / 30회</strong></div>
          <div>반환된 선택 후보<strong>{len(result['cards'])}곳</strong></div>
          <div>개인화 순위 유지율<strong>{result['stability']*100:.0f}%</strong></div>
          <div>과거 예측 구분 AUC<strong>{result['validation_auc']:.2f}</strong></div></div>
          <div class="grid">{''.join(cards_html)}</div>
          <footer>별도 검증 seed의 사례 한 건을 다시 실행한 캡처입니다. 전체 성적은 검증 그래프를 참고하세요.<br>
          AUC·순위 유지율은 만족 확률이 아닙니다. 이 화면은 모바일 앱 UI가 아닌 API 응답 확인용 뷰어입니다.</footer></body></html>'''
        page_path = Path(temp) / "example.html"
        page_path.write_text(html)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1360, "height": 840}, device_scale_factor=1)
            page.goto(page_path.as_uri(), wait_until="load", timeout=60000)
            page.screenshot(path=str(args.output_dir / "api-four-cards.png"), full_page=True)
            loaded_images = page.locator("img").evaluate_all("imgs => imgs.filter(i => i.complete && i.naturalWidth > 0).length")
            browser.close()
        summary = {"family": family, "synthetic_seed": seed, "first_proposal_at": expected_n,
                   "cards": len(result["cards"]), "loaded_images": loaded_images,
                   "http_parity": True, "validation_auc": result["validation_auc"],
                   "stability": result["stability"], "example_is_not_representative": True}
        (args.output_dir / "api-example-check.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
