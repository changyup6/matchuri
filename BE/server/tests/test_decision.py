"""선택 제안: 개수보다 적합도/다양성, 중복 평가는 증거로 부풀리지 않는다."""
import copy
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from recsys import content, decision, taste


@pytest.fixture
def case(monkeypatch):
    n = 40
    ids = [f"s{i:02}" for i in range(n)]
    sim = np.full((n, n), 0.2)
    np.fill_diagonal(sim, 1)
    art = SimpleNamespace(ids=ids, pos={s: i for i, s in enumerate(ids)},
                          brand=ids.copy(), sim_menu=sim.copy(), sim_text=sim.copy(),
                          sim_review=sim.copy(), sim_food=sim.copy())
    sess = {"m": np.ones(8), "v": np.ones(8), "like_ids": ids[:4],
            "dislike_ids": ids[4:16], "shown": set(ids[:16]), "n": 16}
    # 긍정·부정 검증 수가 각각 min_validation_each(5) 이상이어야 판정이 열린다.
    sess["hist"] = [{"personal_rank": 0.9 if i < 5 else 0.1, "like": i < 5}
                    for i in range(16)]
    sess["decision_checkpoints"] = [copy.deepcopy(sess) for _ in range(4)]
    monkeypatch.setattr(content, "load", lambda: art)
    monkeypatch.setattr(decision, "_scores", lambda *a, **kw:
                        (np.arange(n, dtype=float), np.arange(n, dtype=float), np.zeros(n)))
    return art, sess


def test_ready_returns_exactly_four_and_is_read_only(case):
    art, sess = case
    before = copy.deepcopy(sess)
    first = decision.evaluate(sess)
    assert first["ready"] and len(set(first["store_ids"])) == 4
    assert first == decision.evaluate(sess)
    assert sess["shown"] == before["shown"] and sess["n"] == before["n"]
    np.testing.assert_array_equal(sess["m"], before["m"])


def test_repeated_ratings_do_not_inflate_readiness(case):
    _, sess = case
    sess.update(n=200, like_ids=["s00"], dislike_ids=["s01"])
    result = decision.evaluate(sess)
    assert not result["ready"] and result["rated_count"] == 2


def test_twenty_dislikes_do_not_force_a_proposal(case):
    art, sess = case
    sess.update(like_ids=[], dislike_ids=art.ids[:20], n=20)
    assert not decision.evaluate(sess)["ready"]


def test_unstable_taste_waits(case, monkeypatch):
    _, sess = case
    def scores(_art, state, **kw):
        values = np.arange(40, dtype=float)
        return values, (values if state is sess else -values), np.zeros(40)
    monkeypatch.setattr(decision, "_scores", scores)
    result = decision.evaluate(sess, config=replace(decision.DEFAULT, stable_top=8))
    assert result["status"] == "learning" and result["stability"] == 0


def test_popularity_alone_is_not_personal_fit(case, monkeypatch):
    _, sess = case
    monkeypatch.setattr(decision, "_scores", lambda *a, **kw:
                        (np.arange(40, dtype=float), np.zeros(40), np.zeros(40)))
    result = decision.evaluate(sess)
    assert not result["ready"] and result["qualified_count"] == 0


def test_stable_but_uninformative_predictions_do_not_trigger(case):
    _, sess = case
    for row in sess["hist"]:
        row["personal_rank"] = 0.5
    result = decision.evaluate(sess)
    assert result["validation_auc"] == 0.5 and result["status"] == "learning"


def test_final_candidates_ignore_preference_and_serving_status(case, monkeypatch):
    _, sess = case
    assert not decision.evaluate(sess, allow={"s37", "s38", "s39"})["ready"]
    values = np.zeros(40)
    values[[0, 4, 20, 36]] = 100
    monkeypatch.setattr(decision, "_scores", lambda *a, **kw: (values, values, np.zeros(40)))
    sess["shown"].add("s36")
    result = decision.evaluate(sess)
    # liked s00 / disliked s04 / unseen s20 / served-unanswered s36 all qualify.
    assert set(result["store_ids"]) == {"s00", "s04", "s20", "s36"}
    assert decision.evaluate(sess, allow={"s00", "s04", "s20"})["store_ids"] == []


def test_four_likes_do_not_bypass_personalization(case):
    _, sess = case
    sess["hist"] = []
    sess["n"] = 30
    result = decision.evaluate(sess)
    assert len(sess["like_ids"]) == 4
    assert result["status"] == "learning" and not result["ready"]


