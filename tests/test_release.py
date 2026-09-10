from __future__ import annotations

import json

import pytest

from api.release import (
    atomic_directory_alias,
    data_generation_for_fingerprint,
    release_status,
    safe_run_id,
    validate_model_for_data,
)


def manifest(path, filename, fingerprint):
    (path / filename).write_text(json.dumps({"data_fingerprint": fingerprint}), encoding="utf-8")


def test_release_status_requires_matching_current_aliases(tmp_path):
    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    generation = data_root / "generations" / "gen_a"
    run = models_root / "runs" / "run_a"
    generation.mkdir(parents=True)
    run.mkdir(parents=True)
    manifest(generation, "generation_manifest.json", "same")
    manifest(run, "run_manifest.json", "same")
    atomic_directory_alias(generation, data_root / "current")
    atomic_directory_alias(run, models_root / "current")

    status = release_status(data_root, models_root)

    assert status.compatible
    assert status.data_target == "gen_a"
    assert status.model_target == "run_a"


def test_model_promotion_rejects_missing_data_alias(tmp_path):
    data_root = tmp_path / "data"
    run = tmp_path / "run_a"
    run.mkdir(parents=True)
    manifest(run, "run_manifest.json", "data-a")

    with pytest.raises(ValueError, match="current data manifest"):
        validate_model_for_data(run, data_root)


def test_model_promotion_accepts_an_explicit_generation_directory(tmp_path):
    generation = tmp_path / "gen_a"
    run = tmp_path / "run_a"
    generation.mkdir()
    run.mkdir()
    manifest(generation, "generation_manifest.json", "data-a")
    manifest(run, "run_manifest.json", "data-a")

    assert validate_model_for_data(run, generation) == "data-a"


def test_model_promotion_rejects_stale_data(tmp_path):
    data_root = tmp_path / "data"
    generation = data_root / "generations" / "gen_a"
    run = tmp_path / "run_a"
    generation.mkdir(parents=True)
    run.mkdir(parents=True)
    manifest(generation, "generation_manifest.json", "data-a")
    manifest(run, "run_manifest.json", "data-b")
    atomic_directory_alias(generation, data_root / "current")

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        validate_model_for_data(run, data_root)


def test_data_generation_lookup_is_fingerprint_bound(tmp_path):
    data_root = tmp_path / "data"
    generation = data_root / "generations" / "gen_a"
    generation.mkdir(parents=True)
    manifest(generation, "generation_manifest.json", "target")
    atomic_directory_alias(generation, data_root / "current")

    assert data_generation_for_fingerprint(data_root, "target") == generation
    assert data_generation_for_fingerprint(data_root / "current", "target") == generation
    assert data_generation_for_fingerprint(data_root, "missing") is None


def test_data_generation_lookup_accepts_direct_generation_directory(tmp_path):
    generation = tmp_path / "gen_a"
    generation.mkdir()
    manifest(generation, "generation_manifest.json", "target")

    assert data_generation_for_fingerprint(generation, "target") == generation
    assert data_generation_for_fingerprint(generation, "missing") is None


def test_run_id_rejects_path_traversal():
    assert safe_run_id("run_abc-123") == "run_abc-123"
    with pytest.raises(ValueError):
        safe_run_id("../../outside")
