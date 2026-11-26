"""Readable measurement figures; example IDs fixed before model results existed.

Only consumes frozen audit/fixture summaries and measured CSV exports. Does not
fit models, pick examples from outcomes, or change research inputs.
"""
from pathlib import Path
from collections import Counter, defaultdict
import argparse, csv, hashlib, json, math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, Patch
from matplotlib.colors import LinearSegmentedColormap

O = Path(__file__).resolve().parent
FIXTURE_SHA = '470f765c4634d44eba121daf0e131ac075220399af6dfcaf35981048bec325c5'
SELECTION_SHA = '902f55e1f52581e72f4db8d99da4e10d5a429a64ef682cea5f812ac06bff0aa8'
MODELS = ['operational', 'regularized', 'regularized_context_floor']
MODEL_NAMES = {'operational':'운영 모델 계산', 'regularized':'연구 벡터 모델', 'regularized_context_floor':'연구 벡터 + 현재조건 보호'}
MODEL_SHORT = {'operational':'운영', 'regularized':'연구', 'regularized_context_floor':'연구+보호'}
FOODS = ['cutlet','noodle','rice','soup','grill','salad']
FOOD_NAMES = dict(zip(FOODS,['돈까스','면','밥','국·탕·찌개','구이','샐러드']))
AXES = ['cuisine','taste','cooking','temperature','broth','ingredient','texture','dining','form','category']
AXIS_NAMES = dict(zip(AXES,['요리권','맛 표현','조리법','온도','국물 유무','재료','식감','먹는 분량','음식 형태','세부 업종*']))
INK='#173044'; MUTED='#586a79'; BLUE='#287a91'; NEG='#cd7546'; GRAY='#d6dde2'; BG='#f6f8fb'
FONT='/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'
font_manager.fontManager.addfont(FONT)
plt.rcParams.update({'font.family':font_manager.FontProperties(fname=FONT).get_name(),
    'axes.unicode_minus':False,'svg.fonttype':'path','font.size':11,'text.color':INK,
    'axes.labelcolor':INK,'xtick.color':MUTED,'ytick.color':INK,
    'figure.facecolor':BG,'axes.facecolor':BG,'savefig.facecolor':BG})


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def readj(name): return json.loads((O/name).read_text())
def readcsv(name):
    with (O/name).open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))
