# -*- coding: utf-8 -*-
"""가게 이름 -> 브랜드 키 (PLAN 5절 '체인 문제' / Step 1.5 결정 4번).

Step 1 이 남긴 문제: **같은 브랜드의 다른 지점끼리 메뉴가 같아 MaxSim 이 1.0 이 되고
추천 상위를 점거한다.** 스타벅스 역삼점을 like 한 사람에게 스타벅스 선릉점을 맨 위로
주는 것은 '취향을 맞췄다' 가 아니라 **근접 중복**이다.

그래서 이름을 브랜드 키로 정규화한다. 규칙은 데이터를 보고 정했다.

    이름 2,795개 중 공백이 있는 것 1,583개, 그 중 **1,415개**(89%)가 마지막 토큰이
    `점` 으로 끝난다 — `역삼점`(293) `강남점`(225) `강남역점`(103) `강남본점`(58)
    `선릉점`(54) `역삼본점`(37) `본점`(36) `역삼역점`(35) `강남직영점`(30) …

규칙:

1. NFKC 정규화 → 꼬리 괄호 제거(`태키커피(tackycoffee)`, `솔솥 GFC몰점(강남파이낸스센터)`).
2. 공백으로 자른 **마지막 토큰이 지점 토큰이면 떼어낸다.** 남은 토큰이 1개 이상일
   때만 뗀다(`홍반점`·`문익점` 처럼 이름 자체가 `점` 으로 끝나는 한 단어 가게를
   지키기 위해서다). 여러 개 붙어 있으면 반복한다.
3. 지점 토큰 = `점` 으로 끝나고, 음식·업태 낱말(`반점`·`전문점`·`주점`·`할인점` …)을
   품지 않으며, 14자 이하인 토큰.
4. 남은 것을 casefold 하고 공백을 전부 지워 키로 쓴다(`본죽&비빔밥cafe` 의 띄어쓰기
   변형을 같은 브랜드로 묶기 위해서다).

**하지 않은 것.** `스타벅스 센터필드R`·`스타벅스 큰길타워`·`투썸플레이스 투썸2.0강남`
처럼 `점` 으로 끝나지 않는 지점명 3건과, `웅칼강남점`·`우미야역삼1호점` 처럼 공백
없이 지점명이 붙은 2건은 놓친다. 후자를 정규식으로 뜯으면 `명성양갈비전문점` 같은
이름까지 잘리는데, 놓친 5건이 실제로 묶일 짝(`웅칼`·`우미야`)도 후보 풀에 없다 —
규칙을 위험하게 만들 이유가 없다.
"""
from __future__ import annotations

import collections
import re
import unicodedata

#: 이 낱말을 품은 토큰은 지점명이 아니라 가게 이름의 일부다.
#: (`청담각짜장짬뽕전문점`·`역삼주점`·`강남아이스크림할인점` 이 실제로 데이터에 있다.)
NOT_BRANCH_WORDS = ("반점", "전문점", "주점", "할인점", "상점", "노점", "대리점", "매점")

#: 지점 토큰의 최대 길이. `강남역국기원사거리본점`(11) 같은 긴 것도 지점명이지만,
#: 이보다 길면 이름 전체가 한 토큰인 경우를 의심한다.
MAX_BRANCH_TOKEN = 14

_TAIL_PAREN = re.compile(r"[（(][^（()）]*[)）]\s*$")
_SPACE = re.compile(r"\s+")


def is_branch_token(token: str) -> bool:
    """이 토큰이 '지점명' 인가 — `역삼점`·`강남역점`·`2호점`·`본점`·`GFC점`."""
    if not token.endswith("점") or len(token) > MAX_BRANCH_TOKEN:
        return False
    return not any(w in token for w in NOT_BRANCH_WORDS)


def brand_name(name: str) -> str:
    """지점명을 뗀 **사람이 읽는** 브랜드 이름. 키는 `brand_key` 가 만든다."""
    n = unicodedata.normalize("NFKC", name or "")
    n = _TAIL_PAREN.sub("", n)
    parts = _SPACE.sub(" ", n).strip().split(" ")
    while len(parts) >= 2 and is_branch_token(parts[-1]):
        parts.pop()
    out = " ".join(parts).strip()
    return out or _SPACE.sub(" ", n).strip()


def brand_key(name: str) -> str:
    """비교용 키 — `brand_name` 을 casefold 하고 공백을 지운다."""
    return _SPACE.sub("", brand_name(name)).casefold()


def brand_ids(names: list[str]) -> list[int]:
    """이름 목록 -> 브랜드 번호 목록. 같은 번호면 같은 브랜드다.

    키가 비면(이름이 없는 가게) **자기만의 번호**를 준다 — 이름 없는 가게끼리
    한 브랜드로 묶여 서로를 억제하는 사고를 막는다.
    """
    ids: list[int] = []
    seen: dict[str, int] = {}
    for i, name in enumerate(names):
        key = brand_key(name)
        if not key:
            seen[f"\x00없음{i}"] = len(seen)
            ids.append(len(seen) - 1)
            continue
        if key not in seen:
            seen[key] = len(seen)
        ids.append(seen[key])
    return ids


def brand_groups(store_ids: list[str], names: dict[str, str]) -> dict[str, list[str]]:
    """브랜드 키 -> 그 브랜드의 가게 id 목록 (입력 순서 유지)."""
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for sid in store_ids:
        groups[brand_key(names.get(sid, sid))].append(sid)
    return dict(groups)


def largest_brands(store_ids: list[str], names: dict[str, str],
                   n: int = 20) -> list[tuple[str, int, list[str]]]:
    """가게 수가 많은 브랜드 n 개 — (브랜드 이름, 가게 수, 가게 이름 목록).

    동점은 브랜드 이름 순으로 깨서 실행마다 같은 목록이 나오게 한다.
    """
    groups = brand_groups(store_ids, names)
    display = {key: brand_name(names.get(ids[0], ids[0]))
               for key, ids in groups.items()}
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), display[kv[0]]))
    return [(display[key], len(ids), [names.get(s, s) for s in ids])
            for key, ids in ordered[:n]]
