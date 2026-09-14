"""Field-aware F2LLM dense views for candidate discovery."""

from __future__ import annotations

import gc
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from api.domain.normalize import normalize_record
from api.infrastructure.bundles.aliases import alias_texts
from api.infrastructure.retrieval.dense_index import (
    build_dense_index,
    load_dense_index,
    save_dense_index,
)
from api.infrastructure.runtime.embedding_cache import (
    EMBEDDING_FIELD_SCHEMA_VERSION,
    EMBEDDING_PREPROCESSING_VERSION,
    model_revision,
)
from api.infrastructure.runtime.embedding_config import F2LLM_MPS_BATCH_SIZE, is_f2llm_model
from api.infrastructure.runtime.performance import (
    configure_mps_runtime,
    configure_torch_threads,
    model_thread_budget,
    require_mps_device,
    resolve_device,
)

DENSE_VIEW_NAMES = ("name", "address", "alias")


def selected_dense_views(query: dict[str, Any], available: dict[str, Any]) -> tuple[str, ...]:
    """Select views from populated query families, never from a fixed fusion list."""
    normalized = query if any(key.startswith("n_") for key in query) else normalize_record(query)
    has_name = any(normalized.get(field, "") for field in ("n_name_loose", "n_kana", "n_romaji"))
    has_address = any(normalized.get(field, "") for field in ("n_postal", "n_address"))
    selected: list[str] = []
    if has_name and "name" in available:
        selected.append("name")
    if has_address and "address" in available:
        selected.append("address")
    if has_name and "alias" in available:
        selected.append("alias")
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class DenseView:
    name: str
    embeddings: np.ndarray
    index: Any
    row_indices: np.ndarray | None = None


def _value(record: dict[str, Any], normalized: str, raw: str) -> str:
    value = record.get(normalized)
    if value is None:
        value = record.get(raw, "")
    return "" if value is None or pd.isna(value) else str(value)


def dense_view_text(record: dict[str, Any], view: str) -> str:
    """Build the same labeled text shape for corpus and query records."""
    if view not in DENSE_VIEW_NAMES:
        raise ValueError(f"unsupported dense view {view!r}")
    normalized = record if any(key.startswith("n_") for key in record) else normalize_record(record)
    if view == "alias":
        values = (
            normalized.get("n_name_loose", ""),
            normalized.get("n_kana", ""),
            normalized.get("n_romaji", ""),
        )
        return " ".join(f"name={value}" for value in values if value)
    if view == "name":
        fields: tuple[tuple[str, str, str], ...] = (
            ("party_type", "party_type", "party_type"),
            ("name_kanji", "n_name_loose", "name_kanji"),
            ("name_kana", "n_kana", "name_kana"),
            ("name_romaji", "n_romaji", "name_romaji"),
        )
    else:
        fields = (
            ("party_type", "party_type", "party_type"),
            ("postal_code", "n_postal", "postal_code"),
            ("address", "n_address", "address"),
        )
    parts = []
    for label, normalized_field, raw_field in fields:
        value = _value(normalized, normalized_field, raw_field)
        if value:
            parts.append(f"{label}={value}")
    return " ".join(parts)


def dense_view_queries(record: dict[str, Any], view: str) -> tuple[str, ...]:
    """Return query texts with the same row shape as the selected corpus view."""
    normalized = record if any(key.startswith("n_") for key in record) else normalize_record(record)
    if view == "alias":
        values = (
            normalized.get("n_name_loose", ""),
            normalized.get("n_kana", ""),
            normalized.get("n_romaji", ""),
        )
        return tuple(dict.fromkeys(f"name={value}" for value in values if value))
    if view == "name" and not any(
        normalized.get(field, "") for field in ("n_name_loose", "n_kana", "n_romaji")
    ):
        return ()
    if view == "address" and not any(
        normalized.get(field, "") for field in ("n_postal", "n_address")
    ):
        return ()
    text = dense_view_text(record, view)
    return (text,) if text else ()


def dense_view_texts(
    reference: pd.DataFrame, view: str, aliases: pd.DataFrame | None = None
) -> list[str]:
    if view == "alias":
        if aliases is None:
            raise ValueError("alias dense view requires an alias table")
        return alias_texts(aliases)
    return [dense_view_text(row, view) for row in reference.to_dict("records")]


def _atomic_save_array(path: Path, values: np.ndarray) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.npy")
    try:
        np.save(temporary, values)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _iter_source_texts(source: pd.DataFrame, view: str):
    columns = list(source.columns)
    for values in source.itertuples(index=False, name=None):
        row = dict(zip(columns, values, strict=True))
        if view == "alias":
            yield f"name={row.get('normalized_alias', '')}"
        else:
            yield dense_view_text(row, view)