def writej(name, v): (O/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def save(fig, name):
    for suffix in ['png','svg']: fig.savefig(O/f'{name}.{suffix}',dpi=155)
    plt.close(fig)
def text(fig,x,y,s,size=11,color=INK,**kw): return fig.text(x,y,s,fontsize=size,color=color,**kw)
def clean(ax):
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.tick_params(length=0)
def pct(n,d): return 100*n/d if d else 0

def coverage_figure(cov, audit):
    rr=cov['coverage']; out=[]
    measured=sum(r['status']=='measured' for r in rr)
    reasons=Counter(r.get('reason') for r in rr if r['status']!='measured')
    assert measured==cov['measured_cells']==132
    fig=plt.figure(figsize=(16,10.8))
    text(fig,.045,.952,'먼저, 어떤 음식과 속성을 비교할 자료가 있는가',25,weight='bold')
    text(fig,.045,.911,f'사전에 정한 {len(rr)}개 대조 중 {measured}개를 측정  |  숫자는 측정 가능한 속성쌍 / 계획한 속성쌍',13,color=MUTED)
    cmap=LinearSegmentedColormap.from_list('coverage',['#fff4e9','#b9d6d6',BLUE])
    for idx,scope in enumerate(['core','menu']):
        ax=fig.add_axes([.10,.592-idx*.322,.84,.23])
        grid=np.full((6,10),np.nan)
        for y,food in enumerate(FOODS):
            for x,axis in enumerate(AXES):
                cells=[r for r in rr if r['evidence_scope']==scope and r['food']==food and r['axis']==axis]
                n=len(cells); good=sum(r['status']=='measured' for r in cells)
                out.append({'scope':scope,'food':food,'axis':axis,'measured':good,'planned':n})
                if n:grid[y,x]=good/n
                ax.add_patch(Rectangle((x-.5,y-.5),1,1,facecolor='#e7ecf0' if not n else cmap(good/n),edgecolor=BG,linewidth=2))
                ax.text(x,y,f'{good}/{n}' if n else '해당 없음',ha='center',va='center',fontsize=12 if n else 9,color='white' if n and good/n>=.75 else INK)
        ax.set_xlim(-.5,9.5);ax.set_ylim(5.5,-.5)
        ax.set_xticks(range(10),[AXIS_NAMES[a] for a in AXES],fontsize=11)
        ax.set_yticks(range(6),[FOOD_NAMES[f] for f in FOODS],fontsize=12)
        clean(ax)
        n=sum(r['status']=='measured' for r in rr if r['evidence_scope']==scope)
        title=('주력 음식 범위: 해당 음식을 중심으로 파는 가게' if scope=='core' else '메뉴 제공 범위: 해당 음식을 독립 메뉴로 확인한 가게')
        text(fig,.045,.847-idx*.312,f'{title}  |  {n}개 대조',16,weight='bold')
    text(fig,.045,.186,'빠진 대조가 뜻하는 것',14,weight='bold')
    shortage=reasons['current_and_heldout_need_four_distinct_brands_per_side']
    matching=reasons['no_fixed_six_seed_design_with_cat2_brand_and_food_separation']
    text(fig,.045,.161,f'{shortage}개: 현재 반응용과 미학습 평가용 가게를 두 속성군에 각각 확보하지 못함.\n{matching}개: 음식·브랜드 분리와 조건 맞추기를 모두 만족하는 6개 구성을 만들지 못함.',11,color=MUTED,linespacing=1.55,va='top')
    pop=' / '.join(f'{FOOD_NAMES[f]} {len(audit["available_groups"][f])}곳' for f in FOODS)
    text(fig,.045,.096,'주력 범위의 전체 가게 수: '+pop,11)
    text(fig,.045,.076,'메뉴 제공 범위는 전문점이라는 뜻이 아닙니다. 빈칸이나 0은 상품 부재·추천 실패를 뜻하지 않습니다.\n* 세부 업종 대조는 같은 음식 안의 미학습 가게 비교이며, 다른 음식으로 취향이 옮겨가는 검증과 구분합니다.',10,color=MUTED,linespacing=1.5,va='top')
    save(fig,'information-coverage')
    return {'coverage_cells':out,'missing_reasons':dict(reasons),'core_catalogue_counts':{f:len(audit['available_groups'][f]) for f in FOODS}}


def direction(cases,key):
    values=[float(r[key]) for r in cases]
    return {'positive':sum(x>1e-10 for x in values),'unchanged':sum(abs(x)<=1e-10 for x in values),'negative':sum(x< -1e-10 for x in values),'total':len(values)}

def effects_figure(summary,cases,scope='core'):
    fig=plt.figure(figsize=(18,12.6))
    text(fig,.035,.952,'작은 취향 변화와 최종 4장 변화는 따로 봅니다',25,weight='bold')
    scope_name='주력 음식 범위' if scope=='core' else '메뉴 제공 범위'
    expected=258 if scope=='core' else 534
    text(fig,.035,.916,f'같은 현재 스와이프에서 과거 선호만 A ↔ B로 바꾼 비교  |  {scope_name}, {expected//6}개 대조 × 6개 구성',12,color=MUTED)
    notes=[]
    for mi,model in enumerate(MODELS):
        x=.10+mi*.295
        subset=[r for r in cases if r['evidence_scope']==scope and r['model']==model]
        assert len(subset)==expected
        score=direction(subset,'score_switch');card=direction(subset,'card_switch')
        text(fig,x,.876,MODEL_NAMES[model],15,weight='bold')
        text(fig,x,.85,f'점수 정방향 {score["positive"]}/{expected}  |  카드 균형 정방향 {card["positive"]}/{expected}',10,color=MUTED)
        for row,metric in enumerate(['score_switch','card_switch']):
            ax=fig.add_axes([x,[.505,.186][row],.258,[.295,.27][row]])
            for yi,axis in enumerate(AXES):
                rs=[r for r in subset if r['axis']==axis]
                d=direction(rs,metric);n=d['total'];left=0
                notes.append({'scope':scope,'model':model,'axis':axis,'metric':metric,**d})
                if n:
                    for key,col in [('positive',BLUE),('unchanged',GRAY),('negative',NEG)]:
                        width=pct(d[key],n)
                        ax.barh(yi,width,left=left,height=.64,color=col)
                        if width>=9: ax.text(left+width/2,yi,str(d[key]),ha='center',va='center',fontsize=9,color='white' if key!='unchanged' else INK)
                        left+=width
                    ax.text(102,yi,f'n={n}',fontsize=9,va='center',color=MUTED)
                else:ax.text(3,yi,'측정 불가',va='center',color=MUTED,fontsize=9)
            ax.set_yticks(range(10),[AXIS_NAMES[a] for a in AXES] if mi==0 else ['']*10,fontsize=11)
            ax.set_ylim(9.6,-.8);ax.set_xlim(0,114)
            ax.set_xticks([0,50,100],['0%','50%','100%'],fontsize=9)
            clean(ax)
            title='① 미학습 가게의 점수 차이 방향' if row==0 else '② 최종 4장의 A-B 장수 균형 변화'
            ax.set_title(title,fontsize=12,loc='left',pad=8)
        other_scope='menu' if scope=='core' else 'core'
        other_name='메뉴 제공 범위' if scope=='core' else '주력 음식 범위'
        other_expected=534 if scope=='core' else 258
        menu=[r for r in cases if r['evidence_scope']==other_scope and r['model']==model]
        sm=direction(menu,'score_switch');cm=direction(menu,'card_switch')
        assert len(menu)==other_expected
        text(fig,x,.13,f'{other_name}(별도): 점수 정방향 {sm["positive"]}/{other_expected}\n카드 균형 정방향 {cm["positive"]}, 동일 {cm["unchanged"]}, 역방향 {cm["negative"]}',10,color=MUTED,linespacing=1.55)
    fig.legend(handles=[Patch(color=BLUE,label='정방향'),Patch(color=GRAY,label='0: 균형 변화 없음'),Patch(color=NEG,label='역방향')],loc='lower left',bbox_to_anchor=(.03,.088),ncol=3,frameon=False,fontsize=11)
    text(fig,.035,.072,'정방향: A를 선호한 이력일 때 A-B 차이가 더 큼. 작은 양의 변화도 포함하므로 효과 크기·정확도가 아닙니다.\n장수 균형이 같아도 가게·순위는 바뀔 수 있습니다. 서로 다른 모델의 원점수는 비교하지 않았습니다.\n동결 시점의 실제 가게 벡터 + 가정한 반응을 사용한 계산 결과이며, 6개 구성은 독립 사용자 표본이 아닙니다. * 세부 업종은 같은 음식 안의 비교.\n속성은 가게의 수집 메뉴 전체 근거입니다. 국+찬 음식은 같은 국의 온도라는 뜻이 아닙니다.',10,color=MUTED,linespacing=1.45,va='top')
    save(fig,'information-effects' if scope=='core' else 'information-menu-effects')
    return {'directions' if scope=='core' else 'menu_directions':notes}



def retention_figure(summary,cards):
    fig=plt.figure(figsize=(15,8.6))
    text(fig,.05,.938,'현재 음식에 속한 카드는 늘지만, 새 가게는 줄었습니다',22,weight='bold')
    text(fig,.05,.891,'같은 입력을 세 모델에 재생한 최종 4장  |  이력 없음·균형·A 선호·B 선호 조건 전체 합산',12,color=MUTED)
    colors=['#287a91','#91b9c2','#dce3e9']; out=[]
    for si,scope in enumerate(['core','menu']):
        yy=.565-si*.286
        title='주력 음식 범위' if scope=='core' else '메뉴 제공 범위'
        base=next(t for t in summary['totals'] if t['scope']==scope)
        text(fig,.05,yy+.205,f'{title}  |  모델당 {base["states"]:,}번의 추천 · {base["slots"]:,}장',15,weight='bold')
        ax=fig.add_axes([.205,yy,.58,.174])
        for mi,model in enumerate(MODELS):
            t=next(t for t in summary['totals'] if t['scope']==scope and t['model']==model)
            cc=[c for c in cards if c['scope']==scope and c['model']==model]
            context=[c for c in cc if c['current_context']=='True']
            new_context=[c for c in context if c['unseen_store_and_brand']=='True']
            assert len(cc)==t['slots'] and len(context)==t['context_card_hits'] and len(new_context)==t['context_unseen_card_hits']
            counts=[len(new_context),len(context)-len(new_context),len(cc)-len(context)];left=0
            for n,col in zip(counts,colors):
                w=pct(n,len(cc));ax.barh(mi,w,left=left,height=.55,color=col)
                if w>7:ax.text(left+w/2,mi,f'{w:.1f}%',ha='center',va='center',fontsize=11,color='white' if col==colors[0] else INK)
                left+=w
            ax.text(102,mi,f'현재 음식 {pct(len(context),len(cc)):.1f}% / 새 가게 {pct(len(new_context),len(cc)):.1f}%',va='center',fontsize=10)
            out.append({'scope':scope,'model':model,'total_slots':len(cc),'new_context_slots':counts[0],'observed_context_slots':counts[1],'outside_context_slots':counts[2], 'current_food_fraction':len(context)/len(cc),'new_current_food_fraction':len(new_context)/len(cc)})
        ax.set_xlim(0,100);ax.set_ylim(2.6,-.6);ax.set_yticks(range(3),[MODEL_SHORT[m] for m in MODELS],fontsize=12)
        ax.set_xticks([0,25,50,75,100],['0%','25%','50%','75%','100%'],fontsize=10);clean(ax)
    fig.legend(handles=[Patch(color=colors[0],label='현재 음식 · 새 가게/브랜드'),Patch(color=colors[1],label='현재 음식 · 이미 관측한 가게/브랜드'),Patch(color=colors[2],label='현재 음식 범위 밖')],loc='lower left',bbox_to_anchor=(.045,.166),ncol=3,frameon=False,fontsize=11)
    text(fig,.05,.142,'해석: 연구 모델의 현재 음식 유지율 상승만으로 새로운 가게까지 더 잘 추천했다고 말할 수 없습니다.',12,weight='bold')
    text(fig,.05,.11,'''새 가게/브랜드: 해당 조건의 과거·현재 스와이프에 등장하지 않은 가게와 브랜드. 이미 관측했다는 것이 모두 좋아요였다는 뜻은 아닙니다.
메뉴 제공은 전문점이 아닌 메뉴 존재 기준이며, 음식·속성은 동일 메뉴의 특성을 뜻하지 않습니다. 카드 점유율은 만족도·정확도가 아닙니다.
네 이력 조건과 6개 구성이 가게를 공유합니다. 운영 알고리즘과 연구 대안을 동결된 실제 벡터에 적용한 재생이며, 운영 사용자 노출 로그가 아닙니다.
자기 반응 사용 방식이 달라 모델 우열 비교로 읽지 않습니다. 운영은 일부 반응을 빼고 평가하고, 연구는 입력 반응 전체를 사용합니다.''',10,color=MUTED,linespacing=1.35,va='top')
    save(fig,'information-retention')
    return {'context_retention':out}


def examples_figure(selection,cases,cards,coverage):
    model=selection['named_slate_model'];arms=selection['named_slate_arms']
    out=[]; fig=plt.figure(figsize=(18,17.5))
    text(fig,.035,.965,'같은 현재 반응, 다른 과거 이력: 실제로 계산된 최종 4장',24,weight='bold')
    text(fig,.035,.937,'연구 벡터 + 현재조건 보호 모델  |  모든 사례 seed 200  |  추천 점수를 보기 전에 사례를 고정',12,color=MUTED)
    text(fig,.035,.913,'A·B는 아래에 적힌 속성군입니다. 회색 가게는 현재 음식 범위 밖, ?는 음식 범위 안에서 두 속성군에 속하지 않는 가게입니다.',10,color=MUTED)
    text(fig,.035,.895,'속성은 가게의 수집 메뉴 전체 근거입니다. 국+찬 음식은 같은 국의 온도라는 뜻이 아닙니다.',10,color=INK)
    for ei,example in enumerate(selection['examples']):
        top=.875-ei*.199
        ref=next(r for r in cases if r['context']==example['context'] and r['dimension']==example['dimension'] and int(r['seed'])==example['seed'] and r['model']==model)
        cov=next(r for r in coverage['coverage'] if r['context']==example['context'] and r['dimension']==example['dimension'])
        al,bl=cov['a_label'],cov['b_label'];scope='주력' if example['evidence_scope']=='core' else '메뉴 제공'
        text(fig,.035,top,f'{ei+1}. 지금 {FOOD_NAMES[ref["food"]]} {"주력" if example["evidence_scope"]=="core" else "제공"} 가게  |  A {al} · B {bl}  |  {scope} 범위',15,weight='bold')
        text(fig,.035,top-.02,f'속성군 전체 A {cov["group_a"]}곳 / B {cov["group_b"]}곳 · 현재·과거 학습 제외 평가 A {int(ref["heldout_a"])}곳 / B {int(ref["heldout_b"])}곳 · 과거 {example["history_n"]}회',10,color=MUTED)
        data={'selection':example,'population_a':cov['group_a'],'population_b':cov['group_b'],'heldout_a':int(ref['heldout_a']),'heldout_b':int(ref['heldout_b']),'models':{}}
        for mi,m in enumerate(MODELS):
            r=next(r for r in cases if r['context']==example['context'] and r['dimension']==example['dimension'] and int(r['seed'])==example['seed'] and r['model']==m)
            data['models'][m]={k:float(r[k]) for k in ['score_switch','card_switch','a_vs_neutral','b_vs_neutral']}
            data['models'][m]['arms']={a:{k:int(r[a+'_'+k]) for k in ['target_cards','comparison_cards','context_cards','new_context_cards','all_card_slots','target_top10','comparison_top10','actual_top10_slots']} for a in arms}
        for ai,arm in enumerate(arms):
            x=.035+ai*.32
            cc=sorted([r for r in cards if r['case']==example['case_ids'][arm] and r['model']==model],key=lambda x:int(x['rank']))
            assert len(cc)==4 and len({r['store_id'] for r in cc})==4
            data['models'][model]['arms'][arm]['cards']=cc
            title={'neutral':'과거의 선호 균형','prefer':f'과거: A {al} 선호','avoid':f'과거: B {bl} 선호'}[arm]
            text(fig,x,top-.045,title,11,color=BLUE,weight='bold')
            for ci,c in enumerate(cc):
                groups=c['groups'].split(';');context=c['current_context']=='True'
                tag='A' if 'target' in groups else 'B' if 'comparison' in groups else '?' if context else '밖'
                name=c['name'];name=name if len(name)<=24 else name[:23]+'…'
                text(fig,x,top-.064-ci*.019,f'{ci+1}. [{tag}] {name}',10.5,color=INK if context else '#7b848b')
                text(fig,x+.274,top-.064-ci*.019,f'{int(c["global_rank"])}위',9,color=MUTED,ha='right')
            aa=data['models'][model]['arms'][arm]
            text(fig,x,top-.143,f'4장 중 현재 음식 {aa["context_cards"]}/4 · A {aa["target_cards"]} · B {aa["comparison_cards"]}\n계산된 상위 {aa["actual_top10_slots"]}곳: A {aa["target_top10"]} · B {aa["comparison_top10"]}',9.6,color=MUTED,linespacing=1.4)
        comparison=[]
        for m in MODELS:
            v=data['models'][m];change=int(v['card_switch']);sg=float(v['score_switch'])
            comparison.append(f'{MODEL_SHORT[m]} 점수 방향 {"+" if sg>1e-10 else "-" if sg< -1e-10 else "0"} / 장수 균형 변화 {change:+d}')
        text(fig,.035,top-.177,'   |   '.join(comparison),9.5,color=MUTED)
        out.append(data)
    text(fig,.035,.061,'위 순위는 전체 1,498곳의 불확실성 보정 점수 순위입니다. 최종 4장은 현재조건·다양성 선택을 거치므로 상위 4위와 다를 수 있습니다.\n상위 10곳은 계산된 후보 순위이며 실제 노출 로그가 아닙니다. A·B 개수의 분모는 각각의 가게 수와 함께 봐야 합니다.\n선호 균형은 이전 반응이 없는 조건과 다릅니다. 음식·속성은 수집 자료에 근거한 감사 라벨이며, 사용자 취향의 정답으로 두지 않았습니다.',10,color=MUTED,linespacing=1.5,va='top')
    save(fig,'information-examples')
    return {'examples':out}


def validate_summary(summary,cases,cards,coverage):
    assert summary['states']==3168 and summary['matched_groups']==792
    assert len(cases)==2376 and len(cards)==summary['actual_cards']==38016
    counts=Counter((r['context'],r['dimension'],r['seed'],r['model']) for r in cases)
    assert len(counts)==len(cases) and set(counts.values())=={1}
    index=defaultdict(list)
    for card in cards:index[(card['case'],card['model'])].append(card)
    assert len(index)==3168*3
    for cc in index.values():
        assert len(cc)==4 and len({x['store_id'] for x in cc})==4
    for r in cases:
        for arm in ['none','neutral','prefer','avoid']:
            cc=index[(f'{r["context"]}-{r["dimension"]}-{r["seed"]}-{arm}',r['model'])]
            hits=sum(x['current_context']=='True' for x in cc)
            a=sum('target' in x['groups'].split(';') for x in cc)
            b=sum('comparison' in x['groups'].split(';') for x in cc)
            assert hits==int(r[arm+'_context_cards']) and a==int(r[arm+'_target_cards']) and b==int(r[arm+'_comparison_cards'])
            assert int(r[arm+'_all_card_slots'])==4
        switch=int(r['prefer_target_cards'])-int(r['prefer_comparison_cards'])-int(r['avoid_target_cards'])+int(r['avoid_comparison_cards'])
        assert abs(switch-float(r['card_switch']))<1e-12
    # Verify rank counts from raw ranked stores and frozen membership, not only
    # agreement between two derived CSV exports. This also verifies display names.
    fixture_groups={r['id']:{g:set(ids) for g,ids in r['evaluation_groups'].items()} for r in readj('fixtures.json')['runs']}
    csv_cases={(r['context'],r['dimension'],r['seed'],r['model']):r for r in cases}
    ranked_slates=0
    for part in range(2):
        raw=readj(f'part-{part}/run/results.json')
        for r in raw['rows']:
            for model,m in r['models'].items():
                c=csv_cases[(r['context'],r['dimension'],str(r['seed']),model)]
                arm=r['prior'];top=m['top20'][:10];groups=fixture_groups[r['id']]
                assert len(top)==int(c[arm+'_actual_top10_slots'])
                for group in ['target','comparison']:
                    n=sum(t['store_id'] in groups[group] for t in top)
                    assert n==int(c[arm+'_'+group+'_top10'])==m['groups'][group]['all']['hits']['top10']
                displayed=sorted(index[(r['id'],model)],key=lambda x:int(x['rank']))
                assert [(c['store_id'],c['name']) for c in displayed]==[(c['store_id'],c['name']) for c in m['cards']]
                ranked_slates+=1
        del raw
    assert ranked_slates==9504
    for cell in summary['cells']:
        cs=[r for r in cases if r['context']==cell['context'] and r['dimension']==cell['dimension'] and r['model']==cell['model']]
        assert len(cs)==cell['seeds']==6
        for metric in ['score_switch','card_switch']:
            d=direction(cs,metric)
            for key in ['positive','negative','unchanged']:assert d[key]==cell[metric][key]
    return {'states':3168,'matched_comparisons':792,'model_case_comparisons':2376,'actual_slate_count':len(index),'actual_card_slots':len(cards),'four_unique_cards_and_csv_counts':'passed','per_case_balance_and_cell_sign_counts':'passed','raw_top10_membership_and_card_names':'passed','ranked_slates_checked':ranked_slates}


def run():
    ap=argparse.ArgumentParser();ap.add_argument('--coverage-only',action='store_true');args=ap.parse_args()
    assert sha(O/'fixtures.json')==FIXTURE_SHA
    assert sha(O/'information-example-selection.json')==SELECTION_SHA
    names=['fixtures.json','coverage.json','context-audit.json','information-example-selection.json','plot-information.py']
    if (O/'data-snapshot-map.json').exists(): names.append('data-snapshot-map.json')
    sources={name:sha(O/name) for name in names}
    cov=readj('coverage.json');audit=readj('context-audit.json');selection=readj('information-example-selection.json')
    out=coverage_figure(cov,audit)
    if not args.coverage_only:
        sources.update({name:sha(O/name) for name in ['summary.json','case-effects.csv','actual-cards.csv','part-0/run/results.json','part-1/run/results.json']})
        summary=readj('summary.json');cases=readcsv('case-effects.csv');cards=readcsv('actual-cards.csv')
        out['validation']=validate_summary(summary,cases,cards,cov)
        out.update(effects_figure(summary,cases));out.update(effects_figure(summary,cases,'menu'));out.update(retention_figure(summary,cards));out.update(examples_figure(selection,cases,cards,cov))
    for name,h in sources.items():assert sha(O/name)==h,f'Input changed during rendering: {name}'
    out['source_sha256']=sources
    out['limitations']=['Frozen-catalogue real vectors with hypothetical feedback; not online user accuracy or a measurement after the later live-data refresh.','Six seeds reuse catalogue stores and are not independent users.','Zero card balance change does not imply identical stores or identical ranking.','Selection examples frozen before model results; alignment diagnostic had already been inspected.','Core and menu label populations must be kept separate.','Attribute labels describe evidence across a store menu, not necessarily the same dish used as the current-food context.','Operational partial leave-one-out scoring and research full-posterior scoring use observed responses differently; final-card retention is not a fair model-superiority estimate.']
    stem='information-coverage' if args.coverage_only else 'information-figures'
    writej(stem+'-data.json',out)
    artifacts=[p for p in sorted(O.glob('information-*.png')) if not args.coverage_only or p.stem=='information-coverage']
    artifacts += [p.with_suffix('.svg') for p in artifacts]
    artifacts += [O/(stem+'-data.json')]
    writej(stem+'-manifest.json',{'source_sha256':sources,'outputs_sha256':{p.name:sha(p) for p in artifacts},'mode':'coverage_only' if args.coverage_only else 'complete','validation':out.get('validation')})
    print(json.dumps({'outputs':[p.name for p in artifacts],'validation':out.get('validation')},ensure_ascii=False))

if __name__=='__main__':run()
