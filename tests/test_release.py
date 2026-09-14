from __future__ import annotations

import json
import os
from argparse import Namespace

import pytest

from api.operational_store import OperationalStore
from api.query_context import BundleReader
from api.release import (
    ReleaseCatalog,
    ReleaseError,
    ReleasePublisher,
    SnapshotResolver,
    atomic_directory_alias,
    data_generation_for_fingerprint,
    release_status,
    safe_run_id,
    validate_model_for_data,
)


def manifest(path, filename, fingerprint):
    (path / filename).write_text(json.dumps({"data_fingerprint": fingerprint}), encoding="utf-8")


def test_release_catalog_publishes_one_fingerprint_bound_snapshot(tmp_path):
    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    generation = data_root / "generations" / "gen_a"
    run = models_root / "runs" / "run_a"
    generation.mkdir(parents=True)
    run.mkdir(parents=True)
    manifest(generation, "generation_manifest.json", "same")
    manifest(run, "run_manifest.json", "same")
    (generation / "reference_records.parquet").touch()

    catalog = ReleaseCatalog(data_root, models_root)
    snapshot = ReleasePublisher(catalog).publish("gen_a", "run_a")

    assert snapshot.data_generation_id == "gen_a"
    assert snapshot.model_run_id == "run_a"
    assert snapshot.data_fingerprint == "same"
    assert catalog.current() == snapshot
    assert (data_root / "release.json").is_file()


def test_release_publication_records_historical_metadata(tmp_path):
    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    generation = data_root / "generations" / "gen_a"
    run = models_root / "runs" / "run_a"
    generation.mkdir(parents=True)
    run.mkdir(parents=True)
    manifest(generation, "generation_manifest.json", "same")
    manifest(run, "run_manifest.json", "same")
    (generation / "reference_records.parquet").touch()
    store = OperationalStore(data_root / "operational.sqlite3")

    snapshot = ReleasePublisher(ReleaseCatalog(data_root, models_root), store).publish(
        "gen_a", "run_a"
    )

    with store.read_connection() as connection:
        row = connection.execute(
            "SELECT release_id, data_generation_id, model_run_id FROM release_history"
        ).fetchone()
    assert row == (snapshot.release_id, "gen_a", "run_a")


def test_snapshot_resolution_is_request_stable_and_historical(tmp_path):
    from api.repositories import RunRepository
    from api.request_context import clear_snapshot_context

    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    for generation_id, run_id in (("gen_a", "run_a"), ("gen_b", "run_b")):
        generation = data_root / "generations" / generation_id
        run = models_root / "runs" / run_id
        generation.mkdir(parents=True)
        run.mkdir(parents=True)
        manifest(generation, "generation_manifest.json", "same")
        manifest(run, "run_manifest.json", "same")
        (generation / "reference_records.parquet").touch()

    catalog = ReleaseCatalog(data_root, models_root)
    store = OperationalStore(data_root / "operational.sqlite3")
    publisher = ReleasePublisher(catalog, store)
    publisher.publish("gen_a", "run_a")
    reader = BundleReader(RunRepository(models_root, catalog), data_root, store)

    try:
        first = reader.snapshot("")
        publisher.publish("gen_b", "run_b")
        assert reader.snapshot("") == first
        clear_snapshot_context()
        assert reader.snapshot("").run_id == "run_b"
        historical = SnapshotResolver(catalog, store).resolve("run_a")
        assert historical.model_run_id == "run_a"
        assert historical.data_generation_id == "gen_a"
    finally:
        clear_snapshot_context()


def test_release_catalog_fails_closed_on_invalid_pointer(tmp_path):
    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    data_root.mkdir()
    (data_root / "release.json").write_text('{"release_id":"broken"}', encoding="utf-8")

    with pytest.raises(ReleaseError):
        ReleaseCatalog(data_root, models_root).current()


