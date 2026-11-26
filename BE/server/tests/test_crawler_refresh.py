# -*- coding: utf-8 -*-
"""주 1회 갱신 — 회차(run) 규칙.

데이터는 주마다 통째로 다시 받는다. 그때 두 가지가 조용히 어긋날 수 있다.

  ① 이어받기 표식(done.jsonl)이 남아 있어 **아무것도 다시 받지 않는다**
  ② panel.jsonl 이 추가 전용이라 **문 닫은 가게가 되살아난다**

둘 다 예외 없이 지나가고, 서비스에는 '지난주와 똑같은 데이터' 나 '없는 가게' 로 나타난다.
회차 표식이 그 둘을 막는다. 여기서 잠그는 것은 그 규칙이다.

크롤러는 서버 패키지가 아니라 파일 경로로 읽는다 — analysis/ 스크립트를 테스트하는
방식과 같다. `kakao_client`(curl_cffi 의존)를 가짜로 끼우는 것은 안전장치다. 지금은
그 import 가 `main()` 안에 있어 없어도 되지만, 모듈 최상단으로 돌아가면 이 스텁이
없으면 테스트가 import 단계에서 죽는다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

CRAWLER = Path(__file__).resolve().parents[2] / "crawler"


def _load(name: str):
    if "kakao_client" not in sys.modules:
        stub = types.ModuleType("kakao_client")
        stub.PHOTO_TAGS = []
        stub.KakaoMapClient = object
        sys.modules["kakao_client"] = stub
    spec = importlib.util.spec_from_file_location(name, CRAWLER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


full = _load("yeoksam_full")
parse = _load("yeoksam_full_parse")


def _done(tmp_path, rows):
    p = tmp_path / "done.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + chr(10) for r in rows),
                 encoding="utf-8")
    return p


# ─────────────────────────────── 수집: 무엇을 건너뛰는가
def test_지난_회차는_건너뛰지_않는다(tmp_path):
    """이게 깨지면 재수집이 0곳으로 끝나고 데이터가 영원히 낡는다."""
    p = _done(tmp_path, [{"store_id": "s1", "ok": True, "run": "2026-09-07"}])
    assert full.done_in_run(p, "2026-09-14") == set()


def test_같은_회차는_이어받는다(tmp_path):
    p = _done(tmp_path, [{"store_id": "s1", "ok": True, "run": "2026-09-14"},
                         {"store_id": "s2", "ok": True, "run": "2026-09-14"}])
    assert full.done_in_run(p, "2026-09-14") == {"s1", "s2"}


def test_실패는_다시_시도한다(tmp_path):
    """한 번의 네트워크 오류로 가게를 영영 놓치면 안 된다."""
    p = _done(tmp_path, [{"store_id": "s1", "ok": False, "reason": "panel_none",
                          "run": "2026-09-14"}])
    assert full.done_in_run(p, "2026-09-14") == set()


def test_회차_없는_옛_줄은_건너뛰지_않는다(tmp_path):
    """이 기능 이전에 모은 done.jsonl 은 전부 다시 받아야 한다."""
    p = _done(tmp_path, [{"store_id": "s1", "ok": True}])
    assert full.done_in_run(p, "2026-09-14") == set()


def test_깨진_줄은_넘어간다(tmp_path):
    p = tmp_path / "done.jsonl"
    p.write_text('{"store_id": "s1", "ok": true, "run": "r"}' + chr(10) + "쓰레기" + chr(10),
                 encoding="utf-8")
    assert full.done_in_run(p, "r") == {"s1"}


def test_파일이_없으면_전부_대상이다(tmp_path):
    assert full.done_in_run(tmp_path / "없다.jsonl", "r") == set()


# ─────────────────────────────── 파싱: 폐업을 어떻게 거르는가
def _panels(spec):
    return {sid: {"store_id": sid, "run": run} for sid, run in spec.items()}


def test_이번_회차에_응답한_가게만_남는다():
    """문 닫은 가게(이번 회차 레코드 없음)가 빠지는 것이 이 기능의 전부다."""
    panels = _panels({"s1": "2026-09-14", "s2": "2026-09-14", "s3": "2026-09-07",
                      "s4": "2026-09-14", "s5": "2026-09-14", "s6": "2026-09-14",
                      "s7": "2026-09-14", "s8": "2026-09-14", "s9": "2026-09-14",
                      "s10": "2026-09-14"})
    kept = parse.select_panels(panels, "latest")
    assert set(kept) == {"s1", "s2", "s4", "s5", "s6", "s7", "s8", "s9", "s10"}


def test_latest_는_가장_최근_회차다():
    panels = _panels({"a": "2026-09-07", "b": "2026-09-14"})
    assert parse.pick_run(panels, "latest") == "2026-09-14"


def test_all_은_거르지_않는다():
    panels = _panels({"a": "2026-09-07", "b": "2026-09-14"})
    assert set(parse.select_panels(panels, "all")) == {"a", "b"}


def test_회차_표식이_없으면_거르지_않는다():
    """이 기능 이전 데이터를 통째로 버리지 않는다."""
    panels = {"a": {"store_id": "a"}, "b": {"store_id": "b"}}
    assert set(parse.select_panels(panels, "latest")) == {"a", "b"}


def test_없는_회차를_주면_멈춘다():
    with pytest.raises(SystemExit):
        parse.pick_run(_panels({"a": "2026-09-14"}), "2026-01-01")


# ─────────────────────────────── 축소 게이트
def test_많이_줄면_멈춘다():
    """수집이 끊긴 것을 폐업으로 착각해 멀쩡한 가게를 지우면 안 된다."""
    panels = _panels({f"s{i}": ("2026-09-14" if i < 5 else "2026-09-07")
                      for i in range(10)})
    with pytest.raises(SystemExit):
        parse.select_panels(panels, "latest")


def test_허용하면_줄어도_쓴다():
    panels = _panels({f"s{i}": ("2026-09-14" if i < 5 else "2026-09-07")
                      for i in range(10)})
    kept = parse.select_panels(panels, "latest", allow_shrink=True)
    assert len(kept) == 5


def test_기준_안쪽_감소는_통과한다():
    """10%% 기준. 100곳 중 10곳이 빠지는 것은 정상 범위다."""
    panels = _panels({f"s{i}": ("2026-09-07" if i < 10 else "2026-09-14")
                      for i in range(100)})
    assert len(parse.select_panels(panels, "latest")) == 90


def test_입력을_바꾸지_않는다():
    """호출한 쪽이 원본 panels 를 계속 쓸 수 있어야 한다."""
    panels = _panels({"a": "2026-09-07", "b": "2026-09-14"})
    before = dict(panels)
    parse.select_panels(panels, "all")
    assert panels == before
