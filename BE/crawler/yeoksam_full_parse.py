# -*- coding: utf-8 -*-
"""yeoksam_full 원본(jsonl) -> 정규화 테이블.

원본은 건드리지 않는다. 필드를 더 뽑고 싶으면 이 파일만 고치고 다시 돌린다.

출력 {out}/parsed/
  stores.jsonl      가게 1행: 기본정보·지하철·영업시간·편의·태그·AI요약·리뷰통계(리뷰태그 집계)·
                    사진집계·방문자시간대·배달연동
  menus.jsonl       메뉴 1행
  photos.jsonl      사진 1행 (분류 tag + 출처 type + URL)
  reviews.jsonl     리뷰 1행 (전문·리뷰태그·작성자 프로필·사진 메타)
  blog_posts.jsonl  블로그 리뷰 1행

**폐업 처리.** `panel.jsonl` 은 추가 전용이라 한 번 수집된 가게는 영원히 남는다. 전체를
그대로 내보내면 문 닫은 가게가 매주 되살아난다. 그래서 **이번 회차에 응답한 가게만**
내보낸다(`--run`). 회차 표식은 `yeoksam_full.py` 가 모든 줄에 적는다.

리뷰·사진·블로그는 회차로 거르지 않는다. 가게는 응답했는데 리뷰 호출만 실패한 경우
직전 회차의 리뷰를 쓰는 편이 낫다 — 빈 값보다 낫고, 가게가 사라지는 것도 아니다.

**줄어들면 멈춘다.** 수집이 중간에 끊기거나 차단당하면 '이번 회차에 본 가게' 가 크게
줄어드는데, 그대로 내보내면 멀쩡한 가게 수백 곳이 서비스에서 조용히 사라진다. 기본 10%
넘게 줄면 쓰지 않고 멈춘다. 진짜 폐업이 그만큼이면 `--allow-shrink` 로 넘긴다.

  python crawler/yeoksam_full_parse.py [--dir data/yeoksam_full]
  python crawler/yeoksam_full_parse.py --run 2026-09-14      # 회차 지정
  python crawler/yeoksam_full_parse.py --run all             # 옛 동작(전부). 폐업이 남는다
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dig(d, *path, default=None):
    for k in path:
        if isinstance(d, dict):
            d = d.get(k)
        elif isinstance(d, list) and isinstance(k, int) and k < len(d):
            d = d[k]
        else:
            return default
    return d if d is not None else default


def last_by(path: Path, key):
    """같은 키의 중복 응답(재수집)은 마지막 것만."""
    out = {}
    if not path.exists():
        return out
    for l in path.open(encoding="utf-8"):
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        out[key(r)] = r
    return out


def open_hours_rows(oh):
    rows = []
    for wp in dig(oh, "week_from_today", "week_periods", default=[]) or []:
        for day in day_list(wp):
            on = day.get("on_days") or {}
            off = day.get("off_days") or {}
            rows.append({"day": day.get("day_of_the_week_desc"),
                         "time": on.get("start_end_time_desc") or off.get("day_desc"),
                         "breaks": on.get("break_times_desc") or []})
    return rows


def day_list(wp):
    return wp.get("days") or []


def parse_store(sid, panel, fetched_at, tab=None):
    sm = panel.get("summary") or {}
    add = panel.get("place_add_info") or {}
    ai = panel.get("ai_mate") or {}
    kr = panel.get("kakaomap_review") or {}
    # 사장님 차단 가게는 panel 에 리뷰 섹션이 없다 -> 리뷰 탭 응답의 통계로 대체
    if not kr.get("score_set") and tab and tab.get("score_set"):
        kr = tab
    sc = kr.get("score_set") or {}
    tag_name = {t["id"]: t["name"] for t in (kr.get("strength_description") or [])}
    vis = panel.get("visitor") or {}
    fw = dig(panel, "find_way", "subway", default={}) or {}
    menus = dig(panel, "menu", "menus", default={}) or {}

    return {
        "source_platform": "kakao", "store_id": sid,
        "name": sm.get("name"),
        "status": sm.get("status"),
        "cat1": dig(sm, "category", "name1"),
        "cat2": dig(sm, "category", "name2"),
        "cat3": dig(sm, "category", "name3"),
        "address": dig(sm, "address", "disp"),
        "address_jibun": dig(sm, "address", "jibun"),
        "address_road": dig(sm, "address", "road"),
        "zone_no": dig(sm, "address", "base_zone_no"),
        "lat": dig(sm, "point", "lat"), "lon": dig(sm, "point", "lon"),
        "region": [r.get("name") for r in (sm.get("regions") or [])],
        "phones": [x.get("tel") for x in (sm.get("phone_numbers") or [])],
        "payments": [x.get("img_text") for x in (sm.get("payments") or [])],
        "roadview_url": dig(sm, "road_view", "url"),
        # 지하철
        "subway_station": fw.get("station_simple_name"),
        "subway_exit": fw.get("exit_num"),
        "subway_distance_m": fw.get("to_exit_distance"),
        "subway_minutes": fw.get("to_exit_minute"),
        "subway_lines": [x.get("subway_line_icon_name") for x in (fw.get("subway_list") or [])],
        # 영업시간
        "open_headline": dig(panel, "open_hours", "headline", "display_text"),
        "open_headline_info": dig(panel, "open_hours", "headline", "display_text_info"),
        "open_hours": open_hours_rows(panel.get("open_hours") or {}),
        # 편의·태그
        "tags": add.get("tags") or [],
        "facilities": [x.get("text") for x in (add.get("store_facility_icons") or [])],
        "parking_summary": dig(add, "simple_parking_infos", "summary"),
        "parking_texts": dig(add, "simple_parking_infos", "texts", default=[]),
        "detail_infos": [{
            "title": x.get("title"), "summary": x.get("summary"),
            "items": [dig(it, "icon", "text") for it in (x.get("items") or [])],
        } for x in (add.get("full_detail_infos") or [])],
        # AI 요약
        "ai_title": dig(ai, "summary", "title"),
        "ai_keywords": dig(ai, "summary", "contents", default=[]),
        "ai_summary": dig(ai, "bottom_sheet", "summary"),
        "ai_sheets": [{"title": s.get("title"), "keywords": s.get("keywords") or [],
                       "text": s.get("text"),
                       "list": [{"title": x.get("title"), "keywords": x.get("keywords") or []}
                                for x in (s.get("list") or [])]}
                      for s in (dig(ai, "bottom_sheet", "sheets", default=[]) or [])],
        "ai_store_infos": [{"title": x.get("title"), "summary": x.get("summary")}
                           for x in (dig(add, "ai_mate", "store_infos", default=[]) or [])],
        "ai_facilities": [x.get("text") for x in
                          (dig(add, "ai_mate", "store_facility_icons", default=[]) or [])],
        # 배달·예약 연동
        "delivery": [{"type": x.get("connect_type"),
                      "service": dig(x, "detail", "service_type"),
                      "url": dig(x, "detail", "mobile_web_out_link_url")}
                     for x in (panel.get("biz_connects") or [])],
        "booking_available": dig(panel, "my_store_notice", "is_available_booking"),
        # 사장님이 리뷰 노출을 차단한 가게: panel 에 리뷰 섹션이 아예 없다.
        # (리뷰 탭 API 로는 여전히 받을 수 있어 reviews.jsonl 에는 존재)
        "review_blocked_by_owner": bool(dig(panel, "restrict", "review_read", "is_restrict")),
        "review_block_code": dig(panel, "restrict", "review_read", "code"),
        # 리뷰 통계 + 리뷰 태그 집계
        "review_count": sc.get("review_count") or 0,
        "review_avg": sc.get("average_score"),
        "review_photo_count": sc.get("photo_count") or 0,
        "review_tags": {tag_name.get(x["id"], str(x["id"])): x["count"]
                        for x in (sc.get("strength_counts") or [])},
        "review_tag_uv": sc.get("strength_uv"),
        "blog_count": dig(panel, "blog_review", "review_count", default=0),
        # 사진 집계 (분류별)
        "photo_counts": dig(panel, "photos", "counts", default={}),
        # 방문자 시간대 (카카오맵 조회 UV, -1 = 데이터 없음)
        "visitor_uv": {k: v for k, v in vis.items() if k != "labels"},
        # 길안내 포인트
        "navi_guides": [{"name": g.get("name"), "type": g.get("type"),
                         "lat": dig(g, "point", "lat"), "lon": dig(g, "point", "lon")}
                        for g in (panel.get("navi_guides") or [])],
        "menu_count": len(menus.get("items") or []),
        "menu_type": menus.get("menu_type"),
        "menu_updated_at": menus.get("items_updated_at"),
        "trend_rank_card": dig(panel, "trend_rank", "show_ranking_card"),
        "fetched_at": fetched_at,
    }


def parse_menus(sid, name, panel):
    items = dig(panel, "menu", "menus", "items", default=[]) or []
    return [{
        "source_platform": "kakao", "store_id": sid, "store_name": name, "menu_index": i,
        "product_id": it.get("product_id"), "menu_name": it.get("name"),
        "price": it.get("price"), "price_text": it.get("price_text"),
        "is_recommend": bool(it.get("is_recommend")),
        "is_ai_mate": bool(it.get("is_ai_mate")),
        "description": it.get("desc"), "ai_mate_desc": it.get("ai_mate_desc"),
        "recommend_reasons": it.get("recommend_reasons") or [],
        "photo_url": it.get("photo_url"), "mod_at": it.get("mod_at"),
    } for i, it in enumerate(items)]


def parse_photos(sid, tag, data):
    out = []
    for p in data.get("photos") or []:
        owner = dig(p, "kakaomap_review_photo_meta", "owner", default={}) or {}
        out.append({
            "source_platform": "kakao", "store_id": sid, "tag": tag, "type": p.get("type"),
            "url": p.get("url"), "title": p.get("title") or "",
            "photo_id": p.get("photo_id"), "review_id": p.get("review_id"),
            "media_type": p.get("media_type"),
            "registered_at": p.get("registered_at"),
            "blog_url": p.get("blog_link_url"),
            "author_id": owner.get("map_user_id"),
        })
    return out


def parse_reviews(sid, by_order, tag_name):
    """추천순·최신순 합집합. 같은 review_id 는 한 번만."""
    seen = {}
    for order, data in by_order.items():
        for r in data.get("reviews") or []:
            rid = r.get("review_id")
            if rid in seen:
                seen[rid]["orders"].append(order)
                continue
            m = r.get("meta") or {}
            o = m.get("owner") or {}
            seen[rid] = {
                "source_platform": "kakao", "store_id": sid, "review_id": rid,
                "rating": r.get("star_rating"),
                "content": (r.get("contents") or "").strip(),
                "date": r.get("registered_at"), "updated_at": r.get("updated_at"),
                "status": r.get("status"),
                "photo_count": r.get("photo_count") or 0,
                "photos": [{"url": ph.get("url"), "photo_id": ph.get("photo_id"),
                            "media_type": ph.get("media_type"),
                            "view_count": dig(ph, "meta", "view_count"),
                            "like_count": dig(ph, "meta", "like_count")}
                           for ph in (r.get("photos") or [])],
                "strength_ids": r.get("strength_ids") or [],
                "strength_tags": [tag_name.get(x, str(x)) for x in (r.get("strength_ids") or [])],
                "like_count": m.get("like_count") or 0,
                "is_owner_pick": bool(m.get("is_place_owner_pick")),
                "author_id": o.get("map_user_id"),
                "author_nickname": o.get("nickname"),
                "author_level": dig(o, "timeline_level", "now_level"),
                "author_review_count": o.get("review_count"),
                "author_follower_count": o.get("follower_count"),
                "author_avg_score": o.get("average_score"),
                "author_profile_image": o.get("profile_image_url"),
                "orders": [order],
            }
    return list(seen.values())


def parse_blog(sid, data):
    return [{
        "source_platform": "kakao", "store_id": sid, "blog_id": b.get("review_id"),
        "title": b.get("title"), "contents": b.get("contents"),
        "author": b.get("author"), "registered_at": b.get("registered_at"),
        "origin_url": b.get("origin_url"),
        "photo_count": b.get("photo_count") or 0,
        "photo_urls": [p.get("url") for p in (b.get("photos") or []) if p.get("url")],
    } for b in (data.get("reviews") or [])]


def pick_run(panels: dict, want: str) -> str | None:
    """쓸 회차를 정한다. `None` 은 '거르지 않는다'.

    `latest` 는 원본에 있는 회차 중 가장 큰 값이다. 회차가 하나도 없으면(이 기능 이전에
    모은 데이터) 거르지 않는다 — 옛 데이터를 통째로 버리지 않기 위해서다.
    """
    if want == "all":
        return None
    runs = {r.get("run") for r in panels.values() if r.get("run")}
    if want != "latest":
        if want not in runs:
            raise SystemExit(f"그런 회차가 없다: {want} (있는 것: {sorted(runs) or '없음'})")
        return want
    if not runs:
        print("[알림] 원본에 회차 표식이 없다(옛 수집). 전부 내보낸다 — 폐업이 섞여 있을 수 있다.")
        return None
    return max(runs)


def select_panels(panels: dict, want: str, *, max_shrink: float = 0.10,
                  allow_shrink: bool = False) -> dict:
    """이번 회차에 응답한 가게만 남긴다. 너무 많이 줄면 `SystemExit` 로 멈춘다.

    `panels` 는 `last_by` 가 만든 store_id -> 마지막 panel 레코드다. 문 닫은 가게는
    이번 회차 레코드가 없으므로 자연히 빠진다.
    """
    known = len(panels)
    run = pick_run(panels, want)
    if run is None:
        return panels
    kept = {sid: r for sid, r in panels.items() if r.get("run") == run}
    gone = known - len(kept)
    print(f"회차 {run}: 아는 가게 {known:,}곳 중 {len(kept):,}곳이 응답 (빠짐 {gone:,}곳)")
    if known and gone > known * max_shrink and not allow_shrink:
        print(f"멈춘다: {gone:,}곳({gone/known:.1%})이 빠졌다. 기준은 {max_shrink:.0%} 다.")
        print("  수집이 끊겼거나 차단당했을 가능성이 크다.")
        print(f"  먼저 `yeoksam_full.py --run {run}` 로 이어받아라.")
        print("  정말 폐업이면 --allow-shrink 를 준다.")
        raise SystemExit(1)
    return kept


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "yeoksam_full"))
    ap.add_argument("--run", default="latest",
                    help="이 회차에 응답한 가게만 내보낸다. latest(기본)·회차이름·all")
    ap.add_argument("--max-shrink", type=float, default=0.10,
                    help="가게 수가 이 비율 넘게 줄면 멈춘다 (기본 0.10 = 10%%)")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="줄어도 그대로 쓴다. 진짜 폐업이 많을 때만")
    args = ap.parse_args()
    d = Path(args.dir)
    out = d / "parsed"
    out.mkdir(parents=True, exist_ok=True)

    panels = last_by(d / "panel.jsonl", lambda r: r["store_id"])
    panels = select_panels(panels, args.run, max_shrink=args.max_shrink,
                           allow_shrink=args.allow_shrink)
    reviews = collections.defaultdict(dict)
    for (sid, order), r in last_by(d / "reviews.jsonl",
                                   lambda r: (r["store_id"], r["order"])).items():
        reviews[sid][order] = r["data"]
    photos = last_by(d / "photos.jsonl", lambda r: (r["store_id"], r["tag"], r["page"]))
    blogs = last_by(d / "blog.jsonl", lambda r: (r["store_id"], r["page"]))

    n = collections.Counter()
    with (out / "stores.jsonl").open("w", encoding="utf-8") as fs, \
         (out / "menus.jsonl").open("w", encoding="utf-8") as fm, \
         (out / "reviews.jsonl").open("w", encoding="utf-8") as fr, \
         (out / "photos.jsonl").open("w", encoding="utf-8") as fp, \
         (out / "blog_posts.jsonl").open("w", encoding="utf-8") as fb:
        for sid, rec in panels.items():
            panel = rec["data"]
            tab = (reviews.get(sid) or {}).get("RECOMMENDED")
            st = parse_store(sid, panel, rec.get("fetched_at"), tab=tab)
            fs.write(json.dumps(st, ensure_ascii=False) + "\n")
            n["stores"] += 1
            for m in parse_menus(sid, st["name"], panel):
                fm.write(json.dumps(m, ensure_ascii=False) + "\n")
                n["menus"] += 1
            tag_name = {t["id"]: t["name"] for t in
                        (dig(panel, "kakaomap_review", "strength_description", default=[])
                         or dig(tab or {}, "strength_description", default=[]) or [])}
            for r in parse_reviews(sid, reviews.get(sid, {}), tag_name):
                fr.write(json.dumps(r, ensure_ascii=False) + "\n")
                n["reviews"] += 1
            for (psid, tag, page), prec in photos.items():
                if psid != sid:
                    continue
                for p in parse_photos(sid, tag, prec["data"]):
                    fp.write(json.dumps(p, ensure_ascii=False) + "\n")
                    n["photos"] += 1
            for (bsid, page), brec in blogs.items():
                if bsid != sid:
                    continue
                for b in parse_blog(sid, brec["data"]):
                    fb.write(json.dumps(b, ensure_ascii=False) + "\n")
                    n["blog_posts"] += 1

    print(f"파싱 완료: {dict(n)}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
