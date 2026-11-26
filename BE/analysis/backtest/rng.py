# -*- coding: utf-8 -*-
"""결정적 난수 — 파이썬 `hash()` 는 실행마다 달라진다(PYTHONHASHSEED).

실험은 몇 번을 돌려도 같은 숫자가 나와야 하므로 문자열이 섞인 seed 는 전부 여기를
거친다.
"""
from __future__ import annotations

import hashlib

import numpy as np


def stable_seed(*parts) -> int:
    h = hashlib.blake2b("|".join(str(p) for p in parts).encode("utf-8"), digest_size=8)
    return int.from_bytes(h.digest(), "big")


def generator(*parts) -> np.random.Generator:
    return np.random.default_rng(stable_seed(*parts))
