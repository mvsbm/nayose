"""Fast, server-owned reads for immutable dataset generations."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from fastapi import HTTPException

from api.domain.evidence import VISIBLE_FIELDS
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.read_model.dataset_query import DatasetQueryBuilder, DatasetTableDescriptor


@lru_cache(maxsize=128)
def _visible_columns(path: str, allowed: tuple[str, ...]) -> tuple[str, ...]:
    con = duckdb.connect()
    try:
        available = {
            str(row[0])
            for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{path}'::VARCHAR)"
            ).fetchall()
        }
    finally:
        con.close()
    return tuple(column for column in allowed if column in available)


class DatasetService:
    TABLES: dict[str, DatasetTableDescriptor] = {
        "reference": DatasetTableDescriptor(
            "reference",
            "reference_records.parquet",
            "Customer observations",
            "Observed customer data from reference documents.",
            tuple(VISIBLE_FIELDS),
            "record_id",
            "observations",
        ),
        "queries": DatasetTableDescriptor(
            "queries",
            "incoming_queries.parquet",
            "Incoming matching cases",
            "Incoming cases used to test identity resolution.",
            ("query_id", *[field for field in VISIBLE_FIELDS if field != "record_id"]),
            "query_id",
            "queries",
        ),
        "agencies": DatasetTableDescriptor(
            "agencies",
            "agencies.parquet",
            "Source offices and channels",
            "Source offices, channels, and document quality factors.",
            (
                "agency_id",
                "channel",
                "missing_factor",
                "ocr_factor",
                "format_factor",
                "shared_phone",
                "shared_email",
            ),
            "agency_id",
        ),
    }

    def __init__(self, root: Path | str, stats: Stats | None = None):
        self.root = Path(root).resolve()
        self._stats = stats

    @staticmethod
    def _friendly_name(generation: Path, manifest: dict[str, Any], current: bool) -> dict[str, str]:
        config = manifest.get("config", {})
        counts = manifest.get("counts", {})
        entities = counts.get("parties") or counts.get("entities")
        observations = counts.get("observations")
        size = f"{int(entities):,} entities" if entities is not None else "Entity dataset"
        rows = f"{int(observations):,} observations" if observations is not None else "observations"
        snapshot = str(config.get("snapshot_date", ""))
        difficulty = str(config.get("difficulty", "standard"))
        current_label = " · Current" if current else ""
        label_en = f"{size} · {rows} · {snapshot or 'undated'}{current_label}"
        label_ja = (
            f"{int(entities):,} エンティティ · {int(observations):,} 観測 · "
            f"{snapshot or '日付なし'}"
            if entities is not None and observations is not None
            else label_en
        )
        description_en = (
            f"{difficulty.title()} generation; query date {config.get('query_date', '—')}; "
            f"created {manifest.get('created_at_utc', '—')}. ID: {generation.name}"
        )
        description_ja = (
            f"{difficulty} 設定。入力日 {config.get('query_date', '—')}、"
            f"生成日時 {manifest.get('created_at_utc', '—')}。ID: {generation.name}"
        )
        return {
            "label_en": label_en,
            "label_ja": label_ja,
            "description_en": description_en,
            "description_ja": description_ja,
        }

    def _generation(self, generation_id: str) -> Path:
        base = (self.root / "generations").resolve()
        candidate = (base / generation_id).resolve()
        if base not in candidate.parents or not candidate.is_dir():
            raise HTTPException(404, f"unknown generation {generation_id!r}")
        return candidate

    def _manifest(self, generation: Path) -> dict[str, Any]:
        path = generation / "generation_manifest.json"
        if not path.exists():
            raise HTTPException(404, "generation_manifest_missing")
        return json.loads(path.read_text(encoding="utf-8"))

    def generations(self) -> list[dict[str, Any]]:
        generations = self.root / "generations"
        current = (self.root / "current").resolve() if (self.root / "current").exists() else None
        if not generations.is_dir():
            return []
        rows = []
        for generation in sorted(generations.iterdir(), key=lambda path: path.name, reverse=True):
            if not generation.is_dir() or not generation.name.startswith("gen_"):
                continue
            manifest = self._manifest(generation)
            config = manifest.get("config", {})
            is_current = bool(current and generation.resolve() == current)
            rows.append(
                {
                    "id": generation.name,
                    "current": is_current,
                    "created_at": manifest.get("created_at_utc", ""),
                    "fingerprint": str(manifest.get("data_fingerprint", ""))[:12],
                    "snapshot_date": config.get("snapshot_date", ""),
                    "query_date": config.get("query_date", ""),
                    "counts": manifest.get("counts", {}),
                    **self._friendly_name(generation, manifest, is_current),
                }
            )
        return rows

    def _columns(self, generation: Path, table: str) -> list[str]:
        descriptor = self.TABLES[table]
        path = generation / descriptor.filename
        if not path.exists():
            raise HTTPException(404, f"dataset_table_missing:{table}")
        columns = list(_visible_columns(str(path), descriptor.visible_columns))
        if not columns or descriptor.cursor_column not in columns:
            raise HTTPException(500, f"dataset_table_schema_invalid:{table}")
        return columns

    def summary(self, generation_id: str) -> dict[str, Any]:
        generation = self._generation(generation_id)
        manifest = self._manifest(generation)
        current = (self.root / "current").resolve() if (self.root / "current").exists() else None
        tables = []
        with self._duckdb("duckdb.dataset_summary") as con:
            for table, descriptor in self.TABLES.items():
                path = generation / descriptor.filename
                if not path.exists():
                    continue
                count_row = con.execute(
                    f"SELECT count(*) FROM read_parquet('{path}'::VARCHAR)"
                ).fetchone()
                rows = int(count_row[0]) if count_row else 0
                tables.append(
                    {
                        "id": descriptor.id,
                        "label": descriptor.label,
                        "description": descriptor.description,
                        "rows": rows,
                        "columns": self._columns(generation, table),
                        "cursor_column": descriptor.cursor_column,
                    }
                )
        is_current = bool(current and generation.resolve() == current)
        return {
            "id": generation.name,
            "current": is_current,
            "created_at": manifest.get("created_at_utc", ""),
            "fingerprint": str(manifest.get("data_fingerprint", ""))[:12],
            "config": manifest.get("config", {}),
            "counts": manifest.get("counts", {}),
            "tables": tables,
            **self._friendly_name(generation, manifest, is_current),
        }

    @staticmethod
    def _filters(raw: dict[str, str] | None, columns: list[str]) -> dict[str, str]:
        if not raw:
            return {}
        if len(raw) > 12:
            raise HTTPException(422, "too_many_column_filters")
        allowed = set(columns)
        result: dict[str, str] = {}
        for column, value in raw.items():
            if column not in allowed:
                raise HTTPException(422, f"unknown dataset column {column!r}")
            text = str(value).strip()
            if len(text) > 100:
                raise HTTPException(422, f"dataset filter too long:{column}")
            if text:
                result[column] = text
        return result

    def _known_count(self, manifest: dict[str, Any], table: str) -> int | None:
        key = self.TABLES[table].manifest_count_key
        value = manifest.get("counts", {}).get(key) if key else None
        return int(value) if value is not None else None

    @contextmanager
    def _duckdb(self, name: str) -> Iterator[Any]:
        started = time.perf_counter()
        try:
            connection = duckdb.connect()
            try:
                yield connection
            finally:
                connection.close()
        except BaseException:
            self._record(name, started, error=True)
            raise
        else:
            self._record(name, started)

    def _record(self, name: str, started: float, *, error: bool = False) -> None:
        if self._stats is not None:
            self._stats.record(name, time.perf_counter() - started, error=error)

    def records(
        self,
        generation_id: str,
        table: str,
        query: str,
        offset: int,
        limit: int,
        column_filters: dict[str, str] | None = None,
        include_total: bool = True,
        after: str | None = None,
    ) -> dict[str, Any]:
        if table not in self.TABLES:
            raise HTTPException(422, f"unknown dataset table {table!r}")
        generation = self._generation(generation_id)
        manifest = self._manifest(generation)
        descriptor = self.TABLES[table]
        path = generation / descriptor.filename
        columns = self._columns(generation, table)
        filters = self._filters(column_filters, columns)
        if after is not None and len(after) > 200:
            raise HTTPException(422, "dataset_cursor_too_long")
        source = f"read_parquet('{str(path).replace(chr(39), chr(39) * 2)}'::VARCHAR)"
        builder = DatasetQueryBuilder(
            source,
            tuple(columns),
            descriptor.cursor_column,
            query.strip(),
            filters,
        )
        page_limit = min(limit, 100)
        page_offset = max(offset, 0)
        has_predicates = bool(query.strip() or filters)
        known_total = self._known_count(manifest, table)
        total_exact = after is None and (
            include_total or (not has_predicates and known_total is not None)
        )
        with self._duckdb("duckdb.dataset_page") as con:
            if total_exact and known_total is not None and not has_predicates:
                total = known_total
            elif total_exact:
                count_sql, count_params = builder.count()
                count_row = con.execute(count_sql, count_params).fetchone()
                total = int(count_row[0]) if count_row else 0
            else:
                total = -1
            fetch_limit = page_limit + 1 if not total_exact else page_limit
            page = builder.page(fetch_limit, after=after, offset=page_offset)
            frame = con.execute(page.sql, page.params).fetchdf()
        has_more = len(frame) > page_limit if not total_exact else page_offset + len(frame) < total
        if len(frame) > page_limit:
            frame = frame.iloc[:page_limit]
        next_cursor = None
        if has_more and len(frame):
            next_cursor = str(frame.iloc[-1][page.cursor_column])
        return {
            "table": table,
            "columns": columns,
            "rows": frame.where(pd.notna(frame), None).to_dict("records"),
            "total": total,
            "total_exact": total_exact,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "cursor_column": descriptor.cursor_column,
            "filters": filters,
            "offset": page_offset,
            "limit": page_limit,
        }
