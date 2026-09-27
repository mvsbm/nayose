from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from api.benchmarking import (
    NayoseBenchmarkAdapter,
    NayoseResolutionAdapter,
    evaluate_resolution,
)
from api.domain.fingerprint import data_fingerprint

VISIBLE = {
    "party_type": ["person", "person", "person"],
    "name_kanji": ["甲 太郎", "乙 花子", "丙 次郎"],
    "name_kana": ["コウ タロウ", "オツ ハナコ", "ヘイ ジロウ"],
    "name_romaji": ["Ko Taro", "Otsu Hanako", "Hei Jiro"],
    "date_birth": ["1980-01-01", "1981-02-02", "1982-03-03"],
    "postal_code": ["1000001", "1500001", "5300001"],
    "address": ["東京都千代田区", "東京都渋谷区", "大阪府大阪市"],
    "phone": ["090-0000-0001", "090-0000-0002", "090-0000-0003"],
    "email": ["a@example.test", "b@example.test", "c@example.test"],
}


def _write_artifacts(root: Path) -> tuple[Path, Path]:
    generation = root / "generation"
    run = root / "run"
    (generation / "ground_truth").mkdir(parents=True)
    run.mkdir()
    reference = pd.DataFrame(
        {
            "record_id": ["R1", "R2", "R3"],
            **VISIBLE,
        }
    )
    incoming = pd.DataFrame(
        {
            "query_id": ["Q1", "Q2", "Q3"],
            **VISIBLE,
        }
    )
    fingerprint = data_fingerprint(reference)
    reference.to_parquet(generation / "reference_records.parquet", index=False)
    incoming.to_parquet(generation / "incoming_queries.parquet", index=False)
    pd.DataFrame({"record_id": ["R1", "R2", "R3"], "truth_id": ["T1", "T2", "T3"]}).to_parquet(
        generation / "ground_truth/record_truth.parquet", index=False
    )
    pd.DataFrame({"record_id": ["R1", "R2", "R3"], "nayose_id": ["E1", "E2", "E3"]}).to_parquet(
        run / "record_to_nayose.parquet", index=False
    )
    pd.DataFrame({"nayose_id": ["E1", "E2", "E3"], **VISIBLE}).to_parquet(
        run / "canonical_entities.parquet", index=False
    )
    pd.DataFrame(
        {
            "query_id": ["Q1", "Q2", "Q3"],
            "target_truth_id": ["T1", "T2", "T999"],
            "split": ["train", "calibration", "test"],
            "scenario": ["existing_stable", "existing_stable", "new_customer"],
            "expected_existing_customer": [True, True, False],
            "expected_action": ["AUTO_LINK", "REVIEW", "NEW_CUSTOMER"],
            "eligible_reference_record_ids": ['["R1"]', '["R2"]', "[]"],
            "related_reference_truth_ids": ["[]", "[]", "[]"],
            "hard_case": ["", "", ""],
        }
    ).to_parquet(generation / "ground_truth/evaluation_truth.parquet", index=False)
    pd.DataFrame({"query_id": ["Q1", "Q2", "Q3"]}).to_parquet(
        generation / "ground_truth/query_truth.parquet", index=False
    )
    (generation / "generation_manifest.json").write_text(
        json.dumps({"created_at_utc": "2026-01-01", "data_fingerprint": fingerprint}),
        encoding="utf-8",
    )
    (run / "run_manifest.json").write_text(
        json.dumps({"created_at_utc": "2026-01-02", "data_fingerprint": fingerprint}),
        encoding="utf-8",
    )
    return generation, run


def test_nayose_adapter_binds_truth_to_serving_entities(tmp_path: Path):
    generation, run = _write_artifacts(tmp_path)

    dataset = NayoseBenchmarkAdapter(generation, run, split_policy="source").load()

    assert {query.split for query in dataset.queries} == {"train", "validation", "test"}
    assert dataset.queries[0].target_ids == frozenset({"E1"})
    assert dataset.queries[2].target_ids == frozenset()
    assert "target_truth_id" not in dataset.queries[0].fields
    assert dataset.metadata["data_fingerprint"]

    entity_split = NayoseBenchmarkAdapter(generation, run).load(require_all_splits=False)
    assert entity_split.metadata["split_policy"] == "entity"


def test_nayose_adapter_rejects_fingerprint_mismatch(tmp_path: Path):
    generation, run = _write_artifacts(tmp_path)
    (run / "run_manifest.json").write_text(
        json.dumps({"data_fingerprint": "wrong"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="run fingerprint"):
        NayoseBenchmarkAdapter(generation, run).load()


def test_resolution_adapter_evaluates_final_decisions(tmp_path: Path):
    generation, run = _write_artifacts(tmp_path)
    dataset = NayoseBenchmarkAdapter(generation, run, split_policy="source").load()

    class Service:
        def recommend(self, run_id: str, record: dict[str, str]):
            if record["name_kanji"] == "丙 次郎":
                return {"action": "NO_MATCH", "candidates": [], "top_probability": 0, "margin": 0}
            target = "E1" if record["name_kanji"] == "甲 太郎" else "E2"
            return {
                "action": "AUTO_LINK",
                "top_nayose_id": target,
                "top_probability": 0.99,
                "margin": 0.5,
                "candidates": [{"nayose_id": target}],
            }

    resolver = NayoseResolutionAdapter(Service())
    report = evaluate_resolution(
        dataset,
        lambda run_id, query: resolver.resolve(run_id, query),
        "run",
    )

    assert report.metrics["train"].top1_identity_accuracy == 1.0
    assert report.metrics["test"].safe_no_auto_rate == 1.0
    assert report.metrics["test"].false_auto_link_rate == 0.0
    assert report.metadata["data_fingerprint"]
