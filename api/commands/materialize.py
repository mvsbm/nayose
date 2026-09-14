"""Materialize target-platform serving artifacts for an existing model run."""

from __future__ import annotations

import argparse
from pathlib import Path

from api.infrastructure.bundles.materialization import materialize_runtime_artifacts


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
    materialize_runtime_artifacts(run, data, index_backend=args.index_backend)
    print(f"materialized target-platform artifacts for {run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
