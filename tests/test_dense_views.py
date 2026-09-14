from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import api.dense_views as dense_views
from api.dense_views import dense_view_queries, dense_view_text
from api.name_variants import NameVariantExpander, build_entity_aliases

RECORD = {
    "party_type": "person",
    "name_kanji": "髙橋 太郎",
    "name_kana": "タカハシ タロウ",
    "name_romaji": "Taro Takahashi",
    "postal_code": "100-0001",
    "address": "東京都千代田区丸の内1-2-3",
}


def test_dense_view_selection_follows_populated_query_families():
    available = {"name": object(), "address": object(), "alias": object()}
    assert dense_views.selected_dense_views(RECORD, available) == ("name", "address", "alias")
    assert dense_views.selected_dense_views({"n_name_loose": "高橋太郎"}, available) == (
        "name",
        "alias",
    )
    assert dense_views.selected_dense_views({"n_address": "東京都"}, available) == ("address",)


def test_dense_name_view_is_field_labeled_and_normalized():
    text = dense_view_text(RECORD, "name")
    assert text.startswith("party_type=person")
    assert "name_kanji=高橋太郎" in text
    assert "name_kana=タカハシタロウ" in text
    assert "name_romaji=tarotakahashi" in text
    assert "postal_code" not in text


def test_dense_address_view_excludes_name_fields():
    text = dense_view_text(RECORD, "address")
    assert "postal_code=1000001" in text
    assert "address=東京都千代田区丸の内123" in text
    assert "name_kanji" not in text


def test_name_variant_expander_preserves_observation_provenance():
    variants = NameVariantExpander({"タカハシタロウ": ("別表記 太郎",)}).expand(RECORD)
    assert {variant.field for variant in variants} == {
        "name_kanji",
        "name_kana",
        "name_romaji",
    }
    assert any(variant.method == "kana_script" for variant in variants)
    assert any(variant.method == "homophone_kanji" for variant in variants)


def test_entity_alias_artifact_maps_aliases_to_reference_rows(tmp_path):
    reference = pd.DataFrame([RECORD | {"record_id": "R1"}])
    descriptor = build_entity_aliases(
        reference,
        {"R1": "N1"},
        tmp_path / "entity_aliases.parquet",
        data_fingerprint="fp",
    )
    aliases = pd.read_parquet(tmp_path / "entity_aliases.parquet")
    assert descriptor["row_count"] == len(aliases)
    assert set(aliases["nayose_id"]) == {"N1"}
    assert set(aliases["record_index"]) == {0}
    assert set(aliases["data_fingerprint"]) == {"fp"}


def test_entity_alias_artifact_is_fingerprint_bound(tmp_path):
    reference = pd.DataFrame([RECORD | {"record_id": "R1"}])
    output = tmp_path / "entity_aliases.parquet"
    build_entity_aliases(reference, {"R1": "N1"}, output, data_fingerprint="fp")
    with pytest.raises(RuntimeError, match="fingerprint"):
        build_entity_aliases(reference, {"R1": "N1"}, output, data_fingerprint="other")


def test_dense_view_artifacts_build_and_restore_with_source_row_mapping(tmp_path, monkeypatch):
    reference = pd.DataFrame([RECORD | {"record_id": "R1"}, RECORD | {"record_id": "R2"}])
    aliases = pd.DataFrame(
        {
            "normalized_alias": ["takahashi", "たかはし"],
            "record_index": [1, 0],
        }
    )

    class FakeModel:
        def encode(self, texts, **kwargs):
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(texts), 1))

    loads: list[tuple[str, str]] = []

    def fake_model(model_id: str, device: str) -> FakeModel:
        loads.append((model_id, device))
        return FakeModel()

    monkeypatch.setattr(dense_views, "_model", fake_model)
    artifacts = dense_views.build_dense_view_artifacts(
        reference,
        tmp_path / "generation",
        tmp_path / "run",
        model_id="fake/model",
        data_fingerprint="fp",
        device="cpu",
        batch_size=1,
        aliases=aliases,
    )
    aliases.to_parquet(tmp_path / "run" / "entity_aliases.parquet", index=False)
    loaded = dense_views.load_dense_view_artifacts(
        tmp_path / "run",
        {"runtime_artifacts": {"dense_views": artifacts}},
        expected_rows=2,
    )
    assert set(loaded) == {"name", "address", "alias"}
    assert loads == [("fake/model", "cpu")]
    alias_indices = loaded["alias"].row_indices
    assert alias_indices is not None
    assert alias_indices.tolist() == [1, 0]


def test_alias_queries_match_single_alias_index_rows():
    queries = dense_view_queries(RECORD, "alias")
    assert queries == (
        "name=高橋太郎",
        "name=タカハシタロウ",
        "name=tarotakahashi",
    )


def test_field_view_evidence_does_not_overwrite_record_dense_score():
    from api.dense_retrieval import _apply_view_dense

    states = {3: {"dense_score": 0.92, "dense_rank": 1}}
    artifact = dense_views.DenseView(
        "name",
        np.empty((1, 1), dtype=np.float32),
        object(),
        row_indices=np.asarray([3], dtype=np.int32),
    )
    _apply_view_dense(
        "name",
        artifact,
        np.asarray([0], dtype=np.int32),
        np.asarray([0.99], dtype=np.float32),
        states,
    )
    assert states[3]["dense_score"] == 0.92
    assert states[3]["dense_rank"] == 1
    assert states[3]["dense_view_scores"] == {"name": pytest.approx(0.99)}


def test_weighted_fusion_keeps_weak_views_below_record_dense_evidence():
    from api.retrieval_fusion import RetrievalFusionPolicy

    policy = RetrievalFusionPolicy()
    record = {"dense_rank": 1}
    weak_views = {"dense_view_ranks": {"name": 1, "address": 1, "alias": 1}}
    assert policy.score(record) > policy.score(weak_views)


def test_fusion_weights_only_populated_query_views():
    from api.retrieval_fusion import RetrievalFusionPolicy

    policy = RetrievalFusionPolicy()
    name_query = {"n_name_loose": "佐藤", "n_phone": ""}
    address_query = {"n_name_loose": "", "n_address": "東京都", "n_postal": ""}
    assert policy.view_weight("name", name_query) == pytest.approx(0.30)
    assert policy.view_weight("address", name_query) == 0.0
    assert policy.view_weight("address", address_query) == pytest.approx(0.45)
    assert policy.view_weight("name", {**name_query, "n_phone": "09012345678"}) == pytest.approx(
        0.18
    )


def test_dense_view_rejects_unknown_view():
    with pytest.raises(ValueError, match="unsupported dense view"):
        dense_view_text(RECORD, "contact")
