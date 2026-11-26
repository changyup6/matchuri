# -*- coding: utf-8 -*-
"""기동 준비 게이트 — **포트를 먼저 열고, 복원은 뒤에서 돈다.**

## 무엇이 문제였나

`main.py` 가 복원(`preferences.jsonl` 을 처음부터 다시 먹여 취향 모델을 되살리는 일)을
**import 시점**에 했다. uvicorn 은 `import main` 이 끝난 뒤에 소켓을 만들므로, 복원하는
동안 8000 포트가 아예 안 열려 있었다. 재생은 기록 수에 선형이라 1,476건 20초 →
4,800건 4분+ 로 자랐고, **배포마다** 그 시간 동안 nginx 가 502 를 냈다(2026-09-17 실측
5분). 감시는 '기동 중' 과 '죽음' 을 구분할 수 없었다 — 둘 다 502 다.

## 무엇을 하나

복원을 **데몬 스레드**로 띄우고 즉시 돌아온다. 그러면 `import main` 이 곧 끝나 포트가
열리고

    /healthz              200  즉시 — 프로세스가 살아 있다는 뜻만 담는다(원래 그 뜻이었다)
    /readyz               503  복원 중, 진행률(선호 n/N건)을 담아서 → 끝나면 200
    복원이 필요 없는 것    200  /docs · /openapi.json · /filters · /admin/trends (아래 OPEN_*)
    그 밖의 전부          503  `Retry-After: 15` — nginx 가 점검 화면으로 바꿔 준다

**공백 자체는 그대로다.** 사용자가 카드를 받기까지 걸리는 시간은 안 줄어든다 — 그건
취향 모델 스냅샷(explainer 7.1 ②)이 할 일이다. 여기서 바뀌는 것은 그 시간 동안 서버가
**말을 한다**는 것이다: 살아 있다, 몇 건 남았다, 언제 다시 오라.

## 왜 `lifespan` 이 아닌가

`uvicorn/server.py` 가 `await self.lifespan.startup()` 을 소켓 생성보다 **먼저** 한다
(`:107` 대 `:145`). lifespan 안에서 동기로 복원하면 지금과 똑같이 포트가 안 열린다.
스레드로 띄우고 즉시 반환하는 것이 요점이고, 그건 import 시점에 해도 된다.

## 테스트는 동기로 돈다

`TestClient(app)` 를 `with` 없이 쓰는 테스트가 700개다. 스레드로 돌리면 첫 요청이 503 을
받아 전부 깨진다. `MATCHURI_RESTORE_SYNC=1` 이면 같은 자리에서 **동기로** 복원한다 —
옛 동작 그대로다. `tests/conftest.py` 가 이 값을 넣는다.

## 실패하면 프로세스를 끝낸다

옛 동작에서 복원 중 예외는 import 실패 = 프로세스 종료였고 `restart: unless-stopped` 가
다시 띄웠다. 스레드에서는 예외가 조용히 죽어 **503 만 내는 좀비**가 된다. 그래서 스레드
모드에서는 `boot.restore_failed` 를 남기고 `os._exit(3)` 한다 — 실패는 크게, 예전처럼.
동기 모드(테스트)에서는 예외를 그대로 올린다.

## 복원 중 열어 둔 경로가 안전한 이유

가려진 경로는 아무것도 못 하므로 취향 모델·세션은 복원 스레드만 만진다. 열어 둔 경로는
전부 **기동 때 동기로 올린 것만** 읽는다 — 카드 카탈로그(`loader`), 가게·리뷰(`recsys.store`),
수집 산출물 파일. `/admin/trends` 의 노출 집중도는 파일을 직접 읽고 `/metrics` 의 카운터는
락 뒤에 있다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import observe

#: 준비 전 503 에 붙이는 `Retry-After`(초). nginx 점검 화면의 자동 재시도 주기와 같다.
RETRY_AFTER = 15

#: 이 값이 참이면 복원을 스레드가 아니라 같은 자리에서 동기로 한다(테스트·CLI).
SYNC_ENV = "MATCHURI_RESTORE_SYNC"

#: 복원 전에도 열어 두는 경로. **기동 때 동기로 올린 것만 읽는 경로여야 한다** —
#: 세션·취향 모델·선호 기록을 만지는 것은 하나도 넣지 않는다.
OPEN_EXACT = frozenset({
    "/healthz", "/readyz", "/metrics",          # 운영 탐침
    "/docs", "/redoc", "/openapi.json",         # 프론트가 타입을 여기서 만든다
    "/filters",                                 # 필터 어휘 — 카탈로그에서만 나온다
})
OPEN_PREFIX = ("/admin/trends", "/admin/api/trend/")   # 관측 화면 — 파일·카탈로그만 읽는다

_lock = threading.Lock()
_event = threading.Event()
_phase = "pending"            # pending → restoring → ready | failed
_total = 0
_done = 0
_started_at: str | None = None
_finished_at: str | None = None
_seconds = 0.0
_error: str | None = None
_thread: threading.Thread | None = None
_started = False
_snapshot_rows: int | None = None


def ready() -> bool:
    """게이트가 매 요청마다 부른다 — 락 없이 문자열 하나만 읽는다(GIL 이 원자성을 준다)."""
    return _phase == "ready"


def phase() -> str:
    return _phase


def set_total(n: int) -> None:
    global _total
    with _lock:
        _total = max(0, int(n))


def set_snapshot_rows(n: int | None) -> None:
    """스냅샷이 대신해 준 행 수(`/readyz` 에 보인다). None 이면 스냅샷 없이 전체 재생."""
    global _snapshot_rows
    with _lock:
        _snapshot_rows = n


def tick(n: int = 1) -> None:
    """선호 한 건을 되먹였다. `/readyz` 의 진행률이 이걸로 움직인다."""
    global _done
    with _lock:
        _done += n


def progress() -> dict:
    with _lock:
        elapsed = _seconds if _finished_at else (
            time.perf_counter() - _t0 if _t0 is not None else 0.0)
        return {"phase": _phase, "preferences_done": _done, "preferences_total": _total,
                "snapshot_rows": _snapshot_rows,
                "seconds": round(elapsed, 3), "started_at": _started_at,
                "finished_at": _finished_at, "error": _error}


_t0: float | None = None


def _run(restore_fn, *, in_thread: bool) -> None:
    global _phase, _started_at, _finished_at, _seconds, _error, _t0
    with _lock:
        _phase = "restoring"
        _started_at = observe._now()
        _t0 = time.perf_counter()
    observe.log("boot.restore_start", in_thread=in_thread)
    try:
        restore_fn()
    except BaseException as exc:                       # noqa: BLE001 — 실패는 크게
        with _lock:
            _phase = "failed"
            _error = f"{type(exc).__name__}: {exc}"[:300]
            _finished_at = observe._now()
            _seconds = time.perf_counter() - _t0
        observe.log_exc("boot.restore_failed", exc, in_thread=in_thread)
        _event.set()
        if in_thread:
            # 옛 동작(import 실패 = 프로세스 종료)을 지킨다. 스레드에서 예외를 삼키면
            # 503 만 내는 좀비가 되고 restart 정책은 그것을 살아 있는 것으로 본다.
            logging.shutdown()
            os._exit(3)
        raise
    with _lock:
        _phase = "ready"
        _finished_at = observe._now()
        _seconds = time.perf_counter() - _t0
    observe.set_ready(True)
    observe.log("boot.ready", seconds=round(_seconds, 3),
                preferences=_done, in_thread=in_thread)
    _event.set()


def start(restore_fn, *, sync: bool | None = None) -> None:
    """복원을 시작한다. 한 번만 — 두 번째 호출은 아무 일도 안 한다.

    `sync` 가 None 이면 `MATCHURI_RESTORE_SYNC` 를 본다. 동기면 이 자리에서 끝까지
    돌고(옛 동작), 아니면 데몬 스레드로 띄우고 **즉시** 돌아온다 — 그래서 포트가 열린다.
    """
    global _thread, _started
    with _lock:
        if _started:
            return
        _started = True
    if sync is None:
        sync = os.environ.get(SYNC_ENV, "").strip().lower() in ("1", "true", "yes")
    if sync:
        _run(restore_fn, in_thread=False)
        return
    _thread = threading.Thread(target=_run, args=(restore_fn,),
                               kwargs={"in_thread": True},
                               name="matchuri-restore", daemon=True)
    _thread.start()


def wait(timeout: float | None = None) -> bool:
    """복원이 끝나기를 기다린다(성공·실패 무관). 테스트와 CLI 용이다."""
    return _event.wait(timeout)


def reset() -> None:
    """테스트 전용 — 게이트를 처음 상태로. 운영 코드가 부를 일은 없다."""
    global _phase, _total, _done, _started_at, _finished_at, _seconds, _error
    global _thread, _started, _t0, _snapshot_rows
    with _lock:
        _phase, _total, _done = "pending", 0, 0
        _snapshot_rows = None
        _started_at = _finished_at = _error = None
        _seconds, _t0 = 0.0, None
        _thread, _started = None, False
        _event.clear()
    observe.set_ready(False)


def _open(path: str) -> bool:
    return path in OPEN_EXACT or path.startswith(OPEN_PREFIX)


class Gate:
    """준비 전 요청을 503 으로. 순수 ASGI — `observe.RequestLog` 와 같은 이유로
    `BaseHTTPMiddleware` 를 쓰지 않는다.

    `observe.RequestLog` **안쪽**에 둔다(그래서 503 도 로그에 남는다), CORS **바깥**에
    둔다. 그러면 프리플라이트(OPTIONS)가 CORS 에 닿기 전에 여기 오므로 OPTIONS 는 그냥
    통과시킨다 — 프리플라이트를 503 으로 막으면 브라우저는 '왜 못 부르나' 를 절대
    말해 주지 않는다.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or ready():
            return await self.app(scope, receive, send)
        if scope.get("method") == "OPTIONS" or _open(scope.get("path") or ""):
            return await self.app(scope, receive, send)

        p = progress()
        body = json.dumps({
            "detail": "서버가 기동 복원 중입니다. 잠시 뒤 다시 시도하세요."
                      if p["phase"] != "failed" else "서버 기동 복원이 실패했습니다.",
            "phase": p["phase"], "progress": p, "retry_after": RETRY_AFTER,
        }, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 503, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(RETRY_AFTER).encode()),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": body})
