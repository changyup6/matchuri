"""Research-only temporal preference model; no production recommendation hooks.

The spatial kernel is the positive spectrum of centered serving similarities,
not the squared similarity kernel. The temporal kernel is
``spatial * (slow_variance + fast_variance * 2**(-abs(t-s)/half_life))``.
Only the latest response per store is retained; session boundaries do nothing.
The common mean has an unpenalized intercept. Predictive uncertainty includes
estimation of that mean, but excludes a future response's observation noise.
It is not a calibrated probability or compatible with existing API scores.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from numbers import Real
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

MODALITIES = ("menu", "text", "review", "food")


def _ids(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("Catalog IDs must be a sequence of strings")
    result = tuple(values)
    if not result or any(not isinstance(s, str) or not s for s in result):
        raise ValueError("Store IDs must be nonempty strings in a nonempty catalog")
    if len(set(result)) != len(result):
        raise ValueError("Duplicate catalog store ID")
    return result


def _readonly(value: np.ndarray) -> np.ndarray:
    value.setflags(write=False)
    return value


def _normalize(x: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(x, axis=1, keepdims=True)
    return np.divide(x, norm, out=np.zeros_like(x), where=norm > 1e-12)


class SpectralProfiles:
    """Response-independent feature map with explicit missing-data diagnostics.

    A nonfinite similarity diagonal means that store has no observed modality;
    its feature block is zero. An absent modality is zero for every store.
    Nonfinite entries *between known stores* are invalid, not imputed.
    Symmetrization and negative-eigenvalue removal are explicit approximations
    because the serving MaxSim similarities need not be positive semidefinite.
    """

    @classmethod
    def from_serving(cls, directory: str | Path) -> SpectralProfiles:
        directory = Path(directory)
        ids = json.loads((directory / "pool_ids.json").read_text())
        arrays, sources = {}, {}
        for modality in MODALITIES:
            path = directory / f"sim_{modality}.npy"
            if path.exists():
                arrays[modality] = np.load(path, allow_pickle=False)
                sources[modality] = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls.from_arrays(ids, arrays, sources=sources)

    @classmethod
    def from_arrays(
        cls,
        ids: Sequence[str],
        similarities: Mapping[str, np.ndarray],
        *,
        sources: Mapping[str, str] | None = None,
    ) -> SpectralProfiles:
        ids = _ids(ids)
        if set(similarities) - set(MODALITIES):
            raise ValueError("Unknown similarity modality")
        blocks, diagnostics = [], {}
        for modality in MODALITIES:
            matrix = similarities.get(modality)
            if matrix is None:
                diagnostics[modality] = {
                    "known": 0, "dimensions": 0, "missing": True,
                    "negative_spectral_mass_fraction": 0.,
                    "original_max_asymmetry": 0.,
                }
                continue
            matrix = np.asarray(matrix, dtype=np.float64)
            if matrix.shape != (len(ids), len(ids)):
                raise ValueError(f"Invalid {modality} similarity shape")
            known = np.flatnonzero(np.isfinite(np.diag(matrix)))
            kernel = matrix[np.ix_(known, known)]
            if not np.isfinite(kernel).all():
                raise ValueError(f"Nonfinite {modality} similarity between known stores")
            asymmetry = float(np.max(abs(kernel - kernel.T), initial=0.))
            kernel = (kernel + kernel.T) / 2
            if len(known):
                kernel = (kernel - kernel.mean(0)[None, :]
                          - kernel.mean(1)[:, None] + kernel.mean())
                values, vectors = np.linalg.eigh(kernel)
                threshold = np.finfo(float).eps * len(values) * max(1., values[-1])
                keep = values > threshold
                block = _normalize(vectors[:, keep] * np.sqrt(values[keep])[None, :])
                mass = float(abs(values).sum())
                negative_mass = float(-values[values < 0].sum() / mass) if mass else 0.
            else:
                block, negative_mass = np.zeros((0, 0)), 0.
            full = np.zeros((len(ids), block.shape[1]))
            full[known] = block
            blocks.append(full)
            diagnostics[modality] = {
                "known": len(known), "dimensions": block.shape[1], "missing": False,
                "negative_spectral_mass_fraction": negative_mass,
                "original_max_asymmetry": asymmetry,
            }
        result = cls()
        result.ids = ids
        result.pos = MappingProxyType({s: i for i, s in enumerate(ids)})
        result.x = _readonly(_normalize(np.concatenate(blocks, axis=1))
                             if blocks else np.zeros((len(ids), 0)))
        result.gram = _readonly(result.x @ result.x.T)
        result.sources = dict(sources or {})
        result.diagnostics = diagnostics
        return result


@dataclass(frozen=True)
class Parameters:
    slow_variance: float = .1
    fast_variance: float = 1.
    half_life: float = 10.
    observation_variance: float = 1.

    def __post_init__(self) -> None:
        values = tuple(asdict(self).values())
        if any(not isinstance(v, Real) or isinstance(v, (bool, np.bool_))
               or not np.isfinite(v) for v in values):
            raise ValueError("Model parameters must be finite numbers")
        if (self.slow_variance < 0 or self.fast_variance < 0
                or self.slow_variance + self.fast_variance <= 0
                or self.half_life <= 0 or self.observation_variance <= 0):
            raise ValueError("Invalid kernel variance, half-life or observation variance")


@dataclass(frozen=True)
class Prediction:
    mean: np.ndarray
    latent_std: np.ndarray
    identified: np.ndarray


@dataclass(frozen=True)
class _Fit:
    indices: np.ndarray
    at: np.ndarray
    y: np.ndarray
    inverse: np.ndarray
    alpha: np.ndarray
    intercept: float
    inverse_ones: np.ndarray
    ones_precision: float


class TemporalTasteModel:
    """Exact dual GP, continuous in swipe time and independent of sessions.

    Use a shared ``SpectralProfiles`` for multiple users, or supply a validated
    PSD Gram matrix and ordered IDs. A clone shares immutable catalog arrays
    and copies only user state. Snapshots contain responses and therefore must
    be handled as private data; they never contain catalog features.
    """

    def __init__(
        self,
        profiles: SpectralProfiles | None = None,
        *,
        ids: Sequence[str] | None = None,
        gram: np.ndarray | None = None,
        slow_variance: float = .1,
        fast_variance: float = 1.,
        half_life: float = 10.,
        observation_variance: float = 1.,
    ) -> None:
        if profiles is not None:
            if ids is not None or gram is not None:
                raise ValueError("Supply profiles or ids/gram, not both")
            ids, gram = profiles.ids, profiles.gram
        elif ids is None or gram is None:
            raise ValueError("Supply profiles or both ids and gram")
        self.ids = _ids(ids)
        matrix = np.asarray(gram, dtype=np.float64)
        if matrix.shape != (len(self.ids), len(self.ids)) or not np.isfinite(matrix).all():
            raise ValueError("Gram matrix must be finite and match catalog size")
        if not np.allclose(matrix, matrix.T, rtol=0, atol=1e-10):
            raise ValueError("Gram matrix must be symmetric")
        if profiles is None:
            matrix = (matrix + matrix.T) / 2
            scale = max(1., float(abs(np.diag(matrix)).max()))
            if np.linalg.eigvalsh(matrix).min() < -1e-10 * scale:
                raise ValueError("Gram matrix must be positive semidefinite")
            matrix = _readonly(matrix)
        self.gram = matrix
        self.pos = MappingProxyType({s: i for i, s in enumerate(self.ids)})
        self.parameters = Parameters(slow_variance, fast_variance, half_life, observation_variance)
        digest = hashlib.sha256(json.dumps(self.ids, ensure_ascii=False).encode())
        digest.update(np.ascontiguousarray(matrix).tobytes())
        self.catalog_fingerprint = digest.hexdigest()
        self._clock = 0
        self._evidence: dict[str, tuple[bool, int]] = {}
        self._cached: _Fit | None = None

    @property
    def clock(self) -> int:
        return self._clock

    @property
    def evidence(self) -> Mapping[str, Mapping[str, bool | int]]:
        return MappingProxyType({s: MappingProxyType({"like": v[0], "at": v[1]})
                                 for s, v in self._evidence.items()})

    def start_session(self) -> None:
        """Session boundaries neither reset preference nor age observations."""

    def record(self, store_id: str, like: bool) -> None:
        if not isinstance(store_id, str) or store_id not in self.pos:
            raise ValueError(f"Unknown store ID: {store_id}")
        if not isinstance(like, (bool, np.bool_)):
            raise ValueError("Response must be a boolean")
        self._clock += 1
        self._evidence[store_id] = (bool(like), self._clock)
        self._cached = None

    def clone(self) -> TemporalTasteModel:
        clone = object.__new__(type(self))
        clone.__dict__ = dict(self.__dict__)
        clone._evidence = dict(self._evidence)
        return clone

    def snapshot(self) -> dict:
        return {
            "version": 1, "catalog_fingerprint": self.catalog_fingerprint,
            "parameters": asdict(self.parameters), "clock": self.clock,
            "evidence": [{"store_id": s, "like": like, "at": at}
                         for s, (like, at) in self._evidence.items()],
        }

    @classmethod
    def from_snapshot(
        cls, profiles: SpectralProfiles | None, snapshot: Mapping,
        *, ids: Sequence[str] | None = None, gram: np.ndarray | None = None,
    ) -> TemporalTasteModel:
        if not isinstance(snapshot, Mapping) or type(snapshot.get("version")) is not int or snapshot["version"] != 1:
            raise ValueError("Unsupported snapshot version")
        try:
            model = cls(profiles, ids=ids, gram=gram, **snapshot["parameters"])
            clock = snapshot["clock"]
            observations = snapshot["evidence"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Malformed snapshot") from exc
        if snapshot.get("catalog_fingerprint") != model.catalog_fingerprint:
            raise ValueError("Snapshot catalog mismatch")
        if isinstance(clock, bool) or not isinstance(clock, int) or clock < 0:
            raise ValueError("Snapshot clock must be a nonnegative integer")
        if not isinstance(observations, list):
            raise ValueError("Snapshot evidence must be a list")
        timestamps = set()
        for row in observations:
            if not isinstance(row, dict) or set(row) != {"store_id", "like", "at"}:
                raise ValueError("Malformed snapshot observation")
            sid, like, at = row["store_id"], row["like"], row["at"]
            if not isinstance(sid, str) or sid not in model.pos or sid in model._evidence:
                raise ValueError("Unknown or duplicate snapshot store")
            if (not isinstance(like, bool) or isinstance(at, bool)
                    or not isinstance(at, int) or not 1 <= at <= clock or at in timestamps):
                raise ValueError("Invalid response, duplicate or future timestamp")
            model._evidence[sid] = (like, at)
            timestamps.add(at)
        model._clock = clock
        return model

    @staticmethod
    def _fit(indices, at, y, inverse) -> _Fit:
        if not len(y):
            return _Fit(indices, at, y, inverse, np.zeros(0), 0., np.zeros(0), 0.)
        u = inverse @ np.ones(len(y))
        precision = float(u.sum())
        if not np.isfinite(precision) or precision <= 0:
            raise ArithmeticError("Invalid free-intercept precision")
        vy = inverse @ y
        intercept = float(vy.sum() / precision)
        return _Fit(indices, at, y, inverse, vy - intercept * u, intercept, u, precision)

    def _solve(self) -> _Fit:
        if self._cached is None:
            indices = np.array([self.pos[s] for s in self._evidence], dtype=int)
            at = np.array([v[1] for v in self._evidence.values()], dtype=int)
            y = np.array([1. if v[0] else -1. for v in self._evidence.values()])
            p = self.parameters
            temporal = p.slow_variance + p.fast_variance * 2. ** (-abs(at[:, None] - at[None, :]) / p.half_life)
            matrix = self.gram[np.ix_(indices, indices)] * temporal + p.observation_variance * np.eye(len(y))
            inverse = np.linalg.solve(matrix, np.eye(len(y))) if len(y) else np.zeros((0, 0))
            self._cached = self._fit(indices, at, y, inverse)
        return self._cached

    def _excluded_fit(self, excluded: set[int]) -> _Fit:
        full = self._solve()
        remove = np.array([i in excluded for i in full.indices], dtype=bool)
        if not remove.any():
            return full
        keep, inverse = ~remove, full.inverse
        rr = inverse[np.ix_(keep, keep)]
        rd = inverse[np.ix_(keep, remove)]
        dd = inverse[np.ix_(remove, remove)]
        # Schur deletion gives the inverse of the retained covariance block.
        reduced = rr - rd @ np.linalg.solve(dd, rd.T)
        reduced = (reduced + reduced.T) / 2
        return self._fit(full.indices[keep], full.at[keep], full.y[keep], reduced)

    def predict(
        self,
        store_ids: Sequence[str] | None = None,
        *, leave_store_out: bool = False,
        exclude_stores: Sequence[str] = (),
    ) -> Prediction:
        """Predict at the current clock; deleting evidence never rewinds time.

        ``leave_store_out`` removes each queried store's latest response from
        its own prediction, including responses from earlier sessions. Other
        queries retain that evidence. ``exclude_stores`` deletes a common group
        first. With no retained observations, mean=0 is a cold-start convention
        and latent_std=NaN, identified=False: a flat intercept is unidentified.
        """
        try:
            indices = np.arange(len(self.ids)) if store_ids is None else np.array([self.pos[s] for s in store_ids], dtype=int)
            excluded = {self.pos[s] for s in exclude_stores}
        except (KeyError, TypeError) as exc:
            raise ValueError("Unknown query or excluded store ID") from exc
        fit = self._excluded_fit(excluded)
        if not len(fit.y):
            return Prediction(_readonly(np.zeros(len(indices))),
                              _readonly(np.full(len(indices), np.nan)),
                              _readonly(np.zeros(len(indices), dtype=bool)))
        p = self.parameters
        age_kernel = p.slow_variance + p.fast_variance * 2. ** (-(self.clock - fit.at) / p.half_life)
        cross = self.gram[np.ix_(indices, fit.indices)] * age_kernel[None, :]
        mean = cross @ fit.alpha + fit.intercept
        prior = np.diag(self.gram)[indices] * (p.slow_variance + p.fast_variance)
        vk = cross @ fit.inverse
        residual = 1. - cross @ fit.inverse_ones
        variance = prior - np.einsum("ij,ij->i", vk, cross) + residual * residual / fit.ones_precision
        identified = np.ones(len(indices), dtype=bool)
        if leave_store_out:
            observation_pos = {i: j for j, i in enumerate(fit.indices)}
            rows = np.array([j for j, i in enumerate(indices) if i in observation_pos], dtype=int)
            columns = np.array([observation_pos[indices[j]] for j in rows], dtype=int)
            if len(fit.y) == 1:
                mean[rows], variance[rows], identified[rows] = 0., np.nan, False
            elif len(rows):
                # Free-intercept augmented inverse top-left block P. The
                # kriging weight h_i for a query is (q @ P + u/s)_i.
                # Deletion: mean -= h_i*alpha_i/P_ii; var += h_i**2/P_ii.
                # Computing only queried diagonal entries avoids n refits.
                u, s = fit.inverse_ones, fit.ones_precision
                projected = fit.inverse - np.outer(u, u) / s
                diagonal = np.diag(projected)[columns]
                if np.any(diagonal <= 0):
                    raise ArithmeticError("Invalid leave-store-out precision")
                weights = np.einsum("ij,ji->i", cross[rows], projected[:, columns]) + u[columns] / s
                mean[rows] -= weights * fit.alpha[columns] / diagonal
                variance[rows] += weights * weights / diagonal
        if not np.isfinite(mean).all() or not np.isfinite(variance[identified]).all():
            raise ArithmeticError("Nonfinite temporal prediction")
        if np.any(variance[identified] < -1e-9 * max(1., float(prior.max(initial=0.)))):
            raise ArithmeticError("Negative predictive variance")
        std = np.sqrt(np.maximum(variance, 0.))
        return Prediction(_readonly(mean), _readonly(std), _readonly(identified))
