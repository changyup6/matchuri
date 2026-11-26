"""Turn every frozen paired case into directional and final-card evidence."""
from pathlib import Path
from collections import defaultdict, Counter
import csv, hashlib, json, math, statistics
import numpy as np

O=Path(__file__).resolve().parent
MODELS=['operational','regularized','regularized_context_floor']
ARMS=['none','neutral','prefer','avoid']
EPS=1e-10
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stats(values):
    v=[float(x) for x in values if x is not None]
    return {'n':len(v),'mean':statistics.mean(v) if v else None,
        'min':min(v) if v else None,'max':max(v) if v else None,
        'positive':sum(x>EPS for x in v),'negative':sum(x< -EPS for x in v),
        'unchanged':sum(abs(x)<=EPS for x in v)}
def gap(m):return m['groups']['target']['heldout']['mean']-m['groups']['comparison']['heldout']['mean']
def hits(m,g,where='cards',scope='all'):return m['groups'][g][scope]['hits'][where]
def cardbalance(m):return hits(m,'target')-hits(m,'comparison')
def csvout(name,rows):
    with (O/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)

def run():
    fixture=json.loads((O/'fixtures.json').read_text());coverage=json.loads((O/'coverage.json').read_text())
    rows=[];part_for={};metadata=[];zs=[];sources={str(p):sha(p) for p in [O/'fixtures.json',O/'coverage.json',Path(__file__)]}
    for i in range(2):
        p=O/f'part-{i}/run/results.json';zpath=p.parent/'predictions.npz'
        result=json.loads(p.read_text());metadata.append({k:v for k,v in result.items() if k!='rows'})
        for r in result['rows']:part_for[r['id']]=i
        rows.extend(result['rows']);zs.append(np.load(zpath))
        sources.update({str(p):sha(p),str(zpath):sha(zpath)})
    assert len(rows)==len(fixture['runs'])==len(part_for)
    assert set(part_for)=={r['id'] for r in fixture['runs']}
    fi={r['id']:r for r in fixture['runs']}
    indexed={(r['context'],r['dimension'],r['seed'],r['prior']):r for r in rows}
    cases=[];vectors=[];cards=[]
    cells=defaultdict(list)
    for r in rows:
        if r['prior']!='neutral':continue
        key=(r['context'],r['dimension'],r['seed'])
        arms={arm:indexed[(*key,arm)] for arm in ARMS}
        base={k:r[k] for k in ['context','dimension','seed','axis','pair_name','evidence_scope','history_mode','attribute_a','attribute_b']}
        base.update(food=r['source_groups']['food'],history_n=r['history_n'])
        for model in MODELS:
            ms={a:v['models'][model] for a,v in arms.items()}
            values={a:gap(m) for a,m in ms.items()}
            case={**base,'model':model,'a_vs_neutral':values['prefer']-values['neutral'],
                'b_vs_neutral':values['neutral']-values['avoid'],
                'score_switch':values['prefer']-values['avoid'],
                'card_switch':cardbalance(ms['prefer'])-cardbalance(ms['avoid']),
                'gap_none':values['none'],'gap_neutral':values['neutral'],
                'gap_prefer':values['prefer'],'gap_avoid':values['avoid'],
                'heldout_a':ms['neutral']['groups']['target']['heldout']['count'],
                'heldout_b':ms['neutral']['groups']['comparison']['heldout']['count']}
            for arm,m in ms.items():
                case.update({arm+'_context_cards':m['context_card_hits'],
                    arm+'_new_context_cards':m['context_unseen_card_hits'],arm+'_disliked_cards':m['disliked_cards'],
                    arm+'_target_cards':hits(m,'target'),arm+'_comparison_cards':hits(m,'comparison'),
                    arm+'_all_card_slots':len(m['cards']),
                    arm+'_target_top10':hits(m,'target','top10'),arm+'_comparison_top10':hits(m,'comparison','top10'),
                    arm+'_actual_top10_slots':min(10,len(m['top20'])),
                    arm+'_context_top10_slots':len(m['context_top10'])})
            cases.append(case);cells[(r['context'],r['dimension'],model)].append(case)
        # Real latent vector change, independent of any semantic coordinate label.
        arr={a:zs[part_for[v['id']]][v['id']+'__research_total'] for a,v in arms.items()}
        for arm in ['prefer','avoid']:
            v,b=arr[arm],arr['neutral'];d=v-b
            norm=float(np.linalg.norm(v));bn=float(np.linalg.norm(b));denom=norm*bn
            cos=float(np.clip(v@b/denom,-1,1)) if denom>1e-15 else None
            top=np.argsort(-np.abs(d))[:5]
            vectors.append({**base,'arm':arm,'norm':norm,'neutral_norm':bn,'distance_from_neutral':float(np.linalg.norm(d)),
                'angle_degrees':float(np.degrees(np.arccos(cos))) if cos is not None else None,
                'largest_coordinate_changes':[{'coordinate':int(j),'neutral':float(b[j]),'preferred':float(v[j]),'change':float(d[j])} for j in top]})
    for r in rows:
        for model,m in r['models'].items():
            for i,card in enumerate(m['cards'],1):
                cards.append({'case':r['id'],'food':r['source_groups']['food'],'scope':r['evidence_scope'],
                    'axis':r['axis'],'pair':r['pair_name'],'seed':r['seed'],'prior':r['prior'],'model':model,'rank':i,
                    **{k:card[k] for k in ['store_id','name','mean','std','score','global_rank','current_context','heldout','unseen_store_and_brand']},
                    'groups':';'.join(card['groups'])})
    summaries=[];flat=[]
    for (context,dimension,model),cs in sorted(cells.items()):
        ref=cs[0];cov=next(x for x in coverage['coverage'] if x['context']==context and x['dimension']==dimension)
        summary={k:ref[k] for k in ['context','food','dimension','axis','pair_name','evidence_scope','history_mode','attribute_a','attribute_b','history_n','model']}
        summary.update(seeds=len(cs),sparse=cov['sparse'],population_a=cov['group_a'],population_b=cov['group_b'],
            heldout_a_range=[min(x['heldout_a'] for x in cs),max(x['heldout_a'] for x in cs)],
            heldout_b_range=[min(x['heldout_b'] for x in cs),max(x['heldout_b'] for x in cs)],
            score_switch=stats([x['score_switch'] for x in cs]),
            a_vs_neutral=stats([x['a_vs_neutral'] for x in cs]),b_vs_neutral=stats([x['b_vs_neutral'] for x in cs]),
            card_switch=stats([x['card_switch'] for x in cs]),arms={})
        for arm in ARMS:
            summary['arms'][arm]={key:sum(x[arm+'_'+key] for x in cs) for key in [
                'context_cards','new_context_cards','disliked_cards','target_cards','comparison_cards','all_card_slots',
                'target_top10','comparison_top10','actual_top10_slots','context_top10_slots']}
        summaries.append(summary)
        fl={k:v for k,v in summary.items() if not isinstance(v,(dict,list))}
        for metric in ['score_switch','a_vs_neutral','b_vs_neutral','card_switch']:
            fl.update({metric+'_'+k:v for k,v in summary[metric].items()})
        fl.update({'heldout_a_min':summary['heldout_a_range'][0],'heldout_b_min':summary['heldout_b_range'][0]})
        for arm,data in summary['arms'].items():fl.update({arm+'_'+k:v for k,v in data.items()})
        flat.append(fl)
    totals=[]
    for scope,model in itertools_product(['core','menu'],MODELS):
        rr=[r for r in rows if r['evidence_scope']==scope]
        # Data availability creates uneven axis counts; this is descriptive, not an overall accuracy metric.
        totals.append({'scope':scope,'model':model,'states':len(rr),'slots':sum(len(r['models'][model]['cards']) for r in rr),
            **{k:sum(r['models'][model][k] for r in rr) for k in ['context_card_hits','context_unseen_card_hits','disliked_cards']},
            'strict_conflicts':sum(r['models'][model]['diversity']['strict_conflicts'] for r in rr)})
    result={'source_sha256':sources,'states':len(rows),'matched_groups':len(cases)//3,'actual_cards':len(cards),
        'cells':summaries,'totals':totals,'metadata':metadata,
        'definitions':{'score_switch':'heldout mean(A)-mean(B) under A-preferred history minus the same gap under B-preferred history. Positive means directional response, never accuracy.',
            'card_switch':'[A cards-B cards] under A-preferred history minus the same balance under B-preferred history. Zero may mean the subtle effect did not cross discrete selection boundaries.',
            'core':'principal/co-main meal evidence','menu':'explicit standalone menu evidence; not a specialist restaurant claim',
            'scope':'real vectors, hypothetical feedback, shared catalogue/seeds, no online test'}}
    (O/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (O/'vector-changes.json').write_text(json.dumps(vectors,ensure_ascii=False,indent=2)+'\n')
    csvout('case-effects.csv',cases);csvout('cell-effects.csv',flat);csvout('actual-cards.csv',cards)
    print(json.dumps({'states':len(rows),'cells':len(summaries)//3,'actual_cards':len(cards),'totals':totals},ensure_ascii=False,indent=2))

def itertools_product(*args):
    import itertools
    return itertools.product(*args)

if __name__=='__main__':run()
