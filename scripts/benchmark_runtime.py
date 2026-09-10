#!/usr/bin/env python3
"""Measure bundle load and warm recommendation latency on representative data."""

from __future__ import annotations

import argparse
import json
import statistics
import time

from api.engine import load_engine
from api.performance import RuntimeProfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="models/current")
    parser.add_argument("--data-dir", default="data/current")
    parser.add_argument("--iterations", type=int, default=20)
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))
    return ordered[index]


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    engine = load_engine(args.model_dir, args.data_dir)
    cold_load_ms = (time.perf_counter() - started) * 1000
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    if engine._reference is None:
        raise RuntimeError("loaded engine has no reference data")
    query = engine._reference.iloc[0].to_dict()
    engine.recommend(query)
    timings = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        engine.recommend(query)
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
                "cpu_threads": profile.cpu_threads,
                "process_workers": profile.process_workers,
                "device": profile.device,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
