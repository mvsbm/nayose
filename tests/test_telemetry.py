import logging
import sys
import threading
from pathlib import Path

from api.admin_process import AdminProcessRunner
from api.telemetry import Stats


def test_stats_are_thread_safe_and_report_bounded_latency_fields():
    stats = Stats()
    stats.record("entities", 0.010)
    stats.record("entities", 0.020)
    stats.record("entities", 0.100, error=True)

    row = stats.snapshot()[0]

    assert row["endpoint"] == "entities"
    assert row["hits"] == 3
    assert row["errors"] == 1
    assert row["total_ms"] == 130.0
    assert row["p50_ms"] == 20.0
    assert row["p95_ms"] == 20.0


def test_admin_subprocess_boundary_is_recorded():
    stats = Stats()
    runner = AdminProcessRunner(Path.cwd(), logging.getLogger(), stats)

    runner.run(
        [sys.executable, "-c", "print('ok')"],
        "test",
        "job-1",
        {},
        threading.Event(),
        lambda _stage: None,
        lambda *_update: None,
        lambda _process: None,
        lambda: None,
    )

    rows = {row["endpoint"]: row for row in stats.snapshot()}
    assert rows["admin.subprocess"]["hits"] == 1


def test_stats_reset_clears_samples_and_counters():
    stats = Stats()
    stats.record("health", 0.001, error=True)
    stats.reset()

    assert stats.snapshot() == []
