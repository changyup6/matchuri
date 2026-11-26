"""Durable people and ownership of independent recommendation sessions.

Old user IDs are session IDs, never evidence that two records are one person.
Migration creates one separate person per old ID without rewriting source logs.
"""
import copy
import hashlib
import json
import os
import secrets
import threading

from fastapi import HTTPException

from app import state, users

_lock = threading.RLock()
_people: dict[str, dict] = {}
_sessions: dict[str, dict] = {}


def save(path, row):
    try:
        with state._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
                f.flush()
                os.fsync(f.fileno())
    except OSError as exc:
        raise HTTPException(503, 'could not save identity/session; retry') from exc


def create(name=None):
    with _lock:
        row = {'user_id': 'usr_' + secrets.token_hex(12), 'name': name,
               'created_at': state.now()}
        save(state.DIR / 'people.jsonl', row)
        _people[row['user_id']] = row
        return public(row)


def public(row):
    return {k: row.get(k) for k in ('user_id', 'name', 'created_at')}


def require(user_id):
    users.validate_id(user_id)
    row = _people.get(user_id)
    if row is None:
        raise HTTPException(404, 'user not found')
    return public(row)


def owner(session_id):
    return _sessions.get(session_id, {}).get('user_id')


def name_of(user_id):
    """Resolve review authors from people, preserving legacy profile names."""
    person = _people.get(user_id)
    return person.get('name') if person is not None else users.name_of(user_id)


def profile_key(user_id):
    """Keep old bookmarks/visits under their original storage key during migration."""
    return _people.get(user_id, {}).get('legacy_session_id', user_id)


def bookmark_key(session_id):
    if session_id in _people:
        return profile_key(session_id)
    return profile_key(owner(session_id)) if owner(session_id) else session_id


def require_session(user_id, session_id):
    require(user_id)
    users.validate_id(session_id)
    if owner(session_id) != user_id:
        raise HTTPException(404, 'session not found for this user')
    return session_id


def register_session(row):
    with _lock:
        _sessions[row['session_id']] = copy.deepcopy(row)


def owned(user_id):
    with _lock:
        return [dict(row) for row in _sessions.values() if row['user_id'] == user_id]


def ensure_legacy(session_id, record=None):
    """Called on boot and by old clients creating a session after deployment."""
    with _lock:
        if owner(session_id):
            return owner(session_id)
        record = record or {}
        uid = 'usr_' + hashlib.sha256(('legacy:' + session_id).encode()).hexdigest()[:24]
        if uid not in _people:
            person = {'user_id': uid, 'name': record.get('name'),
                      'created_at': record.get('created_at') or state.now(),
                      'legacy_session_id': session_id}
            save(state.DIR / 'people.jsonl', person)
            _people[uid] = person
        register_session({'user_id': uid, 'session_id': session_id,
                          'created_at': record.get('created_at'), 'previous_session_id': None})
        return uid


def restore():
    """Load ownership before model replay, including all legacy session boundaries."""
    _people.clear()
    _sessions.clear()
    for row in state._read(state.DIR / 'people.jsonl'):
        if row.get('user_id'):
            _people[row['user_id']] = row
    rows = list(state._read(state.PREFS))
    new_ids = {r['session_id'] for r in rows if r.get('event') == 'session_create'}
    archived_ids = {r.get('previous_session_id') for r in rows if r.get('event') == 'session_start'}
    records = {r['user_id']: r for r in state._read(state.USERS) if r.get('user_id')}
    for row in rows:
        if row.get('user_id') and row.get('event') != 'session_create':
            records.setdefault(state.model_id(row), {})
    for sid, record in records.items():
        if sid not in new_ids and sid not in archived_ids:
            ensure_legacy(sid, record)
    for row in rows:
        if row.get('event') == 'session_create':
            if row['user_id'] not in _people:
                raise RuntimeError('session owner missing from people.jsonl')
            register_session(row)
        elif row.get('event') == 'session_start':
            from app.recommendation_sessions import legacy_id
            sid = row.get('previous_session_id') or legacy_id(row['user_id'])
            register_session({'user_id': owner(row['user_id']), 'session_id': sid,
                              'created_at': None, 'previous_session_id': None,
                              'legacy_origin': row['user_id']})
