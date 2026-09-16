from __future__ import annotations

import json
import os
import time
from pathlib import Path

from api.infrastructure.bundles.artifact_catalog import collect_artifacts, inspect_artifacts
from api.infrastructure.runtime.storage_resources import (
    StorageDemand,
    StoragePolicy,
    StorageSnapshot,
    plan_storage,
)
from api.infrastructure.runtime.workload_storage import bundle_demand


class FixedProbe:
    def inspect(self, path: Path) -> StorageSnapshot:
        return StorageSnapshot(path.resolve(), 1, 1000, 900)


def test_storage_plan_fails_closed_after_reserve() -> None:
    try:
        plan_storage(
            workload="test",
            path=Path("."),
            workspace_root=Path("/tmp/workspaces"),
            demand=StorageDemand(output_bytes=801),
            probe=FixedProbe(),
            policy=StoragePolicy(reserve_ratio=0.1, reserve_minimum_bytes=100),
        )
    except RuntimeError as error:
        assert "insufficient storage" in str(error)
    else:
        raise AssertionError("storage admission should fail")


def test_gc_protects_current_and_uses_two_phase_tombstone(tmp_path: Path) -> None:
    data = tmp_path / "data"
    models = tmp_path / "models"
    generation = data / "generations" / "gen_old"
    current = data / "generations" / "gen_current"
    for path in (generation, current):
        path.mkdir(parents=True)
        (path / "generation_manifest.json").write_text("{}", encoding="utf-8")
        (path / "payload").write_text("x", encoding="utf-8")
    models.mkdir()
    os.symlink(current, data / "current")
    os.utime(generation, (time.time() - 100_000, time.time() - 100_000))
    os.utime(current, (time.time() - 100_000, time.time() - 100_000))

    artifacts = inspect_artifacts(data, models, older_than_days=0)
    old = next(item for item in artifacts if item.identifier == "gen_old")
    active = next(item for item in artifacts if item.identifier == "gen_current")
    assert old.protected_by == ()
    assert active.protected_by == ("current",)


def test_gc_marks_then_deletes_unprotected_artifacts(tmp_path: Path) -> None:
    data = tmp_path / "data"
    models = tmp_path / "models"
    artifact = data / "generations" / "gen_old"
    artifact.mkdir(parents=True)
    (artifact / "generation_manifest.json").write_text(json.dumps({}), encoding="utf-8")
    os.utime(artifact, (time.time() - 100_000, time.time() - 100_000))

    first = inspect_artifacts(data, models, older_than_days=0)
    collect_artifacts(first)
    assert (artifact / ".tombstone.json").is_file()
    second = inspect_artifacts(data, models, older_than_days=0)
    collect_artifacts(second)
    assert not artifact.exists()


def test_bundle_demands_are_nonzero_and_named() -> None:
    estimate = bundle_demand("materialization")
    assert estimate.workload == "materialization"
    assert estimate.demand.required_bytes > 0