@pytest.mark.parametrize("kind", ["brand", "menu", "mean", "missing"])
def test_four_similar_or_unknown_candidates_are_not_enough(case, kind):
    art, sess = case
    if kind == "brand":
        art.brand = ["same"] * 40
    elif kind == "menu":
        art.sim_menu[:] = 0.9
    elif kind == "mean":
        art.sim_menu[:] = 0.84
        art.sim_text[:] = art.sim_review[:] = art.sim_food[:] = 0.99
    else:
        art.sim_text[:] = art.sim_review[:] = art.sim_food[:] = np.nan
    result = decision.evaluate(sess)
    assert result["qualified_count"] >= 4
    assert result["status"] == "insufficient_candidates" and result["store_ids"] == []


def test_search_backtracks_from_highest_scoring_dead_end(case):
    art, _ = case
    # 첫 후보는 모두와 중복이지만 뒤의 네 곳은 서로 다르다.
    art.sim_menu[0, 1:5] = 0.95
    assert decision._select(art, [0, 1, 2, 3, 4], decision.DEFAULT) == [1, 2, 3, 4]


def test_category_labels_do_not_override_vector_diversity(case, monkeypatch):
    art, _ = case
    monkeypatch.setattr(decision, "_fine_groups", lambda art: dict.fromkeys(art.ids, "육류,고기"))
    assert decision._diverse(art, 0, 1, decision.DEFAULT)
    assert decision._select(art, [0, 1, 2, 3, 4], decision.DEFAULT) == [0, 1, 2, 3]


def test_distinct_fine_categories_can_share_the_same_broad_cuisine(case, monkeypatch):
    art, _ = case
    groups = {sid: f"세부{i}" for i, sid in enumerate(art.ids)}
    groups.update(s00="국밥", s01="육류,고기")
    monkeypatch.setattr(decision, "_fine_groups", lambda art: groups)
    assert decision._diverse(art, 0, 1, decision.DEFAULT)


def test_missing_artifacts_returns_unavailable(case, monkeypatch):
    _, sess = case
    monkeypatch.setattr(content, "load", lambda: None)
    assert decision.evaluate(sess)["status"] == "unavailable"


@pytest.mark.parametrize('n,progress,required', [(30, 0, True), (45, .5, True), (59, 29/30, True), (60, 1, False), (90, 1, False)])
def test_fatigue_schedule_uses_distinct_feedback(n, progress, required):
    sess = {'n': n, 'like_ids': [str(i) for i in range(n)], 'dislike_ids': []}
    active, quality = decision.requirements(sess)
    assert decision.relaxation(sess) == pytest.approx(progress)
    assert quality == required
    # 완화는 기준값을 **그대로** 타야 한다. 예전에는 여기에 0.8 을 그대로 적어서
    # `Config.min_validation_auc` 를 바꿔도 이 테스트가 그것을 잡지 못했다.
    assert decision.DEFAULT.min_validation_auc == .70
    assert active.min_validation_auc == pytest.approx(decision.DEFAULT.min_validation_auc * (1 - .25 * progress))
    # 완화가 끝까지 간 자리에서도 실효 문턱이 동전 던지기(0.5) 위에 남아야 한다.
    assert active.min_validation_auc > .5
    assert active.fit_quantile == decision.DEFAULT.fit_quantile
    assert active.max_mean_similarity == decision.DEFAULT.max_mean_similarity
    sess['like_ids'] = ['one']
    assert decision.relaxation(sess) == 0


@pytest.fixture
def long_case(case, monkeypatch):
    art, sess = case
    art.ids = [f's{i:03}' for i in range(100)]
    art.pos = {s: i for i, s in enumerate(art.ids)}
    art.brand = art.ids.copy()
    for kind in ('menu', 'text', 'review', 'food'):
        setattr(art, 'sim_' + kind, np.full((100, 100), .2))
    sess.update(n=60, like_ids=art.ids[:20], dislike_ids=art.ids[20:60])
    sess['hist'] = [{'personal_rank': .1 if i%2 else .9, 'like': bool(i%2)} for i in range(30)]
    values = np.arange(100, dtype=float)
    monkeypatch.setattr(decision, '_scores', lambda a, s, **kw:
        (values, values if s is sess else -values, np.zeros(100)))
    return art, sess


def test_long_exploration_offers_fitting_diverse_candidates_despite_failed_diagnostics(long_case):
    _, sess = long_case
    strict = decision.evaluate(sess, config=replace(decision.DEFAULT, relax_after=1000, relax_until=1030))
    assert not strict['ready']
    relaxed = decision.evaluate(sess)
    assert relaxed['ready'] and len(relaxed['store_ids']) == 4
    assert relaxed['validation_auc'] == 0 and relaxed['stability'] == 0
    sess['hist'] = []  # absent diagnostics are not fabricated as successful.
    result = decision.evaluate(sess)
    assert result['ready'] and result['validation_auc'] is None


