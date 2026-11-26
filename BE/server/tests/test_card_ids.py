# -*- coding: utf-8 -*-
"""`card_id` 고정.

예전에는 `card_id` 가 `sorted(store_ids)` 의 인덱스였다. 가게가 하나만 늘거나 줄어도
그 뒤 번호가 전부 밀리는데, `preferences`·`bookmarks`·`exposures` 가 전부 `card_id` 로
저장돼 있다. 밀리면 **찜이 엉뚱한 가게를 가리키고 학습이 다른 가게로 복원된다** —
예외 하나 없이 조용히.

주 1회 데이터를 갱신하기로 한 이상 이건 매주 일어난다. 그래서 매핑을 파일에 박았다.
여기서 잠그는 것은 그 불변식이다.
"""
from __future__ import annotations

import json

import pytest

from app import card_ids, loader


# ────────────────────────────────────────────── 배정 규칙
def test_기존_번호는_데이터가_바뀌어도_유지된다():
    """이 테스트가 깨지면 찜과 학습이 통째로 어긋난다."""
    known = {"a": 0, "b": 1, "c": 2}
    # b 가 폐업하고 d·e 가 새로 생긴 주간 갱신
    out, fresh = card_ids.assign(known, ["a", "c", "d", "e"])
    assert out["a"] == 0 and out["c"] == 2, "살아남은 가게의 번호가 바뀌었다"
    assert fresh == ["d", "e"]


def test_사라진_가게의_번호는_재사용하지_않는다():
    """회수하면 다음 가게가 그 번호를 물려받아 과거 기록이 다른 가게를 가리킨다."""
    known = {"a": 0, "b": 1, "c": 2}
    out, _ = card_ids.assign(known, ["a", "c", "z"])
    assert out["b"] == 1, "폐업 가게의 항목이 사라졌다"
    assert out["z"] == 3, f"빈 번호 1 을 물려받았다: {out['z']}"


def test_새_가게는_정렬_순서로_번호를_받는다():
    """순서가 흔들리면 같은 입력에 다른 매핑이 나와 재현이 안 된다."""
    out, fresh = card_ids.assign({}, ["c", "a", "b"])
    assert (out["a"], out["b"], out["c"]) == (0, 1, 2)
    assert fresh == ["a", "b", "c"]


def test_배정은_결정적이다():
    """파일 쓰기에 실패해도 다음 기동이 같은 번호를 만들어야 한다."""
    ids = ["s3", "s1", "s2"]
    first, _ = card_ids.assign({"s1": 7}, ids)
    second, _ = card_ids.assign({"s1": 7}, list(reversed(ids)))
    assert first == second


def test_중복_store_id_는_한_번만_센다():
    out, fresh = card_ids.assign({}, ["a", "a", "b"])
    assert out == {"a": 0, "b": 1} and fresh == ["a", "b"]


def test_known_을_바꾸지_않는다():
    """호출자의 기준선을 건드리면 추적이 안 되는 버그가 된다."""
    known = {"a": 0}
    card_ids.assign(known, ["a", "b"])
    assert known == {"a": 0}


# ────────────────────────────────────────────── 파일 입출력
def test_저장하고_다시_읽으면_같다(tmp_path):
    p = tmp_path / "card_ids.json"
    mapping = {"s1": 0, "s2": 1, "s9": 42}
    assert card_ids.save(mapping, p) is True
    assert card_ids.load(p) == mapping


def test_파일이_없으면_빈_매핑이다(tmp_path):
    assert card_ids.load(tmp_path / "없는파일.json") == {}


def test_깨진_파일이어도_죽지_않는다(tmp_path):
    """서비스를 멈추느니 예전 동작(전부 새로 배정)으로 돌아간다."""
    p = tmp_path / "card_ids.json"
    p.write_text("{ 이건 json 이 아니다", encoding="utf-8")
    assert card_ids.load(p) == {}


@pytest.mark.parametrize("bad", [
    '["a", "b"]',                    # dict 가 아니다
    '{"s1": "0"}',                   # 값이 문자열
    '{"s1": true}',                  # bool 은 int 의 하위형이라 따로 막아야 한다
    '{"s1": 1.5}',                   # 실수
])
def test_이상한_항목은_버린다(tmp_path, bad):
    p = tmp_path / "card_ids.json"
    p.write_text(bad, encoding="utf-8")
    assert card_ids.load(p) == {}


