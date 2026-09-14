"""Fingerprint-bound embedding caches for immutable generation products."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

EMBEDDING_PREPROCESSING_VERSION = "normalize-retrieval-v1"
EMBEDDING_FIELD_SCHEMA_VERSION = "retrieval-text-v1"


def model_revision(model: Any) -> str:
    """Return a configured or locally loaded model revision without network calls."""
    configured = os.getenv("NAYOSE_EMBEDDING_REVISION", "").strip()
    if configured:
        return configured
    modules = getattr(model, "_modules", {}).values()
    for module in modules:
        config = getattr(getattr(module, "auto_model", None), "config", None)
        revision = getattr(config, "_commit_hash", None)
        if revision:
            return str(revision)
    return "unresolved"


def text_fingerprint(texts: Sequence[str]) -> str:
    """Hash ordered text values without exposing their contents in metadata."""
    digest = hashlib.sha256()
    for value in texts:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def cache_paths(root: Path, model_id: str, kind: str) -> tuple[Path, Path]:
    """Return deterministic vector and metadata paths for one model/kind pair."""
    if not kind or Path(kind).name != kind:
        raise ValueError("cache kind must be a single path component")
    slug = f"{Path(model_id).name}_{hashlib.sha256(model_id.encode()).hexdigest()[:12]}"
    return root / f"{kind}_embeddings_{slug}.npy", root / f"{kind}_embeddings_{slug}_meta.json"


def load(vector_path: Path, metadata_path: Path, expected: Mapping[str, Any]) -> np.ndarray | None:
    """Load a cache only when all supplied provenance fields match exactly."""
    if not vector_path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if any(metadata.get(key) != value for key, value in expected.items()):
            return None
        values = np.load(vector_path, mmap_mode="r", allow_pickle=False)
        shape = metadata.get("shape")
        if (
            values.ndim != 2
            or shape != list(values.shape)
            or metadata.get("dtype") != str(values.dtype)
        ):
            return None
        if not np.isfinite(values).all():
            return None
        return values
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save(
    vector_path: Path,
    metadata_path: Path,
    values: np.ndarray,
    metadata: Mapping[str, Any],
) -> None:
    """Atomically publish vectors and metadata after a complete finite write."""
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 2 or not np.isfinite(array).all():
        raise ValueError("embedding cache values must be a finite 2-D matrix")
    vector_path.parent.mkdir(parents=True, exist_ok=True)
    vector_tmp = vector_path.with_name(f".{vector_path.name}.tmp.{os.getpid()}")
    metadata_tmp = metadata_path.with_name(f".{metadata_path.name}.tmp.{os.getpid()}")
    payload = {**metadata, "shape": list(array.shape), "dtype": str(array.dtype)}
    try:
        with vector_tmp.open("wb") as handle:
            np.save(handle, array, allow_pickle=False)
        metadata_tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(vector_tmp, vector_path)
        os.replace(metadata_tmp, metadata_path)
    finally:
        vector_tmp.unlink(missing_ok=True)
        metadata_tmp.unlink(missing_ok=True)
