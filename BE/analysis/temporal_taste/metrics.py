"""Count-based recommendation diagnostics with explicit populations.

All rates are set proportions in a fixed catalogue or a synthetic replay. None
is the probability that a person likes a restaurant. Labels are independent
menu-audit sets; overlap is allowed (a restaurant may offer both styles).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def recommendation_counts(ids: Sequence[str], ranking: Sequence[str],
                          slate: Sequence[str], groups: Mapping[str, Sequence[str]],
                          *, observed: Sequence[str] = (),
                          brands: Mapping[str, str] | None = None,
                          top_k: int = 20) -> dict:
    pool = set(ids)
    if len(pool) != len(ids) or set(ranking) != pool or len(ranking) != len(ids):
        raise ValueError("Ranking must be a permutation of the unique catalogue")
    if len(set(slate)) != len(slate) or not set(slate) <= pool:
        raise ValueError("Slate contains duplicate or unknown stores")
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    seen = set(observed)
    if not seen <= pool:
        raise ValueError("Unknown observed store")
    brands = brands or {}
    seen_brands = {brands.get(s) for s in seen if brands.get(s)}
    unseen = {s for s in pool - seen if not brands.get(s) or brands[s] not in seen_brands}
    top = set(ranking[:top_k])
    chosen = set(slate)
    result = {"catalogue_count": len(pool), "observed_store_count": len(seen),
              "unseen_store_and_brand_count": len(unseen), "top_count": len(top),
              "card_count": len(chosen), "groups": {}}
    for name, raw in groups.items():
        group = set(raw)
        if len(group) != len(raw) or not group <= pool:
            raise ValueError(f"Invalid audit group: {name}")
        available = group & unseen
        hit_top, hit_cards = len(top & group), len(chosen & group)
        new_top, new_cards = len(top & available), len(chosen & available)
        result["groups"][name] = {
            "catalogue_count": len(group),
            "unseen_count": len(available),
            "top_hits": hit_top, "top_share": hit_top / len(top) if top else None,
            "card_hits": hit_cards, "card_share": hit_cards / len(chosen) if chosen else None,
            "unseen_top_hits": new_top,
            "unseen_top_recall": new_top / len(available) if available else None,
            "unseen_card_hits": new_cards,
            "unseen_card_recall": new_cards / len(available) if available else None,
        }
    return result


def pairwise_win_rate(scores: np.ndarray, a: Sequence[int], b: Sequence[int]) -> float | None:
    """Untrained-group pair ordering; return undefined for an empty group."""
    if not len(a) or not len(b):
        return None
    left, right = np.asarray(scores)[list(a)], np.asarray(scores)[list(b)]
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("Comparison scores must be finite")
    delta = left[:, None] - right[None, :]
    return float(np.mean((delta > 0) + .5 * (delta == 0)))
