"""Portable resource inspection, compute planning, and stale-session cleanup."""

from __future__ import annotations

import importlib
import json
import os
import platform
import re
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from api.infrastructure.runtime.performance import (
    _cgroup_cpu_quota,
    available_cpu_count,
    configure_mps_runtime,
    configure_native_threads,
    configure_torch_threads,
    native_thread_environment,
)
from api.infrastructure.runtime.resource_lease import (
    ComputeLease,
    LocalProcessController,
    ProcessController,
    ResourceLease,
)
from api.infrastructure.runtime.storage_resources import (
    HostStorageProbe,
    StorageDemand,
    StoragePlan,
    StoragePolicy,
    StorageProbe,
    WorkspaceLease,
    plan_storage,
)
from nayose.config import PerformanceConfig, is_f2llm_model, load_config

resource_module: Any = None
with suppress(ImportError):
    resource_module = importlib.import_module("resource")
psutil_module: Any = None
with suppress(ImportError):
    psutil_module = importlib.import_module("psutil")

_MEMORY_RESERVE_RATIO = 0.10
_MEMORY_RESERVE_MINIMUM = 1 << 30


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    total_bytes: int
    available_bytes: int
    process_rss_bytes: int
    cgroup_limit_bytes: int | None
    cgroup_available_bytes: int | None


@dataclass(frozen=True, slots=True)
class AcceleratorSnapshot:
    kind: str
    framework: str
    name: str
    available: bool
    total_memory_bytes: int | None
    available_memory_bytes: int | None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTelemetry:
    cpu_usage_percent: float | None
    cpu_temperature_celsius: float | None
    gpu_usage_percent: float | None
    gpu_temperature_celsius: float | None
    gpu_memory_total_bytes: int | None
    gpu_memory_available_bytes: int | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "cpu_usage_percent": self.cpu_usage_percent,
            "cpu_temperature_celsius": self.cpu_temperature_celsius,
            "gpu_usage_percent": self.gpu_usage_percent,
            "gpu_temperature_celsius": self.gpu_temperature_celsius,
            "gpu_memory_total_bytes": self.gpu_memory_total_bytes,
            "gpu_memory_available_bytes": self.gpu_memory_available_bytes,
        }


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    operating_system: str
    machine: str
    cpu_count: int
    available_cpus: int
    cpu_quota_cpus: float | None
    memory: MemorySnapshot
    accelerators: tuple[AcceleratorSnapshot, ...]
    telemetry: RuntimeTelemetry | None = None

    def accelerator(self, kind: str) -> AcceleratorSnapshot | None:
        return next((item for item in self.accelerators if item.kind == kind), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operating_system": self.operating_system,
            "machine": self.machine,
            "cpu_count": self.cpu_count,
            "available_cpus": self.available_cpus,
            "cpu_quota_cpus": self.cpu_quota_cpus,
            "memory": {
                "total_bytes": self.memory.total_bytes,
                "available_bytes": self.memory.available_bytes,
                "process_rss_bytes": self.memory.process_rss_bytes,
                "cgroup_limit_bytes": self.memory.cgroup_limit_bytes,
                "cgroup_available_bytes": self.memory.cgroup_available_bytes,
            },
            "accelerators": [
                {
                    "kind": item.kind,
                    "framework": item.framework,
                    "name": item.name,
                    "available": item.available,
                    "total_memory_bytes": item.total_memory_bytes,
                    "available_memory_bytes": item.available_memory_bytes,
                    "version": item.version,
                }
                for item in self.accelerators
            ],
            "telemetry": self.telemetry.as_dict() if self.telemetry else None,
        }


@dataclass(frozen=True, slots=True)
class ComputePlan:
    workload: str
    device: str
    accelerator: str
    framework: str
    cpu_threads: int
    model_threads: int
    process_workers: int
    available_memory_bytes: int
    memory_budget_bytes: int
    environment: tuple[tuple[str, str], ...]
    storage: StoragePlan | None = None

    def env(self) -> dict[str, str]:
        return dict(self.environment)


class ResourceProbe(Protocol):
    def inspect(self) -> ResourceSnapshot: ...


