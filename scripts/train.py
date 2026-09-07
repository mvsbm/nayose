#!/usr/bin/env python3
"""Headless model training: executes nayose.ipynb end-to-end in a fresh kernel.

CLI == notebook contract. The notebook is the reference pipeline (index →
candidates → pair features → entity-disjoint verifier → clustering → query
scoring → bundle export); this command runs it headlessly with NAYOSE_*
overrides, verifies it, and promotes it only after fingerprint checks pass.

Usage:
    python scripts/train.py --data-dir data/current --models-dir models \\
        --seed 42 --pair-model lightgbm --device auto

The produced bundle under models/runs/run_<ts>_<hash> (promoted via
models/current) is exactly what api.main serves.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.release import atomic_directory_alias, validate_model_for_data  # noqa: E402

NOTEBOOK = ROOT / "nayose.ipynb"
REQUIRED_BUNDLE_TABLES = (
    "canonical_entities",
    "record_to_nayose",
    "review_queue",
    "full_dataset_metrics",
    "full_dataset_summary",
    "pair_confusion_test",
    "query_confusion",
    "query_results",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.getenv("NAYOSE_DATA_DIR", "data/current"),
        help="matcher-visible snapshot (generation alias or generation dir)",
    )
    parser.add_argument("--models-dir", default=os.getenv("NAYOSE_MODELS_DIR", "models"))
    parser.add_argument("--seed", type=int, default=int(os.getenv("NAYOSE_SEED", "42")))
    parser.add_argument(
        "--device",
        default=os.getenv("NAYOSE_DEVICE", "auto"),
        choices=["auto", "mps", "cpu", "cuda"],
    )
    parser.add_argument(
        "--pair-model",
        default=os.getenv("NAYOSE_PAIR_MODEL", "auto"),
        choices=["auto", "lightgbm", "sklearn"],
    )
    parser.add_argument(
        "--model-family",
        default=os.getenv("NAYOSE_MODEL_FAMILY", "off"),
        choices=["off", "ruri", "f2llm", "f2llm_0.6b", "qwen"],
    )
    parser.add_argument(
        "--exact-mode",
        default=os.getenv("NAYOSE_EXACT_MODE", "hybrid"),
        choices=["hybrid", "fuzzy_only", "exact_only"],
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("NAYOSE_TRAIN_TIMEOUT", "14400")),
        help="per-cell timeout (s); default 4h",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip post-run bundle verification",
    )
    parser.add_argument(
        "--prune-aborted",
        action="store_true",
        help="delete run dirs without run_manifest.json (aborted executions)",
    )
    return parser.parse_args()


def _env_for(args: argparse.Namespace) -> dict[str, str]:
    """Notebook overrides. NAYOSE_UI stays off (headless); low-memory + SVD
    fallback keep a laptop-sized run safe without touching precision logic."""
    snapshot = Path(args.data_dir).resolve()
    data_dir = (snapshot / "current").resolve() if (snapshot / "current").exists() else snapshot
    env = {
        "NAYOSE_DATA_DIR": str(data_dir),
        "NAYOSE_OUTPUT_DIR": str(Path(args.models_dir).resolve()),
        "NAYOSE_SEED": str(args.seed),
        "NAYOSE_DEVICE": args.device,
        "NAYOSE_PAIR_MODEL": args.pair_model,
        "NAYOSE_MODEL_FAMILY": args.model_family,
        "NAYOSE_EXACT_MODE": args.exact_mode,
        "NAYOSE_UI": "0",
        "NAYOSE_STRICT_BACKENDS": "0",
        "NAYOSE_LOW_MEMORY_MODELS": "1",
        "NAYOSE_ALLOW_SVD_FALLBACK": "1",
        "NAYOSE_PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "NAYOSE_PROMOTE": "0",
    }
    return env


def run_notebook(args: argparse.Namespace, env: dict[str, str]) -> float:
    import nbformat
    from nbconvert.preprocessors import ExecutePreprocessor

    nb = nbformat.read(NOTEBOOK, as_version=4)
    executor = ExecutePreprocessor(timeout=args.timeout, kernel_name="python3")
    os.environ.update(env)
    print(
        f"executing {NOTEBOOK.name} with {len(nb.cells)} cells "
        f"(data={env['NAYOSE_DATA_DIR']}, out={env['NAYOSE_OUTPUT_DIR']}, seed={args.seed})"
    )
    print(
        f"env: device={args.device} pair_model={args.pair_model} "
        f"model_family={args.model_family} exact_mode={args.exact_mode}"
    )
    t0 = time.monotonic()
    executor.preprocess(nb, {"metadata": {"path": str(ROOT)}})
    return time.monotonic() - t0


def verify_bundle(args: argparse.Namespace) -> Path:
    """The load guard in NayoseEngine refuses a bundle whose fingerprint does
    not match the reference; fail here, not at serve time."""
    models = Path(args.models_dir).resolve()
    snapshot = Path(args.data_dir).resolve()
    data = (snapshot / "current").resolve() if (snapshot / "current").exists() else snapshot
    runs_root = models / "runs"
    if not runs_root.is_dir():
        raise SystemExit("FAIL: no run bundle found under models/runs/")
    aborted = [
        p for p in runs_root.iterdir() if p.is_dir() and not (p / "run_manifest.json").exists()
    ]
    if aborted:
        msg = f"aborted/incomplete runs without run_manifest.json: {[p.name for p in aborted]}"
        if args.prune_aborted:
            import shutil

            for p in aborted:
                shutil.rmtree(p, ignore_errors=True)
            print(f"pruned {len(aborted)} aborted run(s)")
        else:
            print(f"WARN: {msg} (--prune-aborted to remove)")
    runs = sorted(
        (p for p in runs_root.iterdir() if p.is_dir() and (p / "run_manifest.json").exists()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    run = runs[0] if runs else None
    if run is None:
        raise SystemExit("FAIL: no complete run bundle found under models/runs/")
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = [t for t in REQUIRED_BUNDLE_TABLES if not (run / f"{t}.parquet").exists()]
    if missing:
        raise SystemExit(f"FAIL: bundle {run.name} missing tables: {missing}")
    gen_manifest = json.loads((data / "generation_manifest.json").read_text(encoding="utf-8"))
    expected_fp = gen_manifest["data_fingerprint"]
    actual_fp = manifest["data_fingerprint"]
    if actual_fp != expected_fp:
        raise SystemExit(
            f"FAIL: fingerprint mismatch — data {expected_fp[:12]} vs bundle {actual_fp[:12]}"
        )
    th = manifest.get("thresholds", {})
    counts = manifest.get("counts", {})
    print(f"OK: bundle {run.name}")
    print(f"  fingerprint {actual_fp[:16]}… == data ✓")
    print(f"  counts {counts}")
    print(
        f"  thresholds pair_auto={th.get('pair_auto')} pair_review={th.get('pair_review')} "
        f"entity_auto={th.get('entity_auto')} entity_review={th.get('entity_review')}"
    )
    for m in ("candidate_metrics", "pair_metrics", "query_metrics"):
        if m in manifest:
            print(f"  {m}: {manifest[m]}")
    return run


def promote_bundle(args: argparse.Namespace, run: Path) -> None:
    models = Path(args.models_dir).resolve()
    snapshot = Path(args.data_dir).resolve()
    if snapshot.name == "current":
        data_root = snapshot.parent
    elif snapshot.parent.name == "generations":
        data_root = snapshot.parent.parent
    else:
        data_root = snapshot
    fingerprint = validate_model_for_data(run, data_root)
    atomic_directory_alias(run, models / "current")
    registry_path = models / "registry.json"
    registry = (
        json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else []
    )
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    registry.append(
        {
            "run_id": run.name,
            "created_at": manifest.get("created_at_utc", ""),
            "config_hash": manifest.get("config_hash", ""),
            "data_fingerprint": fingerprint,
            "model_family": manifest.get("config", {}).get("model_family", ""),
            "device": manifest.get("device", ""),
            "backends": manifest.get("backends", {}),
        }
    )
    registry_tmp = registry_path.with_name(f".{registry_path.name}.tmp.{os.getpid()}")
    registry_tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(registry_tmp, registry_path)
    print(f"promoted {run.name} (fingerprint {fingerprint[:12]})")


def main() -> int:
    args = parse_args()
    snapshot = Path(args.data_dir).resolve()
    data = (snapshot / "current").resolve() if (snapshot / "current").exists() else snapshot
    if not (data / "generation_manifest.json").exists():
        raise SystemExit(
            f"data dir {data} has no generation_manifest.json — run scripts/generate_data.py first"
        )
    env = _env_for(args)
    elapsed = run_notebook(args, env)
    print(f"training finished in {elapsed / 60:.1f} min")
    if args.no_verify:
        print("bundle verification skipped; leaving models/current unchanged")
        return 0
    run = verify_bundle(args)
    promote_bundle(args, run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
