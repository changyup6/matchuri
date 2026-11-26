"""Independent frozen-array accounting audit; imports no model/selector/aggregator.

All raw arrays stay local. Only source hashes, check counts and small summaries
are exported. Run after both shard NPZ archives and the aggregation complete.
"""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv,hashlib,json,traceback
import numpy as np

O=Path(__file__).resolve().parent
PROFILE=Path('/home/ubuntu/matchuri-changes/nuance-audit-20260922/profiles.private.npz')
RELOCATION=O/'data-snapshot-map.json'
EXPECTED_RELOCATION='cc1187a7b6ccc7369e99b979da83a98500888b16ada4b5ecfad2b2cb32f501f6'
MODELS=('operational','regularized','regularized_context_floor')
ARMS=('none','neutral','prefer','avoid')
EXPECTED_FIXTURE='470f765c4634d44eba121daf0e131ac075220399af6dfcaf35981048bec325c5'
CHECKS=Counter();ERRORS=defaultdict(float)
def check(ok,kind,detail=None):
    CHECKS[kind]+=1
    if not ok:raise AssertionError((kind,detail))
def eq(a,b,kind,detail=None):
    if a is None or b is None:return check(a is None and b is None,kind,detail)
    if isinstance(b,(float,np.floating)):
        error=abs(float(a)-float(b));ERRORS[kind]=max(ERRORS[kind],error)
        return check(np.isfinite(error) and error<1e-10,kind,detail)
    return check(a==b,kind,detail)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def load(path):return json.loads(Path(path).read_text())
def stats(values):
    a=[float(x) for x in values if x is not None]
    return dict(n=len(a),mean=float(np.mean(a)) if a else None,min=min(a) if a else None,max=max(a) if a else None,
        positive=sum(x>1e-10 for x in a),negative=sum(x< -1e-10 for x in a),unchanged=sum(abs(x)<=1e-10 for x in a))
def csvrows(path):return list(csv.DictReader(Path(path).open(encoding='utf-8-sig')))
def csv_eq(actual,expected,kind,detail=None):
    if expected is None:return check(actual=='',kind,detail)
    if isinstance(expected,bool):return check(actual==str(expected),kind,detail)
    if isinstance(expected,(int,np.integer)):return eq(int(actual),int(expected),kind,detail)
    if isinstance(expected,(float,np.floating)):return eq(float(actual),float(expected),kind,detail)
    return eq(actual,expected,kind,detail)
def group_stats(values,score,members,allowed,unseen,ranks,lists,pos):
    ss=sorted(members,key=lambda s:pos[s]);ii=[pos[s] for s in ss]
    return dict(count=len(ss),eligible_count=len(members&allowed),unseen_count=len(members&unseen),
        mean=float(np.mean(values[ii])) if ii else None,conservative_mean=float(np.mean(score[ii])) if ii else None,
        global_mean_rank=float(np.mean([ranks[s] for s in ss])) if ii else None,
        hits={k:len(members&v) for k,v in lists.items()},
        unseen_hits={k:len(members&unseen&v) for k,v in lists.items()})