def test_저장은_원자적이다(tmp_path):
    """도중에 죽어도 반쯤 쓰인 파일이 남으면 안 된다 — 임시 파일이 남지 않는지 본다."""
    p = tmp_path / "card_ids.json"
    card_ids.save({"s1": 0}, p)
    assert not (tmp_path / "card_ids.json.tmp").exists()
    assert json.loads(p.read_text(encoding="utf-8")) == {"s1": 0}


# ────────────────────────────────────────────── 실데이터
def test_기준선이_지금_데이터를_전부_덮는다():
    """`data/card_ids.json` 에 빠진 가게가 있으면 기동 때 새 번호가 붙는다.

    그 자체는 안전하지만(결정적이다), 커밋된 기준선과 데이터가 어긋났다는 신호다.
    """
    loader.load_all()
    saved = card_ids.load()
    missing = [sid for sid in loader.card_id_by_store if sid not in saved]
    assert not missing, f"기준선에 없는 가게 {len(missing)}곳: {missing[:5]}"


def test_매핑이_기준선과_일치한다():
    loader.load_all()
    saved = card_ids.load()
    for sid, cid in loader.card_id_by_store.items():
        assert saved[sid] == cid, f"{sid}: 기준선 {saved[sid]} vs 실제 {cid}"


def test_카드_번호는_서로_겹치지_않는다():
    loader.load_all()
    assert len(set(loader.store_id_by_card)) == len(loader.card_id_by_store)


# ────────────────────────────────────── 기준선 + 증분 합치기
# 기준선은 이미지 안이라 읽기 전용이다. 기동 뒤 새로 배정된 번호는 state 볼륨에 쌓이고,
# 읽을 때 둘을 합친다. 여기서 잠그는 것은 "합쳐도 번호가 겹치지 않는다" 이다.

@pytest.fixture
def 두파일(tmp_path, monkeypatch):
    """기준선과 증분을 tmp_path 로 돌려 놓는다."""
    from app import state
    monkeypatch.setattr(card_ids, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(state, "DIR", tmp_path)
    return card_ids.BASELINE, card_ids.live()


def test_증분이_기준선을_넓힌다(두파일):
    base, live = 두파일
    card_ids.save({"s1": 0, "s2": 1}, base)
    card_ids.save({"s3": 2}, live)
    assert card_ids.current() == {"s1": 0, "s2": 1, "s3": 2}


def test_기준선이_이긴다(두파일):
    """같은 가게가 양쪽에 다른 번호로 있으면 기준선 쪽을 쓴다 — 정본이다."""
    base, live = 두파일
    card_ids.save({"s1": 7}, base)
    card_ids.save({"s1": 99}, live)
    assert card_ids.current() == {"s1": 7}


def test_번호가_겹치면_증분을_버린다(두파일):
    """두 가게가 같은 번호를 갖는 것이 번호 하나 밀리는 것보다 훨씬 위험하다."""
    base, live = 두파일
    card_ids.save({"s1": 0, "s2": 1}, base)
    card_ids.save({"s9": 1}, live)          # 1 번은 이미 s2 가 쓴다
    cur = card_ids.current()
    assert cur == {"s1": 0, "s2": 1}
    # 버려진 가게는 새 번호를 받는다 — 겹치지 않는다.
    after, fresh = card_ids.assign(cur, ["s1", "s2", "s9"])
    assert fresh == ["s9"] and after["s9"] == 2
    assert len(set(after.values())) == len(after)


def test_파일이_하나도_없어도_돈다(두파일):
    assert card_ids.current() == {}


def test_저장은_증분_쪽으로_간다(두파일):
    """기준선은 이미지 안이라 못 쓴다. 기본 저장 위치가 state 여야 한다."""
    base, live = 두파일
    card_ids.save({"s1": 0})
    assert live.exists() and not base.exists()


def test_기준선이_이미지에_들어간다():
    """Dockerfile 이 data/card_ids.json 을 복사하지 않으면 서버에서만 조용히 깨진다.

    로컬 테스트는 파일이 있어서 전부 통과하므로 여기서 잡지 않으면 못 잡는다.
    """
    df = (card_ids.ROOT / "server" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY data/card_ids.json" in df, "Dockerfile 에 기준선 COPY 가 없다"
    di = (card_ids.ROOT / ".dockerignore").read_text(encoding="utf-8")
    for line in di.splitlines():
        line = line.strip()
        assert line not in ("data/", "data/*", "data"), f".dockerignore 가 기준선을 막는다: {line}"
