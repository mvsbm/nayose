import os

import pytest


def test_cpu_budget_is_bounded_by_process_count(monkeypatch):
    from api import performance

    monkeypatch.delenv("NAYOSE_CPU_THREADS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("NAYOSE_PROCESS_WORKERS", raising=False)
    monkeypatch.setattr(performance, "available_cpu_count", lambda: 12)
    monkeypatch.setenv("NAYOSE_PROCESS_WORKERS", "3")

    assert performance.cpu_thread_budget() == 4


def test_explicit_cpu_budget_is_capped(monkeypatch):
    from api import performance

    monkeypatch.setattr(performance, "available_cpu_count", lambda: 4)
    monkeypatch.setenv("NAYOSE_CPU_THREADS", "16")

    assert performance.cpu_thread_budget() == 4


def test_explicit_cpu_budget_is_partitioned_across_workers(monkeypatch):
    from api import performance

    monkeypatch.setattr(performance, "available_cpu_count", lambda: 12)
    monkeypatch.setenv("NAYOSE_CPU_THREADS", "16")
    monkeypatch.setenv("NAYOSE_PROCESS_WORKERS", "3")

    assert performance.cpu_thread_budget() == 4


def test_native_thread_environment_disables_nested_parallelism(monkeypatch):
    from api import performance

    monkeypatch.setattr(performance, "available_cpu_count", lambda: 8)
    monkeypatch.delenv("NAYOSE_CPU_THREADS", raising=False)
    monkeypatch.delenv("NAYOSE_PROCESS_WORKERS", raising=False)

    environment = performance.native_thread_environment()

    assert {environment[name] for name in environment if name.endswith("THREADS")} == {"8"}
    assert environment["TOKENIZERS_PARALLELISM"] == "false"


def test_model_threads_never_exceed_process_share(monkeypatch):
    from api import performance

    monkeypatch.setattr(performance, "available_cpu_count", lambda: 8)
    monkeypatch.setenv("NAYOSE_PROCESS_WORKERS", "2")
    monkeypatch.setenv("NAYOSE_MODEL_THREADS", "16")

    assert performance.model_thread_budget() == 4


def test_process_topology_fails_closed_when_workers_exceed_cpus(monkeypatch):
    from api import performance

    monkeypatch.setattr(performance, "available_cpu_count", lambda: 2)
    monkeypatch.setenv("NAYOSE_PROCESS_WORKERS", "3")

    with pytest.raises(ValueError, match="exceed visible CPUs"):
        performance.cpu_thread_budget()


def test_explicit_unavailable_accelerator_fails_fast():
    from api.performance import resolve_device

    assert resolve_device("cpu") == "cpu"
    with pytest.raises((RuntimeError, ValueError)):
        resolve_device("not-a-device")


def test_mps_runtime_disables_torch_cpu_fallback(monkeypatch):
    from api.performance import configure_mps_runtime

    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    configure_mps_runtime()

    assert os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "0"


def test_mps_runtime_rejects_other_devices():
    from api.performance import require_mps_device

    with pytest.raises(RuntimeError, match="MPS-only"):
        require_mps_device("cpu")
