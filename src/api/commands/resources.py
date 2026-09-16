"""Host resource capacity inspection for automation boundaries."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from api.infrastructure.runtime.resource_manager import ResourceManager
from api.infrastructure.runtime.storage_resources import StorageDemand


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nayose resource plan")
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--scratch-bytes", type=int, default=0)
    parser.add_argument("--output-bytes", type=int, default=0)
    parser.add_argument("--cache-bytes", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.path.resolve()
    manager = ResourceManager(path / ".nayose-runtime")
    plan = manager.plan_storage(
        workload=args.workload,
        path=path,
        demand=StorageDemand(
            scratch_bytes=args.scratch_bytes,
            output_bytes=args.output_bytes,
            cache_bytes=args.cache_bytes,
        ),
    )
    print(json.dumps(plan.as_dict(), separators=(",", ":")))
    return 0
