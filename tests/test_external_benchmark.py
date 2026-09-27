from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from api.benchmarking import (
    CsvBenchmarkAdapter,
    NormalizedFieldCandidateProvider,
    NormalizedFieldScorer,
    StressConfig,
    evaluate_ranking,
    stress_dataset,
)
from api.benchmarking.contracts import BenchmarkDataset, BenchmarkEntity, BenchmarkQuery

COLUMNS = {
    "entity_id": "entity_id",
    "query_id": "query_id",
    "target_entity_id": "target_entity_id",
    "split": "split",
    "slice": "corruption",
    "name_kanji": "name",
    "phone": "phone_number",
    "postal_code": "postcode",
}


def _write_benchmark(root: Path) -> None:
    entities = [
        {
            "entity_id": "E1",
            "name": "佐藤 太郎",
            "phone_number": "090-1234-5678",
            "postcode": "100-0001",
        },
        {
            "entity_id": "E2",
            "name": "鈴木 花子",
            "phone_number": "080-2222-3333",
            "postcode": "150-0001",
        },
        {
            "entity_id": "E3",
            "name": "田中 次郎",
            "phone_number": "070-4444-5555",
            "postcode": "530-0001",
        },
    ]
    queries = [
        {
            "query_id": "Q1",
            "target_entity_id": "E1",
            "split": "train",
            "corruption": "phone-format",
            "name": "別名",
            "phone_number": "09012345678",
            "postcode": "",
        },
        {
            "query_id": "Q2",
            "target_entity_id": "E2",
            "split": "val",
            "corruption": "postal-format",
            "name": "",
            "phone_number": "",
            "postcode": "１５０−０００１",
        },
        {
            "query_id": "Q3",
            "target_entity_id": "E3",
            "split": "test",
            "corruption": "name-exact",
            "name": "田中 次郎",
            "phone_number": "",
            "postcode": "",
        },
    ]
    for filename, rows in (("entities.csv", entities), ("queries.csv", queries)):
        with (root / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (root / "benchmark.json").write_text(
        json.dumps(
            {
                "name": "nayose-fixture",
                "version": "1",
                "license": "test-only",
                "entities_file": "entities.csv",
                "queries_file": "queries.csv",
                "columns": COLUMNS,
            }
        ),
        encoding="utf-8",
    )


def test_external_adapter_and_ranker_cover_train_validation_test(tmp_path: Path):
    _write_benchmark(tmp_path)

    dataset = CsvBenchmarkAdapter(tmp_path).load()
    report = evaluate_ranking(
        dataset,
        NormalizedFieldScorer(),
        NormalizedFieldCandidateProvider(dataset.entities),
    )

    assert set(report.metrics) == {"train", "validation", "test"}
    assert all(metric.top1_accuracy == 1.0 for metric in report.metrics.values())
    assert all(metric.candidate_recall == 1.0 for metric in report.metrics.values())
    assert all(metric.mean_candidates == 1.0 for metric in report.metrics.values())
    assert report.slices["phone-format"]["train"].top1_accuracy == 1.0
    assert report.slices["postal-format"]["validation"].top1_accuracy == 1.0


def test_stress_transform_is_reproducible_and_preserves_labels(tmp_path: Path):
    _write_benchmark(tmp_path)
    dataset = CsvBenchmarkAdapter(tmp_path).load()

    first = stress_dataset(dataset, StressConfig(seed=7))
    second = stress_dataset(dataset, StressConfig(seed=7))

    assert first.queries == second.queries
    assert [query.target_entity_id for query in first.queries] == [
        query.target_entity_id for query in dataset.queries
    ]
    assert all(query.slice_name.startswith("stress:") for query in first.queries)
    assert first.metadata["stress_profile"] == "compound_typo_missingness"


def test_benchmark_rejects_entity_overlap_across_splits():
    dataset = BenchmarkDataset(
        name="fixture",
        version="1",
        license="test-only",
        entities=(BenchmarkEntity("E1", {"name_kanji": "A"}), BenchmarkEntity("E2", {})),
        queries=(
            BenchmarkQuery("Q1", "E1", "train", {}),
            BenchmarkQuery("Q2", "E1", "test", {}),
            BenchmarkQuery("Q3", "E2", "validation", {}),
        ),
    )

    with pytest.raises(ValueError, match="entity-disjoint"):
        dataset.validate()


def test_benchmark_rejects_hidden_source_mapping(tmp_path: Path):
    _write_benchmark(tmp_path)
    manifest = json.loads((tmp_path / "benchmark.json").read_text(encoding="utf-8"))
    manifest["columns"]["name_kanji"] = "truth_id"
    (tmp_path / "benchmark.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="hidden benchmark source"):
        CsvBenchmarkAdapter(tmp_path).load()


def test_external_benchmark_command_writes_report(tmp_path: Path, monkeypatch):
    _write_benchmark(tmp_path)
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["nayose", "--dataset-dir", str(tmp_path), "--output", str(output)],
    )

    from api.commands.benchmark_external import main

    assert main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["splits"]["test"]["top1_accuracy"] == 1.0


def test_benchmark_rejects_path_escape(tmp_path: Path):
    _write_benchmark(tmp_path)
    manifest = json.loads((tmp_path / "benchmark.json").read_text(encoding="utf-8"))
    manifest["entities_file"] = "../entities.csv"
    (tmp_path / "benchmark.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes dataset directory"):
        CsvBenchmarkAdapter(tmp_path).load()
