#!/usr/bin/env python3
"""Review-decision audit: score operator decisions against hidden truth.

The review audit: decisions persisted by the dashboard
(models/decisions/<run>.json — reviewer identity included) are compared with
the hidden ground truth (record_truth) to measure reviewer accuracy, and the
verified pairs are exported as an explicit artifact for a future retraining
workflow. Training does not consume this export automatically. Truth
tables are never mutated — this only reads them.

Usage:
    python scripts/audit_decisions.py [--run <run_id>] [--data-dir data/current]
                                     [--models-dir models] [--top 5]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

from api.release import data_generation_for_fingerprint

REPO = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="", help="run id; default = models/current")
    parser.add_argument("--data-dir", default=str(REPO / "data" / "current"))
    parser.add_argument("--models-dir", default=str(REPO / "models"))
    parser.add_argument(
        "--top", type=int, default=5, help="wrong-decision samples to print per reviewer"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    models = Path(args.models_dir)
    data = Path(args.data_dir)

    current = (models / "current").resolve() if (models / "current").exists() else None
    run_dir = current if not args.run else (models / "runs" / args.run).resolve()
    if run_dir is None or not run_dir.is_dir():
        raise SystemExit("no run bundle found (models/current) — train first with scripts/train.py")
    run_id = run_dir.name

    decisions_path = models / "decisions" / f"{run_id}.json"
    if not decisions_path.exists():
        print(f"no decisions recorded yet: {decisions_path}")
        return 0
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    if not decisions:
        print(f"decisions file empty: {decisions_path}")
        return 0

    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    fingerprint = str(manifest.get("data_fingerprint", ""))
    generation = data_generation_for_fingerprint(data, fingerprint)
    if generation is None:
        raise SystemExit(
            f"no data generation matches run fingerprint {fingerprint[:12]} for {run_id}"
        )
    con = duckdb.connect()
    truth = con.execute(
        "select record_id, truth_id from read_parquet(?)",
        [str(generation / "ground_truth" / "record_truth.parquet")],
    ).fetchdf()
    tid = dict(zip(truth.record_id, truth.truth_id, strict=True))

    rows: list[dict[str, object]] = []
    for item in decisions:
        left, right = item.get("left", ""), item.get("right", "")
        tl, tr = tid.get(left), tid.get(right)
        rows.append(
            {
                "left_record_id": left,
                "right_record_id": right,
                "truth_id_left": tl,
                "truth_id_right": tr,
                "same_entity": tl is not None and tl == tr,
                "resolvable": tl is not None and tr is not None,
                "reviewer": item.get("reviewer", "?"),
                "ts": item.get("ts", ""),
            }
        )
    df = pd.DataFrame(rows)

    print(f"run: {run_id}")
    print(f"decisions: {len(df)}\n")
    resolvable = df[df.resolvable]
    if len(resolvable) == 0:
        print("no decisions reference records present in record_truth (nothing to score)")
    else:
        summary = (
            resolvable.groupby("reviewer")
            .agg(n=("same_entity", "size"), correct=("same_entity", "sum"))
            .assign(accuracy=lambda g: g.correct / g.n)
            .round(3)
        )
        print(summary.to_string())
        print(
            f"\noverall accuracy: {resolvable.same_entity.mean():.1%} "
            f"({int(resolvable.same_entity.sum())}/{len(resolvable)})"
        )
        wrong = resolvable[~resolvable.same_entity]
        if len(wrong):
            samples = wrong.head(args.top)
            print(f"\n{samples.to_string()}")
    unknown = len(df) - len(resolvable)
    if unknown:
        print(f"note: {unknown} decisions reference records absent from this run's snapshot")

    promoted = df[df.resolvable & df.same_entity].copy()
    out_path = models / "decisions" / f"promoted_{run_id}.parquet"
    promoted.to_parquet(out_path, index=False)
    print(
        f"\nverified duplicate export (not auto-consumed by training): "
        f"{len(promoted)} -> {out_path}"
    )

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
