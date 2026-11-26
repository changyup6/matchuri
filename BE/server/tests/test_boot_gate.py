# -*- coding: utf-8 -*-
"""준비 게이트(`app/boot.py`) — 포트는 열려 있고, 준비 전 요청은 503 이다.

이 파일이 지키는 것:

    준비 전   가린 경로 503 + Retry-After          열어 둔 경로(탐침·문서·필터·관측)는 통과
    /readyz   복원 중 503 · 진행률 n/N · 이유 문장    끝나면 200
    OPTIONS   항상 통과 — 프리플라이트를 막으면 브라우저는 이유를 말해 주지 않는다
    start()   스레드면 즉시 돌아오고, 동기면 끝까지 돈다(테스트가 이 모드다)
    실패      phase=failed 가 /readyz 이유에 남는다(동기 모드에서는 예외가 올라온다)

`conftest.py` 가 `MATCHURI_RESTORE_SYNC=1` 을 넣으므로 `import main` 뒤에는 이미 준비
상태다. 여기서는 게이트의 상태를 **직접 놓고** 재고, 끝나면 되돌린다.
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import main
from app import boot


@pytest.fixture
def gate():
    """게이트 상태를 잠깐 바꾸고 끝나면 준비 상태로 되돌린다 — 다른 테스트가 503 을
    받으면 안 된다."""
    saved = (boot._phase, boot._total, boot._done, boot._error, boot._started)
    yield boot
    (boot._phase, boot._total, boot._done, boot._error, boot._started) = saved
    boot._event.set()


def _restoring(total=4800, done=1200):
    boot._phase, boot._total, boot._done, boot._error = "restoring", total, done, None


def test_동기_모드에서는_import_가_끝나면_준비돼_있다():
    """테스트 700개가 이 전제로 돈다 — 깨지면 첫 요청부터 503 이다."""
    assert boot.ready() is True
    assert boot.progress()["phase"] == "ready"


def test_준비_전에는_가린_경로가_503_이고_다시_오라고_한다(gate):
    _restoring()
    c = TestClient(main.app)
    r = c.get("/admin")                       # 복원이 필요한 화면 — 세션·취향을 읽는다
    assert r.status_code == 503
    assert r.headers["retry-after"] == str(boot.RETRY_AFTER)
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["phase"] == "restoring"
    assert body["progress"]["preferences_done"] == 1200
    assert body["progress"]["preferences_total"] == 4800
    assert "다시 시도" in body["detail"]


def test_준비_전에도_열어_둔_경로는_통과한다(gate):
    """탐침·문서·필터·관측 화면 — 전부 기동 때 동기로 올린 것만 읽는 경로다."""
    _restoring()
    c = TestClient(main.app)
    assert c.get("/healthz").status_code == 200       # 살아 있다 — 준비와 무관
    assert c.get("/openapi.json").status_code == 200  # 프론트가 타입을 여기서 만든다
    assert c.get("/docs").status_code == 200
    assert c.get("/filters").status_code == 200
    assert c.get("/admin/trends").status_code == 200
    assert c.get("/admin/api/trend/national").status_code == 200
    assert c.get("/metrics").status_code == 200


def test_readyz_는_복원_중_503_에_진행률과_이유를_담는다(gate):
    _restoring(total=10, done=3)
    r = TestClient(main.app).get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["restore"]["phase"] == "restoring"
    assert body["restore"]["preferences_done"] == 3
    assert body["restore"]["preferences_total"] == 10
    assert any("복원 중" in x and "3/10" in x for x in body["reasons"])


def test_readyz_는_준비되면_200_이고_복원_상황을_그대로_준다():
    r = TestClient(main.app).get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["restore"]["phase"] == "ready"
    assert body["reasons"] == []


def test_복원_실패는_readyz_이유에_남는다(gate):
    boot._phase, boot._error = "failed", "RuntimeError: 파일이 깨졌다"
    r = TestClient(main.app).get("/readyz")
    assert r.status_code == 503
    assert any("실패" in x and "파일이 깨졌다" in x for x in r.json()["reasons"])
    # 게이트도 실패를 말한다 — '잠시 뒤' 가 아니라.
    g = TestClient(main.app).get("/admin")
    assert g.status_code == 503 and "실패" in g.json()["detail"]


def test_프리플라이트는_준비_전에도_막지_않는다(gate):
    _restoring()
    r = TestClient(main.app).options(
        "/users/u1/cards",
        headers={"Origin": "http://localhost:3000",
                 "Access-Control-Request-Method": "GET"})
    assert r.status_code != 503


def test_metrics_에_ready_게이지가_있다(gate):
    _restoring()
    from app import observe
    observe.set_ready(False)
    txt = TestClient(main.app).get("/metrics").text
    assert "matchuri_ready 0" in txt
    observe.set_ready(True)
    txt = TestClient(main.app).get("/metrics").text
    assert "matchuri_ready 1" in txt


# ────────────────────────────────────────────── start()
def test_스레드_모드는_즉시_돌아오고_끝나면_준비가_된다(gate):
    """이게 요점이다 — 복원이 도는 동안 `start()` 가 돌아와야 포트가 열린다."""
    boot.reset()
    release = threading.Event()
    seen = []

    def slow_restore():
        seen.append("시작")
        release.wait(5)
        boot.set_total(2)
        boot.tick(2)

    boot.start(slow_restore, sync=False)          # 돌아왔다 = 복원이 끝나길 안 기다렸다
    assert boot.ready() is False
    assert boot.progress()["phase"] == "restoring"
    release.set()
    assert boot.wait(5) is True
    assert boot.ready() is True
    assert boot.progress()["preferences_done"] == 2
    assert seen == ["시작"]


def test_start_는_한_번만_돈다(gate):
    boot.reset()
    calls = []
    boot.start(lambda: calls.append(1), sync=True)
    boot.start(lambda: calls.append(2), sync=True)
    assert calls == [1]
    assert boot.ready() is True


def test_동기_모드의_실패는_예외로_올라오고_상태는_failed_다(gate):
    """옛 동작(import 실패)을 지킨다 — 조용히 503 좀비가 되면 안 된다."""
    boot.reset()

    def broken():
        raise RuntimeError("깨진 기록")

    with pytest.raises(RuntimeError):
        boot.start(broken, sync=True)
    p = boot.progress()
    assert p["phase"] == "failed"
    assert "깨진 기록" in p["error"]


def test_count_preferences_는_restore_와_같은_조건으로_센다(tmp_path, monkeypatch):
    """분모가 분자와 다른 조건으로 세면 진행률이 100% 를 넘거나 못 미친다."""
    import json
    from app import state
    # 세션 행은 `restore` 가 등록부를 찾아 처리하므로(없는 사용자면 404) 여기 안 넣는다 —
    # 이 테스트는 선호 행의 **개수 조건**만 잰다.
    rows = [
        {"event": "session_start", "user_id": "u", "session_id": "s"},   # 둘 다 건너뛴다
        {"user_id": "u", "card_id": 1, "prefer": "like"},
        {"user_id": "u", "card_id": 2, "prefer": "dislike"},
        {"user_id": "u", "card_id": 3, "prefer": "soso"},      # restore 도 건너뛴다
        {"user_id": "u", "prefer": "like"},                      # card_id 없음
    ]
    path = tmp_path / "preferences.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(state, "PREFS", path)
    assert state.count_preferences() == 2
    applied = []
    got = state.restore({}, [], lambda u, c, p: applied.append(c))
    assert got["preferences"] == 2 == len(applied)
