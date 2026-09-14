"""Portable runtime policy for CPU budgets and optional accelerators."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import floor
from pathlib import Path
from threading import RLock
from typing import Any

from nayose.config import PerformanceConfig, load_config


def _cgroup_cpu_quota() -> float | None:
    """Return the Linux cgroup-v2 CPU quota in logical CPUs when available."""
    if sys.platform != "linux":
        return None
    try:
        relative = next(
            line.split(":", 2)[2].strip()
            for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
            if line.startswith("0::")
        )
        root = Path("/sys/fs/cgroup") / relative.lstrip("/")
        quota, period = (root / "cpu.max").read_text(encoding="utf-8").split()
        if quota == "max":
            return None
        value = float(quota) / float(period)
        return value if value > 0 else None
    except (OSError, StopIteration, ValueError, ZeroDivisionError):
        return None


def available_cpu_count() -> int:
    """Return CPUs available to this process, including affinity and cgroup limits."""
    if hasattr(os, "sched_getaffinity"):
        try:
            visible = len(os.sched_getaffinity(0))
        except OSError:
            visible = os.cpu_count() or 1
    else:
        visible = os.cpu_count() or 1
    quota = _cgroup_cpu_quota()
    if quota is not None:
        visible = min(visible, max(1, int(quota)))
    return max(1, visible)


def _settings(settings: PerformanceConfig | None) -> PerformanceConfig:
    return settings if settings is not None else load_config().performance


def process_worker_count(settings: PerformanceConfig | None = None) -> int:
    """Return the configured process count used by the CPU partition."""
    return _settings(settings).process_workers or 1


def cpu_thread_budget(
    *, workers: int | None = None, settings: PerformanceConfig | None = None
) -> int:
    """Allocate a non-oversubscribing native-thread share to each process."""
    resolved = _settings(settings)
    available = available_cpu_count()
    selected_workers = process_worker_count(resolved) if workers is None else workers
    if selected_workers < 1:
        raise ValueError("workers must be positive")
    if selected_workers > available:
        raise ValueError(
            f"configured process workers ({selected_workers}) exceed visible CPUs ({available}); "
            "reduce NAYOSE_PROCESS_WORKERS or WEB_CONCURRENCY"
        )
    per_process = max(1, floor(available / selected_workers))
    explicit = resolved.cpu_threads
    return min(explicit, per_process) if explicit is not None else per_process


def model_thread_budget(
    *, workers: int | None = None, settings: PerformanceConfig | None = None
) -> int:
    """Bound model-library threads by the process's native CPU share."""
    resolved = _settings(settings)
    explicit = resolved.model_threads
    budget = cpu_thread_budget(workers=workers, settings=resolved)
    return min(explicit, budget) if explicit is not None else budget


_NATIVE_THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
_NATIVE_LOCK = RLock()
_NATIVE_LIMIT: Any = None


def native_thread_environment(
    threads: int | None = None,
    *,
    workers: int | None = None,
    settings: PerformanceConfig | None = None,
) -> dict[str, str]:
    """Return the environment contract for common native numerical runtimes."""
    resolved = _settings(settings)
    budget = cpu_thread_budget(workers=workers, settings=resolved)
    if resolved.native_thread_cap is not None:
        budget = min(budget, resolved.native_thread_cap)
    selected = budget if threads is None else min(max(1, threads), budget)
    return {
        **{name: str(selected) for name in _NATIVE_THREAD_VARIABLES},
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_DYNAMIC": "FALSE",
        "MKL_DYNAMIC": "FALSE",
        "KMP_BLOCKTIME": "0",
    }


def configure_native_threads(
    threads: int | None = None, settings: PerformanceConfig | None = None
) -> int:
    """Apply one process-wide native-thread policy before numerical work starts."""
    resolved = _settings(settings)
    budget = cpu_thread_budget(settings=resolved)
    selected = budget if threads is None else min(max(1, threads), budget)
    with _NATIVE_LOCK:
        os.environ.update(native_thread_environment(selected, settings=resolved))
        global _NATIVE_LIMIT
        try:
            from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]
        except ImportError:
            return selected
        if _NATIVE_LIMIT is not None:
            _NATIVE_LIMIT.__exit__(None, None, None)
        _NATIVE_LIMIT = threadpool_limits(limits=selected)
        _NATIVE_LIMIT.__enter__()
    return selected


