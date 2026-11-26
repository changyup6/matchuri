"""Research-only eligibility anchored to a current-session recommendation slate.

An existing selector first picks a strictly diverse slate from the current-only
posterior. Its weakest conservative score defines the eligible region. The same
selector then uses the unchanged full-history ranking inside that region. There
is no new score-zero, quantile, cuisine rule, or model update in this policy.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations
from numbers import Integral, Real
from typing import Any, Protocol

import numpy as np


class FloorConfig(Protocol):
    count: int
    uncertainty_weight: float


@dataclass(frozen=True)
class ContextFloorSelection:
    slate: dict[str, Any]
    qualified_ids: frozenset[str]
    floor: float | None
    used_context: bool
    fallback_reason: str | None


def _ids_from_slate(slate: Mapping[str, Any], pool: set[str]) -> tuple[str, ...] | None:
    if not isinstance(slate, Mapping):
        return None
    values = slate.get("store_ids")
    if not isinstance(values, (list, tuple)) or any(not isinstance(s, str) for s in values):
        return None
    values = tuple(values)
    if len(set(values)) != len(values) or not set(values) <= pool:
        return None
    return values


def select_context_floor(
    ids: Sequence[str],
    current_mean: np.ndarray,
    current_std: np.ndarray,
    *,
    config: FloorConfig,
    full_slate: Mapping[str, Any],
    current_slate: Mapping[str, Any],
    select_allowed: Callable[[frozenset[str]], Mapping[str, Any]],
    is_diverse: Callable[[str, str], bool],
) -> ContextFloorSelection:
    """Preserve the weakest current-only slate score while using full ranking.

    Both input slates come from the existing selector, with the same configuration
    and candidate universe. ``current_slate`` uses the current session's posterior;
    ``full_slate`` and ``select_allowed`` use the full-history posterior. The caller
    remains responsible for continuous current-session replay and latest labels.

    On use, every chosen score is at least the weakest score in ``current_slate``.
    The original strictly diverse current slate remains a feasible witness inside
    the allowed set. A bounded selector may still fail to find a strict slate in
    its search window; in that case the full result is returned unchanged. This
    score guarantee applies only when ``used_context`` is true. It is not a food
    label, satisfaction, average-slate-utility, or calibrated-confidence guarantee.

    Zero, negative, and tied scores are valid. There is no arbitrary positive-score
    gate. Input arrays/slates/configuration are never changed, and returned slate
    metadata are copied so a caller cannot mutate an input through the result.
    """
    if isinstance(ids, (str, bytes)):
        raise ValueError("Catalogue IDs must be a sequence of strings")
    ids = tuple(ids)
    if not ids or any(not isinstance(s, str) or not s for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("Catalogue IDs must be nonempty and unique")
    pool, pos = set(ids), {sid: i for i, sid in enumerate(ids)}
    count, weight = config.count, config.uncertainty_weight
    if not isinstance(count, Integral) or isinstance(count, (bool, np.bool_)) or count <= 0:
        raise ValueError("Selector count must be a positive integer")
    if (not isinstance(weight, Real) or isinstance(weight, (bool, np.bool_))
            or not np.isfinite(weight) or weight < 0):
        raise ValueError("Uncertainty weight must be finite and nonnegative")
    mean, std = np.asarray(current_mean, dtype=float), np.asarray(current_std, dtype=float)
    if mean.shape != (len(ids),) or std.shape != mean.shape:
        raise ValueError("Current prediction shape must match catalogue IDs")
    if not np.isfinite(mean).all() or not np.isfinite(std).all() or np.any(std < 0):
        raise ValueError("Current predictions must be finite with nonnegative standard deviations")
    with np.errstate(over="ignore", invalid="ignore"):
        scores = mean - weight * std
    if not np.isfinite(scores).all():
        raise ValueError("Conservative current scores must be finite")
    if _ids_from_slate(full_slate, pool) is None:
        raise ValueError("Full fallback slate contains invalid store IDs")

    def fallback(reason, qualified=frozenset(), floor=None):
        return ContextFloorSelection(deepcopy(dict(full_slate)), qualified, floor, False, reason)

    def strict(slate_ids):
        return (slate_ids is not None and len(slate_ids) == count
                and all(is_diverse(a, b) for a, b in combinations(slate_ids, 2)))

    current_ids = _ids_from_slate(current_slate, pool)
    if not strict(current_ids):
        return fallback("current_selector_did_not_return_strictly_diverse_slate")
    floor = float(min(scores[pos[s]] for s in current_ids))
    qualified = frozenset(s for i, s in enumerate(ids) if scores[i] >= floor)
    selected = select_allowed(qualified)
    chosen = _ids_from_slate(selected, pool)
    if not strict(chosen) or not set(chosen) <= qualified:
        return fallback("full_ranking_did_not_return_strictly_diverse_context_slate", qualified, floor)
    return ContextFloorSelection(deepcopy(dict(selected)), qualified, floor, True, None)
