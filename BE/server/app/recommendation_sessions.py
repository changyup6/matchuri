"""Independent recommendation sessions plus adapters for the old rotation API.

Boundaries live in the same append-only timeline as preferences. This preserves
ordering across restarts without changing old rows or requiring model snapshots
on disk. User settings, bookmarks and visits are deliberately outside a session.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import json
import os
import secrets

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app import state, users, identities
from recsys import decision_summary, policy, swipe, taste

_active: dict[str, dict] = {}
_initial: dict[str, dict] = {}
_user_memory: dict[str, dict] = {}


class SessionOut(BaseModel):
    user_id: str
    session_id: str
    swipes: int = Field(description='현재 세션에서 학습한 스와이프 수')


class SessionStart(BaseModel):
    previous_session_id: str | None = Field(None, min_length=1, max_length=64,
        description='알고 있는 현재 세션 ID. 보내면 중복 재시도는 같은 새 세션을 반환합니다.')


def legacy_id(user_id):
    return 's_' + hashlib.sha256(user_id.encode()).hexdigest()[:24]


def explicit_id(user_id):
    return _active.get(user_id, {}).get("session_id")


def current_id(user_id):
    meta = identities._sessions.get(user_id, {})
    if meta.get('event') == 'session_create' or meta.get('legacy_origin'):
        return user_id
    return _active.get(user_id, {}).get('session_id', legacy_id(user_id))


def lock_for(user_id):
    return policy._feedback_lock({'user_id': user_id})


def require_current(user_id, session_id):
    if session_id is not None and session_id != current_id(user_id):
        raise HTTPException(409, 'session changed; fetch the current session and discard old cards')


def scoped(fn):
    """Serialize synchronous API work with session rotation for this user only."""
    signature = inspect.signature(fn)
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs).arguments
        log = bound.get('log')
        uid = bound.get('user_id') or (getattr(log, 'user_id', None) if log else None)
        if not isinstance(uid, str):
            return fn(*args, **kwargs)
        users.validate_id(uid)
        if uid in identities._people:
            raise HTTPException(400, 'use /user/{user_id}/session/{session_id} for recommendations')
        with lock_for(identities.owner(uid) or uid), lock_for(uid):
            supplied = bound.get('session_id', getattr(log, 'session_id', None))
            if supplied is not None and isinstance(supplied, str):
                require_current(uid, supplied)
            return fn(*args, **kwargs)
    return wrapped


def info(user_id):
    sess = policy.sessions().get(user_id)
    return {'user_id': user_id, 'session_id': current_id(user_id),
            'swipes': sess['taste']['n'] if sess else 0}


def initial_taste(user_id):
    return copy.deepcopy(_initial.get(user_id) or taste._new_session())


def apply_boundary(row):
    uid = row['user_id']
    with lock_for(uid):
        previous = policy.session_for(uid)
        # Old rotation overwrote the only model. Preserve the previous segment
        # under its own ID so the new nested API can still address its history.
        archived_id = row.get('previous_session_id') or current_id(uid)
        archived = copy.deepcopy(previous)
        archived['user_id'] = archived_id
        policy._sessions[archived_id] = archived
        taste._sessions[archived_id] = archived['taste']
        swipe._sessions[archived_id] = archived['legacy']
        _initial[archived_id] = initial_taste(uid)
        if users.get(uid):
            users.all_users().setdefault(archived_id, {**users.get(uid), 'user_id': archived_id})
        if identities.owner(uid):
            identities.register_session({'user_id': identities.owner(uid),
                'session_id': archived_id, 'created_at': None,
                'previous_session_id': None, 'legacy_origin': uid})
        fresh_taste = taste.start_from(previous['taste'])
        fresh_legacy = swipe._new_session()
        share = taste.MEMORY_SHARE if previous['legacy']['n'] else 1.0
        fresh_legacy['m'] += share * previous['legacy']['m']
        fresh_legacy['prior_m'] = fresh_legacy['m'].copy()
        taste._sessions[uid] = fresh_taste
        swipe._sessions[uid] = fresh_legacy
        policy._sessions[uid] = {'user_id': uid, 'batch': 0,
                                'legacy': fresh_legacy, 'taste': fresh_taste}
        _active[uid] = dict(row)
        _initial[uid] = copy.deepcopy(fresh_taste)
        with policy._lock:
            for key in [key for key in policy._exposed if key[0] == uid]:
                policy._exposed[(archived_id, key[1])] = policy._exposed[key]
                del policy._exposed[key]


def start(user_id, previous_session_id=None):
    if identities._sessions.get(user_id, {}).get('event') == 'session_create':
        raise HTTPException(409, 'create a new session under /user/{user_id}/session')
    with lock_for(user_id):
        active = _active.get(user_id, {})
        if previous_session_id and previous_session_id == active.get('previous_session_id'):
            return info(user_id)  # response was lost; do not rotate twice
        require_current(user_id, previous_session_id)
        row = {'event': 'session_start', 'user_id': user_id,
               'session_id': 's_' + secrets.token_hex(12),
               'previous_session_id': current_id(user_id), 'at': state.now()}
        # A successful reset must survive restart; best-effort logging is insufficient.
        # 파일 쓰기와 메모리 적용을 PREF_LOCK 으로 묶는다 — 그 사이에 스냅샷이 찍히면
        # 절단점이 어긋난다(app/snapshot.py 머리말).
        with state.PREF_LOCK:
            try:
                with state._lock:
                    state.PREFS.parent.mkdir(parents=True, exist_ok=True)
                    with state.PREFS.open('a', encoding='utf-8') as f:
                        f.write(json.dumps(row, ensure_ascii=False) + '\n')
                        f.flush()
                        os.fsync(f.fileno())
            except OSError as exc:
                raise HTTPException(503, 'could not save session; retry') from exc
            apply_boundary(row)
        return info(user_id)


def remember(session_id, store_id, like):
    """All this person's feedback, in arrival order, independent of session resets."""
    uid = identities.owner(session_id)
    if uid is None:
        return
    memory = _user_memory.setdefault(uid, {'taste': taste._new_session(),
                                          'legacy': swipe._new_session()})
    before = memory['taste']['n']
    taste.record(memory['taste'], store_id, like)
    if memory['taste']['n'] > before:
        # 적중 누적을 계정 기억에서도 접는다. `apply_created`(POST /user/{uid}/session)가
        # 이 모델의 누적으로 **새 세션의 배속**을 정하기 때문이다 — 접지 않으면 누적이
        # 영원히 0 이라 배속이 늘 1.0 이고, 앱이 실제로 타는 경로(`createSession`)에서만
        # 승계가 죽는다. 새 세션의 점수 자체는 어느 경로로도 0 에서 시작한다.
        # 조건도 자리도 `policy.record` 와 같다 — 학습이 실제로 일어난 장에서만
        # 정확히 한 번(난수 없음). `remember()` 는 재생 경로(`main._apply`)에도
        # 있으므로 `preferences.jsonl` 을 다시 먹이면 같은 누적이 나온다.
        # `judge_ready=False`: 이 모델의 표시값은 아무 데도 안 나가고 새 세션이
        # 물려받는 것은 배속뿐이며 그 재료는 누적이다. 여덟 조건 재기를 건너뛴다.
        decision_summary.advance_display(memory['taste'], judge_ready=False)
    swipe.record(memory['legacy'], store_id, like)


