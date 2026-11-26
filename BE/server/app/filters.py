# -*- coding: utf-8 -*-
"""필터 — 업종·태그·거리로 후보를 좁힌 **허용 집합**을 만든다.

필터를 응답에서 걸러 내면(뽑고 나서 버리기) 10장 중 몇 장이 사라지고, 버린 카드는 이미
'보여줬다'(`shown`)로 표시돼 그 사용자에게 영영 다시 안 나온다. 그래서 필터는 **후보
선택 단계**에 들어간다 — 여기서 만든 store_id 집합을 `policy.pick_many(..., allow=)` 로
넘기면 두 정책이 각자 자기 규칙대로 뽑되 집합 밖 후보는 건너뛴다(fallback 도 집합 안에서).

**필터는 쿼리가 아니라 사용자 설정이다.** 셋으로 나뉜다 —
어휘 조회 `GET /filters` → 설정 `PUT /users/{id}/filter` · `PUT /users/{id}/location`
(둘 다 사용자 레코드에 남는다, `app/users.py`) → 추천 `GET /cards?user_id=`.
`GET /cards` 에는 필터 쿼리가 없다. 조회가 설정을 정하면 같은 URL 이 요청마다 다른 뜻이
되고, '지금 이 사람에게 무엇이 걸려 있나' 를 한 곳(사용자 레코드)에서 답할 수 없다.

집합은 가게 테이블 전체(2,795곳) 위에서 만든다 — 추천 풀(2,216곳)만 훑어도 되지만 풀은
정책 모듈이 갖고 있고, 교집합은 어차피 선택 단계에서 일어난다. 2,795 × dict 조회는
요청당 한 번이면 무시할 만하고, 코드가 '풀' 이라는 개념을 몰라도 된다.

**어휘는 서버가 정한다.** 데이터에 있는 이름을 그대로 흘리면(cat2 19종 · 태그 수천 종)
클라이언트가 '초밥' 이나 '가성비좋음' 같은 값을 만들어 보내고, 서버는 조용히 빈 목록을
돌려준다 — 오타와 '그런 가게가 없다' 가 구분되지 않는다. 그래서 이 모듈이 업종 9개와
태그 12개를 **고정 목록**으로 들고, `GET /filters` 가 그것을 개수와 함께 내려주고,
목록 밖 값은 **422** 다(`validate` — 필터를 저장하는 PUT 이 그 자리다).

  업종  풀에서 많은 순 상위 8 + `기타`(나머지 전부, cat2 가 없는 4곳 포함).
        꼬리는 치킨 53 · 샐러드 50 · 패스트푸드 41 … 로 잘게 흩어져 필터 칸으로 쓸모가
        없고, 9칸이면 화면 한 줄에 들어간다. 카드의 `store.category_group` 이 같은 라벨이다.
  태그  **카드에 찍히는 태그와 같은 것을 본다** — `card_tags` 가 그 가게를 나타내는
        태그 몇 개를 고르고, 카드도 필터도 그것만 본다. 예전에는 필터가 `가게 태그 ∪ 리뷰
        태그` 전부를 보고 카드는 리뷰 태그 개수 순 5개만 띄워서, `가성비` 로 거른 카드에
        가성비가 안 뜨는 일이 있었다(걸리는 1,835곳 중 734곳 · `점심식사` 는 764곳 전부).
        고른 조건이 카드에 안 보이면 사용자에게는 그냥 오작동이다.
        뜻이 같은 원본 이름은 **한 칸으로 묶는다**(`친절` = 친절 ∪ 친절한,
        `단체모임` = 단체모임 ∪ 회식장소 ∪ 친목모임 …). 원본은 가게 태그(카카오)와 리뷰
        태그(리뷰가 준 것)가 섞여 있어 같은 뜻이 두 이름으로 오고, 사용자가 그 사정을
        알아야 할 이유가 없다. 묶기 전에는 `친절` 842 · `친절한` 1305 로 갈라져 있었다.
        **카드에도 칸 이름을 찍는다** — `데이트` 로 거른 카드에 `소개팅` 이, `친절` 칸에
        `친절한` 이 뜨면 사용자는 고른 것과 다른 태그를 본다.

`swipe.CATS` 와는 **다른 목록**이다. 그쪽은 legacy 정책이 학습하는 특성 자리(업종 10 +
기타)이고 모델 가중치의 배치라 건드리면 학습이 어긋난다. 여기는 사용자에게 보여 줄 필터
칸이다 — 우연히 겹치는 이름이 있어도 서로 독립이다.
"""
from __future__ import annotations

