"""Exact review ordering through cache hits, stale inputs and cache failures."""
import hashlib
import json
import os

import pytest

from recsys import expressiveness, review_rank_cache as cache, store


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCHURI_STATE", str(tmp_path / "state"))
    for attr in ("_stores", "_reviews", "_menus", "_photos", "_mt"):
        monkeypatch.setattr(store, attr, {})
    for attr, name in [("STORES", "stores.jsonl"), ("REVIEWS", "reviews.jsonl"),
                       ("MENUS", "menus.jsonl"), ("PHOTOS", "photos.jsonl")]:
        monkeypatch.setattr(store, attr, tmp_path / name)
    store.STORES.write_text(json.dumps({"store_id": "s", "review_avg": 4}) + "\n")
    rows = [
        {"store_id": "s", "review_id": 1, "content": "맛있어요", "rating": 4},
        {"store_id": "s", "review_id": 2, "content": "국물이 걸쭉해서 계란이 퍽퍽함", "rating": 2},
        {"store_id": "s", "review_id": 3, "content": "맛있어요", "rating": 2},
    ]
    store.REVIEWS.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")
    naver = tmp_path / "reviews_naver.jsonl"
    naver.write_text(json.dumps({"store_id": "s", "review_id": -1,
                                "content": "커피가 연해요", "rating": 4}, ensure_ascii=False) + "\n")
    calls = []
    original = expressiveness.rank_key

    def ranked(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(expressiveness, "rank_key", ranked)
    return calls


def _result():
    return [(sid, r["review_id"], r["_expr"])
            for sid, rows in store._reviews.items() for r in rows]


def _reload():
    store._mt.clear()
    store.load()
    return _result()


def _edit_cache(change):
    header_bytes, _, body = cache.cache_path().read_bytes().partition(b"\n")
    header, rows = json.loads(header_bytes), json.loads(body)
    change(rows)
    body = json.dumps(rows).encode()
    header["sha256"] = hashlib.sha256(body).hexdigest()
    cache.cache_path().write_bytes(json.dumps(header).encode() + b"\n" + body)


def test_warm_cache_preserves_all_ranks_and_review_order_without_rescoring(catalogue):
    expected = _reload()
    assert len(catalogue) == 4
    assert cache.cache_path().exists()
    assert _reload() == expected
    assert len(catalogue) == 4
    # Cache record order is irrelevant: IDs apply ranks to original source rows,
    # preserving stable source order when all tie-break values are equal.
    _edit_cache(lambda rows: rows.reverse())
    assert _reload() == expected
    assert len(catalogue) == 4


def test_raw_bytes_invalidate_even_with_identical_file_size_and_mtime(catalogue):
    _reload()
    previous = store.REVIEWS.stat()
    old_size = previous.st_size
    store.REVIEWS.write_text(store.REVIEWS.read_text().replace("맛있어요", "맛없어요"))
    assert store.REVIEWS.stat().st_size == old_size
    os.utime(store.REVIEWS, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    _reload()
    assert len(catalogue) == 8


def test_naver_bytes_and_scorer_changes_each_invalidate(catalogue, monkeypatch):
    _reload()
    naver = store.REVIEWS.parent / "reviews_naver.jsonl"
    naver.write_text(naver.read_text().replace("커피가 연해요", "빵이 촉촉해요"))
    _reload()
    assert len(catalogue) == 8
    monkeypatch.setattr(cache, "_scorer_digest", lambda: "new scoring rules")
    _reload()
    assert len(catalogue) == 12


def test_cached_ranks_still_use_current_store_average_for_ties(catalogue):
    before = _reload()
    assert [rid for _, rid, _ in before].index(1) < [rid for _, rid, _ in before].index(3)
    store.STORES.write_text(json.dumps({"store_id": "s", "review_avg": 2}) + "\n")
    after = _reload()
    assert [rid for _, rid, _ in after].index(3) < [rid for _, rid, _ in after].index(1)
    assert len(catalogue) == 4


@pytest.mark.parametrize("damage", [
    lambda rows: rows[-1].__setitem__(1, 999),
    lambda rows: rows.__setitem__(-1, rows[0]),
    lambda rows: rows.pop(),
    lambda rows: rows[-1].__setitem__(2, [4, 5, 2]),
    lambda rows: rows[-1].__setitem__(2, [True, 1, 1]),
    lambda rows: rows[-1].__setitem__(2, [3, 1, 1]),
])
def test_invalid_cache_is_rejected_before_any_rank_is_applied(catalogue, damage):
    expected = _reload()
    _edit_cache(damage)
    header = json.loads(cache.cache_path().read_bytes().split(b"\n", 1)[0])
    fresh = {sid: [{k: v for k, v in row.items() if k != "_expr"} for row in rows]
             for sid, rows in store._reviews.items()}
    assert cache.restore(fresh, header["key"]) is False
    assert all("_expr" not in row for rows in fresh.values() for row in rows)
    assert _reload() == expected
    assert len(catalogue) == 8


def test_corrupt_body_checksum_falls_back_to_exact_scoring(catalogue):
    expected = _reload()
    cache.cache_path().write_bytes(cache.cache_path().read_bytes() + b" ")
    assert _reload() == expected
    assert len(catalogue) == 8


def test_cache_write_failure_does_not_break_loading_or_leave_temporary_files(catalogue, monkeypatch):
    def fail(*args):
        raise PermissionError("read-only cache")
    monkeypatch.setattr(cache.os, "replace", fail)
    result = _reload()
    assert len(result) == 4
    assert len(catalogue) == 4
    assert not cache.cache_path().exists()
    assert not list(cache.cache_path().parent.glob(".review-ranks-*"))
    assert _reload() == result
    assert len(catalogue) == 8


def test_raw_file_hash_covers_invalid_and_empty_lines(tmp_path):
    raw = b'\n{"store_id":"s","review_id":1}\r\nbroken\n'
    path = tmp_path / "reviews.jsonl"
    path.write_bytes(raw)
    digest = hashlib.sha256()
    assert len(list(store._read_jsonl(path, hasher=digest))) == 1
    assert digest.hexdigest() == hashlib.sha256(raw).hexdigest()
