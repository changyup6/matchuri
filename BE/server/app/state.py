"""영속화 — 서버를 재시작해도 사용자·선호 기록·학습이 살아남게 한다.

DB 없이 **추가 전용 로그 한 벌**만 쓴다. 취향 모델은 선호 표현을 순서대로 접은
결과(fold)이므로, 로그만 있으면 재시작 때 그대로 복원된다. numpy 가중치를
직렬화할 필요가 없다.

  state/people.jsonl       실제 사용자와 기존 세션의 마이그레이션 연결
  state/users.jsonl        기존 세션 등록부 + 세션 설정(위치·필터), 호환 파일명
  state/preferences.jsonl  선호 표현 기록 (이게 취향 모델의 원본이다)
  state/bookmarks.jsonl    찜 기록 — 담았다/뺐다 (추천 모델과 무관하다)
  state/exposures.jsonl    노출 기록 — 어떤 카드를 **어느 정책이** 뽑아 보여줬나
  state/directions.jsonl   길찾기 기록 — 방문 의사. 지도 앱으로 넘어간 순간이다
  state/surveys.jsonl      설문 응답 — 방문 여부와 추천 만족도 (추천 모델과 무관하다)
  state/decisions.jsonl    최종 제안과 선택/계속 둘러보기 결과 (스와이프 선호와 별도)

`exposures.jsonl` 은 취향 모델의 입력이 아니다(재생에 쓰이지 않는다). 팀-드래프트
인터리빙의 결과를 재는 자리다 — 이 로그와 `preferences.jsonl` 을 카드 단위로 조인하면
정책별 like 율이 나온다(`recsys/policy.py: outcome_stats`).

컨테이너에서는 /app/state 에 볼륨이 붙는다(docker-compose.yml). 로컬에서는
BE/state/ 다. 배치가 저장소와 같으므로 경로 계산이 양쪽에서 동일하다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from app import observe
from app.model import Log

# BE/server/app/state.py -> parents[2] = BE  (app -> server -> BE)
ROOT = Path(__file__).resolve().parents[2]
DIR = Path(os.environ.get("MATCHURI_STATE") or (ROOT / "state"))

USERS = DIR / "users.jsonl"
PREFS = DIR / "preferences.jsonl"
EXPOSURES = DIR / "exposures.jsonl"
BOOKMARKS = DIR / "bookmarks.jsonl"
DIRECTIONS = DIR / "directions.jsonl"
SURVEYS = DIR / "surveys.jsonl"
#: 설문 사진의 메타데이터만. 파일은 `media` 볼륨에 따로 있다(`app/media.py`).
SURVEY_PHOTOS = DIR / "survey_photos.jsonl"

_lock = threading.Lock()
_enabled = True

#: **선호 파일과 메모리 적용을 한 덩어리로 묶는 락.** 스냅샷(`app/snapshot.py`)의
#: 절단점이 정확하려면 "파일에 있는 것 = 메모리에 적용된 것" 이어야 한다. 파일에 쓰는
#: 세 지점(선호·session_start·session_create)과 그 적용이 이 락 안에서 돌고, 스냅샷도
#: 이 락 안에서 행을 세고 담는다. 요청은 사용자 락(`scoped`) 다음에 이것을 잡고 스냅샷은
#: 이것만 잡으므로 순환이 없다. RLock 인 이유: 같은 요청 안에서 두 번 잡히는 경로가 있다.
PREF_LOCK = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _append(path: Path, rec: dict) -> None:
    """한 줄 추가하고 즉시 flush. 쓰기에 실패해도 서비스는 계속 돌아가야 한다."""
    global _enabled
    if not _enabled:
        return
    try:
        with _lock:
            DIR.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
    except OSError as exc:
        # 볼륨이 읽기 전용이거나 디스크가 찼을 때. 한 번만 알리고 이후엔 조용히 넘어간다.
        _enabled = False
        observe.log("state.write_fail", level=logging.ERROR, path=str(path),
                    error=type(exc).__name__, detail=str(exc)[:200])


def _read(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue          # 쓰다 만 마지막 줄은 건너뛴다


def record_user(user: dict) -> None:
    _append(USERS, user)


def record_preference(user_id: str, card_id: int, prefer: str, session_id: str | None = None) -> None:
    from app import identities
    owner = identities.owner(user_id)
    # New records name both identities explicitly; old rows remain byte-for-byte.
    row = {"user_id": user_id, "card_id": card_id,
           "prefer": prefer, "session_id": session_id, "at": now()}
    if owner and identities._sessions.get(user_id, {}).get('event') == 'session_create':
        row.update(event='preference', user_id=owner, session_id=user_id)
    _append(PREFS, row)


def record_exposure(user_id: str, card_id: int | None, policy: str,
                    batch: int, rank: int, session_id: str | None = None) -> None:
    """카드 한 장을 보여줬다는 기록. 추천 응답 경로에 있으므로 실패해도 조용하다."""
    from app import identities
    row = {"user_id": user_id, "card_id": card_id, "policy": policy,
           "batch": batch, "rank": rank, "session_id": session_id, "at": now()}
    owner = identities.owner(user_id)
    if owner and identities._sessions.get(user_id, {}).get('event') == 'session_create':
        row.update(event='exposure', user_id=owner, session_id=user_id)
    _append(EXPOSURES, row)


def model_id(row):
    """Translate durable identity to the recommendation engine's session key."""
    return row.get('session_id') if row.get('event') in ('preference', 'exposure') else row.get('user_id')


