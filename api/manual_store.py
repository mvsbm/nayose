"""Transactional storage for operator-entered records."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import Any

import duckdb
import pandas as pd
from fastapi import HTTPException


class ManualStore:
    def __init__(self, manual_dir: Path, data_root: Path, fields: tuple[str, ...]):
        self.manual_dir = manual_dir.resolve()
        self.data_root = data_root.resolve()
        self.fields = fields
        self._lock = Lock()

    def _path(self) -> Path:
        return self.manual_dir / "records.parquet"

    def count(self) -> int:
        return len(self.rows())

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            path = self._path()
            if not path.exists():
                return []
            return pd.read_parquet(path).to_dict("records")

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

    def _append(self, rows: list[dict[str, str]]) -> list[str]:
        with self._lock:
            self.manual_dir.mkdir(parents=True, exist_ok=True)
            path = self._path()
            existing = (
                pd.read_parquet(path)
                if path.exists()
                else pd.DataFrame(columns=["record_id", *self.fields])
            )
            stamp = int(time.time() * 1000)
            rows_with_id: list[dict[str, Any]] = [
                {
                    **row,
                    "record_id": f"M{stamp + index:010d}",
                    "observed_at": time.strftime("%Y-%m-%d", time.gmtime()),
                    "channel": "manual",
                }
                for index, row in enumerate(rows)
            ]
            frame = pd.concat([existing, pd.DataFrame(rows_with_id)], ignore_index=True)
            self._write_atomic(frame)
            return [row["record_id"] for row in rows_with_id]

    def _write_atomic(self, frame: pd.DataFrame) -> None:
        self.manual_dir.mkdir(parents=True, exist_ok=True)
        target = self._path()
        temporary = target.with_name(f".{target.name}.tmp.{time.time_ns()}")
        frame.to_parquet(temporary, index=False)
        temporary.replace(target)

    def register(self, record: dict[str, Any]) -> str:
        return self._append([self._validate(record)])[0]

    def import_rows(self, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
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
        self._append(cleaned)
        return {"ok": True, "imported": len(cleaned), "duplicates": duplicates}

    def _duplicate_indexes(self, rows: list[dict[str, str]]) -> list[int]:
        corpus: set[str] = set()
        try:
            connection = duckdb.connect()
            try:
                reference = self.data_root / "current" / "reference_records.parquet"
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

    def undo(self, record_ids: list[str]) -> int:
        with self._lock:
            path = self._path()
            if not path.exists():
                return 0
            frame = pd.read_parquet(path)
            keep = frame[~frame["record_id"].isin(record_ids)]
            removed = int(frame.shape[0] - keep.shape[0])
            if removed:
                self._write_atomic(keep)
            return removed
