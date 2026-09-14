"""Build fingerprint-bound serving artifacts for a trained model run."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from api.domain.fingerprint import data_fingerprint
from api.infrastructure.bundles.aliases import build_entity_aliases
from api.infrastructure.retrieval.block_index import (
    BLOCK_INDEX_ARTIFACT,
    BLOCK_INDEX_SCHEMA_VERSION,
    build_block_indexes,
    save_block_indexes,
)
from api.infrastructure.retrieval.dense_index import (
    DENSE_INDEX_ARTIFACT,
    build_dense_index,
    save_dense_index,
    target_platform,
    usearch_version,
)
from api.infrastructure.retrieval.dense_views import build_dense_view_artifacts
from api.infrastructure.retrieval.sparse_retrieval import (
    DEFAULT_INVERTED_MEMORY_BUDGET,
    DEFAULT_QUERY_FEATURE_CAP,
    SPARSE_INVERTED_ARTIFACT,
    inverted_within_budget,
)


def _materialize_sparse_inverted_artifact(
    run: Path, row_count: int, data_fingerprint_value: str
) -> dict[str, object] | None:
    """Persist the inverted sparse representation when its copy fits the budget."""
    source = next(
        (
            run / name
            for name in (
                "reference_sparse_inverted.npz",
                "reference_tfidf.npz",
                "reference_sparse_matrix.npz",
            )
            if (run / name).is_file()
        ),
        None,
    )
    if source is None:
        return None
    try:
        from scipy.sparse import load_npz, save_npz

        matrix = load_npz(source)
        if not inverted_within_budget(matrix, DEFAULT_INVERTED_MEMORY_BUDGET):
            print("WARN: sparse inverted artifact skipped (memory budget)")
            return None
        inverted = matrix.tocsc(copy=False)
        output = run / SPARSE_INVERTED_ARTIFACT
        temporary = output.with_name(f".{output.stem}.tmp.{os.getpid()}.npz")
        try:
            save_npz(temporary, inverted, compressed=True)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    except (ImportError, MemoryError, OSError, ValueError) as exc:
        print(f"WARN: sparse inverted artifact skipped ({type(exc).__name__})")
        return None
    return {
        "path": SPARSE_INVERTED_ARTIFACT,
        "format": "scipy_npz_csc",
        "sha256": _sha256(output),
        "data_fingerprint": data_fingerprint_value,
        "row_count": row_count,
        "dimensions": int(inverted.shape[1]),
        "query_feature_cap": DEFAULT_QUERY_FEATURE_CAP,
        "source": source.name,
    }


def _materialize_dense_artifact(run: Path) -> dict[str, object] | None:
    """Build the optional target-platform ANN artifact."""
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
        import numpy as np

        embeddings = np.load(embedding_path, mmap_mode="r")
        index = build_dense_index(embeddings)
        save_dense_index(run / DENSE_INDEX_ARTIFACT, index)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"WARN: optional dense ANN artifact skipped ({type(exc).__name__})")
        return None
    return {
        "path": DENSE_INDEX_ARTIFACT,
        "format": "usearch",
        "sha256": _sha256(run / DENSE_INDEX_ARTIFACT),
        "row_count": int(embeddings.shape[0]),
        "dimensions": int(embeddings.shape[1]),
        "source": embedding_path.name,
        "platform": target_platform(),
        "usearch_version": usearch_version(),
    }


def materialize_runtime_artifacts(
    run: Path, data: Path, *, index_backend: str | None = None
) -> None:
    """Finalize serving artifacts after model training completes."""
    reference = pd.read_parquet(data / "reference_records.parquet")
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
    alias_artifact["sha256"] = _sha256(run / "entity_aliases.parquet")
    indexes = build_block_indexes(reference, backend=selected_backend)
    save_block_indexes(
        run / BLOCK_INDEX_ARTIFACT,
        indexes,
        fingerprint=actual_fingerprint,
        row_count=len(reference),
    )
    artifact = {
        "path": BLOCK_INDEX_ARTIFACT,
        "format": "npz",
        "compression": "deflate",
        "schema_version": BLOCK_INDEX_SCHEMA_VERSION,
        "data_fingerprint": actual_fingerprint,
        "row_count": len(reference),
        "sha256": _sha256(run / BLOCK_INDEX_ARTIFACT),
        "blocks": {name: len(groups) for name, groups in indexes.items()},
        "backend": selected_backend,
    }
    runtime_artifacts = dict(manifest.get("runtime_artifacts", {}))
    runtime_artifacts["block_indexes"] = artifact
    sparse_inverted_artifact = _materialize_sparse_inverted_artifact(
        run, len(reference), actual_fingerprint
    )
    if sparse_inverted_artifact is not None:
        runtime_artifacts["sparse_inverted"] = sparse_inverted_artifact
    else:
        runtime_artifacts.pop("sparse_inverted", None)
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
                device=os.getenv("NAYOSE_DEVICE", "mps"),
                batch_size=int(
                    os.getenv("NAYOSE_DENSE_VIEW_BATCH", os.getenv("NAYOSE_NEURAL_BATCH", "32"))
                ),
                aliases=aliases,
            )
        )
    if dense_views:
        runtime_artifacts["dense_views"] = dense_views
    manifest["runtime_artifacts"] = runtime_artifacts
    generated_paths = {
        BLOCK_INDEX_ARTIFACT,
        SPARSE_INVERTED_ARTIFACT,
        DENSE_INDEX_ARTIFACT,
        "entity_aliases.parquet",
        *(descriptor["embedding_path"] for descriptor in dense_views.values()),
        *(descriptor["index_path"] for descriptor in dense_views.values()),
    }
    artifacts = [
        entry for entry in manifest.get("artifacts", []) if entry.get("path") not in generated_paths
    ]
    artifacts.append(artifact)
    if sparse_inverted_artifact is not None:
        artifacts.append(sparse_inverted_artifact)
    artifacts.append(alias_artifact)
    if dense_artifact is not None:
        artifacts.append(dense_artifact)
    for view, descriptor in dense_views.items():
        embedding_path = run / str(descriptor["embedding_path"])
        index_path = run / str(descriptor["index_path"])
        artifacts.extend(
            [
                {
                    "name": f"dense_embedding_{view}",
                    "path": str(descriptor["embedding_path"]),
                    "format": "npy",
                    "rows": _metadata_int(descriptor["row_count"], "row_count"),
                    "sha256": _sha256(embedding_path),
                },
                {
                    "name": f"dense_index_{view}",
                    "path": str(descriptor["index_path"]),
                    "format": "usearch",
                    "rows": _metadata_int(descriptor["row_count"], "row_count"),
                    "dimensions": _metadata_int(descriptor["dimensions"], "dimensions"),
                    "sha256": _sha256(index_path),
                },
            ]
        )
    manifest["artifacts"] = artifacts
    temporary = manifest_path.with_name(f".{manifest_path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, manifest_path)
    print(
        f"materialized {BLOCK_INDEX_ARTIFACT}: {len(reference):,} rows, "
        f"fingerprint {actual_fingerprint[:12]}"
    )


def _metadata_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"dense view {field} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"dense view {field} must be an integer") from exc
    else:
        raise ValueError(f"dense view {field} must be an integer")
    if parsed < 1:
        raise ValueError(f"dense view {field} must be positive")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
