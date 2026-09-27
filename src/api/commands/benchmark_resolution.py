"""Evaluate a production Nayose resolver against synthetic ground truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from api.application.matching import ExplainedMatcher
from api.benchmarking import (
    NayoseBenchmarkAdapter,
    NayoseResolutionAdapter,
    evaluate_resolution,
)


class _OfflineRecommendationService:
    def __init__(self, matcher: ExplainedMatcher):
        self.matcher = matcher

    def recommend(self, run_id: str, record: dict[str, str]):
        return self.matcher.recommend(record, lambda _stage: None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/current"))
    parser.add_argument("--model-dir", type=Path, default=Path("models/current"))
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset = NayoseBenchmarkAdapter(args.data_dir, args.model_dir).load()
    matcher = ExplainedMatcher.load(args.model_dir.resolve(), args.data_dir.resolve())
    try:
        resolver = NayoseResolutionAdapter(_OfflineRecommendationService(matcher))
        report = evaluate_resolution(
            dataset,
            lambda run_id, query: resolver.resolve(run_id, query),
            str(args.model_dir),
        )
        text = json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    finally:
        matcher.close()
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
