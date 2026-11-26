# -*- coding: utf-8 -*-
"""역삼동 음식점 전체(2,801곳)의 메뉴판 수집.

메뉴는 별도 엔드포인트가 아니라 장소 상세(panel3) 응답 안에 들어있다.

  GET place-api.map.kakao.com/places/panel3/{id}
  -> menu.menus.items[]  {product_id, name, price, price_text, desc,
                          photo_url, is_recommend, is_ai_mate,
                          recommend_reasons, ai_mate_desc, mod_at}

한 번의 호출로 영업시간·편의시설·태그도 같이 오므로 버리지 않고 store_info 에
따로 적는다(재수집하면 또 50분이 든다).

  python crawler/yeoksam_menus.py
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent

from kakao_client import KakaoMapClient

DATA = ROOT / "data" / "yeoksam"


def dig(d, *path, default=None):
    """중첩 dict 를 안전하게 파고든다. 어디서든 끊기면 default."""
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return d if d is not None else default


def open_hours_rows(oh: dict) -> list[dict]:
    """요일별 영업시간을 평평하게 편다."""
    out = []
    for wp in dig(oh, "week_from_today", "week_periods", default=[]) or []:
        for day in (wp.get("days") or []):
            on = day.get("on_days") or {}
            off = day.get("off_days") or {}
            out.append({
                "day": day.get("day_of_the_week_desc"),
                "time": on.get("start_end_time_desc") or off.get("day_desc"),
                "breaks": on.get("break_times_desc") or [],
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    stores = [json.loads(l) for l in (DATA / "stores.jsonl").open(encoding="utf-8")]
    menu_path = DATA / "menus.jsonl"
    info_path = DATA / "store_info.jsonl"

    done: set[str] = set()
    if info_path.exists():
        for l in info_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(l)["store_id"])
            except Exception:
                pass
        print(f"이어받기: 이미 {len(done):,}곳")

    todo = [s for s in stores if s["store_id"] not in done]
    print(f"대상 {len(stores):,}곳 · 남은 {len(todo):,}곳 · "
          f"예상 {len(todo)*(args.delay+0.5)/60:.0f}분\n")

    cl = KakaoMapClient(delay=args.delay, jitter=0.3)
    t0 = time.time()
    n_menu = 0
    n_nomenu = 0
    fails = 0
    types = collections.Counter()

    fm = menu_path.open("a", encoding="utf-8")
    fi = info_path.open("a", encoding="utf-8")
    try:
        for i, s in enumerate(todo, 1):
            sid = s["store_id"]
            try:
                d = cl.place_detail(sid)
            except Exception as exc:
                fails += 1
                print(f"  [!] {sid} {exc!r}", flush=True)
                continue
            if not d:
                fails += 1
                d = {}

            menus = dig(d, "menu", "menus", default={}) or {}
            items = menus.get("items") or []
            types[menus.get("menu_type")] += 1
            if not items:
                n_nomenu += 1

            for idx, it in enumerate(items):
                fm.write(json.dumps({
                    "store_id": sid,
                    "store_name": s.get("name"),
                    "cat2": s.get("cat2"), "cat3": s.get("cat3"),
                    "menu_index": idx,
                    "product_id": it.get("product_id"),
                    "menu_name": it.get("name"),
                    # price 가 없고 price_text 만 있는 가게가 있다("시가" 등)
                    "price": it.get("price"),
                    "price_text": it.get("price_text"),
                    "is_recommend": bool(it.get("is_recommend")),
                    "is_ai_mate": bool(it.get("is_ai_mate")),
                    "description": it.get("desc"),
                    "ai_mate_desc": it.get("ai_mate_desc"),
                    "recommend_reasons": it.get("recommend_reasons") or [],
                    # 사진은 URL 만. 이미지 파일은 받지 않는다.
                    "photo_url": it.get("photo_url"),
                    "mod_at": it.get("mod_at"),
                }, ensure_ascii=False) + "\n")
                n_menu += 1

            add = d.get("place_add_info") or {}
            fi.write(json.dumps({
                "store_id": sid,
                "name": s.get("name"),
                "menu_count": len(items),
                "menu_type": menus.get("menu_type"),
                "menu_updated_at": menus.get("items_updated_at"),
                "open_headline": dig(d, "open_hours", "headline", "display_text"),
                "open_hours": open_hours_rows(d.get("open_hours") or {}),
                "tags": add.get("tags") or [],
                "facilities": [x.get("text") for x in
                               (add.get("store_facility_icons") or [])],
                "parking": dig(add, "simple_parking_infos", "summary"),
                "ai_store_infos": dig(add, "ai_mate", "store_infos", default=[]),
                "phones": [x.get("tel") for x in
                           (dig(d, "summary", "phone_numbers", default=[]) or [])],
                "payments": [x.get("img_text") for x in
                             (dig(d, "summary", "payments", default=[]) or [])],
            }, ensure_ascii=False) + "\n")
            fm.flush()
            fi.flush()

            if i % 100 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(todo)} · 메뉴 {n_menu:,}건 · 메뉴없음 {n_nomenu} · "
                      f"실패 {fails} · 남은 {(len(todo)-i)*el/i/60:.0f}분", flush=True)
    finally:
        fm.close()
        fi.close()

    el = time.time() - t0
    print(f"\n완료 {el/60:.0f}분 · 메뉴 {n_menu:,}건 · 메뉴없는 가게 {n_nomenu:,} · 실패 {fails}")
    print(f"menu_type: {dict(types)}")
    print(f"-> {menu_path}\n-> {info_path}")


if __name__ == "__main__":
    main()
