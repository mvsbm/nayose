from __future__ import annotations

from threading import Event, Thread

from api.query_context import BundleReader
from api.repositories import RunRepository


def test_cache_single_flight_runs_one_loader_for_same_key(tmp_path):
    reader = BundleReader(RunRepository(tmp_path), tmp_path)
    started = Event()
    release = Event()
    calls = 0
    results: list[str] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
        return "shared"

    threads = [
        Thread(target=lambda: results.append(reader.cached("same", operation))) for _ in range(2)
    ]
    threads[0].start()
    assert started.wait(timeout=1)
    threads[1].start()
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert calls == 1
    assert results == ["shared", "shared"]
    assert all(not thread.is_alive() for thread in threads)


def test_cache_miss_does_not_block_unrelated_keys(tmp_path):
    reader = BundleReader(RunRepository(tmp_path), tmp_path)
    slow_started = Event()
    fast_started = Event()
    release_slow = Event()
    results: dict[str, str] = {}

    def slow_operation() -> str:
        slow_started.set()
        release_slow.wait(timeout=2)
        return "slow"

    def slow_reader() -> None:
        results["slow"] = reader.cached("slow", slow_operation)

    def fast_operation() -> str:
        fast_started.set()
        return "fast"

    slow_thread = Thread(target=slow_reader)
    slow_thread.start()
    assert slow_started.wait(timeout=1)

    fast_thread = Thread(
        target=lambda: results.setdefault("fast", reader.cached("fast", fast_operation))
    )
    fast_thread.start()
    assert fast_started.wait(timeout=1)

    release_slow.set()
    slow_thread.join(timeout=2)
    fast_thread.join(timeout=2)

    assert results == {"slow": "slow", "fast": "fast"}
    assert not slow_thread.is_alive()
    assert not fast_thread.is_alive()
