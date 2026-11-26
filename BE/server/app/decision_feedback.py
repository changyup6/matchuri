"""Issued decision slates and explicit outcomes, separate from taste labels.

Keep the latest 100 proposals per user. GET may register a new ready proposal,
but repeated reads do not append events. An outcome is first-write-wins and
retries are idempotent. Store IDs survive catalogue/card-ID remapping.
"""
import copy
import hashlib
import json
import os
import threading

from app import state

MAX_PROPOSALS = 100
_lock = threading.RLock()
_offers: dict[str, dict[str, dict]] = {}


def scope_key(context, position, session_id=None):
    # GPS refreshes must not erase a continue action. Filters define the intent;
    # current location still applies normally to eligibility and ranking.
    data = {key: context.get(key) for key in ('categories', 'tags', 'within_m')}
    if session_id is not None:
        data['session_id'] = session_id
    for key in ('categories', 'tags'):
        data[key] = sorted(data.get(key, []))
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:20]


def _apply(row):
    uid, pid = row['user_id'], row['proposal_id']
    offers = _offers.setdefault(uid, {})
    if row['event'] == 'offered':
        offers.setdefault(pid, row)
        while len(offers) > MAX_PROPOSALS:
            offers.pop(next(iter(offers)))
    elif row['event'] == 'outcome' and pid in offers:
        offers[pid].setdefault('outcome', row)


def _save(row):
    # A success response must mean durable acceptance, unlike best-effort logs.
    with state._lock:
        state.DIR.mkdir(parents=True, exist_ok=True)
        with (state.DIR / 'decisions.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
    _apply(row)


def offer(user_id, scope, cards):
    ids = sorted(cards.values())
    pid = hashlib.sha256(json.dumps([user_id, scope, ids]).encode()).hexdigest()[:20]
    with _lock:
        if pid not in _offers.get(user_id, {}):
            _save({'event': 'offered', 'user_id': user_id, 'proposal_id': pid,
                   'scope': scope, 'store_ids': ids,
                   'cards': {str(cid): sid for cid, sid in cards.items()},
                   'at': state.now()})
    return pid


def record(user_id, proposal_id, action, card_id):
    with _lock:
        offer = _offers.get(user_id, {}).get(proposal_id)
        if offer is None:
            raise KeyError('proposal not found or expired; fetch decision again')
        if action == 'select' and str(card_id) not in offer['cards']:
            raise ValueError('card_id must belong to this proposal')
        existing = offer.get('outcome')
        if existing:
            if (existing['action'], existing['card_id']) != (action, card_id):
                raise RuntimeError('proposal already has a different outcome')
            return
        _save({'event': 'outcome', 'user_id': user_id, 'proposal_id': proposal_id,
               'action': action, 'card_id': card_id,
               'store_id': offer['cards'].get(str(card_id)), 'at': state.now()})


def declined(user_id, scope):
    with _lock:
        return [list(o['store_ids']) for o in _offers.get(user_id, {}).values()
                if o['scope'] == scope and o.get('outcome', {}).get('action') == 'continue']


def restore():
    with _lock:
        _offers.clear()
        for row in state._read(state.DIR / 'decisions.jsonl'):
            if row.get('event') in ('offered', 'outcome') and row.get('user_id') and row.get('proposal_id'):
                _apply(copy.deepcopy(row))
