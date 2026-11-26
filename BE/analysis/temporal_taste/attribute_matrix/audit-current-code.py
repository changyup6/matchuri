"""Isolated master compatibility replay on the original frozen catalogue.

No writes to the repository, deployment, live source data or real user state.
The 16 cases are the previously fixed four examples, each with all four arms.
Research fits are unchanged source; saved raw means/std drive the latest selector.
"""
from pathlib import Path
import sys,json,time,hashlib
from unittest.mock import patch
import numpy as np
O=Path(__file__).resolve().parent
C=O/'compat-e55f5dd'
sys.path.insert(0,str(C/'BE'))
from analysis.temporal_taste.matrix_bench import runtime
from analysis.temporal_taste.context_floor import select_context_floor
start=time.perf_counter()
fixture=json.loads((O/'fixtures.json').read_text());examples=json.loads((O/'information-example-selection.json').read_text())['examples']
selected={rid for e in examples for rid in e['case_ids'].values()}
runs={r['id']:r for r in fixture['runs'] if r['id'] in selected};old={};arrays={};digests={}
for part in range(2):
 p=O/f'part-{part}/run';results=json.loads((p/'results.json').read_text());z=np.load(p/'predictions.npz')
 for r in results['rows']:
  if r['id'] not in selected:continue
  old[r['id']]=r
  arrays[r['id']]={k:z[r['id']+'__'+k] for k in ['current_mean','current_std','research_mean','research_std','operational_mean','operational_std']}
 z.close();del results
assert len(old)==len(runs)==16
checks=[];errors={};D=O/'private-input/kakao';cache=Path('/home/ubuntu/matchuri-changes/history-depth-20260922/state/review-ranks.cache')
with runtime(D,D/'serving',cache) as (art,decision,taste):
 from recsys import content
 assert Path(taste.__file__).is_relative_to(C)
 for name,module in [('taste',taste),('decision',decision),('content',content)]:digests[name]=dict(path=module.__file__,sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest())
 zero=np.zeros((len(content.IDX),len(art.ids)))
 for rid,f in runs.items():
  previous=taste.replay(f['past']) if f['past'] else None
  op=taste.start_from(previous)
  for sid,like in f['current']:taste.record(op,sid,like)
  z=decision._components(art,op,loo=True);_,mean,std=decision._scores(art,op,z=z)
  assert np.array_equal(z,decision._components(art,op,loo=True))
  a=arrays[rid];r=old[rid]
  for name,got,key in [('operational_mean',mean,'operational_mean'),('operational_std',std,'operational_std')]:
   error=float(np.max(abs(got-a[key])));errors[name]=max(errors.get(name,0),error);assert error<1e-12,(rid,name,error)
  op_slate=decision.recommend(op)
  assert op_slate['store_ids']==[c['store_id'] for c in r['models']['operational']['cards']],(rid,'op slate')
  assert op_slate['qualified_count']==r['models']['operational']['qualified_count']
  latest=dict(f['current']);state={'like_ids':[s for s,v in latest.items() if v],'dislike_ids':[s for s,v in latest.items() if not v],'n':len(latest),'m':np.zeros(len(content.IDX))}
  with patch.object(decision,'_components',return_value=zero),patch.object(decision,'_scores',return_value=(a['current_mean'],a['current_mean'],a['current_std'])):
   current=decision.recommend(state)
  assert current['store_ids']==r['current_slate_ids'],(rid,'current slate')
  with patch.object(decision,'_components',return_value=zero),patch.object(decision,'_scores',return_value=(a['research_mean'],a['research_mean'],a['research_std'])):
   full=decision.recommend(state)
   floor=select_context_floor(art.ids,a['current_mean'],a['current_std'],config=decision.DEFAULT,full_slate=full,current_slate=current,select_allowed=lambda allowed:decision.recommend(state,allow=allowed),is_diverse=lambda x,y:decision._diverse(art,art.pos[x],art.pos[y],decision.DEFAULT))
  assert full['store_ids']==[c['store_id'] for c in r['models']['regularized']['cards']],(rid,'research slate')
  assert floor.slate['store_ids']==[c['store_id'] for c in r['models']['regularized_context_floor']['cards']],(rid,'floor slate')
  assert floor.floor==r['context_floor'] and floor.used_context==r['used_context'] and floor.fallback_reason==r['fallback_reason']
  checks.append(dict(id=rid,operational_mean_bitwise_equal=np.array_equal(mean,a['operational_mean']),operational_std_bitwise_equal=np.array_equal(std,a['operational_std']),operational_slate_equal=True,research_slate_equal=True,current_only_slate_equal=True,context_floor_slate_equal=True))
  print('compatible '+rid,flush=True)
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
report=dict(passed=True,cases=16,slates=64,seconds=time.perf_counter()-start,selection='Previously fixed four examples, all four history arms; no outcome-based sampling',checks=checks,numeric_max_error=errors,code=digests,ast_audit_sha256=sha(O/'current-code-ast-audit.json'),source_sha256={str(p):sha(p) for p in [Path(__file__),O/'fixtures.json',O/'information-example-selection.json',D/'serving/prior.json']},limits=['Compatibility of master e55f5dd on the original frozen catalogue, not a replay on the refreshed live review similarity matrix.','Full operating mean/std are recomputed; research mean/std reuse the saved arrays because research source and manual selector algorithm are unchanged.','Manual four-card selector is tested; newer automatic readiness/display score behavior is outside this study.','The newly committed real-swipe prior has not been built into the checked live serving prior.json, which remains byte-identical to the frozen step3d-clip prior.'])
(O/'current-code-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(dict(passed=True,cases=16,slates=64,seconds=report['seconds'],numeric_max_error=errors)),flush=True)
