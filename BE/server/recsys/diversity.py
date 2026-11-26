"""Compare vector modalities on their observed pair distributions.

Each value is an empirical midrank percentile among finite, distinct unordered
store pairs in the same artifact. It is not a duplicate probability or a human
judgment of substitutability. Raw similarity matrices remain unchanged.

Redundancy keeps the largest observed percentile, so a close match in one
modality cannot disappear inside an average with weaker matches. At least two
observed modalities are required; missing evidence is never replaced with zero.
Personal fit weights are not reused: learning which signals predict a person's
likes does not establish which signals make two options interchangeable.

Artifacts are immutable after loading. Cache one artifact by identity, including
read-only float32 matrices, and rebuild once when the artifact object changes.
"""
from __future__ import annotations

import threading
from types import MappingProxyType

import numpy as np


MODALITIES = ("menu", "text", "review", "food")
_lock = threading.Lock()
_cache_art = None
_cache_values = None
_cache_redundancy = None


def _percentiles(raw, upper):
    """Use each unordered pair once; ties receive their shared midrank."""
    raw = np.asarray(raw)
    reference = raw[upper]
    reference = reference[np.isfinite(reference)]
    result = np.full(raw.shape, np.nan, dtype=np.float32)
    if reference.size:
        knots, counts = np.unique(reference, return_counts=True)
        cumulative = np.concatenate(([0], np.cumsum(counts)))
        known = np.isfinite(raw)
        np.fill_diagonal(known, False)
        values = raw[known]
        left = np.searchsorted(knots, values, side="left")
        right = np.searchsorted(knots, values, side="right")
        result[known] = (cumulative[left] + cumulative[right]) / (2 * reference.size)
    result.setflags(write=False)
    return result


def _build(art):
    size = len(art.ids)
    upper = np.triu_indices(size, 1)
    values = {}
    for kind in MODALITIES:
        raw = getattr(art, "sim_" + kind)
        if np.shape(raw) != (size, size):
            raise ValueError("similarity matrix shape must match artifact IDs")
        values[kind] = _percentiles(raw, upper)

    observed = np.zeros((size, size), dtype=np.uint8)
    redundancy = np.full((size, size), np.nan, dtype=np.float32)
    for matrix in values.values():
        observed += np.isfinite(matrix)
        # fmax retains known evidence when the other modality is missing.
        np.fmax(redundancy, matrix, out=redundancy)
    redundancy[observed < 2] = np.nan
    redundancy.setflags(write=False)
    return MappingProxyType(values), redundancy


def _cached(art):
    global _cache_art, _cache_values, _cache_redundancy
    with _lock:
        if _cache_art is not art:
            values, redundancy = _build(art)
            _cache_art, _cache_values, _cache_redundancy = art, values, redundancy
        return _cache_values, _cache_redundancy


def matrices(art):
    """Read-only modality -> NxN percentile matrices; missing/diagonal are NaN."""
    return _cached(art)[0]


def redundancy_matrix(art):
    """Read-only NxN max percentile; fewer than two observations yield NaN."""
    return _cached(art)[1]


def _number(value):
    return float(value) if np.isfinite(value) else None


def pair_values(art, a, b):
    """Modality percentiles for two pool indices, with None for missing evidence."""
    return {kind: _number(matrix[a, b]) for kind, matrix in matrices(art).items()}


def pair_redundancy(art, a, b):
    """Largest observed percentile, or None without two observed modalities."""
    return _number(redundancy_matrix(art)[a, b])
