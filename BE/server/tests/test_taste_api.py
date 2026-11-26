# -*- coding: utf-8 -*-
"""취향 전달(`app/taste_api.py`) — 궤적의 수식과 문장 규칙, 그리고 읽기 전용.

여기서 잠그는 것은 셋이다.

  ① **수식.** 궤적은 like 좌표의 EMA(α=0.35)이고 지역은 좌표의 중앙값이다. 둘 다 화면이
     아니라 서버가 정하므로 값이 조용히 달라지면 아무도 모른다 — 6곳짜리 가짜 지형에서
     손으로 계산한 숫자와 비교한다(진짜 산출물 62MB 를 읽지 않는다).
  ② **문장.** `headline` 은 실제 유효 좋아요를 설명한다. 지도상의 위치나 이동량을
     음식 취향으로 단정하지 않는다. 상세 관측 규칙은 `test_taste_headline.py`에서 검증한다.
  ③ **읽기 전용.** 사용자가 자기 취향 화면을 여는 것으로 추천이 소모되거나 노출 로그가
     쌓이면 팀-드래프트 인터리빙의 like 율이 조용히 오염된다 — `tests/test_map.py` 의
     `test_지도는_아무것도_바꾸지_않는다` 와 같은 방식으로 호출 전후를 통째로 비교한다.

마지막 절만 **진짜 산출물**을 쓴다(없으면 skip — `test_map.py` 와 같은 규칙). 가짜
지형으로는 '20개 업종' 이나 'card_id 가 실제로 조회되는가' 를 잴 수 없기 때문이다.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import loader, state, taste_api, taste_hierarchy
from recsys import content, taste
from conftest import IDS, NAMES

HTML_PATH = Path(taste_api.__file__).parent / "static" / "taste.html"

HAS_LAYOUT = (content.SERVING / "layout.json").exists()
needs_layout = pytest.mark.skipif(
    not HAS_LAYOUT,
    reason="data/kakao/serving/layout.json 이 없다 "
           "(python -m analysis.build_serving --only layout)")

#: 6곳짜리 가짜 지형. 좌표를 손으로 고른 이유는 중앙값·EMA 를 눈으로 검산할 수 있어야
#: 하기 때문이다. `양식` 은 **구성원이 없는 업종** — 원을 그리지 않는지 잰다.
#: (업종은 conftest 의 CATS 와 같다: s0·s1 한식, s2·s3 일식, s4 카페, s5 중식)
FAKE_LAYOUT = {
    "version": 1, "count": 6,
    "cats": ["한식", "일식", "카페", "중식", "양식"],
    "ids": list(IDS),
    "name": [NAMES[s] for s in IDS],
    "cat": [0, 0, 1, 1, 2, 3],
    "x": [0.1, 0.3, 0.7, 0.9, 0.1, 0.9],
    "y": [0.1, 0.1, 0.7, 0.7, 0.9, 0.1],
}

#: 가짜 카드 번호. `card_id` 를 응답에 실으므로 매핑이 없으면 전부 null 이 된다.
FAKE_CARDS = {sid: 100 + i for i, sid in enumerate(IDS)}


@pytest.fixture
def client() -> TestClient:
    import main
    return TestClient(main.app)


@pytest.fixture
def lay(monkeypatch, art):
    """가짜 지형으로 갈아끼운다. `_load_layout` 만 바꾸면 캐시·파일을 안 건드린다.

    투표 이웃 수를 3으로 줄인다. 진짜 지형은 2,216곳이라 K=15 가 국소적이지만 여기는
    가게가 6곳뿐이라 K=15 면 **온 지도가 한 표씩** 던져 어느 점을 물어도 같은 답이
    나온다(가장 큰 업종). 규칙을 재려면 이웃이 이웃이어야 한다 — K 를 줄이는 것이
    지형을 키우는 것보다 정직하다(손으로 검산할 수 있는 6곳을 유지한다).
    """
    folded = taste_api._fold(FAKE_LAYOUT)
    monkeypatch.setattr(taste_api, "_load_layout", lambda: folded)
    # Trail tests keep their six-store background; hierarchy membership and
    # detail queries are independently exercised in test_taste_hierarchy.py.
    monkeypatch.setattr(taste_hierarchy, "read_level", lambda detail=1: {
        "level": detail, "nodes": [
            {**region, "key": taste_hierarchy._key("cat2", region["name"]), "card_id": None}
            for region in folded["regions"]]})
    monkeypatch.setattr(taste_api, "REGION_K", 3)
    monkeypatch.setattr(loader, "card_id_by_store", dict(FAKE_CARDS))
    return folded


def _likes(sess: dict, *store_ids: str) -> None:
    """like 를 순서대로 먹인다 — 궤적의 순서는 세션 이력에서 나온다."""
    for sid in store_ids:
        taste.record(sess, sid, True)


def _get(client: TestClient, user_id: str = "u_test") -> dict:
    r = client.get(f"/users/{user_id}/taste")
    assert r.status_code == 200, r.text
    return r.json()


# ────────────────────────────────────────────── ① 지형 (중앙값)
def test_지역이_구성원_좌표의_중앙값이다(lay):
    """한식 = s0(0.1,0.1)·s1(0.3,0.1) -> 중심 (0.2,0.1), r = 중심까지 거리의 중앙값."""
    by = {rg["name"]: rg for rg in lay["regions"]}
    assert [rg["name"] for rg in lay["regions"]] == ["한식", "일식", "카페", "중식"]
    assert "양식" not in by                       # 구성원이 없으면 원이 없다
    assert by["한식"] == {"name": "한식", "x": 0.2, "y": 0.1, "r": 0.1, "count": 2}
    assert by["일식"] == {"name": "일식", "x": 0.8, "y": 0.7, "r": 0.1, "count": 2}
    assert by["카페"] == {"name": "카페", "x": 0.1, "y": 0.9, "r": 0.0, "count": 1}
    assert by["중식"]["count"] == 1


def test_지형_API_는_지역만_준다(client, lay):
    """앱이 한 번만 받아 캐시하는 값이다 — 사용자와 무관해야 한다."""
    d = client.get("/taste/map").json()
    assert d == {"version": taste_api.MAP_VERSION, "regions": lay["regions"]}
    assert client.get("/taste/map").json() == d          # 같은 질문에 같은 답


def test_이름은_이웃_가게의_다수결이다(lay):
    """원이 아니라 **가장 가까운 가게 K곳**이 투표한다 (여기서는 K=3).

    (0.15, 0.1) 의 이웃 셋은 s0·s1(한식) 과 s5(중식) 이라 한식,
    (0.75, 0.65) 의 이웃 셋은 s2·s3(일식) 과 s0(한식) 이라 일식이다.
    """
    assert taste_api._region_at(lay, 0.15, 0.1) == "한식"
    assert taste_api._region_at(lay, 0.75, 0.65) == "일식"
    assert taste_api._region_at(lay, 0.5, 0.5) in ("한식", "일식")   # 빈 땅도 이름을 받는다


def test_작은_업종은_이름을_가져가지_못한다(lay):
    """가게가 한 곳뿐인 업종은 **자기 좌표 위에서도** 이름을 못 건다.

    예전 규칙(가장 가까운 지역 **중심**)에서는 카페 중심이 곧 s4 좌표라 그 근처가 전부
    카페였다. 다수결에서는 이웃 3곳 중 2곳이 카페여야 하는데 카페는 한 곳뿐이라
    1 대 1 대 1 동점이 되고, 동점은 풀에 가게가 많은 업종(한식)이 가져간다.
    실서버에서 8곳짜리 `샤브샤브` 가 곱창·갈비의 이름을 가져가던 것이 이 규칙으로 사라진다.
    """
    assert taste_api._region_at(lay, 0.1, 0.9) == "한식"        # s4(카페) 바로 위인데도
    assert taste_api._region_at(lay, 0.12, 0.45) == "한식"


def test_동점이면_풀에_가게가_많은_업종이_이긴다(lay, monkeypatch):
    """답이 호출마다 달라지면 안 된다 — 동점의 순서를 못박는다.

    K 를 6(=전부)으로 올리면 한식 2 · 일식 2 · 카페 1 · 중식 1 로 동점이고,
    두 업종의 가게 수도 2로 같다. 그러면 `regions` 순서(= 풀에서 큰 업종부터)로 간다.
    """
    monkeypatch.setattr(taste_api, "REGION_K", 6)
    assert taste_api._region_at(lay, 0.5, 0.4) == "한식"
    assert taste_api._region_at(lay, 0.9, 0.9) == "한식"        # 어디를 물어도 같다


def test_이웃이_K_보다_적으면_있는_만큼만_센다(lay, monkeypatch):
    """진짜 K(15)를 6곳짜리 지형에 그대로 걸어도 터지지 않는다 — 있는 만큼 센다."""
    monkeypatch.setattr(taste_api, "REGION_K", 15)
    assert taste_api._region_at(lay, 0.5, 0.5) == "한식"        # 6곳 전부가 투표한다
    assert taste_api._region_at(lay, 0.9, 0.7) == "한식"


# ────────────────────────────────────────────── ② 궤적 (EMA)
def test_궤적은_like_좌표의_EMA_다(client, lay, sess):
    """s0 -> s1 -> s2 를 손으로 계산한다 (α=0.35, 첫 like 가 시작점).

        p1 = (0.1, 0.1)
        p2 = 0.1 + .35·(0.3−0.1) = 0.17            (y 는 그대로 0.1)
        p3 = 0.17 + .35·(0.7−0.17) = 0.3555        y = 0.1 + .35·0.6 = 0.31
    """
    taste.record(sess, "s5", False)               # dislike 는 궤적에 없다
    _likes(sess, "s0", "s1", "s2")

    d = _get(client, "u_test")
    assert d["swipes"] == 4 and d["likes"] == 3
    xs = [(p["x"], p["y"]) for p in d["trail"]]
    assert xs == [(0.1, 0.1), (0.17, 0.1), (0.3555, 0.31)]
    assert [p["n"] for p in d["trail"]] == [2, 3, 4]        # 몇 번째 스와이프였나
    assert [p["store"] for p in d["trail"]] == [NAMES[s] for s in ("s0", "s1", "s2")]
    assert [p["card_id"] for p in d["trail"]] == [100, 101, 102]
    assert [p["region"] for p in d["trail"]] == ["한식", "한식", "한식"]
    assert d["now"]["x"] == 0.3555 and d["now"]["region"] == "한식"
    assert d["map"]["version"] == 3 and d["map"]["level"] == 1
    assert [{key: region[key] for key in ("name", "x", "y", "r", "count")}
            for region in d["map"]["regions"]] == lay["regions"]
    assert d["user_id"] == "u_test" and d["drift"] is None


def test_같은_가게를_다시_좋아하면_점이_다시_찍힌다(client, lay, sess):
    """순서는 `hist` 에서 온다 — `like_ids` 는 재평가하면 마지막 라벨만 남는다."""
    _likes(sess, "s0", "s2", "s0")
    d = _get(client, "u_test")
    assert len(d["trail"]) == 3
    assert [p["store"] for p in d["trail"]] == [NAMES["s0"], NAMES["s2"], NAMES["s0"]]
    assert len(taste.sessions()["u_test"]["like_ids"]) == 2     # 집합은 2곳뿐이다


def test_지형도_밖의_가게는_건너뛴다(client, lay, sess):
    """좌표가 없으면 그릴 자리가 없다 — 0 이나 중심으로 채우면 궤적이 거짓으로 움직인다."""
    _likes(sess, "s0", "s2")
    sess["hist"].append({"n": 99, "like": True, "store_id": "밖에있는가게",
                         "cat": None, "pairs": 0})
    d = _get(client, "u_test")
    assert [p["n"] for p in d["trail"]] == [1, 2]


def test_궤적은_최근_60개까지만_준다(client, lay, sess):
    """폰 화면에 60점이면 이미 촘촘하고 응답도 가벼워야 한다."""
    _likes(sess, *(["s0", "s1"] * 40))            # like 80개
    d = _get(client, "u_test")
    assert d["likes"] == 80 and len(d["trail"]) == taste_api.TRAIL_MAX
    assert d["trail"][-1]["n"] == 80


def test_표시를_60개로_잘라도_이전_좋아요가_궤적에서_사라지지_않는다(client, lay, sess):
    """전체 이력으로 EMA를 구한 뒤 표시만 자른다. 먼저 자르면 첫 점이 튄다."""
    _likes(sess, *(["s0"] * 20), *(["s2"] * 60))
    d = _get(client, "u_test")
    first = d["trail"][0]
    assert len(d["trail"]) == 60 and len(sess["hist"]) == 80
    assert first["n"] == 21
    assert (first["x"], first["y"]) == (0.31, 0.31)
    assert (d["now"]["x"], d["now"]["y"]) == (0.7, 0.7)


def test_앵커는_현재_점에_가장_가까운_좋아요_가게다(client, lay, sess):
    """EMA 중심은 빈 땅에 앉을 수 있다 — 이름 3개가 좌표를 통역한다."""
    _likes(sess, "s2", "s3", "s4")                # 일식 둘 + 카페 하나
    d = _get(client, "u_test")
    near = d["now"]["near"]
    assert len(near) == taste_api.NEAR_K
    assert all(set(a) == {"name", "card_id"} for a in near)
    x, y = d["now"]["x"], d["now"]["y"]
    want = sorted(("s2", "s3", "s4"),
                  key=lambda s: math.dist((x, y), lay["xy"][s]))
    assert [a["name"] for a in near] == [NAMES[s] for s in want]
    assert [a["card_id"] for a in near] == [FAKE_CARDS[s] for s in want]


def test_취소한_가게는_앵커가_되지_않는다(client, lay, sess):
    """`like_ids` 를 쓰므로 dislike 로 다시 답한 가게는 앵커에서 사라진다."""
    _likes(sess, "s2", "s3")
    taste.record(sess, "s3", False)
    d = _get(client, "u_test")
    assert [a["name"] for a in d["now"]["near"]] == [NAMES["s2"]]
    assert len(d["trail"]) == 2                   # 궤적에는 그때의 like 가 남아 있다


# ────────────────────────────────────────────── ③ drift 와 문장
def test_좋아요가_없으면_궤적도_문장도_비어_있다(client, lay, sess):
    taste.record(sess, "s0", False)
    d = _get(client, "u_test")
    assert d["trail"] == [] and d["now"] is None and d["drift"] is None
    assert d["headline"] == "조금 더 넘겨 보면 취향 지도가 그려져요"


def test_좋아요_2개까지는_더_넘겨_보라고_한다(client, lay, sess):
    _likes(sess, "s0", "s1")
    d = _get(client, "u_test")
    assert len(d["trail"]) == 2 and d["now"] is not None
    assert d["headline"] == "조금 더 넘겨 보면 취향 지도가 그려져요"


def test_같은_가게_좋아요_3번으로_취향을_확정하지_않는다(client, lay, sess):
    """세 이벤트는 궤적에 남지만 유효 관측 가게는 한 곳이다."""
    _likes(sess, "s0", "s0", "s0")
    d = _get(client, "u_test")
    assert d["drift"] is None
    assert d["headline"] == "조금 더 넘겨 보면 취향 지도가 그려져요"


def test_같은_가게를_반복해도_이동량만_계산하고_취향은_단정하지_않는다(client, lay, sess):
    """같은 가게만 11번 좋아하면 drift 거리가 0 이다(< 0.05)."""
    _likes(sess, *(["s0"] * 11))
    d = _get(client, "u_test")
    assert d["drift"] == {"from_region": "한식", "to_region": "한식",
                          "distance": 0.0, "window": taste_api.DRIFT_WINDOW}
    assert d["headline"] == "조금 더 넘겨 보면 취향 지도가 그려져요"


def test_지도_지역이_바뀌어도_취향이_옮겨갔다고_말하지_않는다(client, lay, sess):
    """한식(0.1,0.1) 에 앉아 있다가 일식(0.7,0.7) 을 5장 좋아한 사람."""
    _likes(sess, *(["s0"] * 6), *(["s2"] * 5))
    d = _get(client, "u_test")
    assert d["drift"]["from_region"] == "한식" and d["drift"]["to_region"] == "일식"
    assert d["drift"]["distance"] > 0
    assert d["headline"] == "조금 더 넘겨 보면 취향 지도가 그려져요"
    # 창은 정확히 10 like 전이다 — 11번째 점과 첫 점 사이의 거리.
    a, b = d["trail"][-11], d["trail"][-1]
    assert d["drift"]["distance"] == round(math.dist((a["x"], a["y"]),
                                                     (b["x"], b["y"])), 4)


def test_같은_지역_안의_이동도_문장의_취향_근거가_아니다(client, lay, sess):
    """s0(0.1,0.1) -> s1(0.3,0.1) 은 둘 다 한식이다. '한식에서 한식으로' 라고 하면 안 된다."""
    _likes(sess, *(["s0"] * 6), *(["s1"] * 5))
    d = _get(client, "u_test")
    assert d["drift"]["from_region"] == d["drift"]["to_region"] == "한식"
    assert d["drift"]["distance"] > 0
    assert d["headline"] == "조금 더 넘겨 보면 취향 지도가 그려져요"


def test_drift_는_11번째_like_에서_생긴다(client, lay, sess):
    """경계 하나를 직접 잰다 — 10개까지는 null, 11개부터 값이 있다."""
    for i in range(1, 12):
        _likes(sess, "s0")
        d = _get(client, "u_test")
        assert (d["drift"] is None) == (i < taste_api.DRIFT_WINDOW + 1), i


# ────────────────────────────────────────────── ④ 오류 (404 · 503)
def test_세션이_없으면_404(client, lay):
    assert client.get("/users/nobody/taste").status_code == 404


def test_산출물이_없으면_503(client, monkeypatch, tmp_path):
    """지형이 없으면 그릴 수 없다 — 빈 지도를 그리는 것보다 503 이 낫다."""
    monkeypatch.setattr(taste_api, "LAYOUT_PATH", tmp_path / "없는layout.json")
    for path in ("/taste/map", "/users/u_test/taste"):
        r = client.get(path)
        assert r.status_code == 503, path
        assert "build_serving" in r.json()["detail"]


# ────────────────────────────────────────────── ⑤ 읽기 전용
def _snapshot(user_id: str) -> dict:
    """살아 있는 세션과 기록 파일의 지문. 취향 API 는 이걸 하나도 바꾸면 안 된다."""
    sess = taste.sessions()[user_id]
    return {
        "m": sess["m"].tolist(), "v": sess["v"].tolist(),
        "shown": set(sess["shown"]), "n": sess["n"], "likes": sess["likes"],
        "served": sess["served"], "hist": copy.deepcopy(sess["hist"]),
        "like_ids": list(sess["like_ids"]), "dislike_ids": list(sess["dislike_ids"]),
        "last_cats": list(sess["last_cats"]), "last_brands": list(sess["last_brands"]),
        "exposures": (state.EXPOSURES.read_bytes() if state.EXPOSURES.exists() else b""),
        "prefs": (state.PREFS.read_bytes() if state.PREFS.exists() else b""),
    }


def test_취향_API_는_아무것도_바꾸지_않는다(client, lay, sess):
    """사용자가 자기 취향 화면을 몇 번 열어도 추천·학습·노출 로그가 그대로여야 한다.

    카드를 '보여줬다'(`shown`) 고 표시하면 그 카드는 추천에서 사라지고, 노출 로그가
    쌓이면 정책별 like 율이 오염된다. 화면 하나를 열었다는 이유로 일어나면 안 된다.
    """
    _likes(sess, "s0", "s2", "s3")
    before = _snapshot("u_test")
    for _ in range(3):
        client.get("/users/u_test/taste")
        client.get("/taste/map")
        client.get("/taste/example?user_id=u_test")
    after = _snapshot("u_test")
    assert after["shown"] == before["shown"], "카드가 '보여준 것' 으로 표시됐다"
    assert after["exposures"] == before["exposures"], "노출 로그가 늘었다"
    assert after["prefs"] == before["prefs"], "선호 기록이 늘었다"
    assert after == before


# ────────────────────────────────────────────── ⑥ 예시 화면
@pytest.fixture(scope="module")
def html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def test_example_이_화면을_준다(client, html):
    r = client.get("/taste/example?user_id=u_test")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text == html


def test_화면의_뼈대가_있다(html):
    for anchor in ('id="headline"',   # 맨 위 한 줄
                   'id="map"',        # 지형 + 궤적 (inline SVG)
                   'id="near"',       # 앵커 3곳
                   'id="scrub"',      # 재생 슬라이더
                   'id="step"',       # 슬라이더가 가리키는 n
                   '<title>'):
        assert anchor in html, anchor
    assert "demo=1" in html or "'demo'" in html          # 오프라인 미리보기
    assert "prefers-color-scheme" in html                # 다크·라이트 둘 다


def test_외부_자원을_쓰지_않는다(html):
    """CDN·웹폰트가 없어야 한다 — 망이 막힌 서버에서도 떠야 한다(map.html 과 같다)."""
    assert "http://" not in html and "https://" not in html
    assert "@import" not in html and "@font-face" not in html
    assert "<script src" not in html and "<link" not in html
    for attr, url in re.findall(r'\b(src|href)="([^"]*)"', html):
        if "${" in url:
            continue
        assert url.startswith(("/", "#")), f"{attr}={url}"


def test_화면은_쓰기를_하지_않는다(html):
    """취향 화면은 읽기만 한다 — POST 가 하나도 없어야 한다."""
    assert "method:" not in html and "method =" not in html
    assert "POST" not in html


def test_폰_화면_기준이다(html):
    """360×720 프레임이 기준이고, 폰에서 전체화면으로 열어도 같은 배치여야 한다."""
    assert "360px" in html and "720px" in html
    assert "@media" in html


# ────────────────────────────────────────────── ⑦ 진짜 산출물 (없으면 skip)
@needs_layout
def test_실제_지형은_업종_20개다(client):
    d = client.get("/taste/map").json()
    assert d["version"] == 1 and len(d["regions"]) == 19
    assert sum(rg["count"] for rg in d["regions"]) == 1629
    for rg in d["regions"]:
        assert 0.0 <= rg["x"] <= 1.0 and 0.0 <= rg["y"] <= 1.0
        assert rg["count"] > 0
        # 가게가 한 곳뿐인 업종은 반지름이 0 이다(기사식당·푸드코트) — 원이 아니라 점이다.
        assert (rg["r"] == 0.0) == (rg["count"] == 1)
        assert 0.0 <= rg["r"] < 1.0
    assert d["regions"][0]["name"] == "한식"          # 후보가 가장 많은 업종이 먼저다


@needs_layout
def test_실제_지형에서_작은_업종은_이름을_가져가지_않는다():
    """이 규칙이 존재하는 이유를 진짜 1,629곳에서 잰다 (K=15, 조회 0.04ms).

    후보 전부의 좌표에 이름을 붙여 보면 **작은 업종은 한 번도 못 이긴다** — 예전의
    '가장 가까운 중심' 규칙에서 8곳짜리 샤브샤브가 곱창·갈비의 땅 이름을 가져가던
    자리다. 그러면서도 대부분의 가게는 자기 업종의 이름을 받는다(땅이 실제로 그렇게
    나뉘어 있다는 뜻이고, 다수결이 지형을 뭉개지 않았다는 뜻이다).

    경계는 데이터에서 온다. 후보 풀이 2,216 -> 1,629 로 줄면서 패스트푸드가
    20곳에서 16곳이 됐고, 16곳으로도 자기 땅 17칸을 지킨다. **못 이기는 쪽은
    그대로다** — 기사식당·도시락·미분류·뷔페·샤브샤브·패밀리레스토랑·퓨전요리
    일곱 업종은 한 칸도 못 가져간다. 규칙이 사는 이유가 바뀌지 않았다.
    """
    lay = taste_api._load_layout()
    count = {rg["name"]: rg["count"] for rg in lay["regions"]}
    labels = [taste_api._region_at(lay, float(x), float(y)) for x, y in lay["pts"]]
    assert len(labels) == 1629 and all(nm in count for nm in labels)
    assert [nm for nm in labels if count[nm] < 16] == []
    agree = sum(1 for got, own in zip(labels, lay["cat_name"]) if got == own)
    assert agree / len(labels) > 0.75          # 실측 84.8%


@needs_layout
def test_실제_사용자의_취향_응답_계약(client):
    """진짜 데이터로 한 번 — 필드가 다 있고, card_id 가 실제로 조회되는가."""
    uid = client.post("/users", json={"name": "취향전달"}).json()["user_id"]
    for _ in range(2):
        for i, c in enumerate(client.get(f"/cards?user_id={uid}&limit=8").json()):
            client.post("/preferences",
                        json={"user_id": uid, "card_id": c["card_id"],
                              "prefer": "dislike" if i == 0 else "like"})

    d = client.get(f"/users/{uid}/taste").json()
    assert set(d) == {"user_id", "name", "swipes", "likes", "map", "trail",
                      "now", "drift", "headline"}
    assert d["name"] == "취향전달" and d["swipes"] == 16
    assert len(d["map"]["regions"]) == 19
    assert 0 < len(d["trail"]) <= taste_api.TRAIL_MAX
    names = {rg["name"] for rg in d["map"]["regions"]}
    for p in d["trail"]:
        assert set(p) == {"n", "x", "y", "region", "store", "card_id"}
        assert 0.0 <= p["x"] <= 1.0 and 0.0 <= p["y"] <= 1.0
        assert p["region"] in names and p["store"]
        assert client.get(f"/cards/{p['card_id']}").status_code == 200
    assert d["now"]["region"] in names and d["now"]["near"]
    assert d["drift"] is not None and d["drift"]["window"] == 10
    assert isinstance(d["headline"], str) and d["headline"]
    # 응답이 가벼워야 한다 — 폰이 화면 한 장을 열 때 받는 값이다.
    assert len(client.get(f"/users/{uid}/taste").content) < 30_000
