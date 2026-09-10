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


def test_explicit_unavailable_accelerator_fails_fast():
    from api.performance import resolve_device

    assert resolve_device("cpu") == "cpu"
    with pytest.raises((RuntimeError, ValueError)):
        resolve_device("not-a-device")
