from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from api.release import validate_artifact_checksums
from api.training.config import TrainingConfig
from api.training.pair_training import threshold_for_precision


def test_training_config_normalizes_locked_family_alias() -> None:
    config = TrainingConfig.from_env(
        {
            "NAYOSE_MODEL_FAMILY": "f2llm_330m",
            "NAYOSE_DEVICE": "mps",
            "NAYOSE_NEURAL_BATCH": "32",
            "NAYOSE_USE_DENSE": "1",
            "NAYOSE_ALLOW_SVD_FALLBACK": "0",
        }
    )

    assert config.model_family == "f2llm"
    assert config.torch_dtype == "float16"


def test_threshold_for_precision_abstains_in_conservative_profile() -> None:
    threshold, curve, status = threshold_for_precision(
        scores=np.array([0.9, 0.8]),
        labels=np.array([1, 0]),
        target=0.99,
        minimum_predictions=1,
        decision_profile="conservative",
    )

    assert threshold > 1.0
    assert status == "TARGET_NOT_MET_ABSTAIN"
    assert len(curve) == 2


def test_modular_artifact_checksums_fail_closed(tmp_path) -> None:
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"immutable")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = {
        "training_algorithm_version": "modular-v1",
        "artifacts": [{"path": artifact.name, "sha256": digest}],
    }
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    validate_artifact_checksums(tmp_path)
    artifact.write_bytes(b"changed")

    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_artifact_checksums(tmp_path)


def test_modular_artifact_metadata_requires_a_checksum(tmp_path) -> None:
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"immutable")
    manifest = {
        "training_algorithm_version": "modular-v1",
        "artifacts": [{"path": artifact.name}],
    }
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="valid checksum"):
        validate_artifact_checksums(tmp_path)
