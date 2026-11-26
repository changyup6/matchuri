import numpy as np
import pytest

from .metrics import pairwise_win_rate, recommendation_counts


def test_card_share_and_unseen_recall_use_different_explicit_denominators():
    ids = list("abcdef")
    values = recommendation_counts(ids, list("bcaefd"), list("bcad"),
        {"food": list("abce"), "style": list("abe"), "unknown": ["f"]},
        observed=["a"], brands={"a": "chain", "b": "chain"}, top_k=3)
    assert values["unseen_store_and_brand_count"] == 4
    assert values["groups"]["food"] == {
        "catalogue_count": 4, "unseen_count": 2,
        "top_hits": 3, "top_share": 1., "card_hits": 3, "card_share": .75,
        "unseen_top_hits": 1, "unseen_top_recall": .5,
        "unseen_card_hits": 1, "unseen_card_recall": .5,
    }
    assert values["groups"]["style"]["card_hits"] == 2
    assert values["groups"]["style"]["unseen_card_recall"] == 0
    assert values["groups"]["unknown"]["catalogue_count"] == 1


def test_empty_population_is_undefined_not_zero_success():
    values = recommendation_counts(["a"], ["a"], [], {"food": ["a"]}, observed=["a"])
    row = values["groups"]["food"]
    assert row["card_share"] is None and row["unseen_top_recall"] is None
    assert pairwise_win_rate(np.array([1., 0.]), [], [1]) is None


def test_pairwise_ties_are_half_and_order_is_symmetric():
    scores = np.array([1., 0., 1., -1.])
    a = pairwise_win_rate(scores, [0, 1], [2, 3])
    b = pairwise_win_rate(scores, [2, 3], [0, 1])
    assert a == .625 and a + b == 1


@pytest.mark.parametrize("kwargs", [
    {"ranking": list("aab")}, {"slate": ["outside"]},
    {"groups": {"bad": ["a", "a"]}}, {"top_k": True}, {"observed": ["outside"]},
])
def test_invalid_population_is_rejected(kwargs):
    inputs = dict(ids=list("abc"), ranking=list("abc"), slate=["a"], groups={"food": ["a"]})
    inputs.update(kwargs)
    with pytest.raises(ValueError):
        recommendation_counts(**inputs)
