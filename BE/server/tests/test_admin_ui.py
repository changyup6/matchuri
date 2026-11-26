# -*- coding: utf-8 -*-
"""관리자 화면(`app/static/admin.html`) 과 그 화면이 부르는 JSON 의 계약.

화면은 자바스크립트라 pytest 가 실행하지 못한다. 대신 **깨지면 화면이 빈다** 는 것만
잠근다.

  ① `/admin` 이 그 HTML 을 준다
  ② HTML 은 같은 출처만 참조한다 (CDN·외부 폰트 없음 — 망이 막힌 곳에서도 뜬다)
  ③ 화면이 부르는 경로가 전부 살아 있고, **화면이 읽는 필드**를 준다
  ④ 성분(`content.COMPONENTS`) 이름을 화면이 하드코딩하지 않는다

④ 가 이 파일에서 제일 중요하다. 성분은 늘어난다(사진 등). 화면이 성분 이름을 알면
성분을 더할 때마다 HTML 을 고쳐야 하고, 안 고치면 새 성분이 조용히 안 보인다. 그래서
가중치 목록·성분 기여를 **API 가 준 순서와 이름 그대로** 그리는지, 그리고 두 곳의
성분 집합이 같은지를 여기서 확인한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import admin
from recsys import content

HTML_PATH = Path(admin.__file__).parent / "static" / "admin.html"


@pytest.fixture(scope="module")
def html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def client() -> TestClient:
    import main
    return TestClient(main.app)


@pytest.fixture(scope="module")
def user(client) -> str:
    """스와이프를 섞어 넣은 사용자 하나 — 화면이 그릴 것이 있어야 계약을 잴 수 있다."""
    uid = client.post("/users", json={"name": "관리자화면"}).json()["user_id"]
    cards = client.get(f"/cards?user_id={uid}&limit=10").json()
    for i, c in enumerate(cards[:8]):
        client.post("/preferences", json={"user_id": uid, "card_id": c["card_id"],
                                          "prefer": "like" if i % 2 else "dislike"})
    return uid


# ────────────────────────────────────────────── ① 화면이 서빙되는가
def test_admin_이_화면을_준다(client, html):
    for path in ("/admin", "/admin/"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert r.text == html


def test_화면의_뼈대가_있다(html):
    for anchor in ('id="exp"',        # 정책 실험 패널
                   'id="users"',      # 사용자 목록
                   'id="main"',       # 사용자 상세
                   '<title>'):
        assert anchor in html, anchor


# ────────────────────────────────────────────── ② 같은 출처만 참조하는가
def test_외부_자원을_쓰지_않는다(html):
    """CDN·웹폰트가 없어야 한다. 관리자 화면은 망이 막힌 서버에서도 떠야 한다."""
    assert "http://" not in html and "https://" not in html
    assert "@import" not in html and "@font-face" not in html
    assert "<script src" not in html and "<link" not in html

    # 정적으로 적힌 링크는 전부 상대 경로다 (`${...}` 는 실행 중에 데이터로 채운다).
    for attr, url in re.findall(r'\b(src|href)="([^"]*)"', html):
        if "${" in url:
            continue
        assert url.startswith(("/", "#")), f"{attr}={url}"


def test_외부_폰트를_안_쓴다(html):
    fonts = re.search(r"font-family:([^;]+);", html).group(1)
    assert "url(" not in html
    assert "Malgun Gothic" in fonts and "system-ui" in fonts


# ────────────────────────────────────────────── ③ 화면이 부르는 경로가 사는가
def test_화면이_부르는_경로가_전부_있다(client, html, user):
    """`api('...')` 로 적힌 경로를 HTML 에서 뽑아 직접 부른다.

    화면에 호출을 추가하고 이 테스트를 잊어도, 없는 경로면 여기서 걸린다.
    """
    paths = set(re.findall(r"api\('([^']+)'", html))
    assert paths, "화면이 아무 API 도 부르지 않는다"
    for p in sorted(paths):
        got = client.get(p + (user if p.endswith("/") else ""))
        assert got.status_code == 200, f"{p} -> {got.status_code}"


def test_전체_현황_필드(client):
    s = client.get("/admin/api/stats").json()
    for key in ("cards", "recommendable", "users", "swipes", "like_rate"):
        assert key in s


def test_사용자_목록_필드(client, user):
    us = client.get("/admin/api/users").json()
    row = next(u for u in us if u["user_id"] == user)
    for key in ("user_id", "name", "swipes", "likes", "like_rate", "quality_weight"):
        assert key in row


def test_정책_패널_필드(client, user):
    """정책 실험 패널 — 정책별 노출/응답과 Wilson 구간, 그리고 판정."""
    p = client.get("/admin/api/policies").json()
    for key in ("policy", "configured_policy", "artifacts", "exposures",
                "by_policy", "verdict"):
        assert key in p
    assert p["by_policy"], "정책 행이 없다"
    for row in p["by_policy"]:
        for key in ("policy", "shown", "liked", "disliked", "unrated", "rated",
                    "users", "like_rate", "ci95"):
            assert key in row, f"{row.get('policy')}.{key}"
        assert row["shown"] == row["liked"] + row["disliked"] + row["unrated"]
        if row["ci95"] is not None:                 # 화면이 막대 두 끝으로 쓴다
            lo, hi = row["ci95"]
            assert 0.0 <= lo <= row["like_rate"] <= hi <= 1.0


def test_구_정책_패널_필드(client, user):
    """legacy 패널은 예전 그대로 그린다 — 읽는 필드가 하나라도 빠지면 화면이 빈다."""
    d = client.get(f"/admin/api/users/{user}").json()
    for key in ("name", "swipes", "likes", "like_rate", "seen", "quality_weight",
                "weights", "history", "swiped", "next", "policy", "taste"):
        assert key in d
    for key in ("cats", "price", "base"):
        assert key in d["weights"]
        assert all({"name", "w"} <= set(x) for x in d["weights"][key])
    assert all({"n", "like", "wq"} <= set(h) for h in d["history"])
    assert all({"quality", "personal", "total"} <= set(c) for c in d["next"])
    assert all({"prefer", "n"} <= set(c) for c in d["swiped"])
    for c in d["next"] + d["swiped"]:               # 카드 타일이 읽는 것
        assert {"card_id", "name", "category", "score", "review_count", "image"} <= set(c)


# ────────────────────────────────────────────── ④ 취향 패널 = 데이터가 그린다
def test_취향_패널_필드(client, user):
    t = client.get(f"/admin/api/users/{user}").json()["taste"]
    for key in ("available", "prior", "likes", "dislikes", "swipes",
                "weights", "prototypes", "hist", "next"):
        assert key in t
    assert t["available"] is True                   # 서빙 산출물이 있는 환경
    assert all({"key", "name", "w", "sd"} <= set(w) for w in t["weights"])
    assert all(isinstance(w["name"], str) and w["name"] for w in t["weights"])
    assert all({"n", "like", "pairs"} <= set(h) for h in t["hist"])
    assert len(t["prototypes"]) <= 5
    assert all({"name", "size", "stores"} <= set(p) for p in t["prototypes"])
    assert all(p["size"] == len(p["stores"]) for p in t["prototypes"])
    assert t["next"], "다음 카드 미리보기가 비었다"
    assert all({"total", "parts", "name", "image"} <= set(c) for c in t["next"])


def test_성분_이름은_API_가_준다(client, user, html):
    """화면은 성분을 세지도 이름 붙이지도 않는다 — 늘어나도 HTML 을 안 고친다."""
    t = client.get(f"/admin/api/users/{user}").json()["taste"]
    keys = [w["key"] for w in t["weights"]]
    assert keys == list(content.COMPONENTS)         # API 순서 = 화면 순서

    # 카드마다의 성분 기여도 같은 성분 집합이어야 막대와 범례가 맞는다.
    for c in t["next"]:
        assert set(c["parts"]) == set(keys), c["name"]

    # 화면 어디에도 성분 key 가 문자열로 박혀 있으면 안 된다.
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    for key in content.COMPONENTS:
        for lit in (f"'{key}'", f'"{key}"'):
            assert lit not in script, f"성분 이름 {lit} 을 화면이 하드코딩했다"
