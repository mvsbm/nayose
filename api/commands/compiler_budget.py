#!/usr/bin/env python3
"""Validate compiler diagnostics against explicit CI budgets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

BYTES_PER_MIB = 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--budgets", type=Path, default=Path("ci/compiler_budgets.json"))
    return parser.parse_args()


def violations(metrics: dict[str, Any], budgets: dict[str, float | int]) -> list[str]:
    values = metrics.get("metrics")
    if not isinstance(values, dict):
        return ["compiler metrics have no metrics object"]
    fields = {
        "max_files": ("Files", float(values.get("Files", 0)), "files"),
        "max_types": ("Types", float(values.get("Types", 0)), "types"),
        "max_instantiations": (
            "Instantiations",
            float(values.get("Instantiations", 0)),
            "instantiations",
        ),
        "max_memory_mb": (
            "Memory used",
            float(values.get("Memory used", 0)) / BYTES_PER_MIB,
            "memory",
        ),
        "max_total_ms": ("Total time", float(values.get("Total time", 0)), "total time"),
    }
    errors: list[str] = []
    for budget_name, (_metric_name, actual, description) in fields.items():
        limit = budgets.get(budget_name)
        if limit is not None and actual > float(limit):
            errors.append(f"TypeScript {description} {actual:.1f} exceeds {limit}")
    return errors


def main() -> int:
    args = parse_args()
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    budgets = json.loads(args.budgets.read_text(encoding="utf-8"))
    errors = []
    if metrics.get("kind") != "compiler_diagnostics" or metrics.get("compiler") != "typescript":
        errors.append("compiler metrics have an invalid schema")
    errors.extend(violations(metrics, budgets))
    values = metrics.get("metrics", {})
    print(
        "TypeScript: "
        f"files={values.get('Files', 0)} types={values.get('Types', 0)} "
        f"instantiations={values.get('Instantiations', 0)} "
        f"memory={float(values.get('Memory used', 0)) / BYTES_PER_MIB:.1f}MiB "
        f"total={values.get('Total time', 0)}ms"
    )
    if errors:
        print("Compiler budget violations:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Compiler budgets passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
