#!/usr/bin/env python3
"""Posthoc decomposition only: no model prediction or policy change.

python audit-posthoc-card-decomposition.py --input-dir .
Inputs may be plain JSON/CSV or gzip copies with the same contents.
"""
from __future__ import annotations
import argparse
import collections
import csv
import gzip
import hashlib
import json
from pathlib import Path

MODELS = ('operational', 'regularized', 'regularized_context_floor')
STATUSES = ('new_store_new_brand', 'current_liked', 'past_liked',
            'samebrand_new_store', 'current_disliked', 'past_disliked')
ARMS = ('none', 'neutral', 'prefer', 'avoid')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def locate(root, name):
    path = root / name
    return path if path.exists() else root / (name + '.gz')


def text_file(path):
    return (gzip.open if path.suffix == '.gz' else open)(path, 'rt', encoding='utf-8-sig', newline='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.input_dir.resolve()
    paths = [locate(root, 'fixtures.json'), locate(root, 'actual-cards.csv')]
    source_sha256 = {str(p): digest(p) for p in paths}
    source_sha256[str(Path(__file__).resolve())] = digest(Path(__file__).resolve())
    with text_file(paths[0]) as stream:
        fixture = json.load(stream)
    runs = {run['id']: run for run in fixture['runs']}
    assert len(runs) == len(fixture['runs'])
    brands = fixture['brand_keys']
    catalogue = set(fixture['catalog_ids'])
    info = {}
    for case, run in runs.items():
        past, current = dict(run['past']), dict(run['current'])
        assert not (past.keys() & current.keys())
        observed = {**past, **current}
        info[case] = (past, current, observed, {brands[sid] for sid in observed},
                      set(run['context_ids']),
                      set(run['evaluation_groups']['target']),
                      set(run['evaluation_groups']['comparison']))
        assert not (info[case][-1] & info[case][-2])
    counts = collections.defaultdict(collections.Counter)
    slates = collections.defaultdict(list)
    novelty_balances = collections.defaultdict(lambda: collections.defaultdict(int))
    matched_meta = {}
    audits = collections.Counter()
    with text_file(paths[1]) as stream:
        for row in csv.DictReader(stream):
            run = runs[row['case']]
            sid, model = row['store_id'], row['model']
            assert sid in catalogue and model in MODELS
            assert row['prior'] == run['prior']
            assert row['scope'] == run['evidence_scope']
            assert row['axis'] == run['axis']
            assert row['food'] == run['source_groups']['food']
            assert int(row['seed']) == run['seed']
            past, current, observed, observed_brands, context, target, comparison = info[row['case']]
            if sid in current:
                status = 'current_liked' if current[sid] else 'current_disliked'
            elif sid in past:
                status = 'past_liked' if past[sid] else 'past_disliked'
            elif brands[sid] in observed_brands:
                status = 'samebrand_new_store'
            else:
                status = 'new_store_new_brand'
            novel = status == 'new_store_new_brand'
            is_context = sid in context
            group = 'target' if sid in target else 'comparison' if sid in comparison else ''
            assert row['unseen_store_and_brand'] == str(novel)
            assert row['current_context'] == str(is_context)
            assert row['groups'] == group
            audits['novelty_flags_verified'] += 1
            audits['context_flags_verified'] += 1
            audits['evaluation_group_flags_verified'] += 1
            key = (run['evidence_scope'], model)
            counts[key]['slots'] += 1
            counts[key]['all_' + status] += 1
            if is_context:
                counts[key]['context_cards'] += 1
                counts[key]['context_' + status] += 1
            slates[(row['case'], model)].append((int(row['rank']), sid))
            matched = (run['context'], run['dimension'], run['seed'], model)
            matched_meta[matched] = dict(scope=run['evidence_scope'], food=run['source_groups']['food'],
                                        axis=run['axis'], history_mode=run['history_mode'], model=model)
            novelty_balances[matched][run['prior']] += int(novel) * (int(sid in target) - int(sid in comparison))
    assert set(slates) == {(case, model) for case in runs for model in MODELS}
    for values in slates.values():
        assert len(values) == 4 and {rank for rank, _ in values} == {1, 2, 3, 4}
        assert len({sid for _, sid in values}) == 4
    audits['unique_four_card_slates_verified'] = len(slates)
    breakdowns = []
    for (scope, model), counter in sorted(counts.items()):
        all_split = {status: counter['all_' + status] for status in STATUSES}
        context_split = {status: counter['context_' + status] for status in STATUSES}
        assert sum(all_split.values()) == counter['slots']
        assert sum(context_split.values()) == counter['context_cards']
        breakdowns.append(dict(scope=scope, model=model, all_card_slots=counter['slots'],
                               context_cards=counter['context_cards'],
                               all_cards_by_status=all_split, context_cards_by_status=context_split))
    direction_groups = collections.defaultdict(collections.Counter)
    for key, balance in novelty_balances.items():
        assert set(balance) == set(ARMS)
        meta = matched_meta[key]
        change = balance['prefer'] - balance['avoid']
        sign = 'positive' if change > 0 else 'negative' if change < 0 else 'unchanged'
        for axis in ('all_axes', meta['axis']):
            group = (meta['scope'], meta['model'], axis)
            direction_groups[group]['matched_groups'] += 1
            direction_groups[group][sign] += 1
            direction_groups[group]['sum_card_balance_change'] += change
    directions = []
    for (scope, model, axis), counter in sorted(direction_groups.items()):
        assert sum(counter[s] for s in ('positive', 'negative', 'unchanged')) == counter['matched_groups']
        directions.append(dict(scope=scope, model=model, axis=axis,
                               **{key: counter[key] for key in ('matched_groups', 'positive', 'negative',
                                                                'unchanged', 'sum_card_balance_change')}))
    for path in paths:
        assert digest(path) == source_sha256[str(path)]
    result = dict(schema_version=1, passed=True, posthoc=True, model_rerun=False, production_changed=False,
                  source_sha256=source_sha256,
                  definitions={
                      'purpose': 'Posthoc decomposition of existing final-four outputs; not a prespecified primary endpoint.',
                      'unit': 'One card slot per hypothetical state and model; duplicated catalogue/seeds are not independent users.',
                      'new_store_new_brand': 'Neither this store nor its brand occurs in the actual past or current feedback for that arm.',
                      'current_liked': 'This exact store was liked in the current session.',
                      'past_liked': 'This exact store was liked in the past history, not the current session.',
                      'samebrand_new_store': 'This store was not observed, but its brand was observed; not automatically a liked store.',
                      'current_disliked': 'This exact store was disliked in the current session.',
                      'past_disliked': 'This exact store was disliked in past history, not current session.',
                      'context_cards': 'Cards with principal/co-main evidence for core or standalone menu evidence for menu; other-attribute evidence may come from another menu.',
                      'novel_card_balance': 'Among the actual four cards, count(new A-group cards) minus count(new B-group cards).',
                      'direction': 'Novel card balance in A-preferred history minus balance in B-preferred history. Positive is a directional count response, not accuracy or exposure probability.',
                      'unchanged': 'Same A-minus-B novel-card count balance, not necessarily the same store names or ranking.',
                      'comparison_limit': 'Operational excludes self feedback from similarity components for observed stores; research outputs here used full posterior predictions including self feedback. New-card decomposition does not make full-slate policies equivalent.',
                      'scope_limit': 'Core and menu share catalogue stores; all_axes includes same-food category generalization as well as cross-food contrasts.'},
                  audits=dict(audits), breakdowns=breakdowns, novel_card_directions=directions)
    output = (args.output or root / 'posthoc-card-decomposition.json').resolve()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), sha256=digest(output), audits=dict(audits)), ensure_ascii=False))


if __name__ == '__main__':
    main()
