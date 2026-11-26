"""Research candidate with a proper intercept prior; v1 remains unchanged.

The temporal/spatial kernel is identical to v1. The single change is replacing
the unpenalized intercept with b ~ N(0, R), where R is the existing observation
variance. This is one neutral pseudo-observation on the global intercept, not
a cuisine-dependent rule or a selected mixing percentage. It prevents an
all-positive history from being fitted exactly by a constant with zero taste
coefficients. The prior is a fixed modeling assumption, not a calibrated fact.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from .model import Prediction, SpectralProfiles, TemporalTasteModel, _Fit, _readonly


class RegularizedTemporalTasteModel(TemporalTasteModel):
    """Same continuous preference model with a finite, neutral intercept prior.

    ``_solve()`` retains v1's alpha/intercept decomposition. With feature rows X
    the slow vector is slow_variance * X_obs.T @ alpha, and the fast vector is
    fast_variance * X_obs.T @ (alpha * decay_to_current_clock). Their sum plus
    the intercept reconstructs predictions before candidate-specific deletion.

    Cold ``identified=True`` means the prior makes variance mathematically
    finite. It does NOT mean the model has learned a user's taste. Evidence is
    still empty and the prediction mean is zero. Uncertainty is latent Gaussian
    variance, not a calibrated confidence or satisfaction probability.
    """

    SNAPSHOT_VERSION = 2
    MODEL_KIND = "temporal_taste_regularized_intercept"

    @property
    def intercept_prior_variance(self) -> float:
        """Tie the intercept prior to existing R; no additional tuned scalar."""
        return self.parameters.observation_variance

    def _fit(self, indices, at, y, inverse) -> _Fit:
        prior_precision = 1. / self.intercept_prior_variance
        if not len(y):
            return _Fit(indices, at, y, inverse, np.zeros(0), 0.,
                        np.zeros(0), prior_precision)
        u = inverse @ np.ones(len(y))
        precision = float(u.sum()) + prior_precision
        if not np.isfinite(precision) or precision <= 0:
            raise ArithmeticError("Invalid regularized-intercept precision")
        vy = inverse @ y
        intercept = float(vy.sum() / precision)
        return _Fit(indices, at, y, inverse, vy - intercept * u,
                    intercept, u, precision)

    def predict(
        self,
        store_ids: Sequence[str] | None = None,
        *, leave_store_out: bool = False,
        exclude_stores: Sequence[str] = (),
    ) -> Prediction:
        """Exact posterior at the current clock, including full-store deletion.

        Integrating out b is equivalent to adding R * 11.T to the covariance.
        The implementation keeps the temporal coefficients and intercept
        separate for vector diagnostics. Removing the last observation returns
        the cold prior, without changing the current clock or remaining times.
        """
        if isinstance(store_ids, (str, bytes)) or isinstance(exclude_stores, (str, bytes)):
            raise ValueError("Query and exclusion IDs must be sequences of strings")
        try:
            indices = (np.arange(len(self.ids)) if store_ids is None
                       else np.array([self.pos[s] for s in store_ids], dtype=int))
            excluded = {self.pos[s] for s in exclude_stores}
        except (KeyError, TypeError) as exc:
            raise ValueError("Unknown query or excluded store ID") from exc
        fit = self._excluded_fit(excluded)
        p = self.parameters
        age_kernel = p.slow_variance + p.fast_variance * 2. ** (-(self.clock - fit.at) / p.half_life)
        cross = self.gram[np.ix_(indices, fit.indices)] * age_kernel[None, :]
        mean = cross @ fit.alpha + fit.intercept
        prior = np.diag(self.gram)[indices] * (p.slow_variance + p.fast_variance)
        vk = cross @ fit.inverse
        residual = 1. - cross @ fit.inverse_ones
        variance = prior - np.einsum("ij,ij->i", vk, cross) + residual * residual / fit.ones_precision

        if leave_store_out:
            observation_pos = {i: j for j, i in enumerate(fit.indices)}
            rows = np.array([j for j, i in enumerate(indices) if i in observation_pos], dtype=int)
            columns = np.array([observation_pos[indices[j]] for j in rows], dtype=int)
            if len(rows):
                # P is the inverse covariance after integrating out the proper
                # constant feature. Unlike v1 it remains positive for n=1.
                u, precision = fit.inverse_ones, fit.ones_precision
                projected = fit.inverse - np.outer(u, u) / precision
                diagonal = np.diag(projected)[columns]
                if not np.isfinite(diagonal).all() or np.any(diagonal <= 0):
                    raise ArithmeticError("Invalid leave-store-out precision")
                weights = (np.einsum("ij,ji->i", cross[rows], projected[:, columns])
                           + u[columns] / precision)
                mean[rows] -= weights * fit.alpha[columns] / diagonal
                variance[rows] += weights * weights / diagonal
        if not np.isfinite(mean).all() or not np.isfinite(variance).all():
            raise ArithmeticError("Nonfinite regularized temporal prediction")
        if np.any(variance < -1e-9 * max(1., float(prior.max(initial=0.)))):
            raise ArithmeticError("Negative predictive variance")
        return Prediction(_readonly(mean), _readonly(np.sqrt(np.maximum(variance, 0.))),
                          _readonly(np.ones(len(indices), dtype=bool)))

    def snapshot(self) -> dict:
        return {**super().snapshot(), "version": self.SNAPSHOT_VERSION,
                "model": self.MODEL_KIND}

    @classmethod
    def from_snapshot(
        cls, profiles: SpectralProfiles | None, snapshot: Mapping,
        *, ids: Sequence[str] | None = None, gram: np.ndarray | None = None,
    ) -> RegularizedTemporalTasteModel:
        if (not isinstance(snapshot, Mapping)
                or type(snapshot.get("version")) is not int
                or snapshot["version"] != cls.SNAPSHOT_VERSION
                or snapshot.get("model") != cls.MODEL_KIND):
            raise ValueError("Snapshot is not a supported regularized temporal model")
        # Reuse v1's catalog/timestamp/label validation only after checking the
        # distinct model identity. The classmethod constructs cls, not v1.
        normalized = dict(snapshot)
        normalized["version"] = 1
        normalized.pop("model")
        return super().from_snapshot(profiles, normalized, ids=ids, gram=gram)
