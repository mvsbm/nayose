from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import cast

import pytest

from api.infrastructure.runtime.resource_manager import (
    AcceleratorSnapshot,
    ComputePlan,
    MemorySnapshot,
    ResourceLease,
    ResourceManager,
    ResourceSnapshot,
    StorageDemand,
    StoragePolicy,
)
from api.infrastructure.runtime.storage_resources import StorageSnapshot
from nayose.config import PerformanceConfig


class Probe:
    def __init__(self, snapshot: ResourceSnapshot) -> None:
        self.snapshot = snapshot

    def inspect(self) -> ResourceSnapshot:
        return self.snapshot


class Storage:
    def __init__(self, available: int, total: int = 100 << 30) -> None:
        self.available = available
        self.total = total

    def inspect(self, path: Path) -> StorageSnapshot:
        return StorageSnapshot(path, 1, self.total, self.available)


class Processes:
    def __init__(self, starts: dict[int, float | None]) -> None:
        self.starts = starts
        self.terminated: list[int] = []

    def start_time(self, pid: int) -> float | None:
        return self.starts.get(pid)

    def is_alive(self, pid: int) -> bool:
        return pid in self.starts

    def terminate_tree(self, pid: int) -> None:
        self.terminated.append(pid)


def snapshot(*accelerators: str) -> ResourceSnapshot:
    return ResourceSnapshot(
        "Linux",
        "x86_64",
        16,
        8,
        8.0,
        MemorySnapshot(32 << 30, 16 << 30, 1 << 30, None, None),
        tuple(
            AcceleratorSnapshot(kind, "test", kind, True, 8 << 30, 7 << 30) for kind in accelerators
        ),
    )


def test_resource_state_dir_follows_models_run_layout(tmp_path: Path) -> None:
    from api.infrastructure.runtime.resource_manager import resource_state_dir

    run = tmp_path / "models" / "runs" / "run_a"

    assert resource_state_dir(run) == tmp_path / ".nayose-runtime"
    assert resource_state_dir(tmp_path / "models") == tmp_path / ".nayose-runtime"


def test_auto_plan_selects_available_framework_and_partitions_cpu() -> None:
    manager = ResourceManager(
        Path("/tmp/nayose-resource-test"),
        probe=Probe(snapshot("cuda", "mps")),
    )

    plan = manager.plan(
        workload="training",
        requested_device="auto",
        settings=PerformanceConfig(process_workers=2, cpu_threads=8, model_threads=4),
    )

    assert plan.device == "cuda"
    assert plan.accelerator == "cuda"
    assert plan.framework == "test"
    assert plan.process_workers == 2
    assert plan.cpu_threads == 4
    assert plan.model_threads == 4
    assert plan.memory_budget_bytes > 0


def test_mlx_cpu_backend_is_selected_as_a_framework_strategy() -> None:
    manager = ResourceManager(Path("/tmp/nayose-resource-test"), probe=Probe(snapshot("mlx_cpu")))

    plan = manager.plan(workload="training", requested_device="cpu", backend="mlx_cpu")

    assert plan.device == "cpu"
    assert plan.accelerator == "mlx_cpu"
    assert plan.framework == "test"


def test_non_neural_manifest_uses_cpu_resource_plan(tmp_path: Path) -> None:
    from api.infrastructure.runtime.engine_config import EngineSettings
    from api.infrastructure.runtime.resource_manager import configure_engine_resources
    from nayose.config import load_config

    settings = EngineSettings(
        model_dir=tmp_path,
        data_dir=tmp_path,
        config_snapshot=load_config({}),
    )

    plan = configure_engine_resources(settings, {"backends": {"neural_enabled": False}})

    assert plan.device == "cpu"
    assert plan.accelerator == "cpu"


def test_native_thread_cap_is_reflected_in_compute_plan() -> None:
    manager = ResourceManager(
        Path("/tmp/nayose-resource-test"),
        probe=Probe(snapshot()),
    )

    plan = manager.plan(
        workload="training",
        requested_device="cpu",
        settings=PerformanceConfig(cpu_threads=8, model_threads=8, native_thread_cap=2),
    )

    assert plan.cpu_threads == 2
    assert plan.model_threads == 2
    assert plan.env()["OMP_NUM_THREADS"] == "2"


