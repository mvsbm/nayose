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

from api.block_index import (  # noqa: E402
    BLOCK_INDEX_ARTIFACT,
    BLOCK_INDEX_SCHEMA_VERSION,
    build_block_indexes,
    data_fingerprint,
    save_block_indexes,
)
from api.dense_index import (  # noqa: E402
    DENSE_INDEX_ARTIFACT,
    build_dense_index,
    save_dense_index,
    target_platform,
    usearch_version,
)
from api.dense_views import build_dense_view_artifacts  # noqa: E402
from api.embedding_config import (  # noqa: E402
    SUPPORTED_MODEL_FAMILIES,
    embedding_model_id,
    notebook_model_family,
)
from api.model_gate import evaluate_f2llm_candidate  # noqa: E402
from api.name_variants import build_entity_aliases  # noqa: E402
from api.performance import cpu_thread_budget  # noqa: E402
from api.release import atomic_directory_alias, validate_model_for_data  # noqa: E402

NOTEBOOK = ROOT / "nayose.ipynb"
REQUIRED_BUNDLE_ARTIFACTS = (BLOCK_INDEX_ARTIFACT,)
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
        choices=list(SUPPORTED_MODEL_FAMILIES),
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("NAYOSE_EMBEDDING_MODEL"),
        help="Hugging Face embedding model override; required explicitly for custom profiles",
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
        "--baseline-run",
        default=os.getenv("NAYOSE_BASELINE_RUN"),
        help="baseline bundle for the F2LLM release gate; defaults to models/current",
    )
    parser.add_argument(
        "--min-top1-gain",
        type=float,
        default=float(os.getenv("NAYOSE_MIN_TOP1_GAIN", "0.01")),
    )
    parser.add_argument(
        "--min-recall5-gain",
        type=float,
        default=float(os.getenv("NAYOSE_MIN_RECALL5_GAIN", "0.01")),
    )
    parser.add_argument(
        "--min-hard-recall5-gain",
        type=float,
        default=float(os.getenv("NAYOSE_MIN_HARD_RECALL5_GAIN", "0.01")),
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
        "NAYOSE_MODEL_FAMILY": notebook_model_family(args.model_family),
        "NAYOSE_EXACT_MODE": args.exact_mode,
        "NAYOSE_REQUESTED_MODEL_FAMILY": args.model_family,
        "NAYOSE_UI": "0",
        "NAYOSE_STRICT_BACKENDS": os.getenv("NAYOSE_STRICT_BACKENDS", "0"),
        "NAYOSE_CPU_THREADS": os.getenv("NAYOSE_CPU_THREADS", str(cpu_thread_budget())),
        "NAYOSE_MODEL_THREADS": os.getenv("NAYOSE_MODEL_THREADS", str(cpu_thread_budget())),
        "NAYOSE_INDEX_BACKEND": os.getenv("NAYOSE_INDEX_BACKEND", "polars"),
        "NAYOSE_LOW_MEMORY_MODELS": "1",
        "NAYOSE_ALLOW_SVD_FALLBACK": os.getenv(
            "NAYOSE_ALLOW_SVD_FALLBACK", "0" if args.model_family == "f2llm_330m" else "1"
        ),
        "NAYOSE_PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "NAYOSE_PROMOTE": "0",
    }
    embedding = embedding_model_id(args.model_family, args.embedding_model)
    if embedding is not None:
        env["NAYOSE_EMBEDDING_MODEL"] = embedding
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


def _materialize_dense_artifact(run: Path) -> dict[str, object] | None:
    """Materialize the optional ANN cache when the portable backend is installed."""
    import numpy as np

    embedding_path = next(
        (
            run / name
            for name in ("reference_embeddings.f16.npy", "reference_embeddings.npy")
            if (run / name).exists()
        ),
        None,
    )
    if embedding_path is None:
        return None
    try:
        embeddings = np.load(embedding_path, mmap_mode="r")
        index = build_dense_index(embeddings)
        save_dense_index(run / DENSE_INDEX_ARTIFACT, index)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"WARN: optional dense ANN artifact skipped ({type(exc).__name__})")
        return None
    return {
        "path": DENSE_INDEX_ARTIFACT,
        "format": "usearch",
        "row_count": int(embeddings.shape[0]),
        "dimensions": int(embeddings.shape[1]),
        "source": embedding_path.name,
        "platform": target_platform(),
        "usearch_version": usearch_version(),
    }