def apply_created(row):
    """Replay at the creation point; later feedback never changes the initial prior."""
    sid, uid = row['session_id'], row['user_id']
    identities.register_session(row)
    previous = _user_memory.get(uid)
    fresh_taste = taste.start_from(previous['taste'] if previous else None)
    fresh_legacy = swipe._new_session()
    if previous:
        fresh_legacy['m'] += taste.MEMORY_SHARE * previous['legacy']['m']
        fresh_legacy['prior_m'] = fresh_legacy['m'].copy()
    taste._sessions[sid] = fresh_taste
    swipe._sessions[sid] = fresh_legacy
    policy._sessions[sid] = {'user_id': sid, 'batch': 0,
                             'legacy': fresh_legacy, 'taste': fresh_taste}
    _initial[sid] = copy.deepcopy(fresh_taste)
    _active[sid] = {'session_id': sid}
    users.all_users().setdefault(sid, {'user_id': sid, 'name': identities.require(uid)['name'],
        'created_at': row['created_at'], 'location': None, 'filter': None})


def independent_info(user_id, session_id):
    identities.require_session(user_id, session_id)
    model = policy.sessions().get(session_id)
    return {'user_id': user_id, 'session_id': session_id,
            'swipes': model['taste']['n'] if model else 0}


def create_independent(user_id, request_id=None):
    identities.require(user_id)
    with lock_for(user_id):
        owned = identities.owned(user_id)
        if request_id:
            for row in owned:
                if row.get('request_id') == request_id:
                    return independent_info(user_id, row['session_id'])
        row = {'event': 'session_create', 'user_id': user_id,
               'session_id': 's_' + secrets.token_hex(12), 'created_at': state.now(),
               'request_id': request_id}
        with state.PREF_LOCK:          # 파일 + 적용을 한 절단점 안에 (app/snapshot.py)
            identities.save(state.PREFS, row)
            apply_created(row)
        return independent_info(user_id, row['session_id'])
