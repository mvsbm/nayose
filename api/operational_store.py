"""Transactional SQLite boundary for mutable operational state."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from api.telemetry import Stats

_SCHEMA_VERSION = 4


class OperationalStore:
    """Own SQLite configuration, schema migrations, and transaction boundaries."""

    def __init__(self, path: Path, stats: Stats | None = None):
        self.path = Path(path).resolve()
        self._stats = stats
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def schema_version(self) -> int:
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("operational schema version is missing")
        return int(row[0])

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Start one serialized write unit of work and commit it atomically."""
        started = time.perf_counter()
        try:
            with self._lock:
                connection = self._connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
        except BaseException:
            self._record("sqlite.transaction", started, error=True)
            raise
        else:
            self._record("sqlite.transaction", started)

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        started = time.perf_counter()
        try:
            with self._lock:
                connection = self._connect()
                try:
                    yield connection
                finally:
                    connection.close()
        except BaseException:
            self._record("sqlite.read", started, error=True)
            raise
        else:
            self._record("sqlite.read", started)

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA)
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("operational schema version is missing")
            try:
                version = int(row[0])
            except (TypeError, ValueError) as exc:
                raise sqlite3.DatabaseError("operational schema version is invalid") from exc
            if version > _SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"operational schema {version} is newer than supported {_SCHEMA_VERSION}"
                )
            while version < _SCHEMA_VERSION:
                for statement in _MIGRATIONS.get(version, ()):
                    connection.execute(statement)
                version += 1
                connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(version),),
                )
            connection.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _record(self, name: str, started: float, *, error: bool = False) -> None:
        if self._stats is not None:
            self._stats.record(name, time.perf_counter() - started, error=error)


_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS release_history (
            release_id TEXT PRIMARY KEY,
            data_generation_id TEXT NOT NULL,
            model_run_id TEXT NOT NULL,
            data_fingerprint TEXT NOT NULL,
            pointer_sha256 TEXT NOT NULL,
            published_at TEXT NOT NULL
        )
        """,
    ),
    2: (
        "ALTER TABLE decision_sets ADD COLUMN revision INTEGER NOT NULL DEFAULT 0",
        """
        CREATE TABLE IF NOT EXISTS decision_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            actor TEXT NOT NULL,
            operation TEXT NOT NULL,
            decisions_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES decision_sets(run_id) ON DELETE CASCADE
        )
        """,
    ),
    3: (
        """
        CREATE TABLE IF NOT EXISTS job_snapshots (
            namespace TEXT NOT NULL,
            job_id TEXT NOT NULL,
            action TEXT NOT NULL,
            idempotency_key TEXT,
            state_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (namespace, job_id),
            UNIQUE (namespace, idempotency_key)
        )
        """,
        "ALTER TABLE job_snapshots ADD COLUMN version INTEGER NOT NULL DEFAULT 0",
    ),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')
    ON CONFLICT(key) DO NOTHING;

CREATE TABLE IF NOT EXISTS job_snapshots (
    namespace TEXT NOT NULL,
    job_id TEXT NOT NULL,
    action TEXT NOT NULL,
    idempotency_key TEXT,
    state_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (namespace, job_id),
    UNIQUE (namespace, idempotency_key)
);

CREATE TABLE IF NOT EXISTS decision_sets (
    run_id TEXT PRIMARY KEY,
    data_fingerprint TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL,
    left_record_id TEXT NOT NULL,
    right_record_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    PRIMARY KEY (run_id, left_record_id, right_record_id),
    FOREIGN KEY (run_id) REFERENCES decision_sets(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_records (
    record_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS release_history (
    release_id TEXT PRIMARY KEY,
    data_generation_id TEXT NOT NULL,
    model_run_id TEXT NOT NULL,
    data_fingerprint TEXT NOT NULL,
    pointer_sha256 TEXT NOT NULL,
    published_at TEXT NOT NULL
);
"""