def materialize_runtime_artifacts(
    run: Path, data: Path, *, index_backend: str | None = None
) -> None:
    """Finalize serving artifacts after notebook training completes."""
    import pandas as pd

    reference_path = data / "reference_records.parquet"
    reference = pd.read_parquet(reference_path)
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_fingerprint = str(manifest.get("data_fingerprint", ""))
    actual_fingerprint = data_fingerprint(reference)
    if not expected_fingerprint or actual_fingerprint != expected_fingerprint:
        raise SystemExit(
            "FAIL: refusing to materialize block indexes for a fingerprint-mismatched bundle"
        )
    selected_backend = index_backend or os.getenv("NAYOSE_INDEX_BACKEND") or "polars"
    mapping_path = run / "record_to_nayose.parquet"
    if not mapping_path.is_file():
        raise SystemExit(f"FAIL: cannot materialize aliases without {mapping_path}")
    mapping = pd.read_parquet(mapping_path, columns=["record_id", "nayose_id"])
    record_to_entity = dict(
        zip(mapping["record_id"].astype(str), mapping["nayose_id"].astype(str), strict=True)
    )
    alias_artifact = build_entity_aliases(
        reference,
        record_to_entity,
        run / "entity_aliases.parquet",
        data_fingerprint=actual_fingerprint,
    )
    aliases = pd.read_parquet(run / "entity_aliases.parquet")
    indexes = build_block_indexes(reference, backend=selected_backend)
    artifact_path = run / BLOCK_INDEX_ARTIFACT
    save_block_indexes(
        artifact_path,
        indexes,
        fingerprint=actual_fingerprint,
        row_count=len(reference),
    )
    artifact = {
        "path": BLOCK_INDEX_ARTIFACT,
        "format": "npz",
        "schema_version": BLOCK_INDEX_SCHEMA_VERSION,
        "data_fingerprint": actual_fingerprint,
        "row_count": len(reference),
        "blocks": {name: len(groups) for name, groups in indexes.items()},
        "backend": selected_backend,
    }
    runtime_artifacts = dict(manifest.get("runtime_artifacts", {}))
    runtime_artifacts["block_indexes"] = artifact
    runtime_artifacts["entity_aliases"] = alias_artifact
    dense_artifact = _materialize_dense_artifact(run)
    if dense_artifact is not None:
        runtime_artifacts["dense_index"] = dense_artifact
    dense_views: dict[str, dict[str, object]] = {}
    if dense_artifact is not None:
        dense_views["record"] = {
            "view": "record",
            "data_fingerprint": actual_fingerprint,
            "embedding_path": str(dense_artifact["source"]),
            "index_path": DENSE_INDEX_ARTIFACT,
            "format": "usearch",
            "row_count": dense_artifact["row_count"],
            "dimensions": dense_artifact["dimensions"],
            "reference_row_count": len(reference),
        }
    if (
        manifest.get("backends", {}).get("neural_enabled")
        and manifest.get("embedding_model")
        and os.getenv("NAYOSE_DENSE_VIEWS", "1").lower() not in {"0", "false", "no"}
    ):
        dense_views.update(
            build_dense_view_artifacts(
                reference,
                data,
                run,
                model_id=str(manifest["embedding_model"]),
                data_fingerprint=actual_fingerprint,
                device=os.getenv("NAYOSE_DEVICE", "auto"),
                batch_size=int(
                    os.getenv("NAYOSE_DENSE_VIEW_BATCH", os.getenv("NAYOSE_NEURAL_BATCH", "16"))
                ),
                aliases=aliases,
            )
        )
    if dense_views:
        runtime_artifacts["dense_views"] = dense_views
    manifest["runtime_artifacts"] = runtime_artifacts
    generated_paths = {
        BLOCK_INDEX_ARTIFACT,
        DENSE_INDEX_ARTIFACT,
        "entity_aliases.parquet",
        *(descriptor["embedding_path"] for descriptor in dense_views.values()),
        *(descriptor["index_path"] for descriptor in dense_views.values()),
    }
    artifacts = [
        entry for entry in manifest.get("artifacts", []) if entry.get("path") not in generated_paths
    ]
    artifacts.append(artifact)
    artifacts.append(alias_artifact)
    if dense_artifact is not None:
        artifacts.append(dense_artifact)
    artifacts.extend(
        {
            "name": f"dense_index_{view}",
            "path": str(descriptor["index_path"]),
            "format": "usearch",
            "rows": descriptor["row_count"],
            "dimensions": descriptor["dimensions"],
        }
        for view, descriptor in dense_views.items()
        if view != "record"
    )
    manifest["artifacts"] = artifacts
    temporary = manifest_path.with_name(f".{manifest_path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, manifest_path)
    print(
        f"materialized {BLOCK_INDEX_ARTIFACT}: {len(reference):,} rows, "
        f"fingerprint {actual_fingerprint[:12]}"
    )


def materialize_latest_runtime_artifacts(args: argparse.Namespace) -> Path:
    """Materialize serving artifacts for the newest complete run."""
    models = Path(args.models_dir).resolve()
    snapshot = Path(args.data_dir).resolve()
    data = (snapshot / "current").resolve() if (snapshot / "current").exists() else snapshot
    runs_root = models / "runs"
    runs = sorted(
        (p for p in runs_root.iterdir() if p.is_dir() and (p / "run_manifest.json").exists()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not runs:
        raise SystemExit("FAIL: no complete run bundle found under models/runs/")
    materialize_runtime_artifacts(runs[0], data)
    return runs[0]


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
    required_artifacts = list(REQUIRED_BUNDLE_ARTIFACTS)
    if any(
        (run / name).exists()
        for name in ("reference_embeddings.f16.npy", "reference_embeddings.npy")
    ):
        required_artifacts.append("dense_index.usearch")
    missing_artifacts = [
        artifact for artifact in required_artifacts if not (run / artifact).exists()
    ]
    if missing_artifacts:
        raise SystemExit(f"FAIL: bundle {run.name} missing artifacts: {missing_artifacts}")
    if getattr(args, "model_family", "off") == "f2llm_330m":
        expected_model = embedding_model_id(
            args.model_family, getattr(args, "embedding_model", None)
        )
        actual_model = manifest.get("embedding_model")
        backends = manifest.get("backends", {})
        if actual_model != expected_model:
            raise SystemExit(
                f"FAIL: 330M profile recorded {actual_model!r}, expected {expected_model!r}"
            )
        if not backends.get("neural_enabled") or backends.get("degraded_run"):
            raise SystemExit("FAIL: 330M profile produced a degraded or non-neural bundle")
        embedding_path = next(
            (
                run / name
                for name in ("reference_embeddings.f16.npy", "reference_embeddings.npy")
                if (run / name).exists()
            ),
            None,
        )
        if embedding_path is None:
            raise SystemExit("FAIL: 330M profile produced no reference embeddings")
        import numpy as np

        shape = np.load(embedding_path, mmap_mode="r").shape
        if len(shape) != 2 or shape[1] != 896:
            raise SystemExit(f"FAIL: F2LLM-v2-330M must produce 896 dimensions, got {shape}")
        runtime_views = manifest.get("runtime_artifacts", {}).get("dense_views", {})
        missing_views = [
            view
            for view in ("name", "address", "alias")
            if not isinstance(runtime_views, dict)
            or not isinstance(runtime_views.get(view), dict)
            or runtime_views[view].get("dimensions") != 896
            or not (run / str(runtime_views[view].get("embedding_path", ""))).is_file()
            or not (run / str(runtime_views[view].get("index_path", ""))).is_file()
        ]
        if missing_views:
            raise SystemExit(f"FAIL: F2LLM-v2-330M missing field-aware views: {missing_views}")
        print(f"OK: verified exact F2LLM-v2-330M profile ({shape[0]:,} × {shape[1]})")
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


def verify_performance(args: argparse.Namespace, run: Path) -> None:
    if args.model_family != "f2llm_330m":
        return
    baseline_path = (
        Path(args.baseline_run) if args.baseline_run else Path(args.models_dir) / "current"
    )
    manifest_path = baseline_path / "run_manifest.json"
    baseline = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    )
    candidate = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    candidate_results = None
    baseline_results = None
    if baseline is not None:
        import pandas as pd

        candidate_results = pd.read_parquet(run / "query_results.parquet").to_dict("records")
        baseline_results = pd.read_parquet(baseline_path / "query_results.parquet").to_dict(
            "records"
        )
    decision = evaluate_f2llm_candidate(
        candidate,
        baseline,
        candidate_results=candidate_results,
        baseline_results=baseline_results,
        min_top1_gain=args.min_top1_gain,
        min_recall5_gain=args.min_recall5_gain,
        min_hard_recall5_gain=args.min_hard_recall5_gain,
    )
    print(f"F2LLM release gate: {decision.status} — {decision.message}")
    if not decision.passed:
        raise SystemExit(f"FAIL: F2LLM performance gate: {decision.message}")


def promote_bundle(args: argparse.Namespace, run: Path) -> None:
    models = Path(args.models_dir).resolve()
    snapshot = Path(args.data_dir).resolve()
    fingerprint = validate_model_for_data(run, snapshot)
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
    materialize_latest_runtime_artifacts(args)
    run = verify_bundle(args)
    verify_performance(args, run)
    promote_bundle(args, run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
