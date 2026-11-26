"""Exact latent-coordinate diagnostics for hypothetical replay, not API scores.

Coordinates are basis-dependent, unlabeled spectral dimensions. Their norm,
cosine and dot products are invariant to an orthogonal basis change. A centroid
contrast is a reporting reference, never a feature or a ranking constraint.
These vectors reconstruct the common posterior mean. Candidate-specific
leave-store-out predictions have a different posterior for each rated store.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .model import SpectralProfiles, TemporalTasteModel


@dataclass(frozen=True)
class LatentState:
    slow: np.ndarray
    fast: np.ndarray
    total: np.ndarray
    history: np.ndarray
    current: np.ndarray
    intercept: float
    clock: int
    observations: int
    history_observations: int
    reconstruction_max_error: float


def latent_state(model: TemporalTasteModel, profiles: SpectralProfiles,
                 *, history_end_clock: int = 0) -> LatentState:
    """Export learned coordinates without modifying user evidence.

    History/current components partition the *jointly fitted* coefficients, not
    causal effects. Compare with the matched no-history model for that effect.
    A reassessed store belongs to its latest observation's side of the cutoff.
    """
    if tuple(model.ids) != tuple(profiles.ids) or (model.gram is not profiles.gram and not np.allclose(model.gram, profiles.gram, atol=1e-12)):
        raise ValueError("Model and spectral coordinates describe different catalogues")
    if type(history_end_clock) is not int or not 0 <= history_end_clock <= model.clock:
        raise ValueError("History cutoff must be an integer within the model clock")
    fit = model._solve()
    x = profiles.x[fit.indices]
    p = model.parameters
    age = np.exp2(-(model.clock - fit.at) / p.half_life)
    slow = x.T @ (fit.alpha * p.slow_variance)
    fast = x.T @ (fit.alpha * p.fast_variance * age)
    coefficients = fit.alpha * (p.slow_variance + p.fast_variance * age)
    historical = fit.at <= history_end_clock
    history = x[historical].T @ coefficients[historical]
    current = x[~historical].T @ coefficients[~historical]
    total = slow + fast
    reconstructed = profiles.x @ total + fit.intercept
    actual = model.predict().mean
    error = float(np.max(abs(actual - reconstructed), initial=0.))
    if not np.allclose(actual, reconstructed, atol=1e-10, rtol=1e-10):
        raise ArithmeticError("Latent vectors do not reconstruct the model mean")
    for vector in (slow, fast, total, history, current):
        vector.setflags(write=False)
    return LatentState(slow, fast, total, history, current, float(fit.intercept),
                       model.clock, len(fit.indices), int(historical.sum()), error)


def geometry(vector: np.ndarray, reference: np.ndarray) -> dict:
    """Full-dimensional geometry; a zero vector has no defined direction."""
    vector, reference = np.asarray(vector, float), np.asarray(reference, float)
    if vector.shape != reference.shape or vector.ndim != 1:
        raise ValueError("Both vectors must have the same one-dimensional shape")
    if not np.isfinite(vector).all() or not np.isfinite(reference).all():
        raise ValueError("Vectors must be finite")
    norm = float(np.linalg.norm(vector))
    ref_norm = float(np.linalg.norm(reference))
    cosine = float(np.clip(vector @ reference / (norm * ref_norm), -1, 1)) if min(norm, ref_norm) > 1e-12 else None
    return {
        "norm": norm,
        "reference_norm": ref_norm,
        "distance": float(np.linalg.norm(vector - reference)),
        "cosine": cosine,
        "angle_degrees": float(np.degrees(np.arccos(cosine))) if cosine is not None else None,
    }


def reference_contrast(state: LatentState, profiles: SpectralProfiles,
                       groups: Mapping[str, Sequence[str]]) -> dict:
    """Exact group-mean score gap, not an invented semantic coordinate label.

    Group membership comes from an independent menu audit. Unknown/mixed stores
    remain separate in the evaluation. The result measures the relationship
    with these reference restaurants only, not a universal 'Japanese axis'.
    """
    if len(groups) != 2 or any(not group for group in groups.values()):
        raise ValueError("Two nonempty reference groups are required")
    names = list(groups)
    a, b = groups.values()
    if len(set(list(a) + list(b))) != len(a) + len(b):
        raise ValueError("Reference groups must contain distinct stores")
    try:
        direction = profiles.x[[profiles.pos[s] for s in a]].mean(0) - profiles.x[[profiles.pos[s] for s in b]].mean(0)
    except KeyError as exc:
        raise ValueError("Unknown reference store") from exc
    norm = float(np.linalg.norm(direction))
    return {
        "positive_group": names[0], "negative_group": names[1],
        "group_counts": {k: len(v) for k, v in groups.items()},
        "reference_norm": norm,
        "mean_score_gap": float(state.total @ direction),
        "history_score_gap": float(state.history @ direction),
        "current_score_gap": float(state.current @ direction),
        "slow_score_gap": float(state.slow @ direction),
        "fast_score_gap": float(state.fast @ direction),
        "unit_axis_projection": float(state.total @ direction / norm) if norm > 1e-12 else None,
    }