class HostResourceProbe:
    """Inspect host, cgroup, and installed accelerator frameworks lazily."""

    def inspect(self) -> ResourceSnapshot:
        total, available, limit, cgroup_available = _memory()
        return ResourceSnapshot(
            operating_system=platform.system(),
            machine=platform.machine(),
            cpu_count=os.cpu_count() or 1,
            available_cpus=available_cpu_count(),
            cpu_quota_cpus=_cgroup_cpu_quota(),
            memory=MemorySnapshot(
                total,
                available,
                _process_rss_bytes(),
                limit,
                cgroup_available,
            ),
            accelerators=tuple(_accelerators(total, available)),
            telemetry=_runtime_telemetry(),
        )


class ResourceManager:
    """Central local compute policy and exclusive workload admission."""

    def __init__(
        self,
        state_dir: Path,
        *,
        probe: ResourceProbe | None = None,
        process_controller: ProcessController | None = None,
        storage_probe: StorageProbe | None = None,
        storage_policy: StoragePolicy | None = None,
    ) -> None:
        self.state_dir = state_dir.resolve()
        self.probe = probe or HostResourceProbe()
        self.process_controller = process_controller or LocalProcessController()
        self.storage_probe = storage_probe or HostStorageProbe()
        self.storage_policy = storage_policy or StoragePolicy.from_config(load_config().storage)

    def inspect(self) -> ResourceSnapshot:
        return self.probe.inspect()

    def inspect_storage(self, path: Path) -> dict[str, int | str]:
        return self.storage_probe.inspect(path).as_dict()

    def plan(
        self,
        *,
        workload: str,
        requested_device: str = "auto",
        backend: str | None = None,
        locked_mps: bool = False,
        settings: PerformanceConfig | None = None,
        require_accelerator: bool = True,
        storage_demand: StorageDemand | None = None,
        storage_path: Path | None = None,
    ) -> ComputePlan:
        snapshot = self.inspect()
        accelerator = _select_accelerator(
            snapshot,
            requested_device=requested_device,
            backend=backend,
            locked_mps=locked_mps,
            require_accelerator=require_accelerator,
        )
        configured_workers = settings.process_workers if settings else None
        workers = 1 if locked_mps else min(configured_workers or 1, snapshot.available_cpus)
        per_process = max(1, snapshot.available_cpus // workers)
        cpu_threads = (
            min(settings.cpu_threads or per_process, per_process) if settings else per_process
        )
        model_threads = (
            min(settings.model_threads or cpu_threads, cpu_threads) if settings else cpu_threads
        )
        if settings and settings.native_thread_cap is not None:
            cpu_threads = min(cpu_threads, settings.native_thread_cap)
            model_threads = min(model_threads, settings.native_thread_cap)
        reserve = max(
            _MEMORY_RESERVE_MINIMUM, int(snapshot.memory.total_bytes * _MEMORY_RESERVE_RATIO)
        )
        budget = max(0, snapshot.memory.available_bytes - reserve)
        if budget <= 0:
            raise RuntimeError(
                f"insufficient available memory for {workload}: "
                f"{snapshot.memory.available_bytes} bytes available"
            )
        storage = (
            self.plan_storage(
                workload=workload,
                path=storage_path or self.state_dir,
                demand=storage_demand,
            )
            if storage_demand is not None
            else None
        )
        env = native_thread_environment(cpu_threads, workers=workers, settings=settings)
        env.update(
            {
                "NAYOSE_CPU_THREADS": str(cpu_threads),
                "NAYOSE_MODEL_THREADS": str(model_threads),
                "NAYOSE_PROCESS_WORKERS": str(workers),
                "NAYOSE_DEVICE": accelerator[0],
            }
        )
        return ComputePlan(
            workload=workload,
            device=accelerator[0],
            accelerator=accelerator[1].kind,
            framework=accelerator[1].framework,
            cpu_threads=cpu_threads,
            model_threads=model_threads,
            process_workers=workers,
            available_memory_bytes=snapshot.memory.available_bytes,
            memory_budget_bytes=budget,
            environment=tuple(sorted(env.items())),
            storage=storage,
        )

    def plan_storage(
        self,
        *,
        workload: str,
        path: Path,
        demand: StorageDemand,
    ) -> StoragePlan:
        return plan_storage(
            workload=workload,
            path=path,
            workspace_root=self.state_dir / "workspaces",
            demand=demand,
            probe=self.storage_probe,
            policy=self.storage_policy,
        )

    def configure(self, plan: ComputePlan, settings: PerformanceConfig | None = None) -> None:
        configure_native_threads(plan.cpu_threads, workers=plan.process_workers, settings=settings)
        if plan.device in {"mps", "cuda"}:
            configure_torch_threads(plan.model_threads, settings=settings)
        if plan.device == "mps":
            configure_mps_runtime()

    def session(self, plan: ComputePlan) -> ComputeLease | ResourceLease:
        compute = ComputeLease(self.state_dir, plan, self.process_controller)
        if plan.storage is None:
            return compute
        return ResourceLease(compute, WorkspaceLease(plan.storage, self.process_controller))


def resource_state_dir(path: Path) -> Path:
    resolved = path.resolve()
    if (resolved / "pyproject.toml").is_file():
        root = resolved
    elif resolved.parent.name == "runs" and len(resolved.parents) >= 3:
        root = resolved.parents[2]
    elif resolved.name == "models":
        root = resolved.parent
    else:
        root = resolved.parent
    for parent in (root, *root.parents):
        if (parent / "pyproject.toml").is_file():
            root = parent
            break
    return root / ".nayose-runtime"


def plan_for_bundle(
    manager: ResourceManager,
    config: Any,
    run_dir: Path,
    *,
    workload: str,
    storage_demand: StorageDemand | None = None,
) -> ComputePlan:
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    model_id = str(manifest.get("embedding_model") or "")
    runtime = manifest.get("runtime_policy", {})
    requested_device = (
        runtime.get("neural_device") or config.embedding.device
        if isinstance(runtime, dict) and model_id
        else "cpu"
    )
    backend = (
        str(manifest.get("embedding_backend") or config.embedding.backend) if model_id else None
    )
    return manager.plan(
        workload=workload,
        requested_device=str(requested_device),
        backend=backend,
        locked_mps=is_f2llm_model(model_id),
        settings=config.performance,
        storage_demand=storage_demand,
    )


def configure_engine_resources(config: Any, manifest: dict[str, Any] | None = None) -> ComputePlan:
    neural_enabled: bool | None = None
    if manifest is not None:
        manifest_backends = manifest.get("backends", {})
        neural_enabled = bool(
            manifest.get("embedding_model")
            and (
                not isinstance(manifest_backends, dict)
                or manifest_backends.get("neural_enabled", True)
            )
        )
        if neural_enabled:
            manifest_runtime = manifest.get("runtime_policy", {})
            manifest_device = (
                manifest_runtime.get("neural_device")
                if isinstance(manifest_runtime, dict)
                else None
            )
            resource_embedding = replace(
                config.embedding,
                model_id=str(manifest["embedding_model"]),
                backend=str(manifest.get("embedding_backend") or config.embedding.backend),
                device=str(manifest_device or config.embedding.device),
            )
            config = replace(config, embedding=resource_embedding)
    state_dir = resource_state_dir(Path(config.model_dir))
    if neural_enabled is False:
        backend = None
        requested = "cpu"
        locked_mps = False
    else:
        backend = str(config.embedding.backend)
        requested = "auto" if backend.startswith("mlx") else str(config.embedding.device)
        locked_mps = is_f2llm_model(str(config.embedding.model_id or ""))
    manager = ResourceManager(state_dir)
    plan = manager.plan(
        workload="serving",
        requested_device=requested,
        backend=backend,
        locked_mps=locked_mps,
        settings=config.performance,
    )
    manager.configure(plan, config.performance)
    return plan


def _select_accelerator(
    snapshot: ResourceSnapshot,
    *,
    requested_device: str,
    backend: str | None,
    locked_mps: bool,
    require_accelerator: bool,
) -> tuple[str, AcceleratorSnapshot]:
    requested = requested_device.casefold()
    if requested not in {"auto", "cpu", "mps", "cuda", "rocm", "mlx_gpu", "mlx_cpu"}:
        raise ValueError(f"unsupported compute device {requested_device!r}")
    target = requested
    if target == "auto":
        target = {
            "mlx_gpu": "mlx_gpu",
            "mlx_cpu": "mlx_cpu",
        }.get(str(backend), "auto")
    if locked_mps:
        target = "mps"
    if backend == "mlx_gpu" and target not in {"auto", "mlx_gpu"}:
        raise RuntimeError("the MLX GPU backend requires device='mlx_gpu'")
    if backend == "mlx_cpu" and target in {"auto", "cpu"}:
        target = "mlx_cpu"
    elif backend == "mlx_cpu" and target != "mlx_cpu":
        raise RuntimeError("the MLX CPU backend requires device='cpu' or 'mlx_cpu'")
    if target == "auto":
        target = next(
            (
                kind
                for kind in ("cuda", "rocm", "mps", "mlx_gpu")
                if (item := snapshot.accelerator(kind)) is not None and item.available
            ),
            "cpu",
        )
    if target == "mlx_cpu":
        candidate = snapshot.accelerator("mlx_cpu")
        if candidate is None or not candidate.available:
            if require_accelerator:
                raise RuntimeError("the MLX CPU backend is unavailable")
            candidate = AcceleratorSnapshot("mlx_cpu", "unverified", "MLX CPU", False, None, None)
        return "cpu", candidate
    if target == "cpu":
        return "cpu", _cpu_accelerator(snapshot)
    candidate = snapshot.accelerator(target)
    if candidate is None or not candidate.available:
        if require_accelerator:
            if locked_mps:
                raise RuntimeError(
                    "the locked F2LLM profile requires an available Apple MPS backend"
                )
            raise RuntimeError(f"requested compute accelerator {target!r} is unavailable")
        candidate = AcceleratorSnapshot(target, "unverified", target, False, None, None)
    if locked_mps and candidate.kind != "mps":
        raise RuntimeError("the locked F2LLM profile requires Apple MPS")
    return ("cuda" if candidate.kind == "rocm" else candidate.kind), candidate


def _cpu_accelerator(snapshot: ResourceSnapshot) -> AcceleratorSnapshot:
    return AcceleratorSnapshot(
        "cpu", "stdlib", platform.processor() or "cpu", True, None, None, platform.python_version()
    )


def _accelerators(total: int, available: int) -> list[AcceleratorSnapshot]:
    found: list[AcceleratorSnapshot] = []
    torch_module: Any = None
    with suppress(ImportError, OSError, RuntimeError):
        torch_module = __import__("torch")
    if torch_module is not None:
        cuda = False
        with suppress(AttributeError, RuntimeError):
            cuda = bool(torch_module.cuda.is_available())
        if cuda:
            hip = str(getattr(torch_module.version, "hip", "") or "")
            kind = "rocm" if hip else "cuda"
            name, total_memory, available_memory = _torch_memory(torch_module)
            found.append(
                AcceleratorSnapshot(
                    kind,
                    "pytorch-rocm" if hip else "pytorch-cuda",
                    name,
                    True,
                    total_memory,
                    available_memory,
                    hip or str(getattr(torch_module, "__version__", "")),
                )
            )
        mps = False
        with suppress(AttributeError, RuntimeError):
            mps = bool(torch_module.backends.mps.is_available())
        if mps:
            found.append(
                AcceleratorSnapshot(
                    "mps",
                    "pytorch",
                    "Apple MPS",
                    True,
                    total,
                    available,
                    str(torch_module.__version__),
                )
            )
    if platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}:
        try:
            mlx_module = __import__("mlx.core", fromlist=["core"])
        except (ImportError, OSError, RuntimeError):
            pass
        else:
            del mlx_module
            found.extend(
                (
                    AcceleratorSnapshot("mlx_gpu", "mlx", "Apple MLX GPU", True, total, available),
                    AcceleratorSnapshot("mlx_cpu", "mlx", "Apple MLX CPU", True, total, available),
                )
            )
    return found


