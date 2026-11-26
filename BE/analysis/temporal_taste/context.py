"""Research-only current-context eligibility; no runtime or food-label imports.

The caller supplies the *current session's* posterior, accumulated continuously
across its phases with latest-store reassessments. That posterior defines an
eligible region using the existing selector configuration. A supplied selector
ranks that region with the unchanged full-history posterior. If it cannot return
the requested number of strictly diverse choices, the original full slate is
returned. This helper neither learns preferences nor supplies an API policy.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations
from numbers import Integral, Real
from typing import Any, Protocol

import numpy as np


class SelectorConfig(Protocol):
    """Read these values from the existing selector; do not add new defaults."""

    count: int
    fit_quantile: float
    uncertainty_weight: float


@dataclass(frozen=True)
class ContextSelection:
    slate: dict[str, Any]
    qualified_ids: frozenset[str]
    cutoff: float
    used_context: bool
    fallback_reason: str | None


def _slate_ids(slate: Mapping[str, Any], pool: set[str]) -> tuple[str, ...]:
    if not isinstance(slate, Mapping):
        raise ValueError("Selector slate must be a mapping")
    values = slate.get("store_ids")
    if not isinstance(values, (tuple, list)):
        raise ValueError("Selector slate must provide a store_ids sequence")
    if any(not isinstance(s, str) for s in values):
        raise ValueError("Slate store IDs must be strings")
    ids = tuple(values)
    if len(set(ids)) != len(ids) or not set(ids) <= pool:
        raise ValueError("Slate contains duplicate or unknown stores")
    return ids


def select_context(
    ids: Sequence[str],
    current_mean: np.ndarray,
    current_std: np.ndarray,
    current_observed: Sequence[str],
    *,
    config: SelectorConfig,
    full_slate: Mapping[str, Any],
    select_allowed: Callable[[frozenset[str]], Mapping[str, Any]],
    is_diverse: Callable[[str, str], bool],
) -> ContextSelection:
    """Apply the frozen eligibility experiment to an already computed full slate.

    ``current_observed`` includes only stores answered in this session. A store
    answered solely in an earlier session stays in the quantile reference pool.
    Duplicate current IDs (reassessments) do not change that reference pool; the
    supplied predictions must already reflect their latest feedback and time.

    ``select_allowed`` receives the eligible IDs and must preserve the existing
    full-history scores, ordering, and diversity policy. Food/category labels
    have no role here. Neither input arrays, input slate, nor configuration are
    changed. Returned slate data are detached copies, including on fallback.
    """
    if isinstance(ids, (str, bytes)):
        raise ValueError("Catalogue IDs must be a sequence of strings")
    ids = tuple(ids)
    if not ids or any(not isinstance(s, str) or not s for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("Catalogue IDs must be nonempty and unique")
    pool = set(ids)
    if isinstance(current_observed, (str, bytes)):
        raise ValueError("Current observations must be a sequence of IDs")
    current_observed = tuple(current_observed)
    if any(not isinstance(s, str) for s in current_observed) or not set(current_observed) <= pool:
        raise ValueError("Unknown current observation")
    observed = set(current_observed)
    count, quantile, weight = config.count, config.fit_quantile, config.uncertainty_weight
    if not isinstance(count, Integral) or isinstance(count, (bool, np.bool_)) or count <= 0:
        raise ValueError("Selector count must be a positive integer")
    if (not isinstance(quantile, Real) or isinstance(quantile, (bool, np.bool_))
            or not np.isfinite(quantile) or not 0 <= quantile <= 1):
        raise ValueError("Selector quantile must lie between zero and one")
    if (not isinstance(weight, Real) or isinstance(weight, (bool, np.bool_))
            or not np.isfinite(weight) or weight < 0):
        raise ValueError("Uncertainty weight must be finite and nonnegative")
    mean, std = np.asarray(current_mean, dtype=float), np.asarray(current_std, dtype=float)
    if mean.shape != (len(ids),) or std.shape != mean.shape:
        raise ValueError("Current prediction shape must match catalogue IDs")
    if not np.isfinite(mean).all() or not np.isfinite(std).all() or np.any(std < 0):
        raise ValueError("Current predictions must be finite with nonnegative standard deviations")
    _slate_ids(full_slate, pool)
    with np.errstate(over="ignore", invalid="ignore"):
        score = mean - weight * std
    if not np.isfinite(score).all():
        raise ValueError("Conservative current scores must be finite")
    reference = [i for i, sid in enumerate(ids) if sid not in observed] or list(range(len(ids)))
    with np.errstate(over="ignore", invalid="ignore"):
        cutoff = float(np.quantile(score[reference], quantile))
    if not np.isfinite(cutoff):
        raise ValueError("Current eligibility cutoff must be finite")
    qualified = frozenset(s for i, s in enumerate(ids) if score[i] >= cutoff and mean[i] > 0)

    def fallback(reason: str) -> ContextSelection:
        return ContextSelection(deepcopy(dict(full_slate)), qualified, cutoff, False, reason)

    if not observed:
        return fallback("no_current_evidence")
    if len(qualified) < count:
        return fallback("fewer_than_requested_current_qualified")
    selected = select_allowed(qualified)
    chosen = _slate_ids(selected, pool)
    if not set(chosen) <= qualified:
        raise ValueError("Selector returned a store outside the allowed context")
    if len(chosen) != count or not all(is_diverse(a, b) for a, b in combinations(chosen, 2)):
        return fallback("context_selector_did_not_return_strictly_diverse_slate")
    return ContextSelection(deepcopy(dict(selected)), qualified, cutoff, True, None)