import collections
import threading

from fastapi import HTTPException

from app import geo
from recsys import store as svc

#: 클라이언트가 쓰는 업종 — 후보 풀에서 많은 순 상위 8. 나머지는 전부 `기타` 다.
#
# **순서는 풀에서 온다.** 카드 자격을 조인 뒤(2,294 -> 1,629) 분식(61)이 간식(52)을
# 앞질러 둘이 뒤바뀌었다. `GET /filters` 가 "상위 8 은 많은 순" 을 계약으로
# 내걸고 테스트가 그걸 잠그므로, 풀이 바뀌면 이 목록도 같이 고쳐야 한다.
CATS = ["한식", "카페", "일식", "술집", "양식", "중식", "분식", "간식"]

#: 상위 8 밖(그리고 cat2 가 없는) 가게의 라벨. 목록의 **맨 뒤**에 온다.
ETC = "기타"

#: `cat` 이 받는 값 전부(순서 그대로 `GET /filters` 에 나간다).
CAT_CHOICES = CATS + [ETC]

#: 클라이언트가 쓰는 태그 → 데이터의 원본 이름들(가게 태그 ∪ 리뷰 태그).
#: 순서가 곧 `GET /filters` 의 순서다. 묶음마다 풀에서 238곳 이상이 걸린다(최소 `술한잔`).
TAGS: dict[str, set[str]] = {
    "가성비": {"가성비"},
    "친절": {"친절", "친절한"},
    "양많은": {"양많은"},
    "혼밥": {"혼밥"},
    "점심식사": {"점심식사", "점심특선"},
    "야식": {"야식", "심야식당"},
    "단체모임": {"단체모임", "회식장소", "친목모임", "가족모임", "상견례"},
    "분위기": {"분위기", "감성적", "분위기좋은"},
    "데이트": {"데이트", "데이트코스", "소개팅"},
    "술한잔": {"술한잔", "혼술"},
    "조용한": {"조용한", "조용한카페"},
    "주차": {"주차"},
}

#: 거리 필터의 추천 반경. 역삼역 기준 300m 는 '한 블록', 2km 면 동을 넘는다.
WITHIN_CHOICES = [300, 500, 1000, 2000]

#: 원본 이름 -> 그 이름이 속한 칸. `TAGS` 에서 만든다(한 이름은 한 칸에만 있다).
_slot_of: dict[str, str] = {raw: name for name, group in TAGS.items() for raw in group}

#: 카드에 찍히는 태그의 **상한**. 알고리즘이 정한 값이 아니라 **화면이 감당할 수 있는 수**다.
#: 카드 아래 칩이 세 줄로 늘어지면 사진을 너무 가린다(2026-09-21 결정).
#:
#: 추천 풀 1,498곳에서 잰 값 — 상한을 늘리면 필터 후보가 늘지만 카드가 길어진다.
#:     상한  카드당 태그(중앙)   가성비   점심식사   조용한
#:       5      4.59 (5)       946     323      123
#:      10      6.95 (7)      1010     578      164
#:   (옛 규칙: 리뷰 태그 개수 순 5개 = 2.61 (2) · 706 · 0 · 70)
#: 5로 조여도 **옛 규칙보다는 모든 태그가 낫다** — 개수 순으로 자르던 때는 `점심식사` 가
#: 풀에서 0곳, `야식` 이 16곳이라 그 필터를 눌러도 카드가 안 나왔다.
CARD_TAGS = 5
#: 이 값 미만이면 카드에 안 싣는다. 1.0 = 그 태그의 **가게당 평균**만큼 받았다는 뜻.
#: `맛` 을 남보다 적게 받은 가게에 `맛` 을 달면 고르는 데 방해만 된다.
MIN_LIFT = 1.0
#: 투표 수가 없는 가게 태그를 몇 배로 볼까. 1.0 = 딱 평균만큼 받은 셈이다.
#: 2.0 까지 올려 봐도 238곳 기준에 걸리는 태그는 그대로고(술한잔·조용한), 1.0 이 전체
#: 후보를 가장 많이 남긴다(가성비 1,655 vs 1,407곳).
CURATED_LIFT = 1.0

