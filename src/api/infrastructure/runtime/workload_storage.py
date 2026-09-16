"""Conservative disk-demand estimates for bounded local workloads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.infrastructure.runtime.storage_resources import StorageDemand

_GIB = 1 << 30


@dataclass(frozen=True, slots=True)
class WorkloadEstimate:
    """Named estimate kept separate from admission and filesystem policy."""

    workload: str
    demand: StorageDemand
    basis: str


def generation_demand(config: Any, *, clean_only: bool = False) -> WorkloadEstimate:
    parties = max(1, int(getattr(config, "n_parties", 100_000)))
    bytes_per_party = 8 * 1024 if clean_only else 32 * 1024
    output = max(512 * _GIB // 1024, parties * bytes_per_party)
    return WorkloadEstimate(
        "clean-generation" if clean_only else "generation",
        StorageDemand(scratch_bytes=1 * _GIB, output_bytes=output),
        f"{parties} parties at {bytes_per_party} bytes per party",
    )


def training_demand(data_dir: Path) -> WorkloadEstimate:
    input_bytes = _directory_bytes(data_dir.resolve())
    scratch = max(4 * _GIB, input_bytes // 2)
    output = max(8 * _GIB, input_bytes // 2)
    return WorkloadEstimate(
        "training",
        StorageDemand(scratch_bytes=scratch, output_bytes=output, cache_bytes=2 * _GIB),
        f"input directory size {input_bytes} bytes",
    )


def bundle_demand(workload: str) -> WorkloadEstimate:
    if workload == "materialization":
        demand = StorageDemand(scratch_bytes=2 * _GIB, output_bytes=8 * _GIB)
    elif workload == "retrieval-evaluation":
        demand = StorageDemand(scratch_bytes=2 * _GIB, cache_bytes=1 * _GIB)
    elif workload == "benchmark":
        demand = StorageDemand(scratch_bytes=512 * 1024 * 1024)
    else:
        demand = StorageDemand()
    return WorkloadEstimate(workload, demand, "fixed workload safety envelope")


def _directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
