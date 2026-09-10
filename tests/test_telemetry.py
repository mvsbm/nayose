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


def test_stats_reset_clears_samples_and_counters():
    stats = Stats()
    stats.record("health", 0.001, error=True)
    stats.reset()

    assert stats.snapshot() == []