_lock = threading.Lock()
_vocab_key = None
_vocab: dict | None = None
_stats_lock = threading.Lock()
_stats_key = None
_stats: dict | None = None


def split(value: str | None) -> list[str]:
    """`"일식,한식"` -> `["일식", "한식"]`. 빈 값·공백은 버린다."""
    if not value:
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


def category_group(cat2: str | None) -> str:
    """가게의 cat2 -> 클라이언트가 보는 업종 라벨 9개 중 하나.

    `loader._store` 가 카드의 `store.category_group` 에 이 값을 넣는다 — 필터에 쓰는 라벨과
    카드에 찍히는 라벨이 같은 함수에서 나와야 '이 카드가 왜 걸렸나' 가 설명된다.
    """
    return cat2 if cat2 in CATS else ETC


def tag_names(s: dict) -> set[str]:
    """그 가게의 **원본** 태그 이름 전부 — 가게 태그 ∪ 리뷰 태그 이름.

    고르는 데는 쓰지 않는다(`card_tags` 가 그 일을 한다). 어떤 이름이 들어와 있는지
    들여다볼 때만 쓴다.
    """
    return set(s.get("tags") or []) | set((s.get("review_tags") or {}).keys())


def _tag_stats() -> dict[str, float]:
    """전체 기준 — 태그마다 **가게당 평균 표 수**. 데이터가 바뀔 때만 다시 센다."""
    global _stats_key, _stats
    svc.load()
    ids = svc.all_store_ids()
    # 파일 수정시각만으로는 **테이블이 통째로 바뀐 것**을 못 본다. 테스트는 `svc.get_store` 를
    # 합성 가게로 바꿔 끼우는데 파일은 그대로라, 실데이터 평균이 합성 가게에 그대로 쓰였다
    # (`친절한` 평균 5.59 -> 합성 가게의 3표가 평균 미달이 됐다). 가게 목록과 조회 함수까지
    # 키에 넣는다 — 운영에서는 둘 다 고정이라 캐시가 그대로 산다.
    key = (tuple(sorted(svc._mt.items())), len(ids), ids[0] if ids else None,
           ids[-1] if ids else None, id(svc.get_store))
    with _stats_lock:                      # `_lock`(vocabulary) 과 따로 둔다 — 겹쳐 잡으면 멈춘다
        if _stats_key == key and _stats is not None:
            return _stats
        votes: collections.Counter = collections.Counter()
        for sid in ids:
            for t, c in ((svc.get_store(sid) or {}).get("review_tags") or {}).items():
                votes[t] += c
        _stats = {t: c / len(ids) for t, c in votes.items()} if ids else {}
        _stats_key = key
        return _stats


