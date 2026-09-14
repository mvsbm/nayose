"""Headless synthetic-data generation with the shared engine/save contract.

Usage:
    nayose data generate --profile smoke --data-dir tmp_smoke --seed 42

Profiles: preflight | smoke | mac | large_mac | generate_only (default).
Use --clean-only to stream a canonical entity layer without sabotage or policy context.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from api.infrastructure.process.work_ledger import WorkflowLedger
from data_generation.canonical import write_clean_entities
from data_generation.preview import config_from_controls
from data_generation.workflow import generate, save_all
from nayose.config import load_config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["preflight", "smoke", "mac", "large_mac", "generate_only"],
        help="size profile (default: NAYOSE_PROFILE or generate_only)",
    )
    parser.add_argument(
        "--difficulty",
        choices=["debug", "realistic", "severe", "challenge"],
        help="dirtiness difficulty (default: NAYOSE_DIFFICULTY or realistic)",
    )
    parser.add_argument("--seed", type=int, help="random seed (default: NAYOSE_SEED or 42)")
    parser.add_argument(
        "--data-dir", help="output/data directory (default: NAYOSE_DATA_DIR or data/)"
    )
    parser.add_argument(
        "--n-parties", type=int, help="override party count (default: NAYOSE_N_PARTIES)"
    )
    parser.add_argument(
        "--controls-json", help="validated generation controls from the admin preview"
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="stream canonical entities only; skip sabotage, policies, and queries",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50_000,
        help="rows per output chunk for --clean-only (default: 50,000)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    if args.controls_json:
        try:
            controls = json.loads(args.controls_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid --controls-json: {exc}") from exc
        if not isinstance(controls, dict):
            raise SystemExit("--controls-json must contain a JSON object")
        cfg = config_from_controls(controls)
        if args.data_dir:
            cfg = replace(cfg, output_dir=Path(args.data_dir))
    else:
        cfg = config.generation
        updates: dict[str, Any] = {}
        if args.profile:
            updates["profile"] = args.profile
        if args.difficulty:
            updates["difficulty"] = args.difficulty
        if args.seed is not None:
            updates["seed"] = args.seed
        if args.data_dir:
            updates["output_dir"] = Path(args.data_dir)
        if args.n_parties:
            updates["n_parties_override"] = args.n_parties
        if updates:
            cfg = replace(cfg, **updates)
    ledger = WorkflowLedger(
        "generation",
        metadata={"profile": cfg.profile, "seed": cfg.seed},
    )
    t0 = time.perf_counter()
    if args.clean_only:
        with ledger.span("clean_generation", "pipeline"):
            clean_result = write_clean_entities(cfg, chunk_size=args.chunk_size)
        clean_dir = Path(str(clean_result["generation_dir"]))
        clean_counts = clean_result["counts"]
        if not isinstance(clean_counts, dict):
            raise RuntimeError("clean generation returned invalid counts")
        ledger.counter("entities", int(clean_counts["entities"]))
        ledger.write(clean_dir / "workflow_metrics.json")
        print(f"Wrote {clean_result['counts']} clean entities in {time.perf_counter() - t0:.1f}s")
        return 0
    with ledger.span("generate", "pipeline"):
        result = generate(cfg)
    for name, amount in result.counts.items():
        ledger.counter(name, amount)
    print(f"Generated {result.counts} in {time.perf_counter() - t0:.1f}s; saving...")
    with ledger.span("save_generation", "file"):
        paths = save_all(cfg, result)
    generation_dir = paths[0].parent
    manifest = json.loads(paths[-1].read_text(encoding="utf-8"))
    ledger.metadata("data_fingerprint", str(manifest.get("data_fingerprint", "")))
    ledger.counter("artifacts", len(paths))
    ledger.write(generation_dir / "workflow_metrics.json")
    print(f"Wrote {len(paths)} artifacts under {cfg.output_dir}")
    print(
        f"Done: run the matcher with `NAYOSE_DATA_DIR={cfg.output_dir} "
        "nayose model train --data-dir <generation> --models-dir models`"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
