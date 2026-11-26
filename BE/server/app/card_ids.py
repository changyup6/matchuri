# -*- coding: utf-8 -*-
"""`card_id` 를 데이터 갱신에도 고정한다.

**왜 필요한가.** 예전에는 `card_id` 가 `sorted(store_ids)` 의 **인덱스**였다. 가게가 하나만
늘거나 줄어도 그 뒤 번호가 전부 밀린다. 그런데 `card_id` 로 저장된 것이 셋이나 있다.

    state/preferences.jsonl   스와이프 기록 — 재시작 때 되먹여 취향 모델을 복원한다
    state/bookmarks.jsonl     찜
    state/exposures.jsonl     어느 정책이 어느 카드를 보여줬나

번호가 밀리면 **찜이 엉뚱한 가게를 가리키고, 학습이 다른 가게로 복원되고, 정책 집계가
어긋난다.** 그것도 예외 하나 없이 조용히. 주 1회 데이터를 갱신하기로 한 이상 이건
매주 일어난다.

**어떻게 고정하나.** `store_id -> card_id` 매핑을 파일에 박아 둔다(`data/card_ids.json`).
이후로는 `store_id` 가 진짜 키이고 `card_id` 는 그 별칭이다.

    기존 가게   파일에 적힌 번호를 그대로 쓴다
    새 가게     남은 가장 큰 번호 + 1 부터, **store_id 정렬 순서로** 부여한다
    사라진 가게 번호를 파일에 남겨 둔다. 카드로는 안 나오지만 **재사용하지 않는다**
                — 재사용하면 과거 기록이 다른 가게를 가리키게 된다

**파일이 없어도 결과는 같다.** 배정 규칙이 결정적이라(정렬 순서 + max+1) 같은 입력이면
같은 매핑이 나온다. 그래서 파일 쓰기에 실패해도 그 실행은 정상이고, 다음 실행도 같은
번호를 만든다. 파일은 '빠르고 확실한 기준선' 이지 정확성의 전제가 아니다.

**파일을 git 에 커밋하는 이유.** 70KB 남짓이고, 모든 환경(서버·로컬·테스트)이 같은 번호를
써야 하며, state 볼륨이 날아가도 복구되어야 한다. 사용자 상태가 아니라 데이터 계보다.

**파일이 둘인 이유.** 커밋된 기준선은 이미지에 구워져 **읽기 전용**이다. 기동한 뒤 새로 본
가게의 번호를 거기에 쓰면 컨테이너를 재생성할 때 사라진다. 그래서 증분은 재배포에도
살아남는 state 볼륨에 쌓는다.

    data/card_ids.json    기준선. git 에 있고 이미지에 굽는다(server/Dockerfile)
    state/card_ids.json   증분. 기동 뒤 새로 배정된 번호. 볼륨이라 남는다

읽을 때는 `current()` 로 둘을 합친다. **기준선이 이긴다** — 모든 환경이 공유하는 정본이기
때문이다. 합칠 때 번호가 겹치면 그 증분 항목을 버린다. 번호 중복은 두 가게가 같은 카드가
되는 것이라 어긋난 번호 하나보다 훨씬 나쁘다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from app import observe, state

#: `BE/server/app/card_ids.py` -> parents[2] = BE
ROOT = Path(__file__).resolve().parents[2]

#: 기준선. git 에 커밋돼 있고 이미지에도 들어간다.
BASELINE = ROOT / "data" / "card_ids.json"


def live() -> Path:
    """증분 파일. `state.DIR` 을 **부를 때** 읽는다 — 테스트가 state 경로를 바꿔 끼운다."""
    return state.DIR / "card_ids.json"


def load(path: Path | None = None) -> dict[str, int]:
    """파일 하나를 읽는다. 없거나 깨졌으면 빈 매핑(= 전부 새로 배정)."""
    p = path or BASELINE
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # 깨진 파일로 서비스를 멈추지 않는다. 빈 매핑이면 지금 데이터 기준으로 다시
        # 배정되는데, 그건 예전 동작과 같다 — 더 나빠지지는 않는다.
        observe.log("card_ids.read_fail", level=logging.ERROR, path=str(p),
                    error=type(exc).__name__)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for sid, cid in raw.items():
        # bool 은 int 의 하위형이라 따로 막는다(True 가 1 로 들어오면 충돌한다).
        if isinstance(sid, str) and isinstance(cid, int) and not isinstance(cid, bool):
            out[sid] = cid
    return out


def current() -> dict[str, int]:
    """지금 유효한 매핑 = 기준선 + 증분.

    기준선이 이긴다. 증분에만 있는 가게는 **번호가 비어 있을 때만** 받아들인다 —
    누가 기준선을 늘려 번호가 부딪히면, 그 증분 항목을 버리고 `assign` 이 새 번호를
    준다. 번호가 하나 밀리는 것보다 두 가게가 같은 번호를 갖는 쪽이 훨씬 위험하다.
    """
    merged = load(BASELINE)
    used = set(merged.values())
    dropped = 0
    # 번호 순으로 넣어야 어느 쪽을 버릴지가 실행마다 같다.
    for sid, cid in sorted(load(live()).items(), key=lambda kv: (kv[1], kv[0])):
        if sid in merged:
            continue
        if cid in used:
            dropped += 1
            continue
        merged[sid] = cid
        used.add(cid)
    if dropped:
        observe.log("card_ids.conflict", level=logging.WARNING, dropped=dropped)
    return merged


def assign(known: dict[str, int], store_ids: Iterable[str]) -> tuple[dict[str, int], list[str]]:
    """`(전체 매핑, 이번에 새로 번호를 받은 store_id)`.

    `known` 은 바꾸지 않는다. 사라진 가게의 항목도 **그대로 남긴다** — 번호를 회수하면
    다음 가게가 그 번호를 물려받아 과거 기록이 어긋난다.
    """
    out = dict(known)
    nxt = max(out.values(), default=-1) + 1
    fresh: list[str] = []
    for sid in sorted(set(store_ids)):          # 정렬 순서로 배정해야 재현 가능하다
        if sid in out:
            continue
        out[sid] = nxt
        nxt += 1
        fresh.append(sid)
    return out, fresh


def save(mapping: dict[str, int], path: Path | None = None) -> bool:
    """매핑을 저장한다. 실패해도 서비스는 계속 돈다(배정이 결정적이라 다음에 같은 값이 나온다).

    같은 디렉터리에 임시 파일로 쓰고 바꿔치기한다 — 도중에 죽어도 반쯤 쓰인 파일이
    남지 않는다.
    """
    p = path or live()
    tmp = p.with_suffix(".json.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(dict(sorted(mapping.items(), key=lambda kv: kv[1])),
                       ensure_ascii=False, indent=0) + "\n",
            encoding="utf-8")
        tmp.replace(p)
        return True
    except OSError as exc:
        observe.log("card_ids.write_fail", level=logging.WARNING, path=str(p),
                    error=type(exc).__name__)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
