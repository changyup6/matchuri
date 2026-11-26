"""Collection provenance survives parsing, export and every shared API data model."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import loader, user_reviews
from recsys import store

CRAWLER = Path(__file__).resolve().parents[2] / 'crawler'


def module(name):
    spec = importlib.util.spec_from_file_location('test_crawler_'+name, CRAWLER/(name+'.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kakao_parsing_and_runtime_export_preserve_sources(tmp_path):
    parser = module('yeoksam_full_parse')
    panel = {'summary': {'name': 'test'}, 'menu': {'menus': {'items': [{'name': 'test', 'price': 1000}]}}}
    data = {
        'stores': [parser.parse_store('s1', panel, '2026-09-15')],
        'menus': parser.parse_menus('s1', 'test', panel),
        'reviews': parser.parse_reviews('s1', {'latest': {'reviews': [{'review_id': 1, 'contents': 'test'}]}}, {}),
        'photos': parser.parse_photos('s1', 'FOOD', {'photos': [{'type': 'VENDOR', 'url': 'https://naver.example/image.jpg'}]}),
    }
    assert all(row['source_platform'] == 'kakao' for rows in data.values() for row in rows)
    # Explicit provenance wins; unknown is not rewritten as Kakao.
    data['reviews'] += [{**data['reviews'][0], 'review_id': 2, 'source_platform': 'naver'},
                        {**data['reviews'][0], 'review_id': 3, 'source_platform': None}]
    for name, rows in data.items():
        (tmp_path/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    out = tmp_path/'runtime'
    subprocess.run([sys.executable, str(CRAWLER/'build_runtime_data.py'), '--src', str(tmp_path), '--out', str(out)], check=True, capture_output=True)
    for name, rows in data.items():
        exported = [json.loads(line) for line in (out/(name+'.jsonl')).read_text().splitlines()]
        assert [r['source_platform'] for r in exported] == [r['source_platform'] for r in rows]


def test_platform_specific_review_collectors_and_csv_columns():
    common = dict(store_id='s1', name='test', hub='test', category='test', category_full='test',
                  address='test', url='https://example.com', avg_rating=None, review_count=1)
    kakao, naver = module('crawl_reviews_kakao'), module('crawl_reviews_naver')
    kr = kakao.normalize(common, {'reviews': [{'review_id': 1, 'contents': 'test'}]}, 1)
    nr = naver.normalize(common, {'items': [{'reviewId': 'n1', 'body': 'test'}]}, 1)
    assert kr[0]['source_platform'] == 'kakao'
    assert nr[0]['source_platform'] == 'naver'
    assert set(kr[0]) <= set(kakao.CSV_COLS) and set(nr[0]) <= set(naver.CSV_COLS)


def test_raw_envelope_records_collection_without_changing_response(tmp_path):
    sink = module('yeoksam_full').Sink(tmp_path)
    payload = {'store_id': 's1', 'data': {'contents': 'original'}}
    sink.write('reviews', payload)
    sink.close()
    row = json.loads((tmp_path/'reviews.jsonl').read_text())
    # 봉투에 붙는 필드가 둘이다 — 출처(source_platform)와 회차(run). 둘 다 떼고 나면
    # payload 가 그대로 남아야 한다(`crawler/yeoksam_full.py: Sink.write`).
    assert row.pop('source_platform') == 'kakao'
    assert row.pop('run')
    assert row == payload


def test_legacy_kakao_adapter_preserves_explicit_and_unknown_sources(tmp_path):
    path = tmp_path/'reviews.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in [
        {'store_id': 's1'}, {'store_id': 's2', 'source_platform': 'naver'},
        {'store_id': 's3', 'source_platform': None}]))
    assert [r['source_platform'] for r in store._read_jsonl(path)] == ['kakao', 'naver', None]
    assert loader._review({'store_id': 's1'}).source_platform is None
    assert loader._review({'store_id': 's1', 'source_platform': 'naver'}).source_platform == 'naver'


def test_backfill_is_additive_idempotent_and_atomic_on_invalid_input(tmp_path):
    backfill = module('backfill_source_platform').backfill
    path = tmp_path/'reviews.jsonl'
    rows = [{'review_id': 1, 'content': '원문\n둘째 줄'},
            {'review_id': 2, 'source_platform': 'naver'}, {'review_id': 3, 'source_platform': None}]
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
    assert backfill(path, 'kakao') == {'table': 'reviews', 'rows': 3, 'added': 1}
    after = [json.loads(line) for line in path.read_text().splitlines()]
    assert after == [{**rows[0], 'source_platform': 'kakao'}, *rows[1:]]
    original = path.read_bytes()
    assert backfill(path, 'kakao')['added'] == 0 and path.read_bytes() == original
    path.write_bytes(original+b'broken json\n')
    original = path.read_bytes()
    with pytest.raises(ValueError):
        backfill(path, 'kakao')
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('.reviews.jsonl.*'))


def test_api_models_and_review_routes_expose_source_platform():
    import main
    client = TestClient(main.app)
    cards = client.get('/cards?limit=4').json()
    cid = cards[0]['card_id']
    detail = client.get(f'/cards/{cid}').json()
    reviews = client.get(f'/cards/{cid}/reviews').json()
    assert reviews
    # 가게·메뉴는 카카오다. 리뷰는 네이버 방문자 리뷰(`reviews_naver.jsonl`)와
    # 우리 사용자 후기(`app/user_reviews.py`)가 같은 배열에 섞인다.
    # 앱이 id 를 int 로 읽으므로 **모든 리뷰 id 는 정수**이고, 출처마다 대역이 갈린다 —
    # 카카오는 양수, 네이버는 음수, 우리 후기는 10^12 위다. 셋이 겹치면 앱이 id 로
    # 리뷰를 찾을 때 엉뚱한 줄을 집는다.
    def review_ok(r):
        return isinstance(r['id'], int) and (
            (r['source_platform'] == 'kakao' and 0 < r['id'] < user_reviews.ID_BASE)
            or (r['source_platform'] == 'naver' and r['id'] < 0)
            or (r['source_platform'] == 'matchuri' and r['id'] >= user_reviews.ID_BASE))
    for card in [*cards, detail]:
        assert card['store']['source_platform'] == 'kakao'
        assert all(review_ok(r) for r in card['store']['reviews'])
    assert all(review_ok(r) for r in reviews)
    assert all(m['source_platform'] == 'kakao' for m in detail['menus'])
    schemas = client.get('/openapi.json').json()['components']['schemas']
    for name in ('Review', 'Store', 'Food'):
        prop = schemas[name]['properties']['source_platform']
        assert prop['description'] and prop['examples'] == ['kakao']
