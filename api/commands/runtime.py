#!/usr/bin/env python3
"""Measure bundle load and warm recommendation latency on representative data."""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import sys
import time
from pathlib import Path

from api.matching import ExplainedMatcher
from api.performance import RuntimeProfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="models/current")
    parser.add_argument("--data-dir", default="data/current")
    parser.add_argument("--iterations", type=int, default=20)
    return parser.parse_args()


def peak_rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    bytes_used = value if sys.platform == "darwin" else value * 1024
    return bytes_used / (1024 * 1024)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))
    return ordered[index]


def main() -> int:
    args = parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    started = time.perf_counter()
    matcher = ExplainedMatcher.load(Path(args.model_dir), Path(args.data_dir))
    cold_load_ms = (time.perf_counter() - started) * 1000
    if matcher.engine._reference is None:
        raise RuntimeError("loaded engine has no reference data")
    query = matcher.engine._reference.iloc[0].to_dict()
    matcher.recommend(query, lambda _: None)
    timings = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        matcher.recommend(query, lambda _: None)
        timings.append((time.perf_counter() - started) * 1000)
    profile = RuntimeProfile.resolve()
    print(
        json.dumps(
            {
                "cold_load_ms": round(cold_load_ms, 2),
                "warm_recommend_p50_ms": round(statistics.median(timings), 2),
                "warm_recommend_p95_ms": round(percentile(timings, 0.95), 2),
                "warm_recommend_min_ms": round(min(timings), 2),
                "warm_recommend_max_ms": round(max(timings), 2),
                "iterations": args.iterations,
                "available_cpus": profile.available_cpus,
                "cpu_quota_cpus": profile.cpu_quota_cpus,
                "cpu_threads": profile.cpu_threads,
                "process_workers": profile.process_workers,
                "device": profile.device,
                "peak_rss_mb": round(peak_rss_mb(), 2),
                "reference_rows": len(matcher.engine._reference),
                "embedding_artifact_mb": round(
                    next(
                        (
                            path.stat().st_size / (1024 * 1024)
                            for path in Path(args.model_dir).glob("reference_embeddings.*")
                        ),
                        0.0,
                    ),
                    2,
                ),
            },
            sort_keys=True,
        )
    )
    matcher.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
