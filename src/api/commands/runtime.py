#!/usr/bin/env python3
"""Measure bundle load and warm recommendation latency on representative data."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from api.application.matching import ExplainedMatcher
from api.infrastructure.runtime.resource_manager import (
    ComputePlan,
    ResourceManager,
    ResourceSnapshot,
    plan_for_bundle,
    resource_state_dir,
)
from api.infrastructure.runtime.workload_storage import bundle_demand
from nayose.config import ConfigSnapshot, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="models/current")
    parser.add_argument("--data-dir", default="data/current")
    parser.add_argument("--iterations", type=int, default=20)
    return parser.parse_args()


def peak_rss_mb(snapshot: ResourceSnapshot) -> float:
    return snapshot.memory.process_rss_bytes / (1024 * 1024)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))
    return ordered[index]


def _run(
    args: argparse.Namespace,
    manager: ResourceManager,
    config: ConfigSnapshot,
    plan: ComputePlan,
) -> int:
    started = time.perf_counter()
    matcher = ExplainedMatcher.load(
        Path(args.model_dir), Path(args.data_dir), config_snapshot=config
    )
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
    resources = manager.inspect()
    print(
        json.dumps(
            {
                "cold_load_ms": round(cold_load_ms, 2),
                "warm_recommend_p50_ms": round(statistics.median(timings), 2),
                "warm_recommend_p95_ms": round(percentile(timings, 0.95), 2),
                "warm_recommend_min_ms": round(min(timings), 2),
                "warm_recommend_max_ms": round(max(timings), 2),
                "iterations": args.iterations,
                "available_cpus": resources.available_cpus,
                "cpu_quota_cpus": resources.cpu_quota_cpus,
                "cpu_threads": plan.cpu_threads,
                "process_workers": plan.process_workers,
                "device": plan.device,
                "peak_rss_mb": round(peak_rss_mb(resources), 2),
                "resources": resources.as_dict(),
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


def main() -> int:
    args = parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    config = load_config()
    manager = ResourceManager(resource_state_dir(Path(args.model_dir)))
    plan = plan_for_bundle(
        manager,
        config,
        Path(args.model_dir),
        workload="benchmark",
        storage_demand=bundle_demand("benchmark").demand,
    )
    with manager.session(plan):
        manager.configure(plan, config.performance)
        return _run(args, manager, config, plan)


if __name__ == "__main__":
    raise SystemExit(main())
