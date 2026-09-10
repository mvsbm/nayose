"""Optional dense retrieval index persistence."""

from __future__ import annotations

import importlib.metadata
import os
import platform
from pathlib import Path
from typing import Any

import numpy as np

DENSE_INDEX_ARTIFACT = "dense_index.usearch"


def target_platform() -> str:
    machine = platform.machine().lower()
    normalized_machine = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }.get(machine, machine)
    return f"{platform.system().lower()}-{normalized_machine}"


def usearch_version() -> str:
    try:
        return importlib.metadata.version("usearch")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def build_dense_index(embeddings: np.ndarray) -> Any:
    """Build the optional ANN index from the portable embedding matrix."""
    from usearch.index import Index

    index = Index(ndim=embeddings.shape[1], metric="cos", dtype="f16")
    index.add(np.arange(len(embeddings), dtype=np.int64), embeddings)
    return index


def save_dense_index(path: Path, index: Any) -> None:
    """Persist an ANN index atomically; the embedding matrix remains canonical."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        index.save(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_dense_index(path: Path, *, expected_rows: int, expected_dimensions: int) -> Any:
    """Restore and validate an optional ANN cache."""
    from usearch.index import Index

    index = Index.restore(path)
    if index is None or index.size != expected_rows or index.ndim != expected_dimensions:
        raise RuntimeError("dense ANN artifact dimensions do not match the embedding matrix")
    return index
