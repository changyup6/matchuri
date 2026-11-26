# -*- coding: utf-8 -*-
"""리뷰 표현력 채점기 — **본체는 `server/recsys/expressiveness.py` 로 옮겼다.**

`score_review(text)` 의 반환 모양은 그대로다(`levels`·`grade`·`breadth`·`depth_sum`·
`top_category`·`attrs`·`signals`·`hangul_len`). `lang` 이 하나 늘었다 — 외국어·기호만인
글은 0 이 아니라 '못 잰 것' 이라는 표시다. 여기는 옛 import 경로를 살려 두는 껍데기다.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1] / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from recsys.expressiveness import score_review, score_sentence  # noqa: E402,F401