def _encode_to_cache(
    model: Any,
    source: pd.DataFrame,
    view: str,
    reference: pd.DataFrame,
    aliases: pd.DataFrame | None,
    output: Path,
    batch_size: int,
) -> tuple[int, int]:
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}.npy")
    matrix: np.memmap | None = None
    offset = 0
    try:
        texts = _iter_source_texts(source, view)
        chunk_size = 1024
        started = time.perf_counter()
        report_every = max(chunk_size, len(source) // 20)
        next_report = report_every
        while offset < len(source):
            batch = list(next(texts) for _ in range(min(chunk_size, len(source) - offset)))
            encoded = np.asarray(
                model.encode(
                    batch,
                    batch_size=min(batch_size, len(batch)),
                    show_progress_bar=False,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                ),
                dtype=np.float32,
            )
            if matrix is None:
                if encoded.ndim != 2 or encoded.shape[0] != len(batch):
                    raise RuntimeError(f"dense view {view} encoder returned an invalid shape")
                matrix = np.lib.format.open_memmap(
                    temporary,
                    mode="w+",
                    dtype=np.float32,
                    shape=(len(source), encoded.shape[1]),
                )
            matrix[offset : offset + len(batch)] = encoded
            offset += len(batch)
            if offset >= next_report or offset == len(source):
                elapsed = time.perf_counter() - started
                rate = offset / max(elapsed, 1e-9)
                eta = int((len(source) - offset) / max(rate, 1e-9))
                print(
                    f"  [dense:{view}] {offset:,}/{len(source):,} "
                    f"({100.0 * offset / len(source):.0f}%) "
                    f"eta {eta // 60:02d}:{eta % 60:02d}",
                    flush=True,
                )
                next_report += report_every
        if matrix is None:
            raise RuntimeError(f"dense view {view} has no source rows")
        matrix.flush()
        dimensions = int(matrix.shape[1])
        del matrix
        os.replace(temporary, output)
        return len(source), dimensions
    finally:
        temporary.unlink(missing_ok=True)


def _write_float16(path: Path, values: np.ndarray) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.npy")
    try:
        output = np.lib.format.open_memmap(
            temporary, mode="w+", dtype=np.float16, shape=values.shape
        )
        for start in range(0, len(values), 8192):
            output[start : start + 8192] = values[start : start + 8192]
        output.flush()
        del output
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _model(model_id: str, device: str) -> Any:
    import torch
    from sentence_transformers import SentenceTransformer

    configure_torch_threads(model_thread_budget())
    kwargs: dict[str, Any] = {"device": device}
    if device in {"mps", "cuda"}:
        kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
    return SentenceTransformer(model_id, **kwargs)


def _model_dimensions(model: Any) -> int:
    for name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        method = getattr(model, name, None)
        if callable(method):
            return int(method())
    return 0


def _cache_paths(cache_dir: Path, model_id: str, view: str) -> tuple[Path, Path]:
    suffix = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12]
    slug = f"{Path(model_id).name}_{suffix}"
    return (
        cache_dir / f"reference_embeddings_{slug}_{view}.npy",
        cache_dir / f"reference_embeddings_{slug}_{view}_meta.json",
    )