def _torch_memory(torch: Any) -> tuple[str, int | None, int | None]:
    name = "torch accelerator"
    total = available = None
    with suppress(RuntimeError, AttributeError, TypeError):
        properties = torch.cuda.get_device_properties(0)
        name = str(properties.name)
        total = int(properties.total_memory)
    with suppress(RuntimeError, AttributeError, TypeError):
        available, total_from_runtime = torch.cuda.mem_get_info(0)
        total = int(total_from_runtime)
        available = int(available)
    return name, total, available


def _memory() -> tuple[int, int, int | None, int | None]:
    total = available = 0
    if psutil_module is not None:
        with suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
            virtual = psutil_module.virtual_memory()
            total = int(virtual.total)
            available = int(virtual.available)
    if not total:
        with suppress(OSError, ValueError):
            page = int(os.sysconf("SC_PAGE_SIZE"))
            total = int(os.sysconf("SC_PHYS_PAGES")) * page
            available = int(os.sysconf("SC_AVPHYS_PAGES")) * page
    limit = _cgroup_memory_limit()
    current = _cgroup_memory_current()
    if limit is not None:
        available = min(available or limit, max(0, limit - (current or 0)))
        total = min(total or limit, limit)
    return (
        total or 1,
        available or total or 1,
        limit,
        max(0, limit - current) if limit is not None and current is not None else None,
    )


