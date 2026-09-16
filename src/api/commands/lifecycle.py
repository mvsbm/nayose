"""Headless model training through the checked-in Python pipeline.

The pipeline covers index construction, candidate verification, entity-disjoint
clustering, query scoring, and bundle export. This command supplies validated
runtime overrides, verifies the resulting bundle, and promotes it only after
fingerprint and model gates pass.

Usage:
    nayose model train --data-dir data/current --models-dir models \\
        --seed 42 --pair-model lightgbm --device mps --model-family f2llm_330m

The produced bundle under models/runs/run_<ts>_<hash> (promoted via
models/current) is exactly what api.main serves.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from api.infrastructure.bundles.materialization import materialize_runtime_artifacts  # noqa: E402
from api.infrastructure.bundles.release import (  # noqa: E402
    ReleaseCatalog,
    ReleasePublisher,
    validate_model_for_data,
)
from api.infrastructure.retrieval.block_index import BLOCK_INDEX_ARTIFACT  # noqa: E402
from api.infrastructure.runtime.model_gate import evaluate_f2llm_candidate  # noqa: E402
from api.infrastructure.runtime.resource_manager import (  # noqa: E402
    ResourceManager,
    resource_state_dir,
)
from api.infrastructure.runtime.workload_storage import bundle_demand, training_demand  # noqa: E402
from nayose.config import (  # noqa: E402
    F2LLM_330M_MODEL_ID,
    F2LLM_MPS_BATCH_SIZE,
    F2LLM_MPS_DEVICE,
    F2LLM_MPS_PROCESS_WORKERS,
    F2LLM_MPS_TORCH_DTYPE,
    SUPPORTED_MODEL_FAMILIES,
    embedding_model_id,
    load_config,
    pipeline_model_family,
)

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
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=str(config.runtime.data_dir),
        help="matcher-visible snapshot (generation alias or generation dir)",
    )
    parser.add_argument("--models-dir", default=str(config.runtime.models_dir))
    parser.add_argument("--seed", type=int, default=config.training.seed)
    parser.add_argument(
        "--device",
        default=config.embedding.device,
        choices=["auto", "mps", "cpu", "cuda"],
    )
    parser.add_argument(
        "--pair-model",
        default=config.training.pair_model_backend,
        choices=["auto", "lightgbm", "sklearn"],
    )
    parser.add_argument(
        "--model-family",
        default=config.training.model_family,
        choices=list(SUPPORTED_MODEL_FAMILIES),
    )
    parser.add_argument(
        "--embedding-model",
        default=config.training.embedding_model,
        help="Hugging Face embedding model override; required explicitly for custom profiles",
    )
    parser.add_argument(
        "--exact-mode",
        default=config.runtime.exact_mode,
        choices=["hybrid", "fuzzy_only", "exact_only"],
    )
    parser.add_argument(
        "--baseline-run",
        default=config.release.baseline_run,
        help="baseline bundle for the F2LLM release gate; defaults to models/current",
    )
    parser.add_argument(
        "--min-top1-gain",
        type=float,
        default=config.release.min_top1_gain,
    )
    parser.add_argument(
        "--min-recall5-gain",
        type=float,
        default=config.release.min_recall5_gain,
    )
    parser.add_argument(
        "--min-hard-recall5-gain",
        type=float,
        default=config.release.min_hard_recall5_gain,
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


def _validate_locked_profile(args: argparse.Namespace) -> None:
    if args.model_family not in {"f2llm", "f2llm_330m"}:
        return
    if args.device != F2LLM_MPS_DEVICE:
        raise SystemExit(
            "FAIL: f2llm_330m is locked to Apple MPS; use --device mps "
            "(CPU/CUDA/auto fallback is disabled)"
        )
    if args.pair_model == "sklearn":
        raise SystemExit("FAIL: f2llm_330m is locked to LightGBM pair training")
    expected = embedding_model_id(args.model_family, args.embedding_model)
    if expected != F2LLM_330M_MODEL_ID:
        raise SystemExit(
            f"FAIL: f2llm_330m is locked to {F2LLM_330M_MODEL_ID}; "
            "custom embedding models are disabled"
        )


def _env_for(args: argparse.Namespace) -> dict[str, str]:
    """Build the headless environment, including the locked F2LLM profile."""
    _validate_locked_profile(args)
    config = load_config()
    locked = args.model_family in {"f2llm", "f2llm_330m"}
    manager = ResourceManager(resource_state_dir(Path(args.models_dir)))
    plan = manager.plan(
        workload="training",
        requested_device=args.device,
        backend=config.embedding.backend,
        locked_mps=locked,
        settings=config.performance,
        require_accelerator=False,
        storage_demand=training_demand(Path(args.data_dir)).demand,
    )
    embedding = embedding_model_id(args.model_family, args.embedding_model)
    snapshot = Path(args.data_dir).resolve()
    data_dir = (snapshot / "current").resolve() if (snapshot / "current").exists() else snapshot
    env = {
        **plan.env(),
        "NAYOSE_DATA_DIR": str(data_dir),
        "NAYOSE_OUTPUT_DIR": str(Path(args.models_dir).resolve()),
        "NAYOSE_SEED": str(args.seed),
        "NAYOSE_DEVICE": plan.device,
        "NAYOSE_PAIR_MODEL": args.pair_model,
        "NAYOSE_MODEL_FAMILY": pipeline_model_family(args.model_family),
        "NAYOSE_EXACT_MODE": args.exact_mode,
        "NAYOSE_REQUESTED_MODEL_FAMILY": args.model_family,
        "NAYOSE_UI": "0",
        "NAYOSE_STRICT_BACKENDS": "1"
        if locked
        else ("1" if config.training.strict_backends else "0"),
        "NAYOSE_INDEX_BACKEND": config.retrieval.index_backend,
        "NAYOSE_LOW_MEMORY_MODELS": "1",
        "NAYOSE_ALLOW_SVD_FALLBACK": "0"
        if locked
        else ("1" if config.training.allow_svd_dense_fallback else "0"),
        "PYTORCH_ENABLE_MPS_FALLBACK": "0" if locked else "0",
    }
    if locked:
        env.update(
            {
                "NAYOSE_DEVICE": F2LLM_MPS_DEVICE,
                "NAYOSE_PAIR_MODEL": "lightgbm",
                "NAYOSE_EMBEDDING_MODEL": F2LLM_330M_MODEL_ID,
                "NAYOSE_NEURAL_BATCH": str(F2LLM_MPS_BATCH_SIZE),
                "NAYOSE_DENSE_VIEW_BATCH": str(F2LLM_MPS_BATCH_SIZE),
                "NAYOSE_PROCESS_WORKERS": str(F2LLM_MPS_PROCESS_WORKERS),
                "NAYOSE_USE_DENSE": "1",
            }
        )
    if embedding is not None:
        env["NAYOSE_EMBEDDING_MODEL"] = embedding
    return env


def run_pipeline(args: argparse.Namespace, env: dict[str, str]) -> float:
    """Run the ML-heavy worker in an isolated child process."""
    print(
        f"executing api.training.worker "
        f"(data={env['NAYOSE_DATA_DIR']}, out={env['NAYOSE_OUTPUT_DIR']}, seed={args.seed})"
    )
    print(
        f"env: device={args.device} pair_model={args.pair_model} "
        f"model_family={args.model_family} exact_mode={args.exact_mode}"
    )
    started = time.monotonic()
    child_environment = {**os.environ, **env}
    manager = ResourceManager(resource_state_dir(Path(env["NAYOSE_OUTPUT_DIR"])))
    config = load_config()
    plan = manager.plan(
        workload="training",
        requested_device=env["NAYOSE_DEVICE"],
        backend=config.embedding.backend,
        locked_mps=args.model_family in {"f2llm", "f2llm_330m"},
        settings=config.performance,
        storage_demand=training_demand(Path(env["NAYOSE_DATA_DIR"])).demand,
    )
    with manager.session(plan) as session:
        process = subprocess.Popen(
            [sys.executable, "-m", "api.training.worker"],
            cwd=Path.cwd(),
            env=child_environment,
            start_new_session=True,
        )
        session.register_process(process.pid)
        returncode = process.wait()
        if returncode:
            raise subprocess.CalledProcessError(returncode, process.args)
    return time.monotonic() - started


def _materialize_with_resources(args: argparse.Namespace, environment: dict[str, str]) -> Path:
    config = load_config()
    manager = ResourceManager(resource_state_dir(Path(args.models_dir)))
    neural = embedding_model_id(args.model_family, getattr(args, "embedding_model", None))
    locked = args.model_family in {"f2llm", "f2llm_330m"}
    plan = manager.plan(
        workload="materialization",
        requested_device=args.device if neural else "cpu",
        backend=config.embedding.backend if neural else None,
        locked_mps=locked,
        settings=config.performance,
        storage_demand=bundle_demand("materialization").demand,
    )
    with manager.session(plan):
        manager.configure(plan, config.performance)
        return materialize_latest_runtime_artifacts(args, environment)


def materialize_latest_runtime_artifacts(
    args: argparse.Namespace, environment: dict[str, str] | None = None
) -> Path:
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
    previous = {key: os.environ.get(key) for key in environment} if environment else {}
    if environment:
        os.environ.update(environment)
    try:
        materialize_environment = dict(os.environ)
        if materialize_environment.get("NAYOSE_DEVICE", "mps") != F2LLM_MPS_DEVICE:
            materialize_environment["NAYOSE_DEVICE"] = F2LLM_MPS_DEVICE
        materialize_runtime_artifacts(
            runs[0], data, config_snapshot=load_config(materialize_environment)
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
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
    try:
        validate_model_for_data(run, data)
    except ValueError as exc:
        raise SystemExit(f"FAIL: bundle integrity validation: {exc}") from exc
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
    if getattr(args, "model_family", "off") in {"f2llm", "f2llm_330m"}:
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
        if backends.get("pair_model") != "lightgbm":
            raise SystemExit("FAIL: 330M profile is locked to LightGBM pair training")
        policy = manifest.get("runtime_policy", {})
        expected_policy = {
            "neural_device": F2LLM_MPS_DEVICE,
            "torch_dtype": F2LLM_MPS_TORCH_DTYPE,
            "neural_batch_size": F2LLM_MPS_BATCH_SIZE,
            "process_workers": F2LLM_MPS_PROCESS_WORKERS,
            "pytorch_mps_fallback": "0",
        }
        if any(policy.get(key) != value for key, value in expected_policy.items()):
            raise SystemExit(
                "FAIL: 330M profile runtime policy is not the locked MPS configuration"
            )
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
    if args.model_family not in {"f2llm", "f2llm_330m"}:
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
    configured = Path(args.data_dir).resolve()
    explicit_generation = (configured / "generation_manifest.json").is_file()
    if explicit_generation:
        generation = configured
        if generation.parent.name != "generations":
            raise ValueError("data generation must live under a generations directory")
        data_root = generation.parent.parent
    else:
        data_root = configured
        current = data_root / "current"
        if not current.exists():
            raise ValueError("current data generation is unavailable")
        generation = current.resolve()
    fingerprint = validate_model_for_data(run, generation if explicit_generation else data_root)
    release = ReleasePublisher(ReleaseCatalog(data_root, models)).publish(generation.name, run.name)
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
    print(f"promoted {run.name} as {release.release_id} (fingerprint {fingerprint[:12]})")


def main() -> int:
    args = parse_args()
    snapshot = Path(args.data_dir).resolve()
    data = (snapshot / "current").resolve() if (snapshot / "current").exists() else snapshot
    if not (data / "generation_manifest.json").exists():
        raise SystemExit(
            f"data dir {data} has no generation_manifest.json — run nayose data generate first"
        )
    env = _env_for(args)
    elapsed = run_pipeline(args, env)
    print(f"training finished in {elapsed / 60:.1f} min")
    if args.no_verify:
        print("bundle verification skipped; leaving models/current unchanged")
        return 0
    _materialize_with_resources(args, env)
    run = verify_bundle(args)
    verify_performance(args, run)
    promote_bundle(args, run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
