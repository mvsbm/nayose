"""Shared bundle access, caching, and bounded debug SQL."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any

import duckdb

from api.infrastructure.bundles.release import (
    ReleaseError,
    SnapshotResolver,
    data_generation_for_fingerprint,
    manifest_fingerprint,
)
from api.infrastructure.bundles.repositories import RunRepository
from api.infrastructure.bundles.request_context import bind_snapshot
from api.infrastructure.bundles.request_context import snapshot_for as request_snapshot_for
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.operational_store import OperationalStore

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
_DANGEROUS_SQL = re.compile(
    r"\b(?:attach|copy|create|delete|drop|export|import|install|insert|load|pragma|update)\b",
    re.IGNORECASE,
)
_TABLE_PATH = re.compile(
    r"(?:\bfrom\s+|\bjoin\s+|\bread_parquet\s*\(\s*)['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_READ_FUNCTION = re.compile(r"\bread_([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)

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


def validate_debug_sql(sql: str) -> None:
    statement = sql.strip()
    if not statement or len(statement) > 8_000:
        raise ValueError("debug SQL is empty or too long")
    if ";" in statement:
        raise ValueError("debug SQL must contain one statement")
    if not re.match(r"^(?:select|with)\b", statement, re.IGNORECASE):
        raise ValueError("debug SQL must be a SELECT or WITH query")
    if _DANGEROUS_SQL.search(statement):
        raise ValueError("debug SQL contains a disabled operation")
    read_functions = {name.casefold() for name in _READ_FUNCTION.findall(statement)}
    if read_functions - {"parquet"}:
        raise ValueError("debug SQL may read parquet only")
    if "read_parquet" in statement.casefold() and not _TABLE_PATH.search(statement):
        raise ValueError("debug SQL parquet paths must be literal bundle/ or data/ paths")
    for path in _TABLE_PATH.findall(statement):
        if not (path.startswith("bundle/") or path.startswith("data/")):
            raise ValueError("debug SQL paths must use bundle/ or data/ roots")
        if ".." in Path(path).parts:
            raise ValueError("debug SQL paths may not contain parent traversal")


class BundleSnapshotError(RuntimeError):
    """Raised when a model run cannot be paired with an immutable data generation."""


@dataclass(frozen=True, slots=True)
class BundleSnapshot:
    """The model/data pair that a request is allowed to read."""

    run_id: str
    run_dir: Path
    data_dir: Path
    data_fingerprint: str
    release_id: str = ""
    data_manifest_sha256: str = ""
    model_manifest_sha256: str = ""


class BundleReader:
    """Resolve bundles and provide bounded, thread-safe read caching."""

    def __init__(
        self,
        repo: RunRepository,
        data_dir: Path,
        operational_store: OperationalStore | None = None,
        stats: Stats | None = None,
    ):
        self.repo = repo
        self.data_dir = data_dir
        self._stats = stats
        catalog = getattr(repo, "release_catalog", None)
        self._resolver = (
            SnapshotResolver(catalog, operational_store) if catalog is not None else None
        )
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
        requested = run_id.strip()
        cached = request_snapshot_for(requested)
        if cached is not None:
            return BundleSnapshot(
                cached.model_run_id,
                cached.model_dir,
                cached.data_dir,
                cached.data_fingerprint,
                cached.release_id,
                cached.data_manifest_sha256,
                cached.model_manifest_sha256,
            )
        if self._resolver is not None:
            try:
                release = self._resolver.resolve(requested)
            except ReleaseError as exc:
                raise BundleSnapshotError(str(exc)) from exc
            bind_snapshot(requested, release)
            return BundleSnapshot(
                release.model_run_id,
                release.model_dir,
                release.data_dir,
                release.data_fingerprint,
                release.release_id,
                release.data_manifest_sha256,
                release.model_manifest_sha256,
            )
        run_dir = self.repo.resolve(requested)
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
        started = time.perf_counter()
        try:
            connection = duckdb.connect()
        except BaseException:
            self._record("duckdb.connect", started, error=True)
            raise
        self._record("duckdb.connect", started)
        return connection, snapshot

    def _record(self, name: str, started: float, *, error: bool = False) -> None:
        if self._stats is not None:
            self._stats.record(name, time.perf_counter() - started, error=error)

    def run_sql(self, run_id: str, sql: str) -> dict[str, Any]:
        validate_debug_sql(sql)
        started = time.perf_counter()
        connection, snapshot = self.open(run_id)
        error = False
        try:
            run_path = str(snapshot.run_dir).replace("'", "''")
            data_path = str(snapshot.data_dir).replace("'", "''")
            resolved = (
                sql.replace("'bundle/", f"'{run_path}/")
                .replace('"bundle/', f'"{run_path}/')
                .replace("'data/", f"'{data_path}/")
                .replace('"data/', f'"{data_path}/')
            )
            frame = connection.execute(resolved).fetchdf().head(MAX_ROWS)
            result = {
                "columns": [str(column) for column in frame.columns],
                "rows": frame.where(frame.notna(), None).astype(object).values.tolist(),
                "row_count": len(frame),
            }
        except BaseException:
            error = True
            raise
        finally:
            connection.close()
            self._record("duckdb.debug_sql", started, error=error)
        return result