def _cgroup_memory_limit() -> int | None:
    if sys.platform != "linux":
        return None
    for path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            value = path.read_text(encoding="utf-8").strip()
            limit = None if value == "max" else int(value)
            if limit is None or limit < (1 << 60):
                return limit
        except (OSError, ValueError):
            continue
    return None


def _cgroup_memory_current() -> int | None:
    if sys.platform != "linux":
        return None
    for path in (
        Path("/sys/fs/cgroup/memory.current"),
        Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        try:
            return int(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def _process_rss_bytes() -> int:
    if sys.platform == "linux":
        try:
            pages = int(Path("/proc/self/statm").read_text(encoding="utf-8").split()[1])
            return pages * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, IndexError, ValueError):
            pass
    if resource_module is None:
        return 0
    value = int(resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _runtime_telemetry() -> RuntimeTelemetry:
    cpu_usage = _cpu_usage_percent()
    cpu_temperature = _cpu_temperature_celsius()
    gpu_usage, gpu_temperature, gpu_total, gpu_available = _nvidia_smi()
    if gpu_usage is None:
        gpu_usage, gpu_temperature, gpu_total, gpu_available = _apple_gpu_ioreg()
    return RuntimeTelemetry(
        cpu_usage,
        cpu_temperature,
        gpu_usage,
        gpu_temperature,
        gpu_total,
        gpu_available,
    )


def _cpu_usage_percent() -> float | None:
    if psutil_module is None:
        return None
    with suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
        value = float(psutil_module.cpu_percent(interval=0.05))
        return value if 0 <= value <= 100 else None
    return None


def _cpu_temperature_celsius() -> float | None:
    if psutil_module is None:
        return None
    with suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
        sensors = psutil_module.sensors_temperatures()
        values = [
            float(reading.current)
            for name, readings in sensors.items()
            if any(token in name.casefold() for token in ("cpu", "package", "core", "tdie"))
            for reading in readings
            if reading.current is not None
        ]
        return max(values) if values else None
    return None


def _nvidia_smi() -> tuple[float | None, float | None, int | None, int | None]:
    command = (
        "nvidia-smi",
        "--query-gpu=utilization.gpu,temperature.gpu,memory.total,memory.free",
        "--format=csv,noheader,nounits",
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=0.75, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, None, None, None
    line = next((item.strip() for item in result.stdout.splitlines() if item.strip()), "")
    values = [item.strip() for item in line.split(",")]
    if result.returncode != 0 or len(values) != 4:
        return None, None, None, None
    try:
        return (
            _percent(values[0]),
            float(values[1]),
            int(float(values[2]) * 1024 * 1024),
            int(float(values[3]) * 1024 * 1024),
        )
    except (TypeError, ValueError):
        return None, None, None, None


def _apple_gpu_ioreg() -> tuple[float | None, float | None, int | None, int | None]:
    if sys.platform != "darwin":
        return None, None, None, None
    try:
        result = subprocess.run(
            ("ioreg", "-l", "-w", "0", "-r", "-c", "IOAccelerator"),
            capture_output=True,
            text=True,
            timeout=0.75,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None, None, None
    if result.returncode != 0:
        return None, None, None, None
    line = next(
        (item for item in result.stdout.splitlines() if "PerformanceStatistics" in item),
        "",
    )
    match = re.search(r'"Device Utilization %"=(\d+(?:\.\d+)?)', line)
    if match is None:
        match = re.search(r'"Renderer Utilization %"=(\d+(?:\.\d+)?)', line)
    if match is None:
        return None, None, None, None
    try:
        return _percent(match.group(1)), None, None, None
    except (TypeError, ValueError):
        return None, None, None, None


def _percent(value: str) -> float | None:
    parsed = float(value)
    return parsed if 0 <= parsed <= 100 else None
