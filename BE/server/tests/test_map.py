# -*- coding: utf-8 -*-
"""취향 지형도(`/admin/map`) — 지도 API 와 그 화면의 계약.

이 파일에서 제일 중요한 것은 **읽기 전용**을 잠그는 테스트다
(`test_지도는_아무것도_바꾸지_않는다`). 지도는 관리자가 마음껏 돌려 보는 화면이고,
돌릴 때마다 카드가 '보여진 것' 이 되거나 노출 로그가 쌓이면 팀-드래프트 인터리빙의
결과가 조용히 오염된다. 그 오염은 지표에서 '이상한 like 율' 로만 나타나므로 눈으로는
못 잡는다 — 그래서 세션 상태·`shown`·`state/exposures.jsonl` 을 호출 전후로 비교한다.

나머지는 화면이 빈 채로 뜨는 것을 막는 계약이다(`tests/test_admin_ui.py` 와 같은 규칙):
좌표·이웃의 모양, 사용자 응답의 필드, 가중치 재채점, `upto=` 재생, 콜드스타트.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import admin, state
from recsys import content, swipe, taste

HTML_PATH = Path(admin.__file__).parent / "static" / "map.html"

pytestmark = pytest.mark.skipif(
    not (content.SERVING / "layout.json").exists(),
    reason="data/kakao/serving/layout.json 이 없다 "
           "(python -m analysis.build_serving --only layout)")


@pytest.fixture(scope="module")
def html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def client() -> TestClient:
    import main
    return TestClient(main.app)


@pytest.fixture(scope="module")
def layout(client) -> dict:
    return client.get("/admin/api/map/layout").json()


@pytest.fixture(scope="module")
def user(client) -> str:
    """좋아요·별로가 섞인 사용자 하나. 지도에 색을 얹으려면 이력이 있어야 한다."""
    uid = client.post("/users", json={"name": "지형도"}).json()["user_id"]
    for n in range(2):
        cards = client.get(f"/cards?user_id={uid}&limit=8").json()
        for i, c in enumerate(cards):
            client.post("/preferences", json={"user_id": uid, "card_id": c["card_id"],
                                              "prefer": "like" if (i + n) % 3 else "dislike"})
    return uid


@pytest.fixture(scope="module")
def cold_user(client) -> str:
    """별로만 누른 사용자 — 좋아요가 0이면 점수가 콜드스타트로 간다."""
    uid = client.post("/users", json={"name": "콜드"}).json()["user_id"]
    cards = client.get(f"/cards?user_id={uid}&limit=4").json()
    for c in cards:
        client.post("/preferences", json={"user_id": uid, "card_id": c["card_id"],
                                          "prefer": "dislike"})
    return uid


# ────────────────────────────────────────────── ① 지형도 (좌표·메타·이웃)
def test_지형도가_후보_풀_전체를_준다(layout):
    ids = swipe.pool()["ids"]
    assert layout["count"] == len(ids) == 1629
    assert layout["ids"] == ids                      # i 번째 좌표 = i 번째 가게
    for key in ("x", "y", "name", "cat", "brand", "reviews", "rating",
                "nbr", "nbr_sim", "nbr_cross"):
        assert len(layout[key]) == layout["count"], key


def test_좌표가_0_1_안에_있다(layout):
    """화면이 [0,1]² 를 그대로 화면 좌표로 편다 — 벗어나면 점이 캔버스 밖으로 나간다."""
    for axis in ("x", "y"):
        v = layout[axis]
        assert min(v) >= 0.0 and max(v) <= 1.0, axis
    # 두 축의 배율이 같으므로 한 축은 [0,1] 을 꽉 채운다.
    spans = [max(layout[a]) - min(layout[a]) for a in ("x", "y")]
    assert max(spans) > 0.99
    assert len(set(zip(layout["x"], layout["y"]))) == layout["count"]   # 겹친 점 없음


def test_이웃이_5개이고_유사도_내림차순이다(layout):
    assert layout["neighbors"] == 5
    for i in (0, layout["count"] // 2, layout["count"] - 1):
        nbr, sims, cross = layout["nbr"][i], layout["nbr_sim"][i], layout["nbr_cross"][i]
        assert len(nbr) == len(sims) == len(cross) == 5
        assert i not in nbr and len(set(nbr)) == 5       # 자기 자신은 이웃이 아니다
        assert sims == sorted(sims, reverse=True)
        for j, n in enumerate(nbr):                      # cat2 를 넘는가는 cat 으로 다시 셀 수 있다
            assert bool(cross[j]) == (layout["cat"][i] != layout["cat"][n])


def test_지형도가_그린_방법과_주의를_적는다(layout):
    """UMAP 인지 t-SNE 인지, 무엇으로 만든 거리인지가 산출물에 적혀 있어야 한다."""
    assert layout["method"] in ("umap", "tsne")
    assert layout["embedding"]["method"] == layout["method"]
    assert set(layout["weights"]) == {"menu", "text", "review", "food"}
    assert all(name.endswith(".npy") and meta["sha256"]
               for name, meta in layout["sources"].items())
    assert "UMAP" in layout["caveat"]                    # 거리를 절대값으로 읽지 말 것
    assert 0.0 <= layout["stats"]["cross_category_share"] <= 1.0


def test_지형도_응답은_한_번만_읽는다(client):
    """두 번째 호출은 캐시라 파일을 다시 읽지 않는다(0.4MB 를 매번 다시 찍지 않는다)."""
    a = client.get("/admin/api/map/layout")
    b = client.get("/admin/api/map/layout")
    assert a.status_code == b.status_code == 200
    assert a.content == b.content
    assert a.headers["content-type"].startswith("application/json")


# ────────────────────────────────────────────── ② 사용자 응답 계약
def test_사용자_응답의_모양(client, user, layout):
    d = client.get(f"/admin/api/map/user/{user}").json()
    for key in ("user_id", "prefs", "swipes", "cold", "override", "weights",
                "like_ids", "dislike_ids", "shown", "scores", "top"):
        assert key in d
    assert len(d["scores"]) == layout["count"]
    assert all(isinstance(v, float) for v in d["scores"][:50])
    assert d["cold"] is False and d["override"] is False
    assert [w["key"] for w in d["weights"]] == list(content.COMPONENTS)   # API 순서
    assert all({"key", "name", "w", "sd", "used"} <= set(w) for w in d["weights"])
    assert all(w["w"] == w["used"] for w in d["weights"])                 # 덮어쓰지 않았다
    rated = set(d["like_ids"]) | set(d["dislike_ids"])
    assert rated and all(0 <= i < layout["count"] for i in rated)


def test_상위_카드는_답한_카드를_안_준다(client, user):
    d = client.get(f"/admin/api/map/user/{user}").json()
    rated = set(d["like_ids"]) | set(d["dislike_ids"])
    assert len(d["top"]) == 10
    assert d["top"] == sorted(d["top"], key=lambda c: -c["total"])
    for c in d["top"]:
        assert c["idx"] not in rated
        assert c["store_id"] not in taste.sessions()[user]["shown"]
        assert d["scores"][c["idx"]] == c["total"]
        assert {"card_id", "store_id", "idx", "rank", "total", "parts", "links",
                "reason", "name", "image"} <= set(c)
        assert set(c["parts"]) == set(content.COMPONENTS)   # 성분 집합이 가중치와 같다


def test_연결선은_좋아요한_가게로_간다(client, user):
    """양수 성분마다 '그 성분에서 가장 닮은 좋아요 가게' 하나. 근거의 출처를 밝힌다."""
    d = client.get(f"/admin/api/map/user/{user}").json()
    likes = set(d["like_ids"])
    seen_pair = False
    for c in d["top"]:
        keys = [lk["key"] for lk in c["links"]]
        assert len(keys) == len(set(keys))              # 성분마다 한 줄
        assert c["links"] == sorted(c["links"], key=lambda lk: -lk["part"])
        for lk in c["links"]:
            assert {"key", "name", "part", "idx", "store_id", "store_name",
                    "pair", "pair_kind", "pair_label", "cos"} <= set(lk)
            assert lk["idx"] in likes                   # 반드시 좋아요한 가게다
            assert lk["part"] > 0 and c["parts"][lk["key"]] == lk["part"]
            assert lk["name"] == content.LABELS[lk["key"]]
            if lk["pair"] is not None:
                seen_pair = True
                assert len(lk["pair"]) == 2 and all(lk["pair"])
                assert all(len(s) <= admin.MAP_SNIPPET for s in lk["pair"])
                assert lk["pair_label"]                 # 무엇을 보고 고른 쌍인지 적는다
    assert seen_pair, "근거 문장이 붙은 연결선이 하나도 없다"


def test_응답이_가볍다(client, user):
    """화면이 슬라이더를 움직일 때마다 다시 받는다 — 200KB 를 넘으면 안 된다."""
    r = client.get(f"/admin/api/map/user/{user}")
    assert len(r.content) < 200_000, f"{len(r.content) / 1000:.0f}KB"


def test_없는_사용자와_잘못된_가중치(client, user):
    assert client.get("/admin/api/map/user/nobody").status_code == 404
    for bad in ("1,2,3", "a,b,c,d,e,f,g,h", ",".join(["1e9"] * 8)):
        assert client.get(f"/admin/api/map/user/{user}?weights={bad}").status_code == 400


# ────────────────────────────────────────────── ③ 가중치 덮어쓰기
def test_가중치를_주면_순위가_바뀐다(client, user):
    """슬라이더는 '이 성분을 더 믿었다면' 을 묻는다 — 점수만 다시 낸다."""
    base = client.get(f"/admin/api/map/user/{user}").json()
    only_one = [0.0] * content.N_COMPONENTS
    only_one[content.IDX["menu"]] = 2.0
    q = ",".join(str(v) for v in only_one)
    got = client.get(f"/admin/api/map/user/{user}?weights={q}").json()

    assert got["override"] is True
    assert [w["w"] for w in got["weights"]] == [w["w"] for w in base["weights"]]
    assert [w["used"] for w in got["weights"]] == only_one      # 쓴 것은 슬라이더 값
    assert got["scores"] != base["scores"]
    assert [c["store_id"] for c in got["top"]] != [c["store_id"] for c in base["top"]]
    # 성분 기여도 새 가중치로 다시 분해된다 (가중치가 0인 성분은 기여가 0).
    for c in got["top"]:
        for key, part in c["parts"].items():
            if only_one[content.IDX[key]] == 0.0:
                assert part == 0.0, key


def _snapshot(user_id: str) -> dict:
    """살아 있는 세션과 노출 로그의 지문. 지도 API 는 이걸 하나도 바꾸면 안 된다."""
    sess = taste.sessions()[user_id]
    legacy = swipe.sessions()[user_id]
    exposures = state.EXPOSURES.read_bytes() if state.EXPOSURES.exists() else b""
    prefs = state.PREFS.read_bytes() if state.PREFS.exists() else b""
    return {
        "learn": copy.deepcopy(taste.learn_state(sess)),
        "m": sess["m"].tolist(), "v": sess["v"].tolist(),
        "shown": set(sess["shown"]), "n": sess["n"],
        "like_ids": list(sess["like_ids"]), "dislike_ids": list(sess["dislike_ids"]),
        "served": sess["served"], "last_cats": list(sess["last_cats"]),
        "legacy_shown": set(legacy["shown"]), "legacy_m": legacy["m"].tolist(),
        "exposures": exposures, "prefs": prefs,
    }


def test_지도는_아무것도_바꾸지_않는다(client, user):
    """지도 API 를 아무리 두들겨도 **모델도 노출 로그도 그대로**여야 한다.

    카드를 '보여줬다'(`shown`)고 표시하면 그 카드는 추천에서 사라지고, 노출 로그가
    쌓이면 정책별 like 율이 오염된다. 둘 다 화면 하나를 열었다는 이유로 일어나면 안 된다.
    """
    before = _snapshot(user)
    weights = ",".join(["0.5"] * content.N_COMPONENTS)
    client.get("/admin/api/map/layout")
    client.get(f"/admin/api/map/user/{user}")
    client.get(f"/admin/api/map/user/{user}?weights={weights}")
    client.get(f"/admin/api/map/user/{user}?upto=3")
    client.get(f"/admin/api/map/user/{user}?upto=0")
    client.get("/admin/map")
    after = _snapshot(user)

    assert after["shown"] == before["shown"], "카드가 '보여준 것' 으로 표시됐다"
    assert after["exposures"] == before["exposures"], "노출 로그가 늘었다"
    assert after["prefs"] == before["prefs"], "선호 기록이 늘었다"
    assert after["m"] == before["m"] and after["v"] == before["v"]
    assert after["learn"] == before["learn"]
    assert after["legacy_shown"] == before["legacy_shown"]
    assert after["legacy_m"] == before["legacy_m"]
    assert after == before


# ────────────────────────────────────────────── ④ `upto=` 재생
def test_upto_전체는_살아_있는_세션과_같다(client, user):
    """학습에 난수가 없으므로 앞에서부터 다시 먹이면 같은 모델이 나온다."""
    live = client.get(f"/admin/api/map/user/{user}").json()
    full = client.get(f"/admin/api/map/user/{user}?upto={live['prefs']}").json()
    assert live["prefs"] == live["swipes"] > 0
    assert [w["w"] for w in full["weights"]] == [w["w"] for w in live["weights"]]
    assert [w["sd"] for w in full["weights"]] == [w["sd"] for w in live["weights"]]
    assert full["like_ids"] == live["like_ids"]
    assert full["dislike_ids"] == live["dislike_ids"]
    assert full["scores"] == live["scores"]
    assert full["swipes"] == live["swipes"]


def test_upto_가_모델을_되감는다(client, user):
    """중간 시점은 스와이프 수도 좋아요 수도 그만큼이어야 한다."""
    d = client.get(f"/admin/api/map/user/{user}").json()
    half = client.get(f"/admin/api/map/user/{user}?upto={d['prefs'] // 2}").json()
    assert half["swipes"] == d["prefs"] // 2
    assert len(half["like_ids"]) + len(half["dislike_ids"]) <= half["swipes"]
    assert len(half["like_ids"]) <= len(d["like_ids"])
    assert half["scores"] != d["scores"]
    assert half["prefs"] == d["prefs"]                 # 전체 길이는 그대로 알려 준다
    # 같은 질문에 같은 답 (재생은 결정적이다)
    again = client.get(f"/admin/api/map/user/{user}?upto={d['prefs'] // 2}").json()
    assert again["scores"] == half["scores"]


@pytest.mark.parametrize("at", [0, 1, 8, None])
def test_선택_가게의_실제_성분_합이_총점과_같다(client, user, at):
    """콜드/학습/실험 모두 실제 채점식 분해여야 전후 변화 설명이 성립한다."""
    before = _snapshot(user)
    query = "?focus=10&weights=2,1,0.5,0.3,0.8,0.4,0.2,-0.7"
    if at is not None:
        query += f"&upto={at}"
    d = client.get(f"/admin/api/map/user/{user}{query}").json()
    assert d["focus"]["idx"] == 10
    assert set(d["focus"]["parts"]) == set(content.COMPONENTS)
    assert sum(d["focus"]["parts"].values()) == pytest.approx(d["scores"][10], abs=0.00006)
    assert _snapshot(user) == before


def test_반응_타임라인은_시점과_재평가를_구분한다(client, layout):
    from app import loader
    uid = client.post("/users", json={"name": "반응 타임라인"}).json()["user_id"]
    ids = layout["ids"][:12]
    for n in range(12):
        # 첫 가게를 12회에 재평가. 과거 상태에서만 원래 반응이 유효하다.
        sid = ids[0] if n == 11 else ids[n]
        client.post("/preferences", json={"user_id": uid, "card_id": loader.card_id_by_store[sid],
                                          "prefer": "like" if n == 0 else "dislike"})
    before = _snapshot(uid)
    live = client.get(f"/admin/api/map/user/{uid}").json()
    past = client.get(f"/admin/api/map/user/{uid}?upto=11").json()
    assert len(live["feedback"]) == 12
    assert live["feedback"][0]["weight"] == 0
    assert live["feedback"][-1] == {"n": 12, "idx": 0, "like": False, "weight": 1}
    assert len(past["feedback"]) == 11
    assert past["feedback"][0]["weight"] == pytest.approx(0.5)  # 10회 뒤 반감
    assert sum(live["focus"]["parts"].values()) == pytest.approx(live["scores"][live["focus"]["idx"]], abs=0.00006)
    assert _snapshot(uid) == before


def test_지도_비교는_지난_세션과_잘못된_인덱스를_거절한다(client, user, layout):
    assert client.get(f"/admin/api/map/user/{user}?focus={layout['count']}").status_code == 400
    assert client.get(f"/admin/api/map/user/{user}?focus=-1").status_code == 422
    assert client.get(f"/admin/api/map/user/{user}?upto=0&session_id=old").status_code == 409


def test_trace_replays_all_feedback_but_bounds_display_and_never_changes_live_state(client, user, monkeypatch):
    before = _snapshot(user)
    monkeypatch.setattr(admin, 'TRACE_POINTS', 3)
    live = client.get(f'/admin/api/map/user/{user}').json()
    path = f'/admin/api/map/user/{user}/trace?session_id={live["session_id"]}'
    trace = client.get(path).json()
    assert trace['upto'] == live['prefs']
    assert trace['points'][0] == {'n': 0, 'idx': None}
    assert trace['points'][-1] == {'n': live['prefs'], 'idx': live['anchor']}
    assert len(trace['points']) <= 3
    calls = []
    original = taste.record
    def record(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(taste, 'record', record)
    assert client.get(path).json() == trace
    assert calls == []  # repeated inspection does not replay the entire history again.
    historical = client.get(path+'&upto=7').json()
    at7 = client.get(f'/admin/api/map/user/{user}?upto=7').json()
    assert historical['points'][-1] == {'n': 7, 'idx': at7['anchor']}
    assert client.get(f'/admin/api/map/user/{user}/trace?session_id=old').status_code == 409
    assert client.get('/admin/api/map/user/missing/trace').status_code == 404
    assert _snapshot(user) == before


def test_trace_anchor_uses_high_dimensional_personal_set_and_not_popularity():
    import numpy as np
    from conftest import make_artifacts
    art = make_artifacts()
    z = np.zeros((content.N_COMPONENTS, len(art.ids)))
    z[content.IDX['cf'], [1,2,3]] = 1
    z[content.IDX['pop'], 0] = 1000
    art.sim_menu[np.ix_([1,2,3],[1,2,3])] = [[1,.9,.1],[.9,1,.8],[.1,.8,1]]
    st = {'cold': False, 'w': np.ones(content.N_COMPONENTS), 'z': z}
    assert admin._taste_anchor(art, st, {'menu': 1}) == 2
    st['cold'] = True
    assert admin._taste_anchor(art, st, {'menu': 1}) is None


# ────────────────────────────────────────────── ⑤ 콜드스타트
def test_좋아요가_없으면_콜드스타트_점수다(client, cold_user, layout):
    """좋아요가 0이면 콘텐츠 성분이 전부 0이라 `인기 + 0.5·평판` 이 점수다."""
    d = client.get(f"/admin/api/map/user/{cold_user}").json()
    assert d["cold"] is True and d["like_ids"] == [] and d["dislike_ids"]
    assert len(d["scores"]) == layout["count"]
    assert d["top"] and all(c["links"] == [] for c in d["top"])   # 닮을 like 가 없다
    assert all("인기" in c["reason"] or "리뷰" in c["reason"] for c in d["top"])


def test_스와이프가_없어도_upto_0_은_그린다(client, user):
    """스크러버를 맨 왼쪽으로 끌면 '아직 아무것도 모르는' 모델이다."""
    d = client.get(f"/admin/api/map/user/{user}?upto=0").json()
    assert d["cold"] is True and d["swipes"] == 0
    assert d["like_ids"] == [] and d["dislike_ids"] == []
    assert d["prefs"] > 0                              # 전체 기록은 그대로 있다


# ────────────────────────────────────────────── ⑥ 화면
def test_map_이_화면을_준다(client, html):
    for path in ("/admin/map", "/admin/map/"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert r.text == html


def test_화면의_뼈대가_있다(html):
    for anchor in ('id="cv"',        # 캔버스
                   'id="users"',     # 사용자 목록
                   'id="legend"',    # 업종 범례 / 점수 램프
                   'id="edges"',     # 이웃 선 토글
                   'id="scrub"',     # 이력 스크러버
                   'id="panel-body"',
                   '<title>'):
        assert anchor in html, anchor


def test_외부_자원을_쓰지_않는다(html):
    """CDN·웹폰트가 없어야 한다 — 망이 막힌 서버에서도 떠야 한다(admin.html 과 같다)."""
    assert "http://" not in html and "https://" not in html
    assert "@import" not in html and "@font-face" not in html
    assert "<script src" not in html and "<link" not in html
    for attr, url in re.findall(r'\b(src|href)="([^"]*)"', html):
        if "${" in url:
            continue
        assert url.startswith(("/", "#")), f"{attr}={url}"


def test_화면이_부르는_경로가_전부_있다(client, html, user):
    paths = set(re.findall(r"api\('([^']+)'", html))
    assert "/admin/api/map/layout" in paths
    for p in sorted(paths):
        # `/…/user/' + id` 처럼 이어 붙이는 경로는 끝이 `/` 다 (admin.html 과 같은 규칙).
        got = client.get(p + (user if p.endswith("/") else ""))
        assert got.status_code == 200, f"{p} -> {got.status_code}"


def test_성분_이름을_화면이_모른다(html):
    """지도도 성분을 세지 않는다 — 슬라이더·툴팁·연결선을 전부 API 이름으로 그린다."""
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    for key in content.COMPONENTS:
        for lit in (f"'{key}'", f'"{key}"'):
            assert lit not in script, f"성분 이름 {lit} 을 화면이 하드코딩했다"


def test_스와이프만_쓰기다(html):
    """화면에서 상태를 바꾸는 곳은 `POST /preferences` 하나뿐이어야 한다."""
    posts = re.findall(r"fetch\('([^']+)',\s*\{\s*\n?\s*method:\s*'POST'", html)
    assert posts == ["/preferences"], posts
    assert "모델을 바꾸지 않습니다" in html            # 슬라이더에 붙은 경고
