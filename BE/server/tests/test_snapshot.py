# -*- coding: utf-8 -*-
"""취향 모델 스냅샷(`app/snapshot.py`) — 스냅샷 뒤 재생 == 전체 재생.

이 파일이 지키는 것:

    등가성    앞 k건을 스냅샷으로 올리고 뒤 n-k건만 재생한 상태 == n건을 다 재생한 상태
              (legacy·taste 학습 상태가 비트 단위로)
    절단점    watermark 는 `state._read` 가 세는 행 수와 같다(빈 줄·깨진 줄 제외)
    건너뛰기  `state.restore(skip=k)` 는 앞 k행을 적용하지 않지만 선호는 `logs` 에 남긴다
    도장      학습 코드·데이터 도장이 다르면 스냅샷을 버린다(없는 것처럼)
    안전      state 쓰기가 죽어 있으면 찍지 않는다 · 올린 뒤 노출 진행 상태는 0

`test_replay.py` 와 같은 이유로 **진짜 산출물**을 쓴다 — 합성 풀에서 되는 것과 실제
성분 공간에서 되는 것은 다른 주장이다.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import numpy as np
import pytest

from recsys import content, policy, swipe, taste

pytestmark = pytest.mark.skipif(
    not content.available(),
    reason="data/kakao/serving 이 없다 (python -m analysis.build_serving)")

SEQ = ["like", "dislike", "like", "like", "dislike", "dislike", "like", "like",
       "dislike", "like", "like", "dislike"]


def _card_ids(n: int) -> list[int]:
    from app import loader
    loader.load_all()
    out = []
    for sid in content.load().ids:
        cid = loader.card_id_by_store.get(sid)
        if cid is not None:
            out.append(cid)
        if len(out) >= n:
            break
    return out


def _drop(*user_ids: str) -> None:
    from app import recommendation_sessions as rec
    for uid in user_ids:
        policy._sessions.pop(uid, None)
        swipe._sessions.pop(uid, None)
        taste._sessions.pop(uid, None)
        rec._user_memory.pop(uid, None)


def _rows(uid: str, cards: list[int]) -> list[dict]:
    return [{"user_id": uid, "card_id": cid, "prefer": pref, "at": "now"}
            for pref, cid in zip(SEQ, cards)]


def _learned(uid: str) -> tuple:
    sess = policy.session_for(uid)
    return (sess["taste"]["m"].tobytes(), sess["taste"]["v"].tobytes(),
            sess["legacy"]["m"].tobytes(), sess["legacy"]["v"].tobytes(),
            json.dumps(policy.learn_state(sess), sort_keys=True, default=str))


# ────────────────────────────────────────────── 등가성 (핵심)
def test_스냅샷_뒤_재생은_전체_재생과_같다(tmp_path, monkeypatch):
    import main
    from app import snapshot, state

    cards = _card_ids(len(SEQ))
    k = 7                                          # 앞 7건은 스냅샷, 뒤 5건은 재생
    uid_full, uid_snap = "u_snapfull", "u_snapshot"
    _drop(uid_full, uid_snap)

    # 기준: 12건을 한 번에 다 먹인 사람.
    for r in _rows(uid_full, cards):
        main._apply(uid_full, r["card_id"], r["prefer"])
    expect = _learned(uid_full)

    # 실험: 7건만 먹인 상태에서 스냅샷을 찍는다(파일에도 7행만 있다 = 절단점이 맞다).
    rows = _rows(uid_snap, cards)
    prefs = tmp_path / "preferences.jsonl"
    prefs.write_text("\n".join(json.dumps(r) for r in rows[:k]) + "\n", encoding="utf-8")
    monkeypatch.setattr(state, "PREFS", prefs)
    for r in rows[:k]:
        main._apply(uid_snap, r["card_id"], r["prefer"])
    snap_file = tmp_path / "snapshot.pkl"
    info = snapshot.write(snap_file, only={uid_snap})
    assert info and info["watermark"] == k and info["sessions"] == 1

    # 그 뒤 5건이 더 쌓였고, 서버가 재시작됐다: 메모리는 비고 파일에는 12행.
    prefs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    _drop(uid_snap)
    snap = snapshot.load(snap_file)
    assert snap is not None
    assert snapshot.install(snap) == 1
    logs: list = []
    got = state.restore({}, logs, main._apply, skip=int(snap["watermark"]))
    assert got["skipped"] == k and got["preferences"] == len(SEQ) - k
    assert len(logs) == len(SEQ)                   # 건너뛴 것도 logs 에는 다 있다

    # user_id 만 다르므로 학습 상태는 완전히 같아야 한다 — 비트 단위로.
    assert _learned(uid_snap) == expect
    assert policy.session_for(uid_snap)["taste"]["n"] == len(SEQ)


def test_올린_직후_노출_진행_상태는_0_이다(tmp_path, monkeypatch):
    """전체 재생은 0 에서 시작해 restore_exposures 가 다시 세운다. 스냅샷 값이 남아 있으면
    그 위에 또 더해져 두 배가 된다."""
    import main
    from app import snapshot, state

    uid = "u_snapserved"
    _drop(uid)
    cards = _card_ids(3)
    monkeypatch.setattr(state, "PREFS", tmp_path / "p.jsonl")
    (tmp_path / "p.jsonl").write_text("", encoding="utf-8")
    for cid, pref in zip(cards, SEQ):
        main._apply(uid, cid, pref)
    sess = policy.session_for(uid)
    sess["batch"], sess["served"] = 4, 17
    sess["taste"]["served"], sess["taste"]["last_cats"] = 9, ["한식"]
    info = snapshot.write(tmp_path / "s.pkl", only={uid})
    assert info
    _drop(uid)
    snapshot.install(snapshot.load(tmp_path / "s.pkl"))
    sess = policy.session_for(uid)
    assert (sess["batch"], sess["served"]) == (0, 0)
    assert sess["taste"]["served"] == 0 and sess["taste"]["last_cats"] == []
    assert sess["taste"]["n"] == 3                 # 학습 상태는 그대로다


# ────────────────────────────────────────────── 절단점·건너뛰기
def test_watermark_는_read_가_세는_행_수와_같다(tmp_path, monkeypatch):
    from app import snapshot, state
    p = tmp_path / "p.jsonl"
    p.write_text('{"user_id":"a","card_id":1,"prefer":"like"}\n\n깨진 줄\n'
                 '{"event":"session_start","user_id":"a","session_id":"s"}\n',
                 encoding="utf-8")
    monkeypatch.setattr(state, "PREFS", p)
    assert snapshot.prefs_count() == 2 == sum(1 for _ in state._read(p))


def test_restore_skip_은_앞_행을_적용하지_않고_logs_에만_남긴다(tmp_path, monkeypatch):
    from app import state
    rows = [{"user_id": "u", "card_id": i, "prefer": "like"} for i in range(1, 6)]
    p = tmp_path / "p.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(state, "PREFS", p)
    applied, logs = [], []
    got = state.restore({}, logs, lambda u, c, pr: applied.append(c), skip=3)
    assert applied == [4, 5]
    assert [l.card_id for l in logs] == [1, 2, 3, 4, 5]
    assert got["skipped"] == 3 and got["preferences"] == 2


# ────────────────────────────────────────────── 도장
def test_학습_동작_도장은_결정적이고_학습식이_바뀌면_바뀐다(monkeypatch):
    """소스 해시 대신 동작 지문을 쓰는 근거다 — 주석이 바뀌어도 같고, 학습식이 바뀌면 다르다."""
    from app import snapshot
    a, b = snapshot.behavior_stamp(), snapshot.behavior_stamp()
    assert a == b
    # 학습 스텝이 바뀌면 결과가 달라야 한다(진짜 학습식 변화). `MAX_STEP` 같은 클립 상수는
    # 열두 건으로는 닿지 않을 수 있어 스텝 자체를 두 배로 만든다.
    orig = taste._logistic_step
    monkeypatch.setattr(taste, "_logistic_step",
                        lambda sess, x, y, weight=1.0: orig(sess, x, y, weight * 2.0))
    assert snapshot.behavior_stamp() != a
    monkeypatch.undo()
    # 학습과 무관한 것(파일 mtime·주석)은 지문에 안 들어간다 — 소스를 안 읽는다.
    import builtins
    real_open = builtins.open
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("소스를 읽었다")))
    assert snapshot.behavior_stamp() == a


def test_학습_코드가_바뀌면_스냅샷을_버린다(tmp_path, monkeypatch):
    from app import snapshot
    _drop("u_stamp")
    info = snapshot.write(tmp_path / "s.pkl", only={"u_stamp"})
    assert info
    assert snapshot.load(tmp_path / "s.pkl") is not None
    monkeypatch.setattr(snapshot, "behavior_stamp", lambda: "다른 동작")
    assert snapshot.load(tmp_path / "s.pkl") is None


def test_데이터_산출물이_바뀌면_스냅샷을_버린다(tmp_path, monkeypatch):
    from app import snapshot
    info = snapshot.write(tmp_path / "s.pkl", only=set())
    assert info
    monkeypatch.setattr(snapshot, "data_stamp", lambda: [["stores.jsonl", 1, 1]])
    assert snapshot.load(tmp_path / "s.pkl") is None


def test_형식_판이_다르면_버린다(tmp_path, monkeypatch):
    from app import snapshot
    snapshot.write(tmp_path / "s.pkl", only=set())
    monkeypatch.setattr(snapshot, "FORMAT", snapshot.FORMAT + 1)
    assert snapshot.load(tmp_path / "s.pkl") is None


def test_깨진_파일도_없는_것으로_친다(tmp_path):
    from app import snapshot
    (tmp_path / "s.pkl").write_bytes(b"\x80\x04garbage")
    assert snapshot.load(tmp_path / "s.pkl") is None
    assert snapshot.load(tmp_path / "없다.pkl") is None


# ────────────────────────────────────────────── 안전
def test_state_쓰기가_죽어_있으면_찍지_않는다(tmp_path, monkeypatch):
    """메모리가 파일보다 앞서 있으면 절단점을 믿을 수 없다."""
    from app import snapshot, state
    monkeypatch.setattr(state, "_enabled", False)
    assert snapshot.capture(only=set()) is None
    assert snapshot.write(tmp_path / "s.pkl", only=set()) is None
    assert not (tmp_path / "s.pkl").exists()


def test_쓰기는_원자적이다_임시파일이_남지_않는다(tmp_path):
    from app import snapshot
    info = snapshot.write(tmp_path / "s.pkl", only=set())
    assert info and (tmp_path / "s.pkl").exists()
    assert not (tmp_path / "s.pkl.tmp").exists()


def test_선호_라우트는_PREF_LOCK_안에서_적용하고_기록한다():
    """절단점의 전제다 — 코드에서 직접 확인한다(락을 우회하는 경로가 생기면 여기서 걸린다)."""
    import inspect
    import main
    from app import recommendation_sessions as rec
    src = inspect.getsource(main.post_preference)
    assert "with state.PREF_LOCK:" in src
    body = src.split("with state.PREF_LOCK:", 1)[1]
    assert "_apply(" in body and "state.record_preference(" in body
    assert "with state.PREF_LOCK:" in inspect.getsource(rec.start)
    assert "with state.PREF_LOCK:" in inspect.getsource(rec.create_independent)


def test_readyz_가_스냅샷_행_수를_준다(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from app import boot
    monkeypatch.setattr(boot, "_snapshot_rows", 8700)
    r = TestClient(main.app).get("/readyz")
    assert r.status_code == 200
    assert r.json()["restore"]["snapshot_rows"] == 8700


def test_복원_완료_직후_첫_주기_전에_스냅샷을_쓴다(monkeypatch):
    from app import boot, snapshot

    class EndLoop(BaseException):
        pass

    calls = []
    monkeypatch.setattr(boot, "wait", lambda: calls.append("restore_complete"))
    monkeypatch.setattr(boot, "ready", lambda: True)
    monkeypatch.setattr(snapshot, "_last_at", None)
    monkeypatch.setattr(snapshot, "write", lambda: calls.append("snapshot"))

    def sleep(seconds):
        calls.append(("sleep", seconds))
        raise EndLoop

    monkeypatch.setattr(snapshot.time, "sleep", sleep)
    with pytest.raises(EndLoop):
        snapshot._loop(300)
    assert calls == ["restore_complete", "snapshot", ("sleep", 300)]


def test_기존_스냅샷_이후_재생한_기록도_즉시_저장한다(monkeypatch):
    from app import boot, snapshot

    class EndLoop(BaseException):
        pass

    writes = []
    monkeypatch.setattr(boot, "wait", lambda: None)
    monkeypatch.setattr(boot, "ready", lambda: True)
    monkeypatch.setattr(snapshot, "_last_at", "already restored")
    monkeypatch.setattr(snapshot, "_last_watermark", 10)
    monkeypatch.setattr(snapshot, "prefs_count", lambda: 12)
    monkeypatch.setattr(snapshot, "write", lambda: writes.append(True))
    monkeypatch.setattr(snapshot.time, "sleep", lambda _: (_ for _ in ()).throw(EndLoop()))
    with pytest.raises(EndLoop):
        snapshot._loop(300)
    assert writes == [True]


def test_기존_스냅샷과_같으면_부팅_때_다시_쓰지_않는다(monkeypatch):
    from app import boot, snapshot

    class EndLoop(BaseException):
        pass

    monkeypatch.setattr(boot, "wait", lambda: None)
    monkeypatch.setattr(boot, "ready", lambda: True)
    monkeypatch.setattr(snapshot, "_last_at", "already restored")
    monkeypatch.setattr(snapshot, "_last_watermark", 10)
    monkeypatch.setattr(snapshot, "prefs_count", lambda: 10)
    monkeypatch.setattr(snapshot, "write", lambda: pytest.fail("unchanged snapshot rewritten"))
    monkeypatch.setattr(snapshot.time, "sleep", lambda _: (_ for _ in ()).throw(EndLoop()))
    with pytest.raises(EndLoop):
        snapshot._loop(300)


def test_복원이_실패하면_스냅샷을_쓰지_않는다(monkeypatch):
    from app import boot, snapshot
    monkeypatch.setattr(boot, "wait", lambda: None)
    monkeypatch.setattr(boot, "ready", lambda: False)
    monkeypatch.setattr(snapshot, "write", lambda: pytest.fail("incomplete model captured"))
    snapshot._loop(300)


def test_동기_복원은_백그라운드_스냅샷을_시작하지_않는다(monkeypatch):
    from app import boot, snapshot
    monkeypatch.setattr(snapshot, "_thread", None)
    monkeypatch.setenv(boot.SYNC_ENV, "1")
    monkeypatch.setenv(snapshot.EVERY_ENV, "300")
    snapshot.start_periodic()
    assert snapshot._thread is None


def test_동시_스냅샷은_캡처부터_게시까지_순서대로_쓴다(tmp_path, monkeypatch):
    from app import snapshot
    captured = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    calls = []
    monkeypatch.setattr(snapshot, "_last_at", None)
    monkeypatch.setattr(snapshot, "_last_watermark", 0)

    def capture(only):
        n = len(calls) + 1
        calls.append(n)
        if n == 1:
            captured.set()
            assert release.wait(5)
        return {"blob": str(n).encode(), "watermark": n, "sessions": 1, "at": str(n)}

    monkeypatch.setattr(snapshot, "capture", capture)
    path = tmp_path / "snapshot.pkl"

    def second_write():
        second_started.set()
        return snapshot.write(path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(snapshot.write, path)
        assert captured.wait(5)
        second = pool.submit(second_write)
        assert second_started.wait(5)
        try:
            # The second capture must wait while the earlier cut is unpublished.
            with pytest.raises(TimeoutError):
                second.result(timeout=0.05)
            assert calls == [1]
        finally:
            release.set()
        assert first.result(timeout=5)["watermark"] == 1
        assert second.result(timeout=5)["watermark"] == 2
    assert path.read_bytes() == b"2"
    assert snapshot.info()["last_watermark"] == 2
    assert not path.with_suffix(".pkl.tmp").exists()