def _engine_row(row):
    return {**row, 'user_id': model_id(row)}


def writable() -> bool:
    """state 디렉터리에 **실제로** 쓸 수 있나 — `/readyz` 가 묻는다.

    권한 비트만 보면 읽기 전용으로 마운트된 볼륨을 놓친다. 그래서 탐침 파일을 한 번
    열었다 지운다(헬스체크는 자주 오지만 파일 하나 여는 값은 무시할 만하다).
    """
    if not _enabled:
        return False
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        probe = DIR / ".probe"
        with probe.open("a", encoding="utf-8"):
            pass
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def record_bookmark(user_id: str, card_id: int, action: str) -> None:
    """찜을 담았다/뺐다. **뺀 것도 남긴다** — 그 줄이 없으면 복원이 지운 찜을 되살린다."""
    _append(BOOKMARKS, {"user_id": user_id, "card_id": card_id,
                        "action": action, "at": now()})


def read_bookmarks():
    """찜 기록을 순서대로 읽는다(복원이 쓴다 — 마지막 줄이 이긴다)."""
    return _read(BOOKMARKS)


def record_direction(user_id: str, store_id: str | None, card_id: int,
                     map_app: str, mode: str) -> None:
    """길찾기를 눌렀다 = **방문 의사**. 3시간 뒤 설문의 대상이 된다.

    `store_id` 를 함께 남기는 이유: `card_id` 는 데이터가 바뀌면 다시 매겨진다
    (`server/README.md`). 조인은 `card_id` 로 해야 하고(노출·선호가 그 키다), 오래
    보관하는 사실은 `store_id` 로 가리켜야 과거 응답이 어긋나지 않는다. 둘 다 남긴다.
    """
    _append(DIRECTIONS, {"user_id": user_id, "store_id": store_id,
                         "card_id": card_id, "map_app": map_app, "mode": mode,
                         "at": now()})


def read_directions():
    """길찾기 기록을 순서대로 읽는다(복원과 설문 대상 판정이 같은 함수를 쓴다)."""
    return _read(DIRECTIONS)


def record_survey(user_id: str, store_id: str | None, card_id: int,
                  visited: bool, answers: dict,
                  hours_after: float | None = None,
                  source: str = "direction") -> None:
    """설문 응답. 추천의 **결과 지표**다 — 노출 로그의 정책 라벨과 조인해서 읽는다.

    `answers` 는 문항이 늘어도 스키마를 바꾸지 않도록 dict 로 둔다. 집계 쪽이 없는 키를
    관용적으로 다뤄야 한다 — 옛 응답에는 나중에 더한 문항이 없다.

    `hours_after` 는 길찾기로부터 몇 시간 뒤에 답했나다. 거절하는 대신 기록해 둔다 —
    엣지에서 막으면 경계(2시간 59분)에 답한 정당한 응답도 잃는다. 대신 집계가 이 값으로
    걸러낼 수 있고, **3시간이 적절한 값인지 데이터로 검증**할 수 있다.

    `source` 는 이 설문 후보가 **어디서 나왔나**다. 지금은 길찾기(`"direction"`) 하나뿐이지만
    나중에 정지 감지(`"stop"`)를 붙이면 후보 소스가 둘이 된다. **지금 남겨 두지 않으면
    그때 과거 응답의 출처를 알 수 없다** — 소급이 안 되는 종류의 정보다.
    """
    _append(SURVEYS, {"user_id": user_id, "store_id": store_id,
                      "card_id": card_id, "visited": visited,
                      "answers": answers, "hours_after": hours_after,
                      "source": source, "at": now()})


def read_surveys():
    """설문 응답을 순서대로 읽는다(설문 중복 방지와 집계가 같은 함수를 쓴다)."""
    return _read(SURVEYS)


