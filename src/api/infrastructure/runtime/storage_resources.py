"""Storage capacity planning and owned ephemeral workspaces."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

from nayose.config_views import STORAGE_DEFAULTS, StorageConfig

_WORKSPACE_HEARTBEAT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class StorageSnapshot:
    path: Path
    device_id: int
    total_bytes: int
    available_bytes: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "path": str(self.path),
            "device_id": self.device_id,
            "total_bytes": self.total_bytes,
            "available_bytes": self.available_bytes,
        }


@dataclass(frozen=True, slots=True)
class StorageDemand:
    scratch_bytes: int = 0
    output_bytes: int = 0
    cache_bytes: int = 0

    def __post_init__(self) -> None:
        if min(self.scratch_bytes, self.output_bytes, self.cache_bytes) < 0:
            raise ValueError("storage demand values must not be negative")

    @property
    def required_bytes(self) -> int:
        return self.scratch_bytes + self.output_bytes + self.cache_bytes


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    reserve_ratio: float = float(STORAGE_DEFAULTS["reserve_ratio"])
    reserve_minimum_bytes: int = int(STORAGE_DEFAULTS["reserve_minimum_bytes"])
    workspace_stale_seconds: float = float(STORAGE_DEFAULTS["workspace_stale_seconds"])

    @classmethod
    def from_config(cls, config: StorageConfig) -> StoragePolicy:
        return cls(
            reserve_ratio=float(config.reserve_ratio),
            reserve_minimum_bytes=int(config.reserve_minimum_bytes),
            workspace_stale_seconds=float(config.workspace_stale_seconds),
        )

    def __post_init__(self) -> None:
        if not 0 <= self.reserve_ratio < 1:
            raise ValueError("storage reserve_ratio must be in [0, 1)")
        if self.reserve_minimum_bytes < 0:
            raise ValueError("storage reserve_minimum_bytes must not be negative")
        if self.workspace_stale_seconds < 1:
            raise ValueError("storage workspace_stale_seconds must be positive")


@dataclass(frozen=True, slots=True)
class StoragePlan:
    workload: str
    snapshot: StorageSnapshot
    demand: StorageDemand
    reserve_bytes: int
    usable_bytes: int
    workspace_root: Path
    stale_seconds: float = 120.0

    def as_dict(self) -> dict[str, object]:
        return {
            "workload": self.workload,
            "volume": self.snapshot.as_dict(),
            "demand": {
                "scratch_bytes": self.demand.scratch_bytes,
                "output_bytes": self.demand.output_bytes,
                "cache_bytes": self.demand.cache_bytes,
                "required_bytes": self.demand.required_bytes,
            },
            "reserve_bytes": self.reserve_bytes,
            "usable_bytes": self.usable_bytes,
            "workspace_root": str(self.workspace_root),
            "stale_seconds": self.stale_seconds,
        }


class StorageProbe(Protocol):
    def inspect(self, path: Path) -> StorageSnapshot: ...


class ProcessIdentityProbe(Protocol):
    def start_time(self, pid: int) -> float | None: ...


class HostStorageProbe:
    def inspect(self, path: Path) -> StorageSnapshot:
        resolved = _existing_path(path.resolve())
        usage = shutil.disk_usage(resolved)
        return StorageSnapshot(
            path=resolved,
            device_id=resolved.stat().st_dev,
            total_bytes=usage.total,
            available_bytes=usage.free,
        )


def plan_storage(
    *,
    workload: str,
    path: Path,
    workspace_root: Path,
    demand: StorageDemand,
    probe: StorageProbe,
    policy: StoragePolicy,
) -> StoragePlan:
    snapshot = probe.inspect(path)
    reserve = max(policy.reserve_minimum_bytes, int(snapshot.total_bytes * policy.reserve_ratio))
    usable = max(0, snapshot.available_bytes - reserve)
    if demand.required_bytes > usable:
        raise RuntimeError(
            f"insufficient storage for {workload}: {snapshot.available_bytes} bytes available, "
            f"{reserve} bytes reserved, {demand.required_bytes} bytes required"
        )
    return StoragePlan(
        workload,
        snapshot,
        demand,
        reserve,
        usable,
        workspace_root.resolve(),
        policy.workspace_stale_seconds,
    )


class WorkspaceLease:
    """Own one bounded directory and reclaim dead leases under the same root."""

    def __init__(self, plan: StoragePlan, process_controller: ProcessIdentityProbe) -> None:
        self.plan = plan
        self.process_controller = process_controller
        self.token = uuid4().hex
        self.path = plan.workspace_root / f"{_safe_name(plan.workload)}-{self.token}"
        self.marker = self.path / ".lease.json"
        self._stop = Event()
        self._heartbeat: Thread | None = None
        self._owner_start: float | None = None

    def __enter__(self) -> WorkspaceLease:
        self.plan.workspace_root.mkdir(parents=True, exist_ok=True)
        self.plan.workspace_root.chmod(0o700)
        self.reap_stale()
        self.path.mkdir(mode=0o700)
        self._owner_start = self.process_controller.start_time(os.getpid())
        self._write()
        self._heartbeat = Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=2.0)
        shutil.rmtree(self.path, ignore_errors=True)

    def reap_stale(self) -> None:
        root = self.plan.workspace_root
        if not root.exists():
            return
        now = time.time()
        for marker in root.glob("*/.lease.json"):
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                if now - float(payload["heartbeat"]) < self.plan.stale_seconds:
                    continue
                pid = int(payload["pid"])
                expected = payload.get("start_time")
                actual = self.process_controller.start_time(pid)
                if actual is not None and expected is not None and actual == expected:
                    continue
                candidate = marker.parent.resolve()
                owns_candidate = payload.get("token") == candidate.name.rsplit("-", 1)[-1]
                if candidate.parent != root.resolve() or not owns_candidate:
                    continue
                shutil.rmtree(candidate, ignore_errors=True)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_WORKSPACE_HEARTBEAT_SECONDS):
            self._write()

    def _write(self) -> None:
        payload = {
            "token": self.token,
            "pid": os.getpid(),
            "start_time": self._owner_start,
            "heartbeat": time.time(),
            "workload": self.plan.workload,
            "budget_bytes": self.plan.demand.required_bytes,
        }
        temporary = self.marker.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.marker)


def _existing_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists():
        raise FileNotFoundError(path)
    return candidate


def _safe_name(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    return normalized.strip("-") or "workspace"