def test_locked_mps_plan_never_falls_back() -> None:
    manager = ResourceManager(Path("/tmp/nayose-resource-test"), probe=Probe(snapshot("cuda")))

    with pytest.raises(RuntimeError, match="MPS"):
        manager.plan(workload="training", locked_mps=True)


def test_explicit_missing_accelerator_fails_fast() -> None:
    manager = ResourceManager(Path("/tmp/nayose-resource-test"), probe=Probe(snapshot("mps")))

    with pytest.raises(RuntimeError, match="cuda"):
        manager.plan(workload="serving", requested_device="cuda")


def test_storage_plan_reserves_capacity_and_rejects_oversubscription(tmp_path: Path) -> None:
    manager = ResourceManager(
        tmp_path / "state",
        probe=Probe(snapshot()),
        storage_probe=Storage(30 << 30),
        storage_policy=StoragePolicy(reserve_minimum_bytes=20 << 30),
    )

    plan = manager.plan(
        workload="e2e",
        requested_device="cpu",
        storage_demand=StorageDemand(scratch_bytes=9 << 30),
        storage_path=tmp_path,
    )

    assert plan.storage is not None
    assert plan.storage.usable_bytes == 10 << 30
    with pytest.raises(RuntimeError, match="insufficient storage"):
        manager.plan(
            workload="e2e",
            requested_device="cpu",
            storage_demand=StorageDemand(scratch_bytes=11 << 30),
            storage_path=tmp_path,
        )


def test_resource_session_owns_and_removes_workspace(tmp_path: Path) -> None:
    manager = ResourceManager(
        tmp_path / "state",
        probe=Probe(snapshot()),
        storage_probe=Storage(40 << 30),
        storage_policy=StoragePolicy(reserve_minimum_bytes=20 << 30),
    )
    plan = manager.plan(
        workload="test-workspace",
        requested_device="cpu",
        storage_demand=StorageDemand(scratch_bytes=1 << 20),
        storage_path=tmp_path,
    )

    with cast(ResourceLease, manager.session(plan)) as lease:
        workspace = lease.workspace
        assert workspace.is_dir()
        assert (workspace / ".lease.json").is_file()
        (workspace / "result.txt").write_text("owned", encoding="utf-8")

    assert not workspace.exists()


def test_compute_session_is_exclusive(tmp_path: Path) -> None:
    plan = ComputePlan("training", "cpu", "cpu", "stdlib", 1, 1, 1, 1, 1, ())
    manager = ResourceManager(tmp_path, probe=Probe(snapshot()))
    contender = ResourceManager(tmp_path, probe=Probe(snapshot()))

    with (
        manager.session(plan),
        pytest.raises(RuntimeError, match="another Nayose compute session"),
        contender.session(plan),
    ):
        pass


def test_session_terminates_registered_process_on_failure(tmp_path: Path) -> None:
    processes = Processes({4321: 9.0})
    manager = ResourceManager(tmp_path, probe=Probe(snapshot()), process_controller=processes)
    plan = ComputePlan("training", "cpu", "cpu", "stdlib", 1, 1, 1, 1, 1, ())

    with pytest.raises(RuntimeError, match="stop"), manager.session(plan) as session:
        session.register_process(4321)
        raise RuntimeError("stop")

    assert processes.terminated == [4321]


def test_session_reaps_only_owned_stale_processes(tmp_path: Path) -> None:
    processes = Processes({1234: 42.0, os.getpid(): 7.0})
    manager = ResourceManager(tmp_path, probe=Probe(snapshot()), process_controller=processes)
    plan = ComputePlan("training", "cpu", "cpu", "stdlib", 2, 2, 1, 1, 1, ())
    tmp_path.mkdir(exist_ok=True)
    stale = tmp_path / "session-old.json"
    stale.write_text(
        json.dumps(
            {
                "uid": getattr(os, "getuid", lambda: -1)(),
                "heartbeat": time.time() - 300,
                "processes": [{"pid": 1234, "start_time": 42.0}],
            }
        ),
        encoding="utf-8",
    )

    with manager.session(plan):
        assert processes.terminated == [1234]
        assert (tmp_path / "compute.lock").exists()
    assert not list(tmp_path.glob("session-*.json"))