@pytest.mark.parametrize('blocker', ['filter', 'brand', 'similarity', 'fit', 'likes'])
def test_long_exploration_never_relaxes_candidate_requirements(long_case, monkeypatch, blocker):
    art, sess = long_case
    kwargs = {}
    if blocker == 'filter':
        kwargs['allow'] = set(art.ids[-3:])
    elif blocker == 'brand':
        art.brand = ['same'] * 100
    elif blocker == 'similarity':
        art.sim_menu[:] = .95
    elif blocker == 'fit':
        monkeypatch.setattr(decision, '_scores', lambda *a, **kw: (np.ones(100), np.zeros(100), np.zeros(100)))
    else:
        sess['like_ids'], sess['dislike_ids'] = [], art.ids[:60]
    assert not decision.evaluate(sess, **kwargs)['ready']


def test_long_exploration_preserves_requested_count_and_continued_slates(long_case):
    _, sess = long_case
    first = decision.evaluate(sess)['store_ids']
    sess['declined_slates'] = [first]
    second = decision.evaluate(sess)
    assert second['ready'] and set(second['store_ids']) != set(first)
    for n in (1, 4, 8):
        out = decision.evaluate(sess, config=replace(decision.DEFAULT, count=n))
        assert out['ready'] and len(out['store_ids']) == n
    assert not decision.evaluate(sess, config=replace(decision.DEFAULT, count=20))['ready']


def test_liked_candidate_is_scored_without_itself(art, sess):
    taste.record(sess, art.ids[0], True)
    score, personal, uncertainty = decision._scores(art, sess, loo=True)
    _, dis, rated = taste._idx(sess, art)
    expected = art.component_scores(np.array([], dtype=int), dis, exclude=rated)
    assert score[0] == pytest.approx(sess["m"] @ expected[:, 0])
    assert np.isfinite(personal).all() and np.isfinite(uncertainty).all()


def test_disliked_candidate_is_scored_without_its_own_penalty(art, sess):
    taste.record(sess, art.ids[0], True)
    taste.record(sess, art.ids[2], False)
    score, _, _ = decision._scores(art, sess, loo=True)
    liked, _, rated = taste._idx(sess, art)
    expected = art.component_scores(liked, np.array([], dtype=int), exclude=rated,
        like_weights=np.array([taste.RECENCY_DECAY]), dislike_weights=np.array([]))
    assert score[2] == pytest.approx(sess["m"] @ expected[:, 2])


def test_checkpoint_replay_is_bounded_and_identical(art):
    feedback = [(sid, i % 2 == 0) for i, sid in enumerate(art.ids)]
    a, b = taste.replay(feedback), taste.replay(feedback)
    assert len(a["decision_checkpoints"]) == 4
    for left, right in zip(a["decision_checkpoints"], b["decision_checkpoints"]):
        np.testing.assert_array_equal(left["m"], right["m"])
        np.testing.assert_array_equal(left["v"], right["v"])
        assert left["like_ids"] == right["like_ids"]


def test_api_opt_in_and_cold_start():
    import main
    client = TestClient(main.app)
    uid = client.post("/users", json={}).json()["user_id"]
    response = client.get(f"/users/{uid}/decision?mode=auto")
    assert response.status_code == 200
    assert response.json()["status"] == "learning" and response.json()["cards"] == []
    card = client.get("/cards?limit=1").json()[0]
    body = {"user_id": uid, "card_id": card["card_id"], "prefer": "like"}
    result = client.post("/preferences?include_decision=true", json=body)
    assert result.status_code == 200 and result.json()["decision"]["status"] == "learning"
    assert set(result.json()["decision"]) == {"ready", "status", "proposal_id", "message", "cards"}
    assert client.post("/preferences", json=body).json()["decision"] is None
    assert client.get("/users/unknown-decision-user/decision").status_code == 404


def test_api_hydrates_four_cards_without_serving_or_learning(monkeypatch):
    import main
    from recsys import policy
    client = TestClient(main.app)
    uid = client.post("/users", json={}).json()["user_id"]
    cards = client.get("/cards?limit=4").json()
    ids = [c["store"]["id"] for c in cards]
    monkeypatch.setattr(decision, "evaluate", lambda *a, **kw: {
        "ready": True, "status": "ready", "store_ids": ids.copy(),
        "rated_count": 20, "liked_count": 5, "stability": 0.8,
        "validation_auc": 0.8, "qualified_count": 10})
    sess = policy.session_for(uid)
    before = copy.deepcopy(sess)
    a = client.get(f"/users/{uid}/decision").json()
    b = client.get(f"/users/{uid}/decision").json()
    assert a == b and a["ready"] and len(a["cards"]) == 4
    assert a["proposal_id"] and a["message"]
    assert set(a) == {"ready", "status", "proposal_id", "message", "cards"}
    for name in ("legacy", "taste"):
        assert sess[name]["shown"] == before[name]["shown"]
        assert sess[name]["n"] == before[name]["n"]
        np.testing.assert_array_equal(sess[name]["m"], before[name]["m"])


