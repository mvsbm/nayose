"""Materialize target-platform serving artifacts for an existing model run."""

from __future__ import annotations

import argparse
from pathlib import Path

from api.infrastructure.bundles.materialization import materialize_runtime_artifacts
from api.infrastructure.runtime.resource_manager import (
    ResourceManager,
    plan_for_bundle,
    resource_state_dir,
)
from api.infrastructure.runtime.workload_storage import bundle_demand
from nayose.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default="models/current")
    parser.add_argument("--data-dir", default="data/current")
    parser.add_argument("--index-backend", choices=("polars", "pandas"), default="polars")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run = Path(args.run_dir).resolve()
    data = Path(args.data_dir).resolve()
    config = load_config()
    manager = ResourceManager(resource_state_dir(run))
    plan = plan_for_bundle(
        manager,
        config,
        run,
        workload="materialization",
        storage_demand=bundle_demand("materialization").demand,
    )
    with manager.session(plan):
        manager.configure(plan, config.performance)
        materialize_runtime_artifacts(
            run,
            data,
            index_backend=args.index_backend,
            config_snapshot=config,
        )
    print(f"materialized target-platform artifacts for {run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