def run():
    eq(sha(O/'fixtures.json'),EXPECTED_FIXTURE,'canonical fixture SHA')
    eq(sha(RELOCATION),EXPECTED_RELOCATION,'frozen relocation map SHA')
    relocation=load(RELOCATION);pathmap=relocation['path_map'];inverse={v:k for k,v in pathmap.items()}
    eq(relocation['parent_fixture_sha256'],EXPECTED_FIXTURE,'relocation parent identity')
    check(len(inverse)==len(pathmap),'relocation is one-to-one')
    def physical(path):return pathmap.get(path,path)
    def logical(path):return inverse.get(path,path)
    for old,new in pathmap.items():
        eq(new,relocation['files'][old]['snapshot'],'relocation path identity')
        eq(sha(new),relocation['files'][old]['sha256'],'relocated archive file SHA')
    fixture=load(O/'fixtures.json');coverage=load(O/'coverage.json');shards=load(O/'shards.json');summary=load(O/'summary.json')
    runs={r['id']:r for r in fixture['runs']};ids=fixture['catalog_ids'];pos={s:i for i,s in enumerate(ids)};catalog=set(ids);brands=fixture['brand_keys'];n=len(ids)
    check(len(runs)==3168 and n==1498,'3168 states /1498 catalogue')
    name_rows={r['store_id']:r['name'] for r in map(json.loads,Path(physical('/home/ubuntu/matchuri-data/kakao/stores.jsonl')).open()) if r['store_id'] in catalog}
    eq(shards['parent_fixture_sha256'],EXPECTED_FIXTURE,'shard parent identity')
    profile=np.load(PROFILE);check(list(profile['ids'])==ids,'private feature catalogue identity');x=profile['x'];check(x.shape==(1498,4317),'4317D full features')
    sources={};source_checks=0
    def verify(mapping):
        nonlocal source_checks
        for supplied_path,h in mapping.items():
            path=logical(supplied_path)
            if path in sources:check(sources[path]==h,'repeated source hashes agree',path);continue
            eq(sha(physical(path)),h,'source SHA matches frozen physical file',path);sources[path]=h;source_checks+=1
    verify(fixture['source_sha256']);verify(summary['source_sha256']);verify({str(PROFILE):sha(PROFILE)})
    state={};vectors={};vector_meta={};actual_index={};matched={};seen=set();perpart=[];max_reconstruction=0.;max_components=0.;list_denoms=Counter();floor_used=0;fallback_reasons=Counter()
    for sh in shards['shards']:
        part=sh['part'];eq(sha(sh['path']),sh['sha256'],'shard fixture SHA');partdoc=load(sh['path']);expected={r['id'] for r in partdoc['runs']}
        result=load(O/f'part-{part}/run/results.json');verify(result['source_sha256']);z=np.load(O/f'part-{part}/run/predictions.npz')
        check(list(z['ids'])==ids,'NPZ catalogue order');check(len(result['rows'])==len(expected)==1584,'1584 shard states')
        check(len(z.files)==1+1584*9,'14257 shard NPZ arrays');check({r['id'] for r in result['rows']}==expected,'shard row membership');check(not(seen&expected),'shards do not repeat states')
        seen|=expected;batch_weights=[];batch_means=[];batch_intercepts=[];part_max=0.
        def flush_vectors():
            nonlocal max_reconstruction,part_max
            if not batch_weights:return
            predicted=x@np.column_stack(batch_weights)+np.asarray(batch_intercepts)[None,:]
            expected_means=np.column_stack(batch_means);error=float(np.max(abs(predicted-expected_means)))
            check(error<1e-10,'batched Xw+b reconstructs every research prediction',dict(part=part,error=error))
            max_reconstruction=max(max_reconstruction,error);part_max=max(part_max,error)
            batch_weights.clear();batch_means.clear();batch_intercepts.clear()
        for row in result['rows']:
            rid=row['id'];f=runs[rid];key=(f['context'],f['dimension'],f['seed'])
            for k in ['context','dimension','seed','prior','history_n','axis','pair_name','evidence_scope','history_mode','attribute_a','attribute_b']:eq(row[k],f[k],'row fixture metadata',rid)
            arrays={k:z[rid+'__'+k] for k in ('research_mean','research_std','current_mean','current_std','operational_mean','operational_std','research_total','research_history','research_current')}
            for k,a in arrays.items():check(a.shape==((4317,) if k in ['research_total','research_history','research_current'] else (1498,)) and np.isfinite(a).all(),'finite raw array shape',(rid,k))
            for k in ['research_std','current_std','operational_std']:check((arrays[k]>=0).all(),'nonnegative uncertainty',(rid,k))
            mean,sd=arrays['research_mean'],arrays['research_std'];cm,cs=arrays['current_mean'],arrays['current_std'];w=arrays['research_total'];wh=arrays['research_history'];wc=arrays['research_current']
            ce=float(np.max(abs(w-wh-wc)));check(ce<1e-10,'joint coefficient components sum');max_components=max(max_components,ce)
            for k,a in [('total',w),('history',wh),('current',wc)]:eq(row['vector']['norms'][k],float(np.linalg.norm(a)),'saved coefficient norm')
            eq(row['vector']['dimensions'],4317,'saved vector dimensions')
            vectors[rid]=w;vector_meta[rid]=row['vector'];batch_weights.append(w);batch_means.append(mean);batch_intercepts.append(row['vector']['intercept'])
            if len(batch_weights)>=64:flush_vectors()
            inv=matched.setdefault(key,dict(current=f['current'],context=f['context_ids'],heldout=f['heldout_ids'],groups=f['evaluation_groups'],mean=cm,std=cs,slate=row['current_slate_ids'],floor=row['context_floor'],full_std=None,past_ids=None,arms=set()))
            check(f['prior'] not in inv['arms'],'one of each arm');inv['arms'].add(f['prior'])
            for k,got in [('current',f['current']),('context',f['context_ids']),('heldout',f['heldout_ids']),('groups',f['evaluation_groups'])]:eq(got,inv[k],'fixed current and evaluation population',key)
            check(np.array_equal(cm,inv['mean']) and np.array_equal(cs,inv['std']),'current posterior bitwise fixed across arms',key)
            check(inv['slate']==row['current_slate_ids'] and inv['floor']==row['context_floor'],'current-only slate and floor fixed',key)
            if f['past']:
                if inv['full_std'] is None:inv['full_std']=sd;inv['past_ids']=[s for s,_ in f['past']]
                else:
                    check(np.array_equal(inv['full_std'],sd),'GP std bitwise invariant to label swap',key)
                    check(inv['past_ids']==[s for s,_ in f['past']],'same past IDs and order',key)
            latest={s:dict(like=like,source=source) for source,ev in [('history',f['past']),('current',f['current'])] for s,like in ev}
            known={brands[s] for s in latest if brands[s]};unseen={s for s in ids if s not in latest and (not brands[s] or brands[s] not in known)}
            context=set(f['context_ids']);heldout=set(f['heldout_ids']);check(heldout<=unseen,'heldout stores and brands unseen');check(not(set(f['evaluation_groups']['target'])&set(f['evaluation_groups']['comparison'])),'A/B fixed groups disjoint')
            current_score=cm-.5*cs;floor=row['context_floor']
            if not row['used_context']:
                fallback_reasons[row['fallback_reason']]+=1
                check([c['store_id'] for c in row['models']['regularized_context_floor']['cards']]==[c['store_id'] for c in row['models']['regularized']['cards']],'fallback retains original full slate')
            if row['used_context']:
                floor_used+=1;eq(floor,min(current_score[pos[s]] for s in row['current_slate_ids']),'floor from current-only reference slate')
            state[rid]={}
            for model in MODELS:
                mu,std=(arrays['operational_mean'],arrays['operational_std']) if model=='operational' else (mean,sd);score=mu-.5*std;output=row['models'][model]
                order=np.lexsort((np.asarray(ids),-score));ranked=[ids[i] for i in order];rank={s:i+1 for i,s in enumerate(ranked)}
                allowed={s for s in ids if current_score[pos[s]]>=floor} if model=='regularized_context_floor' and row['used_context'] else catalog
                eligible=[s for s in ranked if s in allowed];chosen=[c['store_id'] for c in output['cards']]
                check(len(chosen)==len(set(chosen))==4 and set(chosen)<=allowed,'four distinct eligible actual cards',(rid,model))
                lists=dict(cards=chosen,top20=eligible[:20],raw_global_top20=ranked[:20],context_top10=[s for s in eligible if s in context][:10],heldout_context_top10=[s for s in eligible if s in heldout][:10])
                for name,ss in lists.items():check([c['store_id'] for c in output[name]]==ss,'ranked/conditional list identity',(rid,model,name));list_denoms[model+'/'+name]+=len(ss)
                if model=='regularized_context_floor' and row['used_context']:check(all(current_score[pos[s]]>=floor for s in chosen),'floor guarantee on actual cards when used')
                for position,card in enumerate(output['cards'],1):
                    s=card['store_id'];i=pos[s]
                    expected_card=dict(name=name_rows[s],mean=float(mu[i]),std=float(std[i]),score=float(score[i]),global_rank=rank[s],current_context=s in context,heldout=s in heldout,unseen_store_and_brand=s in unseen,latest_feedback=latest.get(s),groups=[g for g,ss in f['evaluation_groups'].items() if s in ss])
                    for k,value in expected_card.items():eq(card[k],value,'actual card metadata '+k,(rid,model,position))
                    actual_index[rid,model,position]=dict(store_id=s,**expected_card)
                basic=dict(eligible_count=len(allowed),context_count=len(context),heldout_count=len(heldout),context_card_hits=len(context&set(chosen)),context_unseen_card_hits=len(context&unseen&set(chosen)),unseen_cards=len(unseen&set(chosen)),disliked_cards=sum(s in latest and not latest[s]['like'] for s in chosen))
                for k,value in basic.items():eq(output[k],value,'actual numerator and denominator '+k,(rid,model))
                hitsets={k:set(v) for k,v in lists.items()};hitsets.update(top10=set(eligible[:10]),raw_global_top10=set(ranked[:10]));gg={}
                for label,ss in f['evaluation_groups'].items():
                    gg[label]={}
                    for scope,members in [('all',set(ss)),('heldout',set(ss)&heldout)]:
                        expected_stats=group_stats(mu,score,members,allowed,unseen,rank,hitsets,pos)
                        for k,value in expected_stats.items():eq(output['groups'][label][scope][k],value,'group raw-array '+k,(rid,model,label,scope))
                        gg[label][scope]=expected_stats
                check(gg['target']['heldout']['count']>0 and gg['comparison']['heldout']['count']>0,'measured case has both heldout groups')
                state[rid][model]=dict(groups=gg,list_slots={k:len(v) for k,v in lists.items()},**basic)
        flush_vectors();perpart.append(dict(part=part,states=len(expected),max_vector_reconstruction_error=part_max));z.close();del result
        print(f'raw shard {part} checked: {len(expected)} states',flush=True)
    check(seen==set(runs),'all 3168 cases present once');check(len(matched)==792,'792 matched comparisons')
    for key,inv in matched.items():check(inv['arms']==set(ARMS),'four arms complete',key)
    # Build every case-level contrast from independently recovered raw group means.
    runindex={(f['context'],f['dimension'],f['seed'],f['prior']):rid for rid,f in runs.items()};case_calc={};cell_calc=defaultdict(list)
    for key in matched:
        reference=runs[runindex[*key,'neutral']]
        for model in MODELS:
            armstats={arm:state[runindex[*key,arm]][model] for arm in ARMS}
            gaps={arm:m['groups']['target']['heldout']['mean']-m['groups']['comparison']['heldout']['mean'] for arm,m in armstats.items()}
            balance=lambda m:m['groups']['target']['all']['hits']['cards']-m['groups']['comparison']['all']['hits']['cards']
            values=dict(a_vs_neutral=gaps['prefer']-gaps['neutral'],b_vs_neutral=gaps['neutral']-gaps['avoid'],score_switch=gaps['prefer']-gaps['avoid'],card_switch=balance(armstats['prefer'])-balance(armstats['avoid']),heldout_a=armstats['neutral']['groups']['target']['heldout']['count'],heldout_b=armstats['neutral']['groups']['comparison']['heldout']['count'])
            for arm,m in armstats.items():
                values['gap_'+arm]=gaps[arm]
                fields=dict(context_cards=m['context_card_hits'],new_context_cards=m['context_unseen_card_hits'],disliked_cards=m['disliked_cards'],target_cards=m['groups']['target']['all']['hits']['cards'],comparison_cards=m['groups']['comparison']['all']['hits']['cards'],all_card_slots=m['list_slots']['cards'],target_top10=m['groups']['target']['all']['hits']['top10'],comparison_top10=m['groups']['comparison']['all']['hits']['top10'],actual_top10_slots=min(10,m['list_slots']['top20']),context_top10_slots=m['list_slots']['context_top10'])
                values.update({arm+'_'+k:v for k,v in fields.items()})
            case_calc[*key,model]=values;cell_calc[key[0],key[1],model].append(values)
    casecsv=csvrows(O/'case-effects.csv');check(len(casecsv)==2376,'2376 case-model CSV rows');check(len({(c['context'],c['dimension'],int(c['seed']),c['model']) for c in casecsv})==2376,'unique case CSV keys')
    for c in casecsv:
        key=c['context'],c['dimension'],int(c['seed']),c['model']
        for k,v in case_calc[key].items():csv_eq(c[k],v,'case effect/numerator/denominator',(*key,k))
    # Cell coverage and unavailable cells are independent of observed model effects.
    cov={(c['context'],c['dimension']):c for c in coverage['coverage']};meas={k for k,v in cov.items() if v['status']=='measured'};unmeas=set(cov)-meas
    check({(k[0],k[1]) for k in cell_calc}==meas,'only feasible fixed cells measured');check(not({(k[0],k[1]) for k in cell_calc}&unmeas),'unmeasured cells stay unavailable')
    eq(len(meas),132,'132 measured cells');eq(len(summary['cells']),396,'396 cell-model summaries')
    cellindex={(c['context'],c['dimension'],c['model']):c for c in summary['cells']};cellcsv=csvrows(O/'cell-effects.csv');eq(len(cellcsv),396,'396 cell-model CSV rows');check(set(cellindex)==set(cell_calc),'all cell summary keys complete');check({(c['context'],c['dimension'],c['model']) for c in cellcsv}==set(cell_calc),'all cell CSV keys complete')
    for key,values in cell_calc.items():
        out=cellindex[key];coverage_cell=cov[key[:2]]
        eq(out['seeds'],len(values),'cell seed denominator');eq(out['sparse'],coverage_cell['sparse'],'sparse label preserved');eq(out['population_a'],coverage_cell['group_a'],'cell A population');eq(out['population_b'],coverage_cell['group_b'],'cell B population')
        eq(out['heldout_a_range'],[min(v['heldout_a'] for v in values),max(v['heldout_a'] for v in values)],'cell heldout A range');eq(out['heldout_b_range'],[min(v['heldout_b'] for v in values),max(v['heldout_b'] for v in values)],'cell heldout B range')
        for metric in ['score_switch','a_vs_neutral','b_vs_neutral','card_switch']:
            for k,v in stats([c[metric] for c in values]).items():eq(out[metric][k],v,'cell effect aggregation '+metric+'.'+k,key)
        for arm in ARMS:
            for k,v in out['arms'][arm].items():eq(v,sum(c[arm+'_'+k] for c in values),'cell actual/conditional totals',(*key,arm,k))
    for c in cellcsv:
        key=c['context'],c['dimension'],c['model'];out=cellindex[key]
        for metric in ['score_switch','a_vs_neutral','b_vs_neutral','card_switch']:
            for k,v in out[metric].items():csv_eq(c[metric+'_'+k],v,'cell CSV effect field')
        for arm in ARMS:
            for k,v in out['arms'][arm].items():csv_eq(c[arm+'_'+k],v,'cell CSV actual/conditional denominator')
    # Validate all saved actual deliveries, including names and latest observations.
    cards=csvrows(O/'actual-cards.csv');eq(len(cards),38016,'38016 actual card CSV rows');check(len(actual_index)==38016,'38016 actual cards in JSON');seen_cards=set()
    for c in cards:
        key=c['case'],c['model'],int(c['rank']);check(key not in seen_cards,'actual card CSV unique position');seen_cards.add(key);expected=actual_index[key]
        for k in ['store_id','name','mean','std','score','global_rank','current_context','heldout','unseen_store_and_brand']:csv_eq(c[k],expected[k],'actual card CSV '+k,key)
        eq(c['groups'],';'.join(expected['groups']),'actual card CSV group membership',key)
    # Saved vector deltas use the same full-dimensional coefficients.
    changes=load(O/'vector-changes.json');eq(len(changes),1584,'1584 preferred/avoided vector comparisons');check({(v['context'],v['dimension'],v['seed'],v['arm']) for v in changes}=={(*key,arm) for key in matched for arm in ['prefer','avoid']},'all vector comparison keys complete')
    for v in changes:
        key=v['context'],v['dimension'],v['seed'];w=vectors[runindex[*key,v['arm']]];baseline=vectors[runindex[*key,'neutral']];d=w-baseline
        norm=float(np.linalg.norm(w));bn=float(np.linalg.norm(baseline));cos=float(np.clip(w@baseline/(norm*bn),-1,1)) if norm*bn>1e-15 else None
        for k,val in [('norm',norm),('neutral_norm',bn),('distance_from_neutral',float(np.linalg.norm(d))),('angle_degrees',float(np.degrees(np.arccos(cos))) if cos is not None else None)]:eq(v[k],val,'vector geometric delta '+k,key)
        top=np.argsort(-np.abs(d))[:5];eq([t['coordinate'] for t in v['largest_coordinate_changes']],list(top),'largest coordinate indices')
        for t in v['largest_coordinate_changes']:
            i=t['coordinate']
            for k,val in [('neutral',baseline[i]),('preferred',w[i]),('change',d[i])]:eq(t[k],float(val),'largest coordinate values')
    check(len(summary['totals'])==6 and {(t['scope'],t['model']) for t in summary['totals']}=={(scope,model) for scope in ['core','menu'] for model in MODELS},'six complete scope/model totals')
    for total in summary['totals']:
        scope,model=total['scope'],total['model'];rr=[rid for rid,f in runs.items() if f['evidence_scope']==scope]
        eq(total['states'],len(rr),'overall scope states');eq(total['slots'],4*len(rr),'overall scope actual slots')
        for k in ['context_card_hits','context_unseen_card_hits','disliked_cards']:eq(total[k],sum(state[rid][model][k] for rid in rr),'overall scope numerator '+k)
    # Recheck source fingerprints after all calculations; no changing inputs accepted.
    for path,h in sources.items():eq(sha(physical(path)),h,'source remains unchanged during audit',path)
    export_sources={str(p):sha(p) for p in [O/'fixtures.json',O/'coverage.json',O/'summary.json',O/'case-effects.csv',O/'cell-effects.csv',O/'actual-cards.csv',O/'vector-changes.json',RELOCATION,PROFILE,Path(__file__)]}
    for part in range(2):
        for filename in ['results.json','predictions.npz']:
            p=O/f'part-{part}/run'/filename;export_sources[str(p)]=sha(p)
    report=dict(passed=True,finished_utc=datetime.now(timezone.utc).isoformat(),independent_of_model_and_aggregator_imports=True,states=3168,matched_groups=792,measured_cells=132,unmeasured_cells=len(unmeas),models=list(MODELS),actual_cards=38016,source_sha256=export_sources,unique_frozen_sources_verified=source_checks,frozen_source_resolution={p:physical(p) for p in sources if physical(p)!=p},assertions_by_kind=dict(CHECKS),numeric_max_errors=dict(ERRORS),vector_reconstruction_max_error=max_reconstruction,vector_component_sum_max_error=max_components,vector_shards=perpart,current_floor_used_states=floor_used,fallback_reasons=dict(fallback_reasons),list_denominators=dict(list_denoms),sparse_measured_cells=sum(bool(cov[k]['sparse']) for k in meas),unmeasured_reasons=dict(Counter(cov[k]['reason'] for k in unmeas)),limits=['Actual selections are validated as saved; this audit does not rerun the selector or independently recompute pair-diversity from every serving component.','All score changes are model-unit directional effects under hypothetical feedback, not satisfaction or accuracy.','Fixed heldout means, globally eligible top10 and context-conditional top10 are distinct denominators.','A-only/B-only means competing positive evidence is absent from this audited snapshot, not confirmed absence of the attribute.','No raw prediction or private feature arrays are exported by this audit.'])
    (O/'result-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    text=['# 속성 실험 독립 검산','',f'**3168개 case × 3개 정책 × 4장 = 38016장**을 원시 NPZ·동결 fixture와 대조했다. 중립 대비 효과와 선호 교체 효과를 집계 코드 import 없이 재계산했다.','',f'- 검산 {sum(CHECKS.values()):,}건 통과; 고유 동결 출처 {source_checks}개 일치.',f'- 현재-only 평균·불확실성은792개 비교 그룹의4개 arm에서 비트 단위 동일.', '- 같은 과거ID·순서에서 선호 라벨만 바꾸어도 GP 불확실성은 비트 단위 동일.',f'- 모든4317D 계수의 X@w+b 재구성 최대오차 {max_reconstruction:.3g}.',f'- 측정132셀, 미측정{len(unmeas)}셀: 미측정을0효과로 치환하지 않음.','', '| 정책 | 실제4장 | 허용top20 | 현재조건부top10 | heldout현재조건부top10 |','|---|---:|---:|---:|---:|']
    for m in MODELS:text.append('| '+m+' | '+' | '.join(str(list_denoms[m+'/'+k]) for k in ['cards','top20','context_top10','heldout_context_top10'])+' |')
    text+=['','## 해석 한계','']+['- '+s for s in report['limits']]
    (O/'result-audit.md').write_text('\n'.join(text)+'\n')
    print(json.dumps(dict(passed=True,assertions=sum(CHECKS.values()),cards=38016,max_vector_error=max_reconstruction)),flush=True)
if __name__=='__main__':
    try:run()
    except Exception as error:
        (O/'result-audit-failure.json').write_text(json.dumps(dict(passed=False,error=repr(error),checks_completed=dict(CHECKS),traceback=traceback.format_exc()),ensure_ascii=False,indent=2)+'\n')
        raise
