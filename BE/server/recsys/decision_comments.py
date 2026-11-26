"""Short grounded explanations for qualified final candidates, without self-reference."""
import numpy as np

from . import content, store, taste


def reasons(sess, store_ids, *, manual=False):
    fallback = ('현재 반응과 조건을 바탕으로 고른 후보예요.' if manual
                else '여러 취향 신호를 종합해 적합 기준을 통과한 후보예요.')
    if manual and not (sess['like_ids'] or sess['dislike_ids'] or sess.get('memory')):
        fallback = '아직 취향 반응이 없어 기본 추천과 현재 조건으로 고른 후보예요.'
    out = dict.fromkeys(store_ids, fallback)
    art = content.load()
    if art is None or not store_ids:
        return out
    likes, dislikes, rated = taste._idx(sess, art)
    z = taste.components(sess, art)
    labels = {'menu': '메뉴 구성', 'text': '가게 소개에 나타난 특징',
              'review': '리뷰에 나타난 특징', 'food': '음식 사진의 특징'}
    for sid in store_ids:
        i = art.pos.get(sid)
        if i is None:
            continue
        refs = likes[likes != i]
        values = (taste.components(sess, art, {sid})[:, i]
                  if i in rated else z[:, i])
        options = []
        for kind, label in labels.items():
            j = content.IDX[kind]
            contribution = float(sess['m'][j] * values[j])
            if not np.isfinite(contribution) or contribution <= 0 or sess['m'][j] <= 0:
                continue
            h = art.best_like(kind, i, refs)
            if h is None or float(getattr(art, 'sim_' + kind)[h, i]) <= 0:
                continue
            name = (store.get_store(art.ids[h]) or {}).get('name')
            if name:
                options.append((contribution, f'좋아요한 {name}의 {label}과 비슷한 점이 있어요.'))
        if options:
            out[sid] = max(options, key=lambda row: row[0])[1]
    return out