def build_dense_view_artifacts(
    reference: pd.DataFrame,
    cache_dir: Path,
    run_dir: Path,
    *,
    model_id: str,
    data_fingerprint: str,
    device: str,
    batch_size: int,
    aliases: pd.DataFrame | None = None,
) -> dict[str, dict[str, Any]]:
    """Build immutable name/address ANN artifacts with generation-local caching."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    selected_device = (
        require_mps_device(device) if is_f2llm_model(model_id) else resolve_device(device)
    )
    if is_f2llm_model(model_id):
        configure_mps_runtime()
        if batch_size != F2LLM_MPS_BATCH_SIZE:
            raise ValueError(f"F2LLM-v2-330M requires dense-view batch size {F2LLM_MPS_BATCH_SIZE}")
    artifacts: dict[str, dict[str, Any]] = {}
    model: Any = _model(model_id, selected_device)
    model_rev = model_revision(model)
    dimensions = _model_dimensions(model)
    torch_dtype = "float16" if selected_device in {"mps", "cuda"} else "float32"
    try:
        for view in DENSE_VIEW_NAMES:
            if view == "alias" and (aliases is None or aliases.empty):
                continue
            source = aliases if view == "alias" else reference
            assert source is not None
            cache_vec, cache_meta = _cache_paths(cache_dir, model_id, view)
            source_columns = (
                [column for column in ("record_index", "normalized_alias") if column in source]
                if view == "alias"
                else [
                    column
                    for column in (
                        "party_type",
                        "name_kanji",
                        "name_kana",
                        "name_romaji",
                        "postal_code",
                        "address",
                    )
                    if column in source
                ]
            )
            source_fingerprint = hashlib.sha256(
                pd.util.hash_pandas_object(source[source_columns], index=False).values.tobytes()
            ).hexdigest()
            expected_metadata = {
                "model_id": model_id,
                "model_revision": model_rev,
                "data_fingerprint": data_fingerprint,
                "source_fingerprint": source_fingerprint,
                "view": view,
                "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
                "field_schema_version": EMBEDDING_FIELD_SCHEMA_VERSION,
                "rows": len(source),
                "dimensions": dimensions,
                "device": selected_device,
                "torch_dtype": torch_dtype,
                "batch_size": batch_size,
            }
            embeddings: np.ndarray | None = None
            encoded_dimensions = dimensions
            if cache_vec.exists() and cache_meta.exists():
                metadata = json.loads(cache_meta.read_text(encoding="utf-8"))
                if all(metadata.get(key) == value for key, value in expected_metadata.items()):
                    candidate = np.load(cache_vec, mmap_mode="r")
                    if (
                        len(candidate.shape) == 2
                        and candidate.shape[0] == len(source)
                        and (dimensions == 0 or candidate.shape[1] == dimensions)
                    ):
                        embeddings = candidate
            if embeddings is None:
                row_count, encoded_dimensions = _encode_to_cache(
                    model,
                    source,
                    view,
                    reference,
                    aliases,
                    cache_vec,
                    batch_size,
                )
                expected_metadata["dimensions"] = encoded_dimensions
                cache_meta.write_text(
                    json.dumps(
                        {
                            **expected_metadata,
                            "rows": row_count,
                            "shape": [row_count, encoded_dimensions],
                            "dtype": "float32",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                embeddings = np.load(cache_vec, mmap_mode="r")
            output_embeddings = run_dir / f"reference_embeddings_{view}.f16.npy"
            _write_float16(output_embeddings, embeddings)
            index = build_dense_index(np.asarray(embeddings, dtype=np.float32))
            output_index = run_dir / f"dense_index_{view}.usearch"
            save_dense_index(output_index, index)
            artifacts[view] = {
                **expected_metadata,
                "view": view,
                "data_fingerprint": data_fingerprint,
                "embedding_path": output_embeddings.name,
                "index_path": output_index.name,
                "format": "usearch",
                "row_count": int(embeddings.shape[0]),
                "dimensions": int(embeddings.shape[1]),
                "reference_row_count": len(reference),
            }
            del index, embeddings
            gc.collect()
    finally:
        if model is not None:
            del model
        gc.collect()
    return artifacts


def load_dense_view_artifacts(
    run_dir: Path,
    manifest: dict[str, Any],
    expected_rows: int,
    expected_fingerprint: str | None = None,
) -> dict[str, DenseView]:
    runtime = manifest.get("runtime_artifacts", {}).get("dense_views", {})
    if not isinstance(runtime, dict):
        return {}
    loaded: dict[str, DenseView] = {}
    for view in DENSE_VIEW_NAMES:
        descriptor = runtime.get(view)
        if not isinstance(descriptor, dict):
            continue
        if expected_fingerprint and descriptor.get("data_fingerprint") != expected_fingerprint:
            raise RuntimeError(f"dense view {view} fingerprint does not match the reference")
        embedding_path = run_dir / str(descriptor.get("embedding_path", ""))
        index_path = run_dir / str(descriptor.get("index_path", ""))
        if not embedding_path.is_file() or not index_path.is_file():
            raise RuntimeError(f"dense view {view} is missing from the model bundle")
        embeddings = np.load(embedding_path, mmap_mode="r")
        row_indices: np.ndarray | None = None
        expected_index_rows = expected_rows
        if view == "alias":
            alias_path = run_dir / "entity_aliases.parquet"
            if not alias_path.is_file():
                raise RuntimeError("alias dense view is missing entity_aliases.parquet")
            aliases = pd.read_parquet(alias_path, columns=["record_index"])
            row_indices = aliases["record_index"].to_numpy(dtype=np.int32)
            expected_index_rows = len(aliases)
        if len(embeddings.shape) != 2 or embeddings.shape[0] != expected_index_rows:
            raise RuntimeError(f"dense view {view} embedding rows do not match its source")
        index = load_dense_index(
            index_path,
            expected_rows=expected_index_rows,
            expected_dimensions=embeddings.shape[1],
        )
        loaded[view] = DenseView(view, embeddings, index, row_indices)
    return loaded
