"""Manifest-driven adapter for public CSV record-linkage benchmarks."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from api.benchmarking.contracts import (
    HIDDEN_FIELDS,
    VISIBLE_FIELDS,
    BenchmarkDataset,
    BenchmarkEntity,
    BenchmarkQuery,
    canonical_split,
)


class CsvBenchmarkAdapter:
    """Load a benchmark without knowing its source-specific column names.

    The directory must contain ``benchmark.json``.  Its ``columns`` object maps
    canonical names to source column names, for example ``{"entity_id":
    "rec_id", "name_kanji": "surname"}``.  The adapter only copies the
    approved visible fields into the domain contract.
    """

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    def load(self, *, require_all_splits: bool = True) -> BenchmarkDataset:
        manifest = self._read_manifest()
        columns = self._columns(manifest)
        entities = tuple(self._entities(self._read_rows(manifest, "entities"), columns))
        queries = tuple(self._queries(self._read_rows(manifest, "queries"), columns))
        dataset = BenchmarkDataset(
            name=self._required_text(manifest, "name"),
            version=self._required_text(manifest, "version"),
            license=self._required_text(manifest, "license"),
            entities=entities,
            queries=queries,
            metadata={str(k): str(v) for k, v in manifest.get("metadata", {}).items()},
        )
        dataset.validate(require_all_splits=require_all_splits)
        return dataset

    def _read_manifest(self) -> dict[str, Any]:
        path = self._safe_path("benchmark.json")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid benchmark manifest: {path}") from exc
        if not isinstance(value, dict):
            raise ValueError("benchmark manifest must be a JSON object")
        return value

    def _read_rows(self, manifest: dict[str, Any], key: str) -> list[dict[str, str]]:
        filename = manifest.get(f"{key}_file")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"benchmark manifest requires {key}_file")
        path = self._safe_path(filename)
        expected_hash = manifest.get(f"{key}_sha256")
        if expected_hash and _sha256(path) != str(expected_hash).lower():
            raise ValueError(f"benchmark checksum mismatch: {path.name}")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                header = [column.strip() for column in next(reader)]
            except StopIteration as exc:
                raise ValueError(f"benchmark CSV has no header: {path.name}") from exc
            if not all(header) or len(header) != len(set(header)):
                raise ValueError(f"benchmark CSV header is invalid: {path.name}")
            rows = [
                {
                    header[index]: str(value or "").strip()
                    for index, value in enumerate(values[: len(header)])
                }
                for values in reader
            ]
        return rows

    @staticmethod
    def _columns(manifest: dict[str, Any]) -> dict[str, str]:
        raw = manifest.get("columns", {})
        if not isinstance(raw, dict):
            raise ValueError("benchmark columns must be an object")
        columns = {str(key).strip(): str(value).strip() for key, value in raw.items()}
        leaked_sources = set(columns.values()) & (HIDDEN_FIELDS - {"split"})
        if leaked_sources:
            raise ValueError(
                f"hidden benchmark source columns are not allowed: {sorted(leaked_sources)}"
            )
        required = {"entity_id", "query_id", "target_entity_id", "split"}
        missing = sorted(required - set(columns))
        if missing:
            raise ValueError(f"benchmark columns missing: {missing}")
        return columns

    @staticmethod
    def _entities(rows: list[dict[str, str]], columns: dict[str, str]):
        source_id = columns["entity_id"]
        for row in rows:
            entity_id = row.get(source_id, "").strip()
            if not entity_id:
                raise ValueError("benchmark entity ID cannot be empty")
            yield BenchmarkEntity(entity_id, _visible_fields(row, columns))

    @staticmethod
    def _queries(rows: list[dict[str, str]], columns: dict[str, str]):
        for row in rows:
            values = {key: row.get(source, "").strip() for key, source in columns.items()}
            query_id = values.get("query_id", "")
            target = values.get("target_entity_id", "")
            if not query_id or not target:
                raise ValueError("benchmark query and target IDs cannot be empty")
            target_ids = _id_set(values.get("target_entity_ids", ""))
            if target:
                target_ids = frozenset((*target_ids, target))
            yield BenchmarkQuery(
                query_id=query_id,
                target_entity_id=target or None,
                split=canonical_split(values.get("split", "")),
                fields=_visible_fields(row, columns),
                slice_name=values.get("slice", "overall") or "overall",
                expected_action=values.get("expected_action", "EXISTING") or "EXISTING",
                target_entity_ids=target_ids,
                related_entity_ids=_id_set(values.get("related_entity_ids", "")),
            )

    def _safe_path(self, filename: str) -> Path:
        candidate = (self.root / filename).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"benchmark file escapes dataset directory: {filename}")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    @staticmethod
    def _required_text(manifest: dict[str, Any], key: str) -> str:
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"benchmark manifest requires {key}")
        return value.strip()


def _visible_fields(row: dict[str, str], columns: dict[str, str]) -> dict[str, str]:
    return {
        field: row.get(columns[field], "").strip()
        for field in VISIBLE_FIELDS
        if field in columns and row.get(columns[field], "").strip()
    }


def _id_set(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.replace(",", "|").split("|") if item.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