def record_survey_photo(user_id: str, store_id: str | None, card_id: int,
                        photo_id: str, action: str, meta: dict | None = None) -> None:
    """설문 사진의 **메타데이터**. 파일 자체는 `media` 볼륨에 있다(`app/media.py`).

    `action` 은 `"add"` / `"remove"` 다. 찜과 같은 방식으로 추가 전용으로 쌓고 기동 때
    순서대로 다시 먹인다 — 마지막 줄이 이긴다. 지운 사진의 줄을 남겨 두는 이유는,
    지우기를 지우기로 기록하지 않으면 재시작 때 되살아나기 때문이다.

    `store_id` 를 함께 남기는 것은 길찾기와 같은 이유다 — `card_id` 는 데이터가 바뀌면
    다시 매겨질 수 있고, 사진은 오래 남는 사실이다.
    """
    rec = {"user_id": user_id, "store_id": store_id, "card_id": card_id,
           "photo_id": photo_id, "action": action, "at": now()}
    if meta:
        rec.update(meta)
    _append(SURVEY_PHOTOS, rec)


def read_survey_photos():
    """설문 사진 기록을 순서대로 읽는다(복원이 쓴다)."""
    return _read(SURVEY_PHOTOS)


def read_preferences():
    """선호 기록을 순서대로 읽는다 (복원과 정책 성적 집계가 같은 함수를 쓴다)."""
    return (_engine_row(row) for row in _read(PREFS) if row.get("event") not in ("session_start", "session_create"))


def read_exposures():
    """노출 기록을 순서대로 읽는다."""
    return (_engine_row(row) for row in _read(EXPOSURES))


def count_preferences() -> int:
    """복원할 선호 기록 수 — `/readyz` 진행률의 분모. `restore` 가 세는 것과 같은 조건이다.

    4,800줄을 한 번 더 읽는 비용은 수 ms 다. 재생 자체(수 분)에 견주면 없다.
    """
    n = 0
    for p in _read(PREFS):
        if p.get("event") in ("session_create", "session_start"):
            continue
        if model_id(p) is not None and p.get("card_id") is not None \
                and p.get("prefer") in ("like", "dislike"):
            n += 1
    return n


def restore(users_registry, logs: list, apply_pref, apply_session=None,
            skip: int = 0) -> dict:
    """기동 시 1회. 사용자 등록부를 되살리고 선호 기록을 순서대로 다시 먹인다.

    `apply_pref(user_id, card_id, prefer)` 는 main 이 넘겨주는 적용 함수다.
    (상태 모듈이 추천 엔진을 알 필요는 없다.)

    `skip` 은 스냅샷(`app/snapshot.py`)이 이미 담고 있는 앞쪽 행 수다. 그 행들은 **적용하지
    않는다** — 세션 이벤트도 선호도 전부 스냅샷 안에 있다. 다만 선호 행은 `logs` 에는
    넣는다(`GET /preferences` 와 개수 지표가 전체를 기대한다). 행 번호는 `_read` 가 낸
    순서다 — 스냅샷이 행을 세는 규칙과 같다(빈 줄·깨진 줄은 양쪽 다 건너뛴다).
    """
    n_users = n_prefs = n_skipped = 0
    for u in _read(USERS):
        if u.get("user_id"):
            users_registry[u["user_id"]] = u
            n_users += 1
    for idx, p in enumerate(_read(PREFS)):
        if idx < skip:
            uid, cid, pref = model_id(p), p.get("card_id"), p.get("prefer")
            if p.get('event') not in ('session_create', 'session_start') \
                    and uid is not None and cid is not None and pref in ("like", "dislike"):
                logs.append(Log(user_id=uid, card_id=cid, prefer=pref,
                                session_id=p.get("session_id")))
                n_skipped += 1
            continue
        if p.get('event') == 'session_create':
            from app.recommendation_sessions import apply_created
            apply_created(p)
            continue
        if p.get('event') == 'session_start':
            if apply_session and p.get('user_id') and p.get('session_id'):
                apply_session(p)
            continue
        uid, cid, pref = model_id(p), p.get("card_id"), p.get("prefer")
        if uid is None or cid is None or pref not in ("like", "dislike"):
            continue
        logs.append(Log(user_id=uid, card_id=cid, prefer=pref, session_id=p.get("session_id")))
        apply_pref(uid, cid, pref)
        n_prefs += 1
    return {"users": n_users, "preferences": n_prefs, "skipped": n_skipped,
            "dir": str(DIR), "enabled": _enabled}