def test_release_catalog_preserves_previous_pointer_when_publish_fails(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    old_generation = data_root / "generations" / "gen_old"
    old_run = models_root / "runs" / "run_old"
    new_generation = data_root / "generations" / "gen_new"
    new_run = models_root / "runs" / "run_new"
    for path in (old_generation, old_run, new_generation, new_run):
        path.mkdir(parents=True)
    manifest(old_generation, "generation_manifest.json", "old")
    manifest(old_run, "run_manifest.json", "old")
    manifest(new_generation, "generation_manifest.json", "new")
    manifest(new_run, "run_manifest.json", "new")
    (old_generation / "reference_records.parquet").touch()
    (new_generation / "reference_records.parquet").touch()
    catalog = ReleaseCatalog(data_root, models_root)
    publisher = ReleasePublisher(catalog)
    publisher.publish("gen_old", "run_old")

    original_replace = os.replace

    def fail_replace(source, target):
        if str(target).endswith("release.json"):
            raise OSError("simulated crash")
        original_replace(source, target)

    monkeypatch.setattr("api.release.os.replace", fail_replace)
    with pytest.raises(OSError):
        publisher.publish("gen_new", "run_new")

    snapshot = catalog.current()
    assert snapshot is not None
    assert snapshot.data_generation_id == "gen_old"


def test_release_catalog_is_authoritative_over_legacy_aliases(tmp_path):
    from api.query_context import BundleReader
    from api.repositories import RunRepository

    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    old_generation = data_root / "generations" / "gen_old"
    old_run = models_root / "runs" / "run_old"
    new_generation = data_root / "generations" / "gen_new"
    new_run = models_root / "runs" / "run_new"
    for path in (old_generation, old_run, new_generation, new_run):
        path.mkdir(parents=True)
    manifest(old_generation, "generation_manifest.json", "old")
    manifest(old_run, "run_manifest.json", "old")
    manifest(new_generation, "generation_manifest.json", "new")
    manifest(new_run, "run_manifest.json", "new")
    (old_generation / "reference_records.parquet").touch()
    (new_generation / "reference_records.parquet").touch()
    catalog = ReleaseCatalog(data_root, models_root)
    ReleasePublisher(catalog).publish("gen_new", "run_new")
    atomic_directory_alias(old_generation, data_root / "current")
    atomic_directory_alias(old_run, models_root / "current")
    runs = RunRepository(models_root, catalog)

    assert runs.resolve("") == new_run
    snapshot = BundleReader(runs, data_root / "current").snapshot("")
    assert snapshot.run_id == "run_new"
    assert snapshot.data_dir == new_generation


def test_release_status_requires_matching_current_aliases(tmp_path):
    data_root = tmp_path / "data"
    models_root = tmp_path / "models"
    generation = data_root / "generations" / "gen_a"
    run = models_root / "runs" / "run_a"
    generation.mkdir(parents=True)
    run.mkdir(parents=True)
    manifest(generation, "generation_manifest.json", "same")
    manifest(run, "run_manifest.json", "same")
    (generation / "reference_records.parquet").touch()
    ReleasePublisher(ReleaseCatalog(data_root, models_root)).publish("gen_a", "run_a")

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


def test_promote_explicit_generation_does_not_use_current_alias(tmp_path):
    data_root = tmp_path / "data"
    generation_a = data_root / "generations" / "gen_a"
    generation_b = data_root / "generations" / "gen_b"
    generation_a.mkdir(parents=True)
    generation_b.mkdir(parents=True)
    manifest(generation_a, "generation_manifest.json", "data-a")
    manifest(generation_b, "generation_manifest.json", "data-b")
    (generation_a / "reference_records.parquet").touch()
    (generation_b / "reference_records.parquet").touch()
    atomic_directory_alias(generation_b, data_root / "current")

    models_root = tmp_path / "models"
    run = models_root / "runs" / "run_a"
    run.mkdir(parents=True)
    manifest(run, "run_manifest.json", "data-a")
    (run / "block_indexes.npz").touch()
    from api.commands.lifecycle import REQUIRED_BUNDLE_TABLES

    for table in REQUIRED_BUNDLE_TABLES:
        (run / f"{table}.parquet").touch()

    from api.commands.lifecycle import promote_bundle

    promote_bundle(
        Namespace(
            data_dir=str(generation_a),
            models_dir=str(models_root),
        ),
        run,
    )

    release = json.loads((data_root / "release.json").read_text(encoding="utf-8"))
    assert release["data_generation_id"] == "gen_a"
    assert (data_root / "current").resolve() == generation_a


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
