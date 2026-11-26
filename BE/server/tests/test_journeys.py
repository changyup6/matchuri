# -*- coding: utf-8 -*-
"""길찾기와 설문 — 추천의 결과를 재는 자리.

길찾기는 **방문 의사**이지 방문 확인이 아니다. 방문 여부는 3시간 뒤 설문에서 직접
묻는다. 여기서 잠그는 것은 두 가지다.

1. **시간 규칙** — 3시간 전에는 묻지 않고, 일주일이 지나면 버리고, 같은 길찾기에
   두 번 답하지 않는다. 표본이 부풀거나 기억 없는 응답이 섞이면 집계를 믿을 수 없다.
2. **추천과의 경계** — `test_길찾기와_설문은_추천을_하나도_바꾸지_않는다` 가 호출 전후를
   통째로 비교한다. 찜(`test_bookmarks.py`)과 같은 방식이고 같은 이유다: 여기서 모델이
   움직이면 팀-드래프트 인터리빙의 like 율이 오염돼 정책 비교가 무의미해진다.
3. **내 기록(`GET /users/{id}/surveys`)** — 마이페이지가 읽는 목록이다. 가게 화면의
   후기와 **다른 것**이라, 안 갔다고 답한 것도 나오고 방문 근거는 나오지 않는다.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import journeys, state
from recsys import policy


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture
def uid(client):
    return client.post("/users", json={"name": "설문"}).json()["user_id"]


@pytest.fixture
def cards(client):
    """실데이터 카드 몇 장(개인화 없는 인기순이라 세션을 만들지 않는다)."""
    return [c["card_id"] for c in client.get("/cards?limit=12").json()]


def _ago(**kw) -> str:
    """지금으로부터 그만큼 **전**의 ISO 시각. `state.now()` 와 같은 모양(aware)이다."""
    return (datetime.now().astimezone() - timedelta(**kw)).isoformat(timespec="seconds")


#: `visited=true` 설문의 필수 항목. 이제 만족도와 후기 세 줄이 둘 다 있어야 한다
#: (`app/model.py: SurveyIn`) — 없으면 422 다.
REVIEW = {"taste": "맛있었다", "price": "적당했다", "mood": "조용했다"}


def visited(card_id: int, satisfaction: str = "good", **extra) -> dict:
    """방문 설문 본문 하나. 필수 항목을 한 곳에서만 채운다."""
    return {"card_id": card_id, "visited": True, "satisfaction": satisfaction,
            "review": dict(REVIEW), **extra}


def _pending_ids(client, uid: str) -> list[int]:
    r = client.get(f"/users/{uid}/surveys/pending")
    assert r.status_code == 200
    return [row["card_id"] for row in r.json()]


# ────────────────────────────────────────────────── 길찾기 기록
def test_길찾기를_기록하면_설문_예정_시각을_알려준다(client, uid, cards):
    r = client.post(f"/users/{uid}/directions",
                    json={"card_id": cards[0], "map_app": "kakao", "mode": "transit"})
    assert r.status_code == 200
    body = r.json()
    assert body["card_id"] == cards[0] and body["count"] == 1
    gap = datetime.fromisoformat(body["survey_at"]) - datetime.fromisoformat(body["at"])
    assert gap == journeys.SURVEY_AFTER


def test_없는_카드에_길찾기하면_404(client, uid):
    r = client.post(f"/users/{uid}/directions",
                    json={"card_id": 99_999_999, "map_app": "naver", "mode": "walk"})
    assert r.status_code == 404


@pytest.mark.parametrize("body", [
    {"card_id": 1, "map_app": "tmap", "mode": "walk"},
    {"card_id": 1, "map_app": "kakao", "mode": "bike"},
])
def test_모르는_지도앱이나_이동수단은_422(client, uid, body):
    assert client.post(f"/users/{uid}/directions", json=body).status_code == 422


# ────────────────────────────────────────────────── 시간 규칙
def test_기본은_시간으로_거르지_않는다(client, uid, cards):
    """언제 물을지는 앱이 정한다. 서버가 막으면 개발 중에 흐름을 확인할 수 없다."""
    client.post(f"/users/{uid}/directions",
                json={"card_id": cards[0], "map_app": "kakao", "mode": "car"})
    assert _pending_ids(client, uid) == [cards[0]], "방금 누른 것도 기본으로는 나와야 한다"
    rows = client.get(f"/users/{uid}/surveys/pending").json()
    assert rows[0]["hours_ago"] == 0, "앱은 이 값을 보고 배너 시점을 판단한다"


def test_after_hours_로_서버에서도_거를_수_있다(client, uid, cards):
    """앱이 직접 거르지 않고 서버에 맡기고 싶을 때 쓴다."""
    client.post(f"/users/{uid}/directions",
                json={"card_id": cards[0], "map_app": "kakao", "mode": "car"})
    got = client.get(f"/users/{uid}/surveys/pending?after_hours=3").json()
    assert got == [], "3시간이 안 지났으므로 걸러져야 한다"

    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    got = client.get(f"/users/{uid}/surveys/pending?after_hours=3").json()
    assert [r["card_id"] for r in got] == [cards[0]]


def test_after_hours_는_범위를_지킨다(client, uid):
    assert client.get(f"/users/{uid}/surveys/pending?after_hours=-1").status_code == 422
    assert client.get(f"/users/{uid}/surveys/pending?after_hours=999").status_code == 422


def test_세시간_지나면_설문_대상이_된다(client, uid, cards):
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    rows = client.get(f"/users/{uid}/surveys/pending").json()
    assert [r["card_id"] for r in rows] == [cards[0]]
    assert rows[0]["hours_ago"] == 4
    assert rows[0]["store_id"] and rows[0]["name"]


def test_after_hours_경계값(client, uid, cards):
    """서버 필터를 쓸 때의 경계. 기본(0)에서는 둘 다 나온다."""
    journeys.apply_direction(uid, cards[0], _ago(hours=2, minutes=59))
    assert client.get(f"/users/{uid}/surveys/pending?after_hours=3").json() == []
    assert _pending_ids(client, uid) == [cards[0]], "기본은 거르지 않는다"

    journeys.apply_direction(uid, cards[0], _ago(hours=3, minutes=1))
    got = client.get(f"/users/{uid}/surveys/pending?after_hours=3").json()
    assert [r["card_id"] for r in got] == [cards[0]]


def test_일주일_넘으면_묻지_않는다(client, uid, cards):
    journeys.apply_direction(uid, cards[0], _ago(days=8))
    assert _pending_ids(client, uid) == []


def test_후보는_최근순이고_상한이_있다(client, uid, cards):
    assert len(cards) > journeys.MAX_PENDING, "실데이터 카드가 상한보다 많아야 잴 수 있다"
    for i, cid in enumerate(cards):
        journeys.apply_direction(uid, cid, _ago(hours=4, minutes=i))
    got = _pending_ids(client, uid)
    assert len(got) == journeys.MAX_PENDING
    assert got == cards[:journeys.MAX_PENDING]


def test_망가진_시각은_조용히_버린다(client, uid, cards):
    journeys.apply_direction(uid, cards[0], "이건 날짜가 아니다")
    assert _pending_ids(client, uid) == []


# ────────────────────────────────────────────────── 설문 응답
def test_답하면_목록에서_빠진다(client, uid, cards):
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    r = client.post(f"/users/{uid}/surveys",
                    json=visited(cards[0]))
    assert r.status_code == 200
    assert r.json()["recorded"] is True and r.json()["remaining"] == 0
    assert _pending_ids(client, uid) == []


def test_같은_길찾기에_두_번_답하면_무시한다(client, uid, cards):
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    payload = visited(cards[0])
    assert client.post(f"/users/{uid}/surveys", json=payload).json()["recorded"] is True
    again = client.post(f"/users/{uid}/surveys", json=payload)
    assert again.status_code == 200
    assert again.json()["recorded"] is False, "두 번 세면 만족도 표본이 부푼다"


def test_답한_뒤_다시_길찾기하면_또_묻는다(client, uid, cards):
    """어제 갔고 오늘 또 갔으면 오늘 방문을 물어야 한다(찜과 반대 판단).

    시각을 직접 심는다 — 판정은 '길찾기가 답변보다 나중인가' 이므로 둘의 **순서**가
    요점이다. 이틀 전에 길을 찾고 40시간 전에 답했고, 4시간 전에 또 길을 찾은 상황.
    """
    journeys.apply_direction(uid, cards[0], _ago(days=2))
    journeys.apply_survey(uid, cards[0], _ago(hours=40))
    assert _pending_ids(client, uid) == []
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    assert _pending_ids(client, uid) == [cards[0]]


def test_길찾기_없이도_답할_수_있다(client, uid, cards):
    """앱이 방문 판단을 스스로 하고 설문만 보내는 것이 기본 경로다(2026-09-16).

    서버가 길찾기 로그를 갖고 있어야 할 이유는 없었다 — 정책 성적의 앵커로 쓰이던
    것을 노출 로그로 옮겼다(`recsys/policy.py: survey_stats`).
    """
    r = client.post(f"/users/{uid}/surveys", json=visited(cards[0]))
    assert r.status_code == 200 and r.json()["recorded"] is True
    row = [x for x in state.read_surveys() if x.get("user_id") == uid][-1]
    assert row["source"] == "app", "배너를 거치지 않은 응답은 따로 표시돼야 한다"
    assert row["hours_after"] is None, "기준이 될 길찾기가 없다"


def test_길찾기_없이_두_번_답하면_무시한다(client, uid, cards):
    """문을 다시 열어 줄 근거(새 길찾기)가 없으면 한 번만 받는다 — 표본이 부푼다."""
    assert client.post(f"/users/{uid}/surveys",
                       json=visited(cards[0])).json()["recorded"] is True
    assert client.post(f"/users/{uid}/surveys",
                       json=visited(cards[0])).json()["recorded"] is False


def test_없는_카드에는_답할_수_없다(client, uid):
    r = client.post(f"/users/{uid}/surveys", json=visited(10 ** 9))
    assert r.status_code == 404


def test_방문하지_않았다도_기록된다(client, uid, cards):
    """이탈 원인이 추천 실패의 단서다 — `visited=false` 를 거절하지 않는다."""
    journeys.apply_direction(uid, cards[0], _ago(hours=5))
    r = client.post(f"/users/{uid}/surveys",
                    json={"card_id": cards[0], "visited": False,
                          "not_visited_reason": "far"})
    assert r.status_code == 200 and r.json()["recorded"] is True
    rows = [j for j in state.read_surveys() if j.get("user_id") == uid]
    assert rows[-1]["visited"] is False
    assert rows[-1]["answers"]["not_visited_reason"] == "far"


def test_경과_시간이_기록된다(client, uid, cards):
    """길찾기 후 몇 시간 뒤에 답했나. 거절하지 않고 기록해 집계가 걸러낼 수 있게 한다."""
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    client.post(f"/users/{uid}/surveys",
                json=visited(cards[0]))
    row = [j for j in state.read_surveys() if j.get("user_id") == uid][-1]
    assert row["hours_after"] == pytest.approx(4.0, abs=0.2)


def test_이른_응답도_받지만_집계가_따로_센다(client, uid):
    """3시간 전 응답은 배너를 통하지 않은 것이다 — 버리지 않고 의심만 표시한다."""
    served = [c["card_id"] for c in client.get(f"/cards?user_id={uid}&limit=5").json()]
    client.post(f"/users/{uid}/directions",
                json={"card_id": served[0], "map_app": "kakao", "mode": "walk"})
    r = client.post(f"/users/{uid}/surveys",           # 방금 눌렀는데 바로 답한다
                    json=visited(served[0]))
    assert r.json()["recorded"] is True, "경계에 답한 정당한 응답을 잃지 않아야 한다"
    row = [j for j in state.read_surveys() if j.get("user_id") == uid][-1]
    assert row["hours_after"] < 3.0
    s = client.get("/admin/api/surveys").json()
    assert sum(b["answered_early"] for b in s["by_policy"]) >= 1
    assert any(b["hours_after_avg"] is not None for b in s["by_policy"])


def test_세부_점수는_1에서_3_밖이면_422(client, uid, cards):
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    r = client.post(f"/users/{uid}/surveys",
                    json=visited(cards[0], satisfaction="nope"))
    assert r.status_code == 422


# ────────────────────────────────────────────────── 영속화
def test_기록에_카드와_가게_id_가_모두_남는다(client, uid, cards):
    """`card_id` 는 데이터가 갱신되면 다시 매겨진다 — 조인은 card_id, 보관은 store_id."""
    client.post(f"/users/{uid}/directions",
                json={"card_id": cards[0], "map_app": "google", "mode": "walk"})
    rows = [j for j in state.read_directions() if j.get("user_id") == uid]
    assert rows[-1]["card_id"] == cards[0]
    assert rows[-1]["store_id"], "store_id 가 없으면 데이터 갱신 후 과거 기록이 어긋난다"


def test_설문에_후보_출처가_남는다(client, uid, cards):
    """지금은 길찾기뿐이지만 나중에 정지 감지가 붙는다. 소급이 안 되는 정보라 지금 남긴다."""
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    client.post(f"/users/{uid}/surveys",
                json=visited(cards[0]))
    row = [j for j in state.read_surveys() if j.get("user_id") == uid][-1]
    assert row["source"] == "direction"

    s = client.get("/admin/api/surveys").json()
    assert s["by_source"].get("direction", 0) >= 1


def test_복원은_설문까지_읽는다(client, uid, cards):
    """`surveys.jsonl` 을 빠뜨리면 재시작 뒤 이미 답한 설문을 또 묻는다."""
    journeys.apply_direction(uid, cards[0], _ago(hours=4))
    client.post(f"/users/{uid}/surveys",
                json=visited(cards[0]))
    journeys._directions.clear()
    journeys._answered.clear()
    n = journeys.restore()
    assert n["directions"] > 0 and n["surveys"] > 0
    assert _pending_ids(client, uid) == [], "복원 후 답한 설문을 다시 물었다"


# ────────────────────────────────────────────────── 추천과의 경계
def _snapshot(user_id: str) -> dict:
    """살아 있는 두 세션과 기록 파일의 지문. 길찾기·설문은 이걸 하나도 바꾸면 안 된다."""
    sess = policy.session_for(user_id)
    legacy, tst = sess["legacy"], sess["taste"]
    return {
        "legacy_m": legacy["m"].tolist(), "legacy_v": legacy["v"].tolist(),
        "legacy_shown": set(legacy["shown"]), "legacy_n": legacy["n"],
        "legacy_hist": copy.deepcopy(legacy["hist"]),
        "taste_m": tst["m"].tolist(), "taste_v": tst["v"].tolist(),
        "taste_shown": set(tst["shown"]), "taste_n": tst["n"],
        "taste_hist": copy.deepcopy(tst["hist"]),
        "like_ids": list(tst["like_ids"]), "dislike_ids": list(tst["dislike_ids"]),
        "served": tst["served"], "batch": sess["batch"],
        "exposures": (state.EXPOSURES.read_bytes() if state.EXPOSURES.exists() else b""),
        "prefs": (state.PREFS.read_bytes() if state.PREFS.exists() else b""),
    }


def test_길찾기와_설문은_추천을_하나도_바꾸지_않는다(client, uid, cards):
    client.get(f"/cards?user_id={uid}&limit=3")
    client.post("/preferences", json={"user_id": uid, "card_id": cards[0],
                                      "prefer": "like"})
    before = _snapshot(uid)

    for cid in cards[:3]:
        client.post(f"/users/{uid}/directions",
                    json={"card_id": cid, "map_app": "kakao", "mode": "transit"})
    journeys.apply_direction(uid, cards[1], _ago(hours=4))
    client.get(f"/users/{uid}/surveys/pending")
    client.post(f"/users/{uid}/surveys",
                json=visited(cards[1]))
    after = _snapshot(uid)

    assert after["taste_shown"] == before["taste_shown"], "길찾기가 카드를 '봤다' 고 표시했다"
    assert after["legacy_shown"] == before["legacy_shown"]
    assert after["exposures"] == before["exposures"], "길찾기·설문이 노출 로그를 남겼다"
    assert after["prefs"] == before["prefs"], "길찾기·설문이 선호 기록을 남겼다"
    assert after == before


# ────────────────────────────────────────────────── 집계
def _unanswered(stats: dict) -> int:
    """길찾기했지만 아직 답하지 않은 카드 수 — 응답률에서 되짚어 구한다."""
    f, rate = stats["funnel"], stats["response_rate"]
    return f["directed_cards"] - round(f["directed_cards"] * (rate or 0))


def test_설문_집계가_퍼널로_나온다(client, uid):
    """`/admin/api/surveys` — 노출 × 길찾기 × 설문을 카드 단위로 조인한 결과.

    `/cards?user_id=` 로 받은 카드는 노출 기록에 정책 라벨(`legacy`/`taste`)이 붙는다.
    그 카드로 길을 찾고 설문에 답하면 그 정책 버킷에 담겨야 한다.
    """
    served = [c["card_id"] for c in client.get(f"/cards?user_id={uid}&limit=5").json()]
    cid = served[0]
    client.post(f"/users/{uid}/directions",
                json={"card_id": cid, "map_app": "kakao", "mode": "car"})
    journeys.apply_direction(uid, cid, _ago(hours=4))
    r = client.post(f"/users/{uid}/surveys",
                    json=visited(cid))
    assert r.json()["recorded"] is True

    s = client.get("/admin/api/surveys").json()
    assert [b["policy"] for b in s["by_policy"]] == ["legacy", "taste", "trend", "none"]

    f = s["funnel"]
    assert f["directed_cards"] >= 1 and f["surveyed_cards"] >= 1
    assert f["visited"] >= 1 and f["satisfied"] >= 1
    # **설문이 길찾기보다 많을 수 있다** — 앱이 방문 판단을 스스로 하고 설문만 보내는
    # 경로가 있다(`app/journeys.py: answer`). 그 수를 따로 센다.
    assert f["surveyed_without_direction"] ==         f["surveyed_cards"] - (f["directed_cards"] - _unanswered(s))
    # 응답률의 분모는 길찾기다 — 앱 경로 응답을 분자에 넣으면 1 을 넘는다.
    assert 0 < s["response_rate"] <= 1

    labelled = [b for b in s["by_policy"] if b["policy"] in ("legacy", "taste")]
    assert sum(b["satisfaction"]["good"] for b in labelled) >= 1, \
        "개인화로 받은 카드의 설문이 정책 버킷에 안 담겼다"
    hit = next(b for b in labelled if b["surveyed"] >= 1)
    assert 0 <= hit["satisfied_rate"] <= 1
    assert hit["visit_ci95"] and hit["satisfied_ci95"]
    assert hit["users"] >= 1


def test_이탈률이_단계별로_나온다(client, uid):
    """`/admin/api/surveys` 의 `dropoff` — 카드를 확인했는데 방문까지 안 간 비율.

    **'방문하지 않음' 이 두 종류라는 것을 잠근다.** 답을 하고 "안 갔다" 고 말한 것과,
    답을 안 해서 모르는 것은 다른 사실이다. 합치면 이탈이 과대 계상된다 — 설문에
    답하지 않은 사람 대부분은 귀찮아서 안 한 것이고 안 간 것이 아니다.
    """
    served = [c["card_id"] for c in client.get(f"/cards?user_id={uid}&limit=4").json()]
    went, skipped, silent = served[0], served[1], served[2]
    for cid in (went, skipped, silent):
        client.post(f"/users/{uid}/directions",
                    json={"card_id": cid, "map_app": "kakao", "mode": "walk"})
        journeys.apply_direction(uid, cid, _ago(hours=4))
    client.post(f"/users/{uid}/surveys", json=visited(went))
    client.post(f"/users/{uid}/surveys",
                json={"card_id": skipped, "visited": False,
                      "not_visited_reason": "no_time"})
    # `silent` 은 길찾기만 하고 답하지 않는다 — '모름' 이다.

    d = client.get("/admin/api/surveys").json()["dropoff"]
    assert d["visited"] >= 1 and d["not_visited"] >= 1 and d["unanswered"] >= 1
    assert d["answered"] == d["visited"] + d["not_visited"]
    assert d["directed"] == d["answered"] + d["unanswered"]

    r = d["rates"]
    # 분모가 다르므로 두 값이 달라야 한다 — 같아지면 '모름' 이 이탈에 섞인 것이다.
    assert 0 < r["not_visited_of_answered"] < 1
    assert r["unconfirmed_of_directed"] > r["not_visited_of_answered"],         "모르는 것을 '안 갔다' 와 같은 분모로 세고 있다"


def test_아무도_없으면_비율이_null_이다(client):
    """0 으로 주면 "아무도 안 갔다" 로 읽히지만 실제로는 "아직 아무도 안 왔다" 다."""
    from recsys import policy as pol
    assert pol._rate(0, 0) is None
    assert pol._rate(0, 5) == 0.0


def test_방문안함은_이유가_집계된다(client, uid):
    """이탈 원인이 추천 실패의 단서다 — 집계에 그대로 올라와야 한다."""
    served = [c["card_id"] for c in client.get(f"/cards?user_id={uid}&limit=5").json()]
    # 실제 POST 를 먼저 해야 `directions.jsonl` 에 줄이 남는다. `apply_direction` 은
    # 복원 전용(메모리만)이라 그것만 쓰면 집계가 그 카드를 보지 못한다.
    client.post(f"/users/{uid}/directions",
                json={"card_id": served[1], "map_app": "naver", "mode": "walk"})
    journeys.apply_direction(uid, served[1], _ago(hours=6))
    client.post(f"/users/{uid}/surveys",
                json={"card_id": served[1], "visited": False,
                      "not_visited_reason": "changed_mind"})
    s = client.get("/admin/api/surveys").json()
    assert s["not_visited_reasons"].get("changed_mind", 0) >= 1
    assert sum(b["not_visited"] for b in s["by_policy"]) >= 1


def test_집계는_길찾기가_없어도_모양이_같다(client):
    """빈 상태에서도 키가 다 있어야 프론트·대시보드가 분기하지 않는다."""
    s = client.get("/admin/api/surveys").json()
    for key in ("direction_rows", "funnel", "response_rate", "by_policy",
                "not_visited_reasons", "verdict"):
        assert key in s
    for b in s["by_policy"]:
        for key in ("directions", "surveyed", "visited", "not_visited", "visit_rate",
                    "visit_ci95", "satisfaction", "rated", "satisfied_rate",
                    "satisfied_ci95", "detail_avg", "users"):
            assert key in b, f"{b['policy']} 버킷에 {key} 가 없다"


# ────────────────────────────────────────── 내가 답한 설문 목록 (마이페이지)
def _mine(client, uid, **q) -> list[dict]:
    r = client.get(f"/users/{uid}/surveys", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def test_답한_설문이_내_목록에_가게와_함께_온다(client, uid, cards):
    client.post(f"/users/{uid}/surveys", json=visited(cards[0]))
    got = _mine(client, uid)
    assert len(got) == 1
    row = got[0]
    assert row["card_id"] == cards[0]
    assert row["store"]["name"], "가게가 안 붙었다 — 목록을 그릴 수 없다"
    assert row["survey"]["visited"] is True
    assert row["survey"]["satisfaction"] == "good"
    assert row["survey"]["review"] == REVIEW
    assert row["survey"]["at"]


def test_안_갔다고_답한_것도_나온다(client, cards):
    """'내가 답한 설문' 의 목록이지 '내가 쓴 후기' 의 목록이 아니다."""
    # 가게 화면은 이 모듈의 다른 테스트가 남긴 후기와 공유된다. **이름으로 내 글만
    # 골라내야** 하므로 여기서만 쓰는 이름으로 발급한다.
    me = "안간사람"
    uid = client.post("/users", json={"name": me}).json()["user_id"]
    client.post(f"/users/{uid}/surveys", json={
        "card_id": cards[0], "visited": False, "not_visited_reason": "no_time"})
    got = _mine(client, uid)
    assert len(got) == 1
    assert got[0]["survey"]["visited"] is False
    assert got[0]["survey"]["not_visited_reason"] == "no_time"
    assert got[0]["survey"]["review"] is None
    # 가게 화면에는 실리지 않는다 — 안 가 본 가게의 글은 후기가 아니다.
    shown = client.get(f"/cards/{cards[0]}").json()["user_reviews"]
    assert [r for r in shown if r["name"] == me] == []


def test_visited_로_거를_수_있다(client, uid, cards):
    client.post(f"/users/{uid}/surveys", json=visited(cards[0]))
    client.post(f"/users/{uid}/surveys", json={
        "card_id": cards[1], "visited": False, "not_visited_reason": "far"})
    assert len(_mine(client, uid)) == 2
    assert [r["card_id"] for r in _mine(client, uid, visited=True)] == [cards[0]]
    assert [r["card_id"] for r in _mine(client, uid, visited=False)] == [cards[1]]


def test_방문_근거는_안_나온다(client, uid, cards):
    """`arrived`·`closest_m`·`dwell_min` 은 앱이 위치로 계산한 값이다.

    이탈 분석에만 쓰고 사용자에게 되돌려 주지 않는다. 화면에 실으면 '서버가 내
    위치를 알고 있다' 로 읽힌다.
    """
    client.post(f"/users/{uid}/surveys",
                json=visited(cards[0], arrived=True, closest_m=12, dwell_min=48))
    body = _mine(client, uid)[0]["survey"]
    assert set(body) == {"visited", "satisfaction", "review",
                         "not_visited_reason", "images", "at"}
    import json as _json
    blob = _json.dumps(_mine(client, uid))
    for leaked in ("arrived", "closest_m", "dwell_min"):
        assert leaked not in blob, f"{leaked} 가 사용자 응답에 실렸다"


def test_카드_사진과_설문_사진이_겹치지_않는다(client, uid, cards):
    """설문 답을 평평하게 펴면 `images` 가 부딪쳐 한쪽이 다른 쪽을 덮는다."""
    client.post(f"/users/{uid}/surveys", json=visited(cards[0]))
    row = _mine(client, uid)[0]
    assert row["survey"]["images"] == []
    assert row["images"], "가게 사진이 비었다"       # 카드 쪽은 그대로 있어야 한다


def test_최근_순이고_페이징이_된다(client, uid, cards):
    for c in cards[:3]:
        client.post(f"/users/{uid}/surveys", json=visited(c))
    got = _mine(client, uid)
    assert [r["card_id"] for r in got] == cards[:3][::-1], "최근 순이 아니다"
    assert [r["card_id"] for r in _mine(client, uid, limit=1)] == [cards[2]]
    assert [r["card_id"] for r in _mine(client, uid, limit=1, offset=1)] == [cards[1]]


def test_남의_설문은_안_섞인다(client, uid, cards):
    other = client.post("/users", json={"name": "남"}).json()["user_id"]
    client.post(f"/users/{other}/surveys", json=visited(cards[0]))
    client.post(f"/users/{uid}/surveys", json=visited(cards[1]))
    assert [r["card_id"] for r in _mine(client, other)] == [cards[0]]
    assert [r["card_id"] for r in _mine(client, uid)] == [cards[1]]


def test_아무것도_안_답했으면_빈_배열이다(client, uid):
    assert _mine(client, uid) == []


def test_재시작해도_내용까지_남는다(client, uid, cards):
    """`apply_survey` 가 '답했다' 만 되살리면 목록이 내용 없이 비어 버린다."""
    client.post(f"/users/{uid}/surveys", json=visited(cards[0]))
    before = _mine(client, uid)
    assert before

    journeys._answered.clear()                   # 재시작 흉내
    journeys._answers.clear()
    assert _mine(client, uid) == []
    assert journeys.restore()["surveys"] > 0
    assert _mine(client, uid) == before


def test_사람_경로에서도_같은_목록이_온다(client, cards):
    """`/user/{id}/surveys` — 설문은 세션이 아니라 사람에 붙는다."""
    uid = client.post("/user", json={"name": "사람경로설문"}).json()["user_id"]
    client.post(f"/user/{uid}/surveys", json=visited(cards[0]))
    r = client.get(f"/user/{uid}/surveys")
    assert r.status_code == 200, r.text
    assert [row["card_id"] for row in r.json()] == [cards[0]]
