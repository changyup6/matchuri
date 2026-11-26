# -*- coding: utf-8 -*-
"""취향 모델 스냅샷 — 기동 복원의 공백을 없앤다 (explainer 7.1 ②).

## 무엇이 문제였나

재시작하면 `preferences.jsonl` 을 **처음부터** 다시 먹여 취향 모델을 되살린다. 한 건마다
`taste.record`(numpy, 최대 5쌍의 성분 재계산)가 두 번 돈다 — 세션 모델과 사용자별 '기억'
모델(`recommendation_sessions.remember`). 기록 수에 선형이라 1,476건 20초 → 8,718건
3분대(2026-09-18 운영 실측, 약 50건/초)로 자라고 있었다. 준비 게이트(`app/boot.py`)는
그 시간 동안 서버가 **말을 하게** 했지만 시간 자체는 그대로였다.

## 무엇을 하나

살아 있는 학습 상태를 **통째로** 주기적으로 파일에 적어 두고(pickle), 부팅 때는 그것을
그대로 올린 뒤 **그 뒤에 쌓인 기록만** 재생한다. 복원이 끝나면 바로 한 번 찍고, 그 뒤
5분마다 새 기록을 담는다. 재배포가 첫 주기 전에 와도 전체 복원을 되풀이하지 않는다.

## 왜 '학습 상태 전체' 인가 — 숫자 부분만 건너뛸 수 없었다

처음엔 재생 중 numpy 만 건너뛰고 나머지(로그·세션 이벤트)는 그대로 돌리려 했다. 안 된다.
`remember` 가 사용자별 기억 모델을 따로 학습하고, `apply_created` 가 **그 기억에서**
새 세션의 사전값을 파생하고, `apply_boundary` 가 현재 세션을 deepcopy 해 보관한다 —
전부 순서에 묶인 상태다. 일부만 건너뛰면 파생된 상태가 어긋난다. 그래서 절단점에서의
메모리 상태를 전부 담는다:

    policy._sessions     정책 봉투(legacy·taste 참조, batch·served)
    taste._sessions      취향 모델 m·v·이력·체크포인트·기억
    swipe._sessions      legacy 모델 m·v·cat_stats·이력
    rec._user_memory     사용자별 기억 모델
    rec._initial / _active   세션 초기값·현재 세션
    users._users         등록부(세션용 합성 항목 포함 — 파일에는 없다)

`identities` 는 넣지 않는다 — `identities.restore()` 가 파일만 훑어 만들고(numpy 없음,
ms 단위) 어차피 매 부팅마다 전체를 다시 만든다.

## 절단점은 정확해야 한다 — `state.PREF_LOCK`

스냅샷의 `watermark` 는 그 순간 `preferences.jsonl` 의 행 수다. 부팅은 그 수만큼
건너뛴다. 그러니 **파일에 있는 것 = 메모리에 적용된 것** 이어야 한다. 그런데
`post_preference` 는 `_apply`(메모리) 뒤에 `record_preference`(파일)를 쓴다 — 그 사이에
찍으면 메모리가 한 건 앞서 있어 재시작 뒤 그 한 건이 **두 번** 학습된다. 그래서 파일에
쓰는 세 지점(선호·`session_start`·`session_create`)과 그 메모리 적용을 `state.PREF_LOCK`
으로 묶고, 스냅샷도 같은 락 안에서 행을 세고 pickle 한다.

락 순서: 요청은 사용자 락(`scoped`) → `PREF_LOCK`, 스냅샷은 `PREF_LOCK` 만. 순환이 없다.
스냅샷이 사용자 락을 잡지 않으므로 카드 뽑기(`shown`·rng 갱신)와 pickle 이 겹칠 수 있다 —
그때는 `RuntimeError`(반복 중 크기 변경)가 나는데, **그 회차를 건너뛰고** 다음에 다시
찍는다. 잘못된 스냅샷을 쓰는 것보다 안 쓰는 것이 낫다.

## 스냅샷이 '거짓' 이 되는 경우 — 도장 둘

- **동작 도장(카나리아)**: 고정된 가게 열두 곳에 고정된 좋아요/싫어요를 새 세션에 먹여
  나온 `m`·`v`(taste·legacy)와 `start_from` 결과, 그리고 세션 dict 의 키 목록을 해시한다.
  학습식이 바뀌면 이 값이 바뀌고, **주석·로그·화면만 바뀌면 안 바뀐다.** 처음엔 학습
  모듈의 소스 해시를 썼는데 이 팀은 그 파일들을 매일 건드려서(2026-09-21 아침 두 배포
  사이에 `policy.py` 6줄) 배포마다 스냅샷을 버리고 8~12분 전체 재생을 했다 — 스냅샷이
  있어도 없는 것과 같았다. 재생 결정성 덕에 동작 지문이 소스보다 정확한 잣대다.
  비용은 열두 건 학습 = 0.3초, 찍을 때와 올릴 때 한 번씩
- **데이터 도장**: `data/kakao` 파일들의 mtime(`store.data_stamp`)과 `serving/` 산출물의
  mtime·크기. 성분 공간이 바뀌면(주간 배치) `m` 의 뜻이 바뀐다 → 마찬가지로 버린다

두 도장이 맞지 않으면 `snapshot.stale` 을 남기고 없는 것처럼 행동한다. **재생 결정성이
이 설계의 근거다** — 살아 있는 상태와 전체 재생이 비트 단위로 같으므로(`tests/
test_replay.py`), 살아 있는 상태의 스냅샷 = 그 시점까지 재생한 상태다. 그래서 스냅샷 뒤
재생과 전체 재생이 같다(`tests/test_snapshot.py` 가 그것을 잰다).

## 되살린 뒤 지우는 것

`batch`·`served`(정책 봉투), `served`·`last_cats`·`last_brands`·`pending`(모델)은 학습
상태가 아니라 **노출 진행 상태**다. 전체 재생은 이것들을 0 에서 시작해
`restore_exposures` 가 노출 기록으로 다시 세운다. 스냅샷에서 살려 두면 그 위에 또 더해져
두 배가 된다 → 올린 직후 0 으로 되돌린다. `shown` 은 집합이라 두 번 더해도 같다 — 둔다.

## pickle 에 대해

`state/` 볼륨 안의 우리가 쓴 파일만 읽는다. 외부 입력이 아니다. 형식이 바뀌면 `FORMAT`
을 올린다(맞지 않으면 버린다).

## 켜고 끄기

`MATCHURI_SNAPSHOT_EVERY`(초, 기본 300). 0 이면 주기 스냅샷을 끈다 — 파일이 있으면
부팅 때 읽는 것은 그대로다. 종료(atexit) 때도 한 번 찍는다 — 배포 직전의 상태가 가장
값지다.
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import os
import pickle
import threading
import time
from pathlib import Path

from . import boot, observe, state
from recsys import content, decision, decision_metrics, decision_summary, policy, swipe, taste
from recsys import store as svc

#: 파일 형식 판. 담는 것이 바뀌면 올린다 — 맞지 않는 파일은 없는 것으로 친다.
#: 2: 도장이 소스 해시(`code`)에서 동작 지문(`behavior`)으로 바뀌었다(2026-09-21).
FORMAT = 2

#: 스냅샷 파일. state 볼륨 안이라 배포를 넘어 남는다.
FILE = state.DIR / "snapshot.pkl"

#: 주기(초). 0 이면 주기 스냅샷을 끈다.
EVERY_ENV = "MATCHURI_SNAPSHOT_EVERY"
EVERY_DEFAULT = 300.0

#: 카나리아에 먹일 가게 수와 패턴. 열둘이면 쌍(`MAX_PAIRS`=5)이 다 채워지고 망각도 한 번 돈다.
CANARY_N = 12
CANARY_PATTERN = (True, False, True, True, False, False, True, False, True, True, False, True)

_lock = threading.Lock()
_write_lock = threading.Lock()
_last_watermark = 0
_last_at: str | None = None
_thread: threading.Thread | None = None


# ────────────────────────────────────────────── 도장
def behavior_stamp() -> str:
    """학습 **동작**의 지문. 소스가 아니라 결과를 잰다.

    같은 가게 열두 곳에 같은 순서로 좋아요/싫어요를 먹인 새 세션의 `m`·`v`(taste·legacy),
    거기서 `start_from` 으로 파생한 다음 세션의 `m`, 그리고 세션 dict 의 **키 목록**(필드가
    생기거나 없어지면 스냅샷의 dict 모양이 코드와 어긋난다). 학습에 난수가 없으므로
    (`tests/test_replay.py`) 같은 코드·같은 산출물이면 언제 재도 같다.

    산출물이 없으면(legacy 만 도는 환경) 그 사실 자체가 도장이다.
    """
    art = content.load()
    h = hashlib.sha1(b"canary-v1")
    if art is None:
        h.update(b"no-artifacts")
        return h.hexdigest()
    ids = sorted(art.pos)[:CANARY_N]
    t = taste._new_session()
    l = swipe._new_session()
    for sid, like in zip(ids, CANARY_PATTERN):
        taste.record(t, sid, like)
        swipe.record(l, sid, like)
    t2 = taste.start_from(t)
    for arr in (t["m"], t["v"], l["m"], l["v"], t2["m"], t2["v"]):
        h.update(arr.astype(float).tobytes())
    for keys in (sorted(t), sorted(l), sorted(t["hist"][-1]) if t["hist"] else [],
                 sorted(t["decision_checkpoints"][-1]) if t.get("decision_checkpoints") else [],
                 sorted(t2.get("memory", [{}])[0]) if t2.get("memory") else []):
        h.update(repr(keys).encode())
    return h.hexdigest()


def data_stamp() -> list:
    """`data/kakao` 와 `serving/` 이 바뀌었나. 성분 공간이 바뀌면 `m` 의 뜻이 바뀐다."""
    out: list = [[k, v] for k, v in svc.data_stamp()]
    serving = content.SERVING
    if serving.is_dir():
        for p in sorted(serving.glob("*")):
            try:
                st = p.stat()
                out.append([p.name, st.st_mtime_ns, st.st_size])
            except OSError:
                continue
    return out


def prefs_count() -> int:
    """`state._read` 와 같은 규칙으로 센다 — 빈 줄·깨진 줄은 양쪽 다 건너뛴다."""
    return sum(1 for _ in state._read(state.PREFS))


# ────────────────────────────────────────────── 찍기
def capture(only: set[str] | None = None) -> dict | None:
    """살아 있는 학습 상태를 한 절단점에서 pickle 로. `PREF_LOCK` 안에서 행을 세고 담는다.

    `only` 는 테스트용이다 — 그 사용자들만 담는다(테스트 프로세스의 다른 세션을 건드리지
    않으려고). 운영은 전부다.
    """
    from app import recommendation_sessions as rec, users

    def pick(d: dict) -> dict:
        return {k: v for k, v in d.items() if k in only} if only is not None else dict(d)

    with state.PREF_LOCK:
        if not state._enabled:
            # 파일 쓰기가 죽어 있으면 메모리가 파일보다 앞서 있다 — 절단점을 믿을 수 없다.
            observe.log("snapshot.skip", level=logging.WARNING, reason="state 쓰기 불가")
            return None
        n = prefs_count()
        payload = {
            "format": FORMAT, "at": state.now(), "watermark": n,
            "behavior": behavior_stamp(), "data": data_stamp(),
            "policy": pick(policy._sessions), "taste": pick(taste._sessions),
            "swipe": pick(swipe._sessions), "memory": pick(rec._user_memory),
            "initial": pick(rec._initial), "active": pick(rec._active),
            "users": pick(users._users),
        }
        try:
            blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        except (RuntimeError, TypeError, pickle.PicklingError, AttributeError) as exc:
            # 다른 요청이 카드를 뽑으며 shown 을 바꾸는 중이었을 수 있다. 다음 회차에.
            observe.log("snapshot.skip", level=logging.WARNING,
                        reason=f"{type(exc).__name__}: {exc}"[:200])
            return None
    return {"blob": blob, "watermark": n, "sessions": len(payload["policy"]),
            "at": payload["at"]}


def write(path: Path | None = None, only: set[str] | None = None) -> dict | None:
    """찍어서 파일로. 임시 파일에 쓰고 `os.replace` — 반쪽 파일이 남지 않는다."""
    # Periodic and shutdown writes share a destination. Serialize capture as well
    # as publication so an older cut cannot replace one captured after it.
    with _write_lock:
        return _write(path, only)


def _write(path: Path | None, only: set[str] | None) -> dict | None:
    global _last_watermark, _last_at
    path = path or FILE
    t0 = time.perf_counter()
    cap = capture(only)
    if cap is None:
        return None
    tmp = path.with_suffix(".pkl.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(cap["blob"])
        os.replace(tmp, path)
    except OSError as exc:
        observe.log("snapshot.write_fail", level=logging.ERROR, path=str(path),
                    error=type(exc).__name__, detail=str(exc)[:200])
        return None
    with _lock:
        _last_watermark, _last_at = cap["watermark"], cap["at"]
    info = {"watermark": cap["watermark"], "sessions": cap["sessions"],
            "bytes": len(cap["blob"]), "ms": round((time.perf_counter() - t0) * 1000, 1),
            "path": str(path)}
    observe.log("snapshot.write", **info)
    return info


# ────────────────────────────────────────────── 올리기
def load(path: Path | None = None) -> dict | None:
    """읽어서 도장을 맞춘다. 하나라도 어긋나면 None — 없는 것처럼 행동한다."""
    path = path or FILE
    if not path.exists():
        return None
    try:
        snap = pickle.loads(path.read_bytes())
    except Exception as exc:                          # noqa: BLE001 — 깨진 파일도 '없음'
        observe.log("snapshot.stale", level=logging.WARNING, path=str(path),
                    reason=f"읽기 실패 {type(exc).__name__}: {exc}"[:200])
        return None
    why = None
    if not isinstance(snap, dict) or snap.get("format") != FORMAT:
        why = f"형식 {snap.get('format') if isinstance(snap, dict) else '?'} ≠ {FORMAT}"
    elif snap.get("behavior") != behavior_stamp():
        why = "학습 동작이 바뀌었다"
    elif snap.get("data") != data_stamp():
        why = "데이터·산출물이 바뀌었다"
    if why:
        observe.log("snapshot.stale", level=logging.WARNING, path=str(path), reason=why,
                    at=snap.get("at") if isinstance(snap, dict) else None)
        return None
    return snap


def install(snap: dict) -> int:
    """스냅샷의 상태를 메모리에 올린다. **노출 진행 상태는 0 으로** — 머리말 참고.

    `update` 다(`clear` 가 아니다). 부팅 때는 어차피 비어 있고, 테스트에서는 다른 세션을
    지우지 않아야 한다.
    """
    from app import recommendation_sessions as rec, users

    policy._sessions.update(snap["policy"])
    taste._sessions.update(snap["taste"])
    swipe._sessions.update(snap["swipe"])
    rec._user_memory.update(snap["memory"])
    rec._initial.update(snap["initial"])
    rec._active.update(snap["active"])
    users._users.update(snap["users"])
    for sess in snap["policy"].values():
        sess["batch"] = 0
        sess["served"] = 0
    for t in snap["taste"].values():
        t["served"] = 0
        t["last_cats"] = []
        t["last_brands"] = []
    for l in snap["swipe"].values():
        l["pending"] = None
        l["last_cats"] = []
    with _lock:
        global _last_watermark, _last_at
        _last_watermark, _last_at = int(snap["watermark"]), snap.get("at")
    return len(snap["policy"])


def info() -> dict:
    with _lock:
        return {"last_watermark": _last_watermark, "last_at": _last_at, "path": str(FILE)}


# ────────────────────────────────────────────── 주기
def _every() -> float:
    raw = os.environ.get(EVERY_ENV, "").strip()
    try:
        return float(raw) if raw else EVERY_DEFAULT
    except ValueError:
        return EVERY_DEFAULT


def _loop(every: float) -> None:
    # Wait for the actual restore completion, not the next five-minute tick.
    # This thread stays separate from readiness and request handling.
    boot.wait()
    if not boot.ready():
        return
    while True:
        try:
            if boot.ready() and (_last_at is None or prefs_count() > _last_watermark):
                write()
        except Exception as exc:                      # noqa: BLE001 — 주기 스레드는 죽지 않는다
            observe.log_exc("snapshot.loop_error", exc)
        time.sleep(every)


def start_periodic() -> None:
    """복원 직후 첫 절단점, 이후 새 기록이 있을 때만 주기적으로 저장한다."""
    global _thread
    every = _every()
    # Synchronous restore is for tests/CLI; it must not create a background writer
    # that can capture temporary monkeypatched model state later in the run.
    sync = os.environ.get(boot.SYNC_ENV, "").strip().lower() in ("1", "true", "yes")
    if every <= 0 or sync:
        observe.log("snapshot.disabled", every=every)
        return
    with _lock:
        if _thread is not None:
            return
        _thread = threading.Thread(target=_loop, args=(every,),
                                   name="matchuri-snapshot", daemon=True)
        _thread.start()
    observe.log("snapshot.periodic", every=every)


def _at_exit() -> None:
    # 배포 직전의 상태가 가장 값지다. 준비도 안 됐으면 절단점이 없으니 찍지 않는다.
    # 동기 복원 모드(테스트·CLI)에서는 찍지 않는다 — pytest 가 스트림을 닫은 뒤에 로그를
    # 쓰려다 "Logging error" 를 내고, 테스트 state 디렉터리에 부산물을 남긴다.
    if os.environ.get(boot.SYNC_ENV, "").strip().lower() in ("1", "true", "yes"):
        return
    if boot.ready() and _every() > 0:
        try:
            write()
        except Exception:                             # noqa: BLE001 — 종료를 막지 않는다
            pass


atexit.register(_at_exit)