def card_tags(s: dict) -> list[str]:
    """그 가게를 나타내는 태그 `CARD_TAGS` 개 — **카드에 찍히는 것이자 필터가 보는 것**.

    한 정의만 두는 것이 요점이다. 카드에 뜬 태그로만 걸리고, 걸린 태그는 반드시 카드에
    있다. 두 곳이 어긋날 방법이 없다.

    **개수가 아니라 "남들보다 많이 받았나" 로 고른다.** 개수 순으로 자르면 흔한 것만 올라온다 —
    `맛` 은 2,192곳 중 639곳(29%)에 붙고 574곳에서 1등이었다. 그래서 그 태그의 **가게당 평균
    표 수로 나눈다.** 카드 2,795장에 `맛` 표가 3,150개면 평균 1.13개이고, `맛` 이 1개뿐인
    가게는 0.88 이라 평균에 못 미쳐 실리지 않는다. 1등 자리가 이렇게 갈라진다 —
    맛 574 -> 294 · 친절한 604 -> 289 · 분위기 75 -> 279.

    **그 가게 자기 표 총합으로 나누지 않는다.** 한때 점유율(이 가게 표 중 몇 %인가)로 쟀는데,
    표가 1개뿐인 가게는 그 태그가 100% 라 무조건 1등이 됐다 — 표 1개로 태그가 붙은 카드가
    708곳(25%)이었다. 지금은 399곳이고 `분위기`(평균 0.76) · `주차`(평균 0.17) 뿐이다.
    평균이 1 미만인 태그는 1표로도 평균을 넘는데, 원래 표가 드문 항목이라 그대로 둔다.

    가게 태그(카카오·리뷰 본문에서 붙인 것)는 투표 수가 없다. **딱 평균만큼(1.0) 받은 것으로
    본다** — 평균을 넘은 리뷰 태그보다는 뒤, 못 미친 것보다는 앞이다. 이 몫이 있어야
    `점심식사`·`야식` 처럼 투표에 아예 없는 태그가 산다(투표만 보면 0곳·22곳이 된다).

    `MIN_LIFT`(=평균) 미만은 싣지 않는다. 그래서 태그 수가 가게마다 다르다(중앙 5개).
    같은 점수(=가게 태그끼리)면 이름 순이다. 뜻이 있는 순서는 아니지만 **늘 같다** — 같은
    가게가 요청마다 다른 태그를 보이면 안 된다.

    **내보내는 이름은 필터 칸 이름(`TAGS` 의 키)이다.** 점수는 원본 이름마다 따로 잰다 — 카카오
    투표 `친절` 과 리뷰 본문 집계 `친절한` 은 재는 방식이 달라 평균도 따로 둬야 한다. 고른 뒤
    이름만 칸 이름으로 바꾼다. 그래서 카드 칩과 필터 칩이 늘 같은 글자다(`소개팅` -> `데이트`,
    `친절한` -> `친절`, `감성적` -> `분위기`, `회식장소` -> `단체모임`).
    """
    avg = _tag_stats()
    score: dict[str, float] = {}
    for t, c in (s.get("review_tags") or {}).items():
        a = avg.get(t)
        if a:
            score[t] = c / a
    for t in (s.get("tags") or []):
        score.setdefault(t, CURATED_LIFT)
    order = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    # 한 칸(`TAGS`)에서는 **이름 하나만** 싣는다. `분위기` 와 `감성적` 은 사용자에게 같은 태그인데
    # 원본 이름을 그대로 내보내면 칩이 둘 뜬다(터프커피: 분위기+감성적). 풀에서 105곳이 그랬다.
    # **필터 어휘 밖의 이름은 싣지 않는다.** 가게 태그에는 카카오 키워드가 그대로 섞여 들어와
    # 어휘가 1,156종이나 되고, 그중에는 가게가 스스로 단 홍보 문구가 많다 —
    # `초복메뉴추천` 29곳 · `강남역맛집` 21곳 · `맛집` · `강남역` 같은 것들이 카드에 떴다.
    # 어휘 밖 319종을 빼면 칸이 풀려 오히려 후보가 는다(점심식사 275 -> 497 · 야식 183 -> 294).
    # 고를 수 없는 태그를 카드에 띄울 이유도 없다 — 카드와 필터를 한 정의로 두는 것이 이 함수다.
    seen: set[str] = set()
    out: list[str] = []
    for t, v in order:
        if v < MIN_LIFT:
            continue
        slot = _slot_of.get(t)
        if slot is None or slot in seen:       # 어휘 밖이거나, 같은 칸을 이미 실었다
            continue
        seen.add(slot)
        out.append(slot)                       # 원본 이름이 아니라 칸 이름 — 필터 칩과 같은 글자
        if len(out) >= CARD_TAGS:
            break
    return out


def client_tags(s: dict) -> set[str]:
    """그 가게가 가진 **클라이언트 태그** — 카드에 찍히는 태그(`card_tags`)만 본다.

    `card_tags` 가 이미 칸 이름을 내므로 그대로다.
    """
    return set(card_tags(s))


