from __future__ import annotations

from api.commands.resource_budget import browser_violations, violations


def _metric(returncode: int = 0, rss_mb: int = 10) -> dict:
    return {
        "kind": "command_resource",
        "label": "train",
        "returncode": returncode,
        "wall_ms": 100,
        "resource": {
            "peak_rss_bytes": rss_mb * 1024 * 1024,
            "process_count": 2,
            "peak_threads": 4,
            "read_bytes": 10,
            "write_bytes": 20,
            "process_metrics_supported": True,
        },
    }


def test_resource_budget_accepts_measurement_within_limits():
    assert not violations(
        {"train": _metric()},
        {"train": {"max_peak_rss_mb": 20, "max_process_count": 4}},
    )


def test_resource_budget_rejects_failed_or_over_budget_measurement():
    errors = violations(
        {"train": _metric(returncode=1, rss_mb=30)},
        {"train": {"max_peak_rss_mb": 20, "max_process_count": 4}},
    )
    assert "train returned exit code 1" in errors
    assert any("peak RSS" in error for error in errors)


def test_resource_budget_requires_every_configured_command():
    assert violations({}, {"train": {}}) == ["missing metrics for train"]


def test_browser_budget_counts_aborted_requests_as_terminal():
    assert not browser_violations(
        {
            "kind": "browser_transport",
            "journeys": 18,
            "api_requests": 200,
            "api_responses": 194,
            "api_failures": 6,
            "api_aborted": 6,
            "api_errors": 0,
        }
    )


def test_browser_budget_rejects_unaccounted_requests_and_errors():
    errors = browser_violations(
        {
            "kind": "browser_transport",
            "journeys": 1,
            "api_requests": 2,
            "api_responses": 1,
            "api_failures": 0,
            "api_errors": 1,
        }
    )
    assert any("terminal counts differ" in error for error in errors)
    assert "browser API errors: 1" in errors