def configure_torch_threads(
    threads: int | None = None, settings: PerformanceConfig | None = None
) -> int:
    """Configure Torch intra-op and inter-op parallelism under the CPU policy."""
    resolved = _settings(settings)
    selected = (
        model_thread_budget(settings=resolved)
        if threads is None
        else min(max(1, threads), cpu_thread_budget(settings=resolved))
    )
    torch_module: Any = sys.modules.get("torch")
    if torch_module is None:
        try:
            import torch as torch_module
        except ImportError:
            return selected
    torch_module.set_num_threads(selected)
    try:
        torch_module.set_num_interop_threads(1)
    except RuntimeError as exc:
        current = int(torch_module.get_num_interop_threads())
        if current > 1:
            raise RuntimeError(
                "Torch inter-op parallelism was initialized above the CPU budget"
            ) from exc
    return selected


def configure_mps_runtime() -> None:
    """Disable PyTorch's silent CPU fallback for the locked MPS profile."""
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"


def require_mps_device(requested: str = "mps", settings: PerformanceConfig | None = None) -> str:
    """Require a working Apple MPS backend without selecting another device."""
    if requested != "mps":
        raise RuntimeError(f"MPS-only runtime requires device='mps', got {requested!r}")
    configure_mps_runtime()
    try:
        import torch

        available = bool(torch.backends.mps.is_available())
    except (AttributeError, ImportError, RuntimeError) as exc:
        raise RuntimeError("MPS-only runtime requires an available PyTorch MPS backend") from exc
    if not available:
        raise RuntimeError("MPS-only runtime requires an available PyTorch MPS backend")
    configure_torch_threads(settings=settings)
    try:
        probe = torch.randn(32, 32, device="mps")
        (probe @ probe.T).mean().cpu()
    except (RuntimeError, TypeError) as exc:
        raise RuntimeError("MPS-only runtime failed its matrix preflight") from exc
    return "mps"


def resolve_device(requested: str = "auto", settings: PerformanceConfig | None = None) -> str:
    """Resolve a requested accelerator for non-neural and compatibility paths."""
    if requested not in {"auto", "cpu", "mps", "cuda"}:
        raise ValueError(f"unsupported device {requested!r}")
    if requested == "cpu":
        return "cpu"
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        mps_available = bool(torch.backends.mps.is_available())
    except (AttributeError, ImportError, RuntimeError):
        cuda_available = False
        mps_available = False
    if "torch" in sys.modules:
        configure_torch_threads(settings=settings)
    if requested == "cuda":
        if not cuda_available:
            raise RuntimeError("CUDA was requested but is unavailable")
        return "cuda"
    if requested == "mps":
        if not mps_available:
            raise RuntimeError("MPS was requested but is unavailable")
        return "mps"
    if cuda_available:
        return "cuda"
    if mps_available:
        return "mps"
    return "cpu"


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """Resolved runtime choices exposed to diagnostics and benchmarks."""

    device: str
    cpu_threads: int
    process_workers: int
    available_cpus: int
    cpu_quota_cpus: float | None

    @classmethod
    def resolve(
        cls, requested_device: str = "mps", settings: PerformanceConfig | None = None
    ) -> RuntimeProfile:
        resolved = _settings(settings)
        return cls(
            device=(
                require_mps_device(requested_device, resolved)
                if requested_device == "mps"
                else resolve_device(requested_device, resolved)
            ),
            cpu_threads=cpu_thread_budget(settings=resolved),
            process_workers=process_worker_count(resolved),
            available_cpus=available_cpu_count(),
            cpu_quota_cpus=_cgroup_cpu_quota(),
        )


@contextmanager
def cpu_thread_limit(threads: int, settings: PerformanceConfig | None = None) -> Iterator[None]:
    """Limit native numerical libraries for one operation when supported."""
    bounded = min(max(1, threads), cpu_thread_budget(settings=settings))
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        yield
        return
    with threadpool_limits(limits=bounded):
        yield
