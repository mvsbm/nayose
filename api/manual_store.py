"""Operator-entered records backed by the operational store."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
from fastapi import HTTPException

from api.operational_store import OperationalStore


class ManualStore:
    """Own manual-record validation while SQLite owns durable storage."""

    def __init__(
        self,
        store_or_dir: OperationalStore | Path,
        data_root: Path,
        fields: tuple[str, ...],
    ):
        self.data_root = Path(data_root).resolve()
        self.fields = fields
        self.store = (
            store_or_dir
            if isinstance(store_or_dir, OperationalStore)
            else OperationalStore(Path(store_or_dir).resolve().parent / "operational.sqlite3")
        )

    def count(self) -> int:
        with self.store.read_connection() as connection:
            row = connection.execute("SELECT count(*) FROM manual_records").fetchone()
        return int(row[0]) if row else 0

    def rows(self) -> list[dict[str, Any]]:
        with self.store.read_connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM manual_records ORDER BY created_at ASC, record_id ASC"
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def _validate(self, record: dict[str, Any]) -> dict[str, str]:
        cleaned = {key: str(value).strip() for key, value in record.items() if key in self.fields}
        if not cleaned.get("name_kanji"):
            raise HTTPException(422, "name_kanji is required")
        for key in ("phone", "name_kanji", "name_kana", "postal_code"):
            cleaned.setdefault(key, "")
        for key in ("email", "date_birth", "address"):
            cleaned.setdefault(key, "")
        cleaned.setdefault("party_type", "person")
        cleaned.setdefault("name_romaji", "")
        return cleaned

    def _append(
        self, rows: list[dict[str, str]], connection: sqlite3.Connection | None = None
    ) -> list[str]:
        now = time.time()
        observed_at = time.strftime("%Y-%m-%d", time.gmtime())
        prepared = []
        for row in rows:
            record_id = f"M{time.time_ns():016d}{uuid4().hex[:8]}"
            payload = {
                **row,
                "record_id": record_id,
                "observed_at": observed_at,
                "channel": "manual",
            }
            prepared.append((record_id, json.dumps(payload, ensure_ascii=False), now))
        if connection is None:
            with self.store.transaction() as transaction:
                self._insert(transaction, prepared)
        else:
            self._insert(connection, prepared)
        return [row[0] for row in prepared]

    @staticmethod
    def _insert(connection: sqlite3.Connection, rows: list[tuple[str, str, float]]) -> None:
        connection.executemany(
            "INSERT INTO manual_records(record_id, payload_json, created_at) VALUES (?, ?, ?)",
            rows,
        )

    def register(self, record: dict[str, Any], connection: sqlite3.Connection | None = None) -> str:
        return self._append([self._validate(record)], connection)[0]

    def import_rows(
        self,
        rows: list[dict[str, Any]],
        dry_run: bool,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        if not rows or len(rows) > 500:
            raise HTTPException(422, "import must have 1..500 rows")
        cleaned: list[dict[str, str]] = []
        errors: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            try:
                cleaned.append(self._validate(row))
            except HTTPException as exc:
                errors.append({"index": index, "error": str(exc.detail)})

        duplicates = self._duplicate_indexes(cleaned)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "candidates": len(rows),
                "valid": len(cleaned),
                "errors": errors,
                "duplicates": duplicates,
            }
        if errors:
            raise HTTPException(422, f"import rows invalid: {errors[:5]}")
        self._append(cleaned, connection)
        return {"ok": True, "imported": len(cleaned), "duplicates": duplicates}

    def _duplicate_indexes(self, rows: list[dict[str, str]]) -> list[int]:
        corpus: set[str] = set()
        try:
            connection = duckdb.connect()
            try:
                reference = str(self.data_root / "current" / "reference_records.parquet").replace(
                    "'", "''"
                )
                query = f"select distinct phone from read_parquet('{reference}') where phone != ''"
                corpus = {
                    str(value[0]).replace("-", "").replace(" ", "")
                    for value in connection.execute(query).fetchall()
                }
            finally:
                connection.close()
        except (duckdb.Error, OSError, ValueError):
            corpus = set()
        return [
            index
            for index, row in enumerate(rows)
            if row["phone"] and row["phone"].replace("-", "").replace(" ", "") in corpus
        ]

    def undo(self, record_ids: list[str], connection: sqlite3.Connection | None = None) -> int:
        if not record_ids:
            return 0
        placeholders = ", ".join("?" for _ in record_ids)
        if connection is None:
            with self.store.transaction() as transaction:
                cursor = transaction.execute(
                    f"DELETE FROM manual_records WHERE record_id IN ({placeholders})", record_ids
                )
        else:
            cursor = connection.execute(
                f"DELETE FROM manual_records WHERE record_id IN ({placeholders})", record_ids
            )
        return cursor.rowcount
