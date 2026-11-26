"""Disposable review-rank cache; exact source bytes and scoring code identify it.

Only three integer scores are cached. Reviews and their stable tie order still
come from the source files, and store metadata still supplies the final sort key.
Cache failures always fall back to normal scoring, without changing model input.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

from . import expressiveness

log = logging.getLogger(__name__)
FORMAT = 1
MAX_BYTES = 64 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]


def cache_path() -> Path:
    return Path(os.environ.get("MATCHURI_STATE") or (ROOT / "state")) / "review-ranks.cache"


def _scorer_digest() -> str:
    return hashlib.sha256(Path(expressiveness.__file__).read_bytes()).hexdigest()


def source_key(digests: list[str]) -> str | None:
    try:
        identity = [FORMAT, _scorer_digest(), digests]
    except OSError:
        return None
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _rank(value) -> bool:
    if not isinstance(value, list) or len(value) != 3 or any(type(n) is not int for n in value):
        return False
    grade, depth, breadth = value
    return (0 <= grade <= 3 and 0 <= breadth <= 5 and
            (grade == depth == breadth == 0 or
             grade >= 1 and breadth >= 1 and grade + breadth - 1 <= depth <= grade * breadth))


def restore(reviews: dict[str, list[dict]], key: str | None) -> bool:
    if key is None:
        return False
    try:
        path = cache_path()
        if path.stat().st_size > MAX_BYTES:
            return False
        with path.open("rb") as f:
            blob = f.read(MAX_BYTES + 1)
        if len(blob) > MAX_BYTES:
            return False
        header_bytes, separator, body = blob.partition(b"\n")
        header = json.loads(header_bytes)
        if (not separator or not isinstance(header, dict) or header.get("format") != FORMAT
                or header.get("key") != key
                or header.get("sha256") != hashlib.sha256(body).hexdigest()):
            return False
        entries = json.loads(body)
        expected = {(sid, r["review_id"]): r for sid, rows in reviews.items() for r in rows}
        if (not isinstance(entries, list) or len(entries) != len(expected)
                or len(expected) != sum(map(len, reviews.values()))):
            return False
        staged = []
        seen = set()
        for entry in entries:
            if (not isinstance(entry, list) or len(entry) != 3 or not isinstance(entry[0], str)
                    or type(entry[1]) is not int or not _rank(entry[2])):
                return False
            identity = (entry[0], entry[1])
            if identity not in expected or identity in seen:
                return False
            seen.add(identity)
            staged.append((expected[identity], tuple(entry[2])))
        # Validate the entire cache before changing any row. A partially corrupt
        # cache must not leave valid-looking ranks behind for fallback sorting.
        for row, rank in staged:
            row["_expr"] = rank
        log.info("Review rank cache hit: %d reviews", len(staged))
        return True
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return False


def save(reviews: dict[str, list[dict]], key: str | None) -> bool:
    if key is None:
        return False
    temporary = None
    try:
        entries = [[sid, r["review_id"], list(r["_expr"])]
                   for sid, rows in reviews.items() for r in rows]
        if any(not _rank(entry[2]) for entry in entries):
            return False
        body = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
        header = json.dumps({"format": FORMAT, "key": key,
                             "sha256": hashlib.sha256(body).hexdigest()}).encode()
        if len(header) + len(body) + 1 > MAX_BYTES:
            return False
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".review-ranks-", delete=False) as f:
            temporary = Path(f.name)
            f.write(header + b"\n" + body)
        os.replace(temporary, path)
        log.info("Review rank cache saved: %d reviews", len(entries))
        return True
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
