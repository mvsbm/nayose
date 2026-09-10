"""Durable local job snapshots backed by SQLite WAL."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any


class SQLiteJobStore:
    """Persist small job state snapshots without owning job execution."""

    def __init__(self, path: Path, namespace: str):
        if not namespace or not namespace.replace("_", "").isalnum():
            raise ValueError("job namespace must be alphanumeric")
        self.path = path.resolve()
        self.namespace = namespace
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
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
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def save(
        self,
        job: dict[str, Any],
        *,
        action: str,
        idempotency_key: str | None = None,
    ) -> None:
        payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO job_snapshots
                    (namespace, job_id, action, idempotency_key, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, job_id) DO UPDATE SET
                    action=excluded.action,
                    idempotency_key=excluded.idempotency_key,
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                (self.namespace, str(job["id"]), action, idempotency_key, payload, now, now),
            )

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT state_json FROM job_snapshots WHERE namespace = ? ORDER BY created_at ASC",
                (self.namespace,),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def find_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM job_snapshots WHERE namespace = ? AND idempotency_key = ?",
                (self.namespace, key),
            ).fetchone()
        return json.loads(str(row[0])) if row else None

    def delete(self, job_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM job_snapshots WHERE namespace = ? AND job_id = ?",
                (self.namespace, job_id),
            )

    def close(self) -> None:
        return None
