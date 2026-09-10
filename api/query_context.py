"""Shared bundle access, caching, and bounded debug SQL."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any

import duckdb

from api.release import data_generation_for_fingerprint, manifest_fingerprint
from api.repositories import RunRepository

MAX_ROWS = 10_000
MAX_CACHE = 96
FUZZY_SEARCH_CAP = 500_000
ENTITY_COLUMNS = (
    "nayose_id",
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "party_type",
    "phone",
    "postal_code",
    "address",
    "email",
    "record_count",
    "policy_count",
    "agency_count",
    "first_seen",
    "last_seen",
)
ENTITY_SEARCH_COLUMNS = (
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "party_type",
    "phone",
    "postal_code",
    "address",
    "email",
    "nayose_id",
)
SEARCH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "or",
        "the",
        "with",
        "customer",
        "customers",
        "顧客",
        "の",
        "に",
        "で",
        "を",
    }
)


class BundleSnapshotError(RuntimeError):
    """Raised when a model run cannot be paired with an immutable data generation."""


@dataclass(frozen=True, slots=True)
class BundleSnapshot:
    """The model/data pair that a request is allowed to read."""

    run_id: str
    run_dir: Path
    data_dir: Path
    data_fingerprint: str


class BundleReader:
    """Resolve bundles and provide bounded, thread-safe read caching."""

    def __init__(self, repo: RunRepository, data_dir: Path):
        self.repo = repo
        self.data_dir = data_dir
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_errors: dict[str, tuple[float, BaseException]] = {}
        self._cache_inflight: dict[str, Event] = {}
        self._cache_lock = Lock()

    def cached(self, key: str, operation: Callable[[], Any]) -> Any:
        while True:
            with self._cache_lock:
                now = time.time()
                hit = self._cache.get(key)
                if hit and now - hit[0] < 300:
                    return hit[1]
                error = self._cache_errors.get(key)
                if error and error[0] > now:
                    raise error[1]
                if error:
                    self._cache_errors.pop(key, None)
                event = self._cache_inflight.get(key)
                if event is None:
                    event = Event()
                    self._cache_inflight[key] = event
                    leader = True
                else:
                    leader = False
            if leader:
                break
            event.wait()

        try:
            value = operation()
        except Exception as exc:
            with self._cache_lock:
                self._cache_errors[key] = (time.time() + 1, exc)
                self._cache_inflight.pop(key, None)
                event.set()
            raise
        with self._cache_lock:
            self._cache[key] = (time.time(), value)
            self._cache_errors.pop(key, None)
            self._cache_inflight.pop(key, None)
            event.set()
            if len(self._cache) > MAX_CACHE:
                oldest = min(self._cache, key=lambda cache_key: self._cache[cache_key][0])
                del self._cache[oldest]
        return value

    def snapshot(self, run_id: str) -> BundleSnapshot:
        run_dir = self.repo.resolve(run_id)
        fingerprint = manifest_fingerprint(run_dir)
        if not fingerprint:
            raise BundleSnapshotError("run manifest with data fingerprint is required")
        data_dir = data_generation_for_fingerprint(self.data_dir, fingerprint)
        if data_dir is None:
            raise BundleSnapshotError(
                f"no data generation matches model fingerprint {fingerprint[:12]}"
            )
        reference = data_dir / "reference_records.parquet"
        if not reference.is_file():
            raise BundleSnapshotError(f"data generation is missing {reference.name}")
        return BundleSnapshot(run_dir.name, run_dir, data_dir, fingerprint)

    def open(self, run_id: str) -> tuple[Any, BundleSnapshot]:
        snapshot = self.snapshot(run_id)
        return duckdb.connect(), snapshot

    def run_sql(self, run_id: str, sql: str) -> dict[str, Any]:
        connection, snapshot = self.open(run_id)
        try:
            resolved = (
                sql.replace("'bundle/", f"'{snapshot.run_dir}/")
                .replace('"bundle/', f'"{snapshot.run_dir}/')
                .replace("'data/", f"'{snapshot.data_dir}/")
                .replace('"data/', f'"{snapshot.data_dir}/')
            )
            frame = connection.execute(resolved).fetchdf().head(MAX_ROWS)
            return {
                "columns": [str(column) for column in frame.columns],
                "rows": frame.where(frame.notna(), None).astype(object).values.tolist(),
                "row_count": len(frame),
            }
        finally:
            connection.close()
