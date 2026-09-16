"""Inspect and conservatively collect durable artifact storage."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from api.infrastructure.bundles.artifact_catalog import collect_artifacts, inspect_artifacts
from api.infrastructure.runtime.resource_manager import ResourceManager, resource_state_dir
from nayose.config import load_config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--path", type=Path, default=Path("."))
    inspect.add_argument("--data-dir", type=Path, default=Path("data"))
    inspect.add_argument("--models-dir", type=Path, default=Path("models"))
    explain = subparsers.add_parser("explain")
    explain.add_argument("--data-dir", type=Path, default=Path("data"))
    explain.add_argument("--models-dir", type=Path, default=Path("models"))
    explain.add_argument("--older-than-days", type=int, default=30)
    gc = subparsers.add_parser("gc")
    gc.add_argument("--data-dir", type=Path, default=Path("data"))
    gc.add_argument("--models-dir", type=Path, default=Path("models"))
    gc.add_argument("--older-than-days", type=int, default=30)
    gc.add_argument("--apply", action="store_true", help="tombstone first, delete on the next run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    result: Mapping[str, object]
    if args.command == "inspect":
        manager = ResourceManager(resource_state_dir(args.models_dir))
        result = manager.inspect_storage(args.path)
    else:
        artifacts = inspect_artifacts(
            args.data_dir,
            args.models_dir,
            operational_db=config.runtime.operational_db,
            older_than_days=args.older_than_days,
        )
        if args.command == "gc" and args.apply:
            collect_artifacts(
                artifacts,
                operational_db=config.runtime.operational_db,
            )
        manager = ResourceManager(resource_state_dir(args.models_dir))
        capacity = manager.inspect_storage(args.data_dir)
        reserve = max(
            manager.storage_policy.reserve_minimum_bytes,
            int(int(capacity["total_bytes"]) * manager.storage_policy.reserve_ratio),
        )
        result = {
            "command": args.command,
            "older_than_days": args.older_than_days,
            "capacity": {
                **capacity,
                "reserve_bytes": reserve,
                "usable_bytes": max(0, int(capacity["available_bytes"]) - reserve),
            },
            "policy": {
                "reserve_ratio": manager.storage_policy.reserve_ratio,
                "reserve_minimum_bytes": manager.storage_policy.reserve_minimum_bytes,
                "workspace_stale_seconds": manager.storage_policy.workspace_stale_seconds,
            },
            "artifacts": [item.as_dict() for item in artifacts],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