def validate(cats: list[str], tags: list[str]) -> None:
    """어휘 밖 값이면 422. 빈 목록으로 뭉개면 오타와 '그런 가게가 없다' 가 구분되지 않는다.

    필터를 **저장할 때**(`PUT /users/{id}/filter`) 부른다 — 잘못된 값은 그 자리에서
    막히고, 저장된 필터를 읽는 `GET /cards` 는 이미 검증된 값만 본다.
    """
    for v in cats:
        if v not in CAT_CHOICES:
            raise HTTPException(422, f"cat '{v}' 은 없는 값입니다. "
                                     f"허용: {', '.join(CAT_CHOICES)}")
    for v in tags:
        if v not in TAGS:
            raise HTTPException(422, f"tag '{v}' 은 없는 값입니다. "
                                     f"허용: {', '.join(TAGS)}")


def allow_ids(*, cats: list[str], tags: list[str], within_m: int | None,
              lat: float | None, lon: float | None) -> set[str] | None:
    """필터를 만족하는 store_id 집합. 필터가 하나도 없으면 None(= 제한 없음).

    **`cat`·`tag` 둘 다 OR 다** — 여러 개를 고르면 그중 하나만 맞아도 통과한다. 칩을 더
    누르면 결과가 **넓어지는** 것이 필터의 상식이고, 좁아지는 쪽은 고를수록 0장에
    가까워진다(태그 AND 였을 때 `혼밥`+`가성비`+`조용한` 이면 후보가 수십 곳까지 줄었다).

    **차원 사이는 AND 다** — 업종 ∧ 태그 ∧ 거리. '일식이면서 혼밥 되는, 500m 안의 곳' 이
    필터가 뜻하는 바이고, 차원까지 OR 로 묶으면 '일식 아무 곳이나, 또는 아무 데나 500m 안' 이
    되어 조건을 건 의미가 사라진다.

    값이 어휘 안이라고 보고 돈다 — 검사는 필터를 저장할 때 `validate` 가 한 번 한다.
    """
    if not cats and not tags and within_m is None:
        return None

    allow: set[str] | None = None
    if cats or tags:
        want_cats, want_tags = set(cats), set(tags)
        allow = set()
        for sid in svc.all_store_ids():
            s = svc.get_store(sid) or {}
            if want_cats and category_group(s.get("cat2")) not in want_cats:
                continue
            if want_tags and want_tags.isdisjoint(client_tags(s)):
                continue
            allow.add(sid)

    if within_m is not None:
        near = geo.within(lat, lon, within_m)       # 위치는 라우터가 이미 확인했다
        allow = near if allow is None else (allow & near)
    return allow


def vocabulary() -> dict:
    """`GET /filters` 응답 — 고정 어휘(업종 9 · 태그 12)와 **추천 후보 풀 기준** 개수.

    전체 가게(2,795곳)로 세면 카드로 나오지 않는 가게가 개수에 섞여 '10곳' 이라고
    적힌 필터가 카드 0장을 주는 일이 생긴다. 세는 대상은 풀(`swipe.pool()["ids"]`) 이다.
    목록과 순서는 데이터가 아니라 이 모듈이 정하므로 개수가 0 이 되어도 칸은 남는다 —
    클라이언트 화면이 데이터에 따라 흔들리지 않는다.
    """
    global _vocab_key, _vocab
    from recsys import swipe

    ids = swipe.pool()["ids"]
    svc.load()
    key = (tuple(sorted(svc._mt.items())), len(ids))
    with _lock:
        if _vocab_key == key and _vocab is not None:
            return _vocab
        cats = dict.fromkeys(CAT_CHOICES, 0)
        tags = dict.fromkeys(TAGS, 0)
        for sid in ids:
            s = svc.get_store(sid) or {}
            cats[category_group(s.get("cat2"))] += 1
            for name in client_tags(s):
                tags[name] += 1
        _vocab = {
            "cats": [{"name": n, "count": cats[n]} for n in CAT_CHOICES],
            "tags": [{"name": n, "count": tags[n]} for n in TAGS],
            "within_m": list(WITHIN_CHOICES),
        }
        _vocab_key = key
        return _vocab
