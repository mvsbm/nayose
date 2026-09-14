"""Job persistence adapter backed by the shared operational store."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from api.infrastructure.persistence.operational_store import OperationalStore


class JobConflict(RuntimeError):
    """Raised when a worker updates a job from an obsolete state version."""


class SQLiteJobStore:
    """Persist job snapshots without owning execution or thread state."""

    def __init__(self, store_or_path: OperationalStore | Path, namespace: str):
        if not namespace or not namespace.replace("_", "").isalnum():
            raise ValueError("job namespace must be alphanumeric")
        self.store = (
            store_or_path
            if isinstance(store_or_path, OperationalStore)
            else OperationalStore(store_or_path)
        )
        self.path = self.store.path
        self.namespace = namespace

    def save(
        self,
        job: dict[str, Any],
        *,
        action: str,
        idempotency_key: str | None = None,
        connection: sqlite3.Connection | None = None,
        expected_version: int | None = None,
    ) -> int:
        payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
        if connection is None:
            with self.store.transaction() as transaction:
                return self._save(
                    transaction, job, action, idempotency_key, payload, expected_version
                )
        return self._save(connection, job, action, idempotency_key, payload, expected_version)

    def _save(
        self,
        connection: sqlite3.Connection,
        job: dict[str, Any],
        action: str,
        idempotency_key: str | None,
        payload: str,
        expected_version: int | None,
    ) -> int:
        now = time.time()
        job_id = str(job["id"])
        if expected_version is None:
            connection.execute(
                """
                INSERT INTO job_snapshots
                    (
                        namespace, job_id, action, idempotency_key,
                        state_json, created_at, updated_at, version
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(namespace, job_id) DO UPDATE SET
                    action=excluded.action,
                    idempotency_key=excluded.idempotency_key,
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at,
                    version=job_snapshots.version + 1
                """,
                (self.namespace, job_id, action, idempotency_key, payload, now, now),
            )
            row = connection.execute(
                "SELECT version FROM job_snapshots WHERE namespace = ? AND job_id = ?",
                (self.namespace, job_id),
            ).fetchone()
            assert row is not None
            return int(row[0])

        if expected_version < 0:
            raise ValueError("job version must be non-negative")
        current = connection.execute(
            "SELECT version FROM job_snapshots WHERE namespace = ? AND job_id = ?",
            (self.namespace, job_id),
        ).fetchone()
        if current is None:
            if expected_version != 0:
                raise JobConflict("job state changed")
            try:
                connection.execute(
                    """
                    INSERT INTO job_snapshots
                        (
                            namespace, job_id, action, idempotency_key,
                            state_json, created_at, updated_at, version
                        )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (self.namespace, job_id, action, idempotency_key, payload, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise JobConflict("job state changed") from exc
            return 1
        if int(current[0]) != expected_version:
            raise JobConflict("job state changed")

        updated = connection.execute(
            """
            UPDATE job_snapshots
            SET action = ?, idempotency_key = ?, state_json = ?, updated_at = ?,
                version = version + 1
            WHERE namespace = ? AND job_id = ? AND version = ?
            """,
            (
                action,
                idempotency_key,
                payload,
                now,
                self.namespace,
                job_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise JobConflict("job state changed")
        return expected_version + 1

    def list(self) -> list[dict[str, Any]]:
        with self.store.read_connection() as connection:
            rows = connection.execute(
                "SELECT state_json FROM job_snapshots WHERE namespace = ? ORDER BY created_at ASC",
                (self.namespace,),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def version(self, job_id: str) -> int:
        with self.store.read_connection() as connection:
            row = connection.execute(
                "SELECT version FROM job_snapshots WHERE namespace = ? AND job_id = ?",
                (self.namespace, job_id),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return int(row[0])

    def find_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self.store.read_connection() as connection:
            row = connection.execute(
                "SELECT state_json FROM job_snapshots WHERE namespace = ? AND idempotency_key = ?",
                (self.namespace, key),
            ).fetchone()
        return json.loads(str(row[0])) if row else None

    def delete(self, job_id: str) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM job_snapshots WHERE namespace = ? AND job_id = ?",
                (self.namespace, job_id),
            )
