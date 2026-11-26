# -*- coding: utf-8 -*-
"""리뷰 표현력 기준 — **본체는 `server/recsys/expressiveness.py` 로 옮겼다.**

배포되는 것은 `server/` 뿐이라 가게 화면의 리뷰 정렬과 사용자 후기 검사가 그 사전을 읽는다.
여기는 옛 import 경로(`from rubric import LEX, ...`)를 살려 두는 껍데기다 — 같은 사전을
두 곳에 두면 언젠가 갈라진다. 기준 설명은 그 모듈의 docstring 과 `README.md` ② 절에 있다.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1] / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from recsys.expressiveness import (  # noqa: E402,F401
    CATEGORIES, CATEGORY_KO, COMPARE, DEGREE, INTENSIFIER_STOP, LEX, QUANT_UNITS,
    REASON, REFERENT, TRAPS,
)
