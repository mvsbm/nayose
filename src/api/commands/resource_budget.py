#!/usr/bin/env python3
"""Validate CI workflow resource metrics against hard safety budgets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

BYTES_PER_MIB = 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-dir", type=Path, required=True)
    parser.add_argument(
        "--budgets",
        type=Path,
        default=Path("ci/resource_budgets.json"),
    )
    parser.add_argument("--browser-metrics", type=Path)
    parser.add_argument("--only", nargs="+", help="check only these configured command labels")
    return parser.parse_args()


def load_metrics(metrics_dir: Path) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for path in sorted(metrics_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") != "command_resource":
            continue
        label = str(payload.get("label", "")).strip()
        if not label:
            raise ValueError(f"metric file has no label: {path}")
        if label in metrics:
            raise ValueError(f"duplicate metric label: {label}")
        metrics[label] = payload
    return metrics


def violations(metrics: dict[str, dict[str, Any]], budgets: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for label, budget in budgets.items():
        payload = metrics.get(label)
        if payload is None:
            errors.append(f"missing metrics for {label}")
            continue
        returncode = int(payload.get("returncode", 1))
        if returncode != 0:
            errors.append(f"{label} returned exit code {returncode}")
        resource = payload.get("resource")
        if not isinstance(resource, dict):
            errors.append(f"{label} has no resource measurements")
            continue
        if resource.get("process_metrics_supported") is not True:
            errors.append(f"{label} lacks process-tree measurements")
        checks = {
            "max_peak_rss_mb": (
                float(resource.get("peak_rss_bytes", 0)) / BYTES_PER_MIB,
                "peak RSS",
            ),
            "max_process_count": (float(resource.get("process_count", 0)), "process count"),
            "max_peak_threads": (float(resource.get("peak_threads", 0)), "peak threads"),
            "max_wall_ms": (float(payload.get("wall_ms", 0)), "wall time"),
            "max_read_bytes": (float(resource.get("read_bytes", 0)), "read bytes"),
            "max_write_bytes": (float(resource.get("write_bytes", 0)), "write bytes"),
            "max_cpu_ms": (
                float(resource.get("user_cpu_ms", 0)) + float(resource.get("system_cpu_ms", 0)),
                "CPU time",
            ),
        }
        for budget_name, (actual, description) in checks.items():
            limit = budget.get(budget_name)
            if limit is not None and actual > float(limit):
                errors.append(f"{label} {description} {actual:.1f} exceeds {limit}")
        cgroup = payload.get("cgroup")
        if isinstance(cgroup, dict):
            cgroup_checks = {
                "max_cgroup_memory_mb": (
                    float(cgroup.get("memory_peak_bytes", 0)) / BYTES_PER_MIB,
                    "cgroup peak memory",
                ),
                "max_cgroup_pids": (float(cgroup.get("pids_peak", 0)), "cgroup peak pids"),
            }
            for budget_name, (actual, description) in cgroup_checks.items():
                limit = budget.get(budget_name)
                if limit is not None and actual > float(limit):
                    errors.append(f"{label} {description} {actual:.1f} exceeds {limit}")
    return errors


def browser_violations(browser: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if browser.get("kind") != "browser_transport":
        errors.append("browser metrics have an invalid kind")
    if int(browser.get("journeys", 0)) < 1:
        errors.append("browser metrics contain no completed journeys")
    terminal_requests = int(browser.get("api_responses", 0)) + int(browser.get("api_failures", 0))
    if int(browser.get("api_requests", 0)) != terminal_requests:
        errors.append(
            "browser API request/terminal counts differ "
            f"(requests={browser.get('api_requests', 0)}, terminal={terminal_requests})"
        )
    if int(browser.get("api_errors", 0)):
        errors.append(f"browser API errors: {browser['api_errors']}")
    return errors


def main() -> int:
    args = parse_args()
    budget_payload = json.loads(args.budgets.read_text(encoding="utf-8"))
    budgets = budget_payload.get("commands")
    if not isinstance(budgets, dict) or not budgets:
        raise SystemExit("resource budget must define non-empty commands")
    if args.only:
        unknown = sorted(set(args.only) - budgets.keys())
        if unknown:
            raise SystemExit(f"unknown command budget(s): {', '.join(unknown)}")
        budgets = {label: budgets[label] for label in args.only}
    metrics = load_metrics(args.metrics_dir)
    errors = violations(metrics, budgets)
    if args.browser_metrics is not None:
        browser = json.loads(args.browser_metrics.read_text(encoding="utf-8"))
        errors.extend(browser_violations(browser))
        api_bytes = int(browser.get("request_bytes", 0)) + int(browser.get("response_bytes", 0))
        print(
            f"browser: journeys={browser.get('journeys', 0)} "
            f"api_requests={browser.get('api_requests', 0)} "
            f"api_bytes={api_bytes}"
        )
    for label in sorted(metrics):
        resource = metrics[label].get("resource", {})
        cpu_ms = float(resource.get("user_cpu_ms", 0)) + float(resource.get("system_cpu_ms", 0))
        print(
            f"{label}: wall={float(metrics[label].get('wall_ms', 0)):.0f}ms "
            f"cpu={cpu_ms:.0f}ms "
            f"rss={float(resource.get('peak_rss_bytes', 0)) / BYTES_PER_MIB:.1f}MiB "
            f"processes={resource.get('process_count', 0)}"
        )
    if errors:
        print("Resource budget violations:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"Resource budgets passed for {len(metrics)} command(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
