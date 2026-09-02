#!/usr/bin/env python3
"""Headless synthetic-data generation (same engine + save contract as the notebook).

Usage:
    python scripts/generate_data.py --profile smoke --data-dir tmp_smoke --seed 42

Profiles: preflight | smoke | mac | large_mac | generate_only (default).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root (no install needed)

from synthetic import SyntheticConfig  # noqa: E402 (sys.path bootstrap above)
from synthetic.pipeline import generate, save_all


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["preflight", "smoke", "mac", "large_mac", "generate_only"],
        help="size profile (default: NAYOSE_PROFILE or generate_only)",
    )
    parser.add_argument(
        "--difficulty",
        choices=["debug", "realistic", "severe"],
        help="dirtiness difficulty (default: NAYOSE_DIFFICULTY or realistic)",
    )
    parser.add_argument("--seed", type=int, help="random seed (default: NAYOSE_SEED or 42)")
    parser.add_argument(
        "--data-dir", help="output/data directory (default: NAYOSE_DATA_DIR or data/)"
    )
    parser.add_argument(
        "--n-parties", type=int, help="override party count (default: NAYOSE_N_PARTIES)"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    kwargs: dict[str, object] = {}  # values validated by dataclass
    if args.profile:
        kwargs["profile"] = args.profile
    if args.difficulty:
        kwargs["difficulty"] = args.difficulty
    if args.seed is not None:
        kwargs["seed"] = args.seed
    if args.data_dir:
        kwargs["output_dir"] = Path(args.data_dir)
    if args.n_parties:
        kwargs["n_parties_override"] = args.n_parties

    cfg = SyntheticConfig(**kwargs)  # type: ignore[arg-type]
    t0 = time.perf_counter()
    result = generate(cfg)
    print(f"Generated {result.counts} in {time.perf_counter() - t0:.1f}s; saving...")
    paths = save_all(cfg, result)
    print(f"Wrote {len(paths)} artifacts under {cfg.output_dir}")
    print(
        f"Done: run the matcher with `NAYOSE_DATA_DIR={cfg.output_dir} "
        "jupyter nbconvert --to notebook --execute nayose.ipynb`"
    )


if __name__ == "__main__":
    main()
