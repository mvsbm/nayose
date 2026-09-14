import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from api.commands.lifecycle import REQUIRED_BUNDLE_TABLES, promote_bundle, verify_bundle
from api.embedding_config import (
    F2LLM_330M_MODEL_ID,
    embedding_model_id,
    pipeline_model_family,
)
from api.model_gate import evaluate_f2llm_candidate


def _args(data_root: Path, models_root: Path) -> Namespace:
    return Namespace(
        data_dir=str(data_root),
        models_dir=str(models_root),
        prune_aborted=True,
    )


def test_f2llm_330m_performance_gate_requires_material_gain():
    baseline = {
        "data_fingerprint": "same",
        "config": {"model_family": "off"},
        "query_metrics": {
            "candidate_recall_at_1_existing": 0.80,
            "candidate_recall_at_5_existing": 0.90,
            "auto_link_precision_pure": 0.98,
            "false_auto_link_rate_new_or_related": 0.0,
        },
    }
    candidate: dict[str, Any] = {
        "data_fingerprint": "same",
        "config": {"model_family": "f2llm"},
        "query_metrics": {
            "candidate_recall_at_1_existing": 0.82,
            "candidate_recall_at_5_existing": 0.92,
            "auto_link_precision_pure": 0.981,
            "false_auto_link_rate_new_or_related": 0.0,
        },
    }

    def results(hard_errors: int) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for index in range(100):
            hard = index < 50
            correct = not hard or index >= hard_errors
            rows.append(
                {
                    "scenario": "existing_changed" if hard else "existing_stable",
                    "identity_top1_correct": correct,
                    "correct_entity_rank": 1 if correct else 6,
                    "action": "AUTO_LINK" if correct else "REVIEW",
                    "pure_top1_correct": correct,
                }
            )
        return rows

    decision = evaluate_f2llm_candidate(
        candidate,
        baseline,
        candidate_results=results(0),
        baseline_results=results(2),
    )
    assert decision.passed and decision.status == "PASSED"

    candidate["query_metrics"]["candidate_recall_at_1_existing"] = 0.805
    assert not evaluate_f2llm_candidate(
        candidate,
        baseline,
        candidate_results=results(0),
        baseline_results=results(2),
    ).passed


def test_f2llm_330m_is_an_explicit_pipeline_profile():
    assert embedding_model_id("f2llm_330m") == F2LLM_330M_MODEL_ID
    assert pipeline_model_family("f2llm_330m") == "f2llm"
    assert embedding_model_id("f2llm", "custom/model") == "custom/model"


def test_verify_ignores_aborted_newer_run_and_promotes_verified_bundle(tmp_path):
    data_root = tmp_path / "data"
    generation = data_root / "generations" / "gen_a"
    generation.mkdir(parents=True)
    fingerprint = "a" * 64
    (generation / "generation_manifest.json").write_text(
        json.dumps({"data_fingerprint": fingerprint}), encoding="utf-8"
    )
    (generation / "reference_records.parquet").touch()
    (data_root / "current").symlink_to(Path("generations/gen_a"), target_is_directory=True)

    models_root = tmp_path / "models"
    run = models_root / "runs" / "run_a"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "data_fingerprint": fingerprint,
                "created_at_utc": "2026-01-01T00:00:00Z",
                "config_hash": "config",
                "config": {"model_family": "off"},
                "device": "cpu",
                "backends": {},
            }
        ),
        encoding="utf-8",
    )
    for table in REQUIRED_BUNDLE_TABLES:
        (run / f"{table}.parquet").touch()
    (run / "block_indexes.npz").touch()
    aborted = models_root / "runs" / "run_aborted"
    aborted.mkdir(parents=True)

    args = _args(data_root, models_root)
    verified = verify_bundle(args)
    assert verified == run
    promote_bundle(args, verified)

    assert (models_root / "current").resolve() == run
    release = json.loads((data_root / "release.json").read_text(encoding="utf-8"))
    assert release["model_run_id"] == "run_a"
    assert release["data_generation_id"] == "gen_a"
    registry = json.loads((models_root / "registry.json").read_text(encoding="utf-8"))
    assert registry[-1]["run_id"] == "run_a"
    assert registry[-1]["data_fingerprint"] == fingerprint
    assert not aborted.exists()
