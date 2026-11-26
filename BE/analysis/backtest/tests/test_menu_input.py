"""Description-aware inputs, legacy compatibility and safe incremental embedding."""
import json

import numpy as np
import pytest

from analysis import embed, vectors
from analysis.content.menus import MenuSpace, VARIANTS, read_menu_names
from analysis.menu_input import menu_input


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def test_same_named_courses_keep_different_contents_and_raw_order(tmp_path, monkeypatch):
    rows = [
        {"store_id": "s", "menu_index": 2, "menu_name": "코스", "description": "새우와 생선"},
        {"store_id": "s", "menu_index": 0, "menu_name": "코스", "description": "고기와 야채"},
        {"store_id": "s", "menu_index": 1, "menu_name": "코스", "description": " 고기와\n야채 "},
        {"store_id": "s", "menu_index": 3, "menu_name": "공기밥", "description": None},
    ]
    write_rows(tmp_path / "menus.jsonl", rows)
    monkeypatch.setattr(embed, "DATA_DIR", tmp_path)
    texts, index = embed.build_menus()
    assert texts == ["코스\n새우와 생선", "코스\n고기와 야채", "공기밥"]
    assert index[0]["key"] != index[1]["key"]
    assert index[2]["key"] == "공기밥"  # old names lookup still works
    keys = read_menu_names(tmp_path, described=True)["s"]
    assert keys == [index[1]["key"], index[0]["key"], "공기밥"]
    assert read_menu_names(tmp_path)["s"] == ["코스", "공기밥"]
    space = MenuSpace(["s"], {"s": keys}, index, np.eye(3, dtype=np.float32))
    packed = space.packed(VARIANTS["top8"])
    np.testing.assert_array_equal(packed.vecs, np.eye(3)[[1, 0, 2]])
    assert packed.names == ["코스", "코스", "공기밥"]


def test_existing_index_keeps_name_only_artifact_compatibility(tmp_path, monkeypatch):
    write_rows(tmp_path / "menus.jsonl", [
        {"store_id": "s", "menu_name": "코스", "description": "새우와 생선"}])
    monkeypatch.setattr(vectors, "load", lambda kind: ([{"row": 0, "text": "코스"}], np.ones((1, 2))))
    monkeypatch.setattr(vectors, "manifest", lambda: {})
    assert MenuSpace.build(["s"], data_dir=tmp_path).packed(VARIANTS["top8"]).counts.tolist() == [1]


class Tokenizer:
    model_max_length = 256

    def __call__(self, texts, **kwargs):
        return {"input_ids": [list(range(len(t) + 2)) for t in texts]}


class Model:
    tokenizer = Tokenizer()
    max_seq_length = 256

    def __init__(self):
        self.encoded = []

    def get_embedding_dimension(self):
        return 2

    def encode(self, texts, **kwargs):
        self.encoded.extend(texts)
        return np.tile(np.array([[0, 1]], dtype=np.float32), (len(texts), 1))


def cache_at(path, texts, *, model=embed.MODEL_NAME):
    path.mkdir()
    matrix = np.tile(np.array([[1, 0]], dtype=np.float32), (len(texts), 1))
    np.save(path / "menus.npy", matrix)
    write_rows(path / "menus.index.jsonl", [{"row": i, "text": t} for i, t in enumerate(texts)])
    (path / "manifest.json").write_text(json.dumps({"menus": {
        "model": model, "dim": 2, "max_length": 8,
        "sha256": embed.sha256_file(path / "menus.npy")}}))


def test_incremental_embeddings_reuse_only_untruncated_exact_text(tmp_path, monkeypatch):
    source, output, old = tmp_path / "source", tmp_path / "out", tmp_path / "old"
    source.mkdir()
    write_rows(source / "menus.jsonl", [
        {"menu_name": "밥"}, {"menu_name": "여덟토큰보다긴메뉴"},
        {"menu_name": "코스", "description": "실제 새우 요리"}])
    cache_at(old, ["밥", "여덟토큰보다긴메뉴", "코스"])
    monkeypatch.setattr(embed, "DATA_DIR", source)
    monkeypatch.setattr(embed, "VECTOR_DIR", output)
    model = Model()
    args = dict(model=model, model_name=embed.MODEL_NAME, batch_size=2,
                max_length=64, limit=None, checkpoint_every=1, reuse_dir=old)
    meta = embed.embed_kind("menus", **args)
    assert model.encoded == ["여덟토큰보다긴메뉴", "코스\n실제 새우 요리"]
    assert (meta["reused_rows"], meta["encoded_rows"]) == (1, 2)
    np.testing.assert_array_equal(np.load(output / "menus.npy"), [[1, 0], [0, 1], [0, 1]])
    assert embed.embed_kind("menus", **args) is None
    # Same count is not the same input: edited course contents must be rebuilt.
    rows = list(embed.iter_jsonl(source / "menus.jsonl"));rows[-1]["description"] = "실제 고기 요리"
    write_rows(source / "menus.jsonl", rows)
    assert embed.embed_kind("menus", **args) is not None
    assert model.encoded[-1] == "코스\n실제 고기 요리"


def test_description_truncation_or_wrong_cache_model_fails_explicitly(tmp_path):
    with pytest.raises(ValueError, match="truncate"):
        embed.menu_token_lengths(Model(), ["코스\n" + "실제 설명 " * 10], 32)
    with pytest.raises(ValueError, match="model limit"):
        embed.menu_token_lengths(Model(), ["밥"], 257)
    cache_at(tmp_path / "cache", ["밥"], model="different-model")
    with pytest.raises(ValueError, match="model/dimension"):
        embed.menu_reuse(tmp_path / "cache", ["밥"], [3], embed.MODEL_NAME, 2)


def test_menu_assignment_and_order_invalidate_similarity_cache():
    index = [{"row": 0, "text": "a"}, {"row": 1, "text": "b"}]
    left = MenuSpace(["s"], {"s": ["a", "b"]}, index, np.eye(2), manifest_sha="same")
    right = MenuSpace(["s"], {"s": ["b", "a"]}, index, np.eye(2), manifest_sha="same")
    assert left.fingerprint(VARIANTS["top8"]) != right.fingerprint(VARIANTS["top8"])


def test_no_description_does_not_rewrite_existing_name():
    assert menu_input({"menu_name": "  A  코스  ", "description": " \n "}).key == "A  코스"


def test_raw_name_cannot_silently_alias_a_described_menu_key(tmp_path, monkeypatch):
    described = {"menu_name": "코스", "description": "새우와 생선"}
    alias = {"menu_name": menu_input(described).key}
    monkeypatch.setattr(embed, "DATA_DIR", tmp_path)
    for rows in ([described, alias], [alias, described]):
        write_rows(tmp_path / "menus.jsonl", rows)
        with pytest.raises(ValueError, match="key collision"):
            embed.build_menus()


def test_new_index_never_silently_drops_an_unknown_description(tmp_path, monkeypatch):
    write_rows(tmp_path / "menus.jsonl", [
        {"store_id": "s", "menu_name": "코스", "description": "새로 바뀐 코스 내용"}])
    old = menu_input({"menu_name": "코스", "description": "예전 코스 내용"})
    index = [{"row": 0, "key": old.key, "text": old.text, "menu_name": old.name}]
    monkeypatch.setattr(vectors, "load", lambda kind: (index, np.ones((1, 2))))
    monkeypatch.setattr(vectors, "manifest", lambda: {})
    with pytest.raises(ValueError, match="misses 1 source input keys"):
        MenuSpace.build(["s"], data_dir=tmp_path)
