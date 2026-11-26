# -*- coding: utf-8 -*-
"""네이버 리뷰 날짜 문자열 정규화.

네이버는 연도를 생략해서 내려준다.
  "8.23.일"      -> 올해 8월 23일 (미래면 작년)
  "23.5.10.수"   -> 2023년 5월 10일
"""
from __future__ import annotations

import datetime as dt
import re

_NUM = re.compile(r"\d+")


def parse(raw: str | None, today: dt.date | None = None) -> str | None:
    if not raw:
        return None
    today = today or dt.date.today()
    parts = _NUM.findall(raw)
    try:
        if len(parts) >= 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            y += 2000 if y < 100 else 0
            return dt.date(y, m, d).isoformat()
        if len(parts) == 2:
            m, d = int(parts[0]), int(parts[1])
            cand = dt.date(today.year, m, d)
            if cand > today:
                cand = dt.date(today.year - 1, m, d)
            return cand.isoformat()
    except ValueError:
        return None
    return None
