#!/usr/bin/env python3
"""Evaluate retrieval ablations on one fingerprint-bound generation."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from api.infrastructure.retrieval.ablation import ABLATION_NAMES, evaluate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="models/current")
    parser.add_argument("--data-dir", default="data/current")
    parser.add_argument("--query-limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=ABLATION_NAMES,
        default=None,
        help="restrict evaluation to selected retrieval modes",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.query_limit is not None and args.query_limit < 1:
        raise SystemExit("--query-limit must be positive")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    report = evaluate(
        Path(args.model_dir),
        Path(args.data_dir),
        query_limit=args.query_limit,
        batch_size=args.batch_size,
        modes=args.modes,
    )
    payload = asdict(report)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is None:
        print(serialized)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
