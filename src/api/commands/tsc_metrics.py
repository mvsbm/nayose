#!/usr/bin/env python3
"""Parse TypeScript extended diagnostics into a stable CI metric artifact."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

_TIME_RE = re.compile(r"^(?P<value>[0-9.]+)(?P<unit>ms|s|us)$")
_MEMORY_RE = re.compile(r"^(?P<value>[0-9.]+)(?P<unit>[KMG]B?)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_value(name: str, value: str) -> int | float | str:
    compact = value.replace(",", "").strip()
    if name.endswith("time"):
        match = _TIME_RE.match(compact)
        if match:
            amount = float(match.group("value"))
            time_factor = {"us": 0.001, "ms": 1.0, "s": 1000.0}[match.group("unit")]
            return round(amount * time_factor, 3)
    if name == "Memory used":
        match = _MEMORY_RE.match(compact)
        if match:
            amount = float(match.group("value"))
            memory_factor = {
                "K": 1024,
                "KB": 1024,
                "M": 1024**2,
                "MB": 1024**2,
                "G": 1024**3,
                "GB": 1024**3,
            }
            return int(amount * memory_factor[match.group("unit").upper()])
    try:
        return int(compact)
    except ValueError:
        try:
            return float(compact)
        except ValueError:
            return compact


def parse_diagnostics(text: str) -> dict[str, int | float | str]:
    values: dict[str, int | float | str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        if name and value:
            values[name] = parse_value(name, value)
    required = {"Files", "Types", "Instantiations", "Memory used", "Total time"}
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError(f"TypeScript diagnostics missing fields: {', '.join(missing)}")
    return values


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    diagnostics = parse_diagnostics(args.input.read_text(encoding="utf-8"))
    write_json(
        args.output,
        {
            "schema_version": 1,
            "kind": "compiler_diagnostics",
            "compiler": "typescript",
            "source": args.input.name,
            "metrics": diagnostics,
        },
    )
    print(json.dumps(diagnostics, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
