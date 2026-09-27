"""Evaluate an external dirty-record benchmark from a local manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from api.benchmarking import (
    CsvBenchmarkAdapter,
    NormalizedFieldCandidateProvider,
    NormalizedFieldScorer,
    StressConfig,
    evaluate_ranking,
    stress_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        required=True,
        help="directory containing benchmark.json and the referenced CSV files",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--stress",
        action="store_true",
        help="apply deterministic compound corruption before evaluation",
    )
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset = CsvBenchmarkAdapter(args.dataset_dir).load()
    if args.stress:
        dataset = stress_dataset(dataset, StressConfig(seed=args.seed))
    report = evaluate_ranking(
        dataset,
        NormalizedFieldScorer(),
        NormalizedFieldCandidateProvider(dataset.entities),
    )
    text = json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