def test_location_required_does_not_fail_preference_write(monkeypatch):
    import main
    client = TestClient(main.app)
    uid = client.post("/users", json={}).json()["user_id"]
    monkeypatch.setattr(main.user_registry, "filter_of", lambda uid:
                        {"cat": [], "tag": [], "within_m": 500})
    card = client.get("/cards?limit=1").json()[0]
    response = client.post("/preferences?include_decision=true", json={
        "user_id": uid, "card_id": card["card_id"], "prefer": "like"})
    assert response.status_code == 200
    assert response.json()["decision"]["status"] == "location_required"


@pytest.mark.parametrize('n', [1, 2, 4, 6])
def test_requested_count_uses_same_qualification_rules(case, n):
    _, sess = case
    result = decision.evaluate(sess, config=replace(decision.DEFAULT, count=n))
    assert result['ready'] and len(result['store_ids']) == n


def test_requested_count_is_not_silently_reduced(case):
    _, sess = case
    result = decision.evaluate(sess, config=replace(decision.DEFAULT, count=7))
    assert not result['ready'] and result['store_ids'] == []


def test_larger_selection_matches_exhaustive_first_feasible(monkeypatch):
    from itertools import combinations
    # Exercise the exact feasible seed search independently of the vector-based
    # refinement, whose pair and relevance constraints have separate coverage.
    monkeypatch.setattr(decision, '_refine', lambda art, ranked, chosen, *a, **kw: chosen)
    for seed in range(8):
        rng = np.random.default_rng(seed)
        mat = rng.random((9, 9)) < .65
        mat = np.triu(mat, 1)
        mat |= mat.T
        monkeypatch.setattr(decision, '_diverse', lambda art, a, b, config: bool(mat[a, b]))
        for n in range(1, 7):
            expected = next((list(c) for c in combinations(range(9), n)
                             if all(mat[a, b] for a, b in combinations(c, 2))), [])
            assert decision._select(None, list(range(9)), replace(decision.DEFAULT, count=n)) == expected


def test_impossible_large_group_uses_safe_pruning(monkeypatch):
    # 60 vertices in 15 mutually exclusive groups: at most 15 distinct choices.
    monkeypatch.setattr(decision, '_diverse', lambda art, a, b, config: a // 4 != b // 4)
    assert decision._select(None, list(range(60)), replace(decision.DEFAULT, count=20)) == []


@pytest.mark.parametrize('n', [1, 3, 4, 6, 20])
def test_http_count_propagates_to_evaluation_and_hydration(monkeypatch, n):
    import main
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    ids = [c['store']['id'] for c in client.get('/cards?limit=20').json()]
    seen = []
    def ready(*args, config=decision.DEFAULT, **kwargs):
        seen.append(config.count)
        return {'ready': True, 'status': 'ready', 'store_ids': ids[:config.count],
                'rated_count': 20, 'liked_count': 5, 'stability': .8,
                'validation_auc': .8, 'qualified_count': 20}
    monkeypatch.setattr(decision, 'evaluate', ready)
    response = client.get(f'/users/{uid}/decision?n={n}')
    assert response.status_code == 200 and len(response.json()['cards']) == n
    cid = main.loader.card_id_by_store[ids[0]]
    saved = client.post(f'/preferences?include_decision=true&n={n}', json={
        'user_id': uid, 'card_id': cid, 'prefer': 'like'})
    assert saved.status_code == 200 and len(saved.json()['decision']['cards']) == n
    # The full export option must use the same count, including n=1 with no pairs.
    combined = client.post(f'/preferences?include_decision=true&include_decision_metrics=true&n={n}', json={
        'user_id': uid, 'card_id': cid, 'prefer': 'dislike'})
    assert combined.status_code == 200, combined.text
    data = combined.json()
    assert len(data['decision']['cards']) == len(data['decision_metrics']['decision']['store_ids']) == n
    assert data['decision_metrics']['config']['count'] == n
    assert len(data['decision_metrics']['pairs']) == n*(n-1)//2
    assert set(seen) == {n}


@pytest.mark.parametrize('n', [0, -1, 21, '1.5'])
def test_http_rejects_invalid_count_without_recording(n):
    import main
    from recsys import policy
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    cid = client.get('/cards?limit=1').json()[0]['card_id']
    assert client.get(f'/users/{uid}/decision?n={n}').status_code == 422
    assert client.get(f'/users/{uid}/decision/metrics?n={n}').status_code == 422
    assert client.post(f'/preferences?include_decision=true&n={n}', json={
        'user_id': uid, 'card_id': cid, 'prefer': 'like'}).status_code == 422
    assert policy.session_for(uid)['taste']['n'] == 0
