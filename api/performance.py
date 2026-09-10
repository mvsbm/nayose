"""Portable runtime policy for CPU budgets and optional accelerators."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil


def available_cpu_count() -> int:
    """Return CPUs available to this process, including container limits."""
    if hasattr(os, "sched_getaffinity"):
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def _positive_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value or value.lower() == "auto":
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer or 'auto'") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def process_worker_count() -> int:
    return _positive_env("NAYOSE_PROCESS_WORKERS") or _positive_env("WEB_CONCURRENCY") or 1


def cpu_thread_budget() -> int:
    """Allocate CPU threads without multiplying API-process oversubscription."""
    explicit = _positive_env("NAYOSE_CPU_THREADS")
    if explicit is not None:
        return min(explicit, available_cpu_count())
    return max(1, ceil(available_cpu_count() / process_worker_count()))


def resolve_device(requested: str = "auto") -> str:
    """Resolve a requested accelerator and fail fast for unavailable explicit devices."""
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

    @classmethod
    def resolve(cls, requested_device: str = "auto") -> RuntimeProfile:
        return cls(
            device=resolve_device(requested_device),
            cpu_threads=cpu_thread_budget(),
            process_workers=process_worker_count(),
            available_cpus=available_cpu_count(),
        )


@contextmanager
def cpu_thread_limit(threads: int) -> Iterator[None]:
    """Limit native numerical libraries for one operation when supported."""
    try:
        from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]
    except ImportError:
        yield
        return
    with threadpool_limits(limits=threads):
        yield
