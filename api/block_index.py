"""Build and persist the exact-match retrieval indexes used by inference."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from api.normalize import (
    normalize_address,
    normalize_dob,
    normalize_email,
    normalize_kana,
    normalize_name,
    normalize_phone,
    normalize_postal,
)

BLOCK_INDEX_ARTIFACT = "block_indexes.npz"
BLOCK_INDEX_SCHEMA_VERSION = 1
BLOCK_SPECS: Mapping[str, tuple[str, ...]] = {
    "exact_email": ("email",),
    "exact_phone": ("phone",),
    "name_dob": ("name_kanji", "date_birth"),
    "kana_dob": ("name_kana", "date_birth"),
    "signature_postal": ("name_kanji", "postal_code"),
    "kana_postal": ("name_kana", "postal_code"),
    "name_address": ("name_kanji", "address"),
    "phone_suffix_name": ("phone", "name_kanji"),
}
BlockIndex = dict[str, dict[str, np.ndarray]]


def data_fingerprint(frame: pd.DataFrame) -> str:
    """Return the serving fingerprint for a reference frame."""
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).values.tobytes()
    ).hexdigest()


def _raw_values(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series("", index=frame.index, dtype=object)
    return frame[column].map(str)


def _normalized_values(frame: pd.DataFrame) -> dict[str, pd.Series]:
    raw = {
        column: _raw_values(frame, column)
        for column in {
            "name_kanji",
            "name_kana",
            "phone",
            "date_birth",
            "postal_code",
            "address",
            "email",
        }
    }
    return {
        "name_kanji": raw["name_kanji"].map(normalize_name),
        "name_kana": raw["name_kana"].map(normalize_kana),
        "phone": raw["phone"].map(normalize_phone),
        "date_birth": raw["date_birth"].map(normalize_dob),
        "postal_code": raw["postal_code"].map(normalize_postal),
        "address": raw["address"].map(normalize_address),
        "email": raw["email"].map(normalize_email),
    }


def _group_keys(keys: pd.Series, valid: pd.Series | None = None) -> dict[str, np.ndarray]:
    valid = keys.ne("") if valid is None else valid
    if not bool(valid.any()):
        return {}
    positions = np.flatnonzero(valid.to_numpy())
    grouped = (
        pd.DataFrame({"key": keys[valid].to_numpy(), "position": positions})
        .groupby("key", sort=False, observed=True)["position"]
        .agg(list)
    )
    return {str(key): np.asarray(values, dtype=np.int32) for key, values in grouped.items()}


def _build_block_indexes_pandas(normalized: dict[str, pd.Series]) -> BlockIndex:
    indexes: BlockIndex = {}
    for block_name, columns in BLOCK_SPECS.items():
        values = [normalized[column] for column in columns]
        if block_name == "phone_suffix_name":
            values[0] = values[0].str[-8:]
        valid = values[0].ne("")
        keys = values[0]
        for value in values[1:]:
            valid &= value.ne("")
            keys = keys + "¦" + value
        indexes[block_name] = _group_keys(keys, valid)
    return indexes


def _build_block_indexes_polars(normalized: dict[str, pd.Series]) -> BlockIndex:
    try:
        import polars as pl
    except ImportError as exc:
        raise RuntimeError("NAYOSE_INDEX_BACKEND=polars requires the performance extra") from exc
    frame = pl.from_pandas(pd.DataFrame(normalized), include_index=False)
    indexes: BlockIndex = {}
    for block_name, columns in BLOCK_SPECS.items():
        working = frame
        key_columns = list(columns)
        if block_name == "phone_suffix_name":
            working = working.with_columns(pl.col("phone").str.slice(-8).alias("phone_suffix"))
            key_columns[0] = "phone_suffix"
        key_expression = pl.concat_str(
            [pl.col(column) for column in key_columns], separator="¦"
        ).alias("key")
        keyed = working.select([*key_columns, key_expression]).with_row_index("position")
        grouped = (
            keyed.filter(pl.all_horizontal([pl.col(column) != "" for column in key_columns]))
            .group_by("key", maintain_order=True)
            .agg(pl.col("position"))
        )
        indexes[block_name] = {
            str(key): np.asarray(positions, dtype=np.int32)
            for key, positions in grouped.iter_rows()
        }
    return indexes


def build_block_indexes(reference: pd.DataFrame, backend: str = "polars") -> BlockIndex:
    """Build exact indexes using the selected bulk dataframe backend."""
    if backend not in {"pandas", "polars"}:
        raise ValueError(f"unsupported block-index backend {backend!r}")
    normalized = _normalized_values(reference)
    if backend == "polars":
        return _build_block_indexes_polars(normalized)
    return _build_block_indexes_pandas(normalized)


def _artifact_arrays(
    indexes: BlockIndex, fingerprint: str, row_count: int
) -> dict[str, np.ndarray]:
    payload: dict[str, np.ndarray] = {
        "schema_version": np.asarray([BLOCK_INDEX_SCHEMA_VERSION], dtype=np.int16),
        "row_count": np.asarray([row_count], dtype=np.int64),
        "data_fingerprint": np.asarray([fingerprint]),
    }
    for block_name in BLOCK_SPECS:
        groups = indexes.get(block_name, {})
        keys = list(groups)
        offsets: np.ndarray = np.zeros(len(keys) + 1, dtype=np.int64)
        arrays: list[np.ndarray] = []
        for position, key in enumerate(keys, start=1):
            values = np.asarray(groups[key], dtype=np.int32)
            if values.ndim != 1:
                raise ValueError(f"block {block_name!r} key {key!r} is not one-dimensional")
            if len(values) and (values.min() < 0 or values.max() >= row_count):
                raise ValueError(f"block {block_name!r} contains an invalid reference row")
            arrays.append(values)
            offsets[position] = offsets[position - 1] + len(values)
        payload[f"{block_name}_keys"] = np.asarray(keys, dtype=np.str_)
        payload[f"{block_name}_offsets"] = offsets
        payload[f"{block_name}_values"] = (
            np.concatenate(arrays) if arrays else np.empty(0, dtype=np.int32)
        )
    return payload


def save_block_indexes(
    path: Path, indexes: BlockIndex, *, fingerprint: str, row_count: int
) -> None:
    """Write a CPU-neutral, non-pickle block-index artifact atomically."""
    if row_count < 0 or not fingerprint:
        raise ValueError("row_count and fingerprint are required")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _artifact_arrays(indexes, fingerprint, row_count)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("wb") as stream:
            np.savez(stream, **payload)  # type: ignore[arg-type]
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_scalar(archive: Mapping[str, np.ndarray], name: str) -> str | int:
    try:
        value = np.asarray(archive[name]).reshape(-1)
        if len(value) != 1:
            raise RuntimeError(f"block-index metadata {name!r} is malformed")
        return value[0].item()
    except KeyError as exc:
        raise RuntimeError(f"block-index artifact is missing {name!r}") from exc


def load_block_indexes(
    path: Path, *, expected_fingerprint: str, expected_row_count: int
) -> BlockIndex:
    """Load and validate a block-index artifact without executing pickle code."""
    if not path.is_file():
        raise FileNotFoundError(path)
    with np.load(path, allow_pickle=False) as archive:
        schema_version = int(_read_scalar(archive, "schema_version"))
        if schema_version != BLOCK_INDEX_SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported block-index schema {schema_version}; "
                f"expected {BLOCK_INDEX_SCHEMA_VERSION}"
            )
        fingerprint = str(_read_scalar(archive, "data_fingerprint"))
        if fingerprint != expected_fingerprint:
            raise RuntimeError(
                "block-index artifact fingerprint does not match the reference fingerprint"
            )
        row_count = int(_read_scalar(archive, "row_count"))
        if row_count != expected_row_count:
            raise RuntimeError(
                f"block-index artifact row count {row_count} does not match reference row count "
                f"{expected_row_count}"
            )
        indexes: BlockIndex = {}
        for block_name in BLOCK_SPECS:
            try:
                keys = np.asarray(archive[f"{block_name}_keys"])
                offsets = np.asarray(archive[f"{block_name}_offsets"], dtype=np.int64)
                values = np.asarray(archive[f"{block_name}_values"], dtype=np.int32)
            except KeyError as exc:
                raise RuntimeError(f"block-index artifact is missing block {block_name!r}") from exc
            if (
                offsets.ndim != 1
                or len(offsets) != len(keys) + 1
                or len(offsets) == 0
                or offsets[0] != 0
                or offsets[-1] != len(values)
                or np.any(offsets[1:] < offsets[:-1])
            ):
                raise RuntimeError(f"block-index artifact block {block_name!r} has invalid offsets")
            if len(values) and (values.min() < 0 or values.max() >= row_count):
                raise RuntimeError(f"block-index artifact block {block_name!r} has invalid rows")
            indexes[block_name] = {
                str(key): values[start:stop].copy()
                for key, start, stop in zip(keys, offsets[:-1], offsets[1:], strict=True)
            }
    return indexes
