"""Fingerprint-bound review decisions backed by the operational store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from api.operational_store import OperationalStore
from api.release import ReleaseCatalog, ReleaseError, SnapshotResolver, safe_run_id
from api.request_context import bind_snapshot
from api.request_context import snapshot_for as request_snapshot_for


class DecisionConflict(ValueError):
    """Raised when a reviewer writes against an obsolete decision revision."""


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    items: list[dict[str, Any]]
    revision: int


class DecisionsStore:
    """Own decision binding and revision semantics while SQLite owns storage."""

    def __init__(
        self,
        models_dir: Path,
        operational_store: OperationalStore | None = None,
        release_catalog: ReleaseCatalog | None = None,
    ):
        self.models_dir = Path(models_dir).resolve()
        self._store = operational_store or OperationalStore(self.models_dir / "operational.sqlite3")
        self._resolver = SnapshotResolver(release_catalog, self._store) if release_catalog else None
        self._lock = Lock()

    def load(self, run_id: str) -> list[dict[str, Any]]:
        return self.load_snapshot(run_id).items

    def load_snapshot(self, run_id: str) -> DecisionSnapshot:
        with self._lock:
            safe_id, manifest_path, manifest = self._binding(run_id)
            with self._store.read_connection() as connection:
                binding = connection.execute(
                    """
                    SELECT data_fingerprint, manifest_sha256, revision
                    FROM decision_sets
                    WHERE run_id = ?
                    """,
                    (safe_id,),
                ).fetchone()
                if binding is None:
                    return DecisionSnapshot([], 0)
                self._verify_binding(manifest, manifest_path, binding)
                rows = connection.execute(
                    """
                    SELECT left_record_id, right_record_id, ts, reviewer
                    FROM decisions
                    WHERE run_id = ?
                    ORDER BY rowid
                    """,
                    (safe_id,),
                ).fetchall()
        items = [
            {"left": row[0], "right": row[1], "ts": row[2], "reviewer": row[3]} for row in rows
        ]
        return DecisionSnapshot(items, int(binding[2]))

    def save(
        self,
        run_id: str,
        items: list[dict[str, Any]],
        expected_revision: int = 0,
        actor: str = "",
        connection: sqlite3.Connection | None = None,
    ) -> int:
        if expected_revision < 0:
            raise ValueError("decision revision must be non-negative")
        with self._lock:
            safe_id, manifest_path, manifest = self._binding(run_id)
            fingerprint = str(manifest["data_fingerprint"])
            manifest_sha256 = _sha256(manifest_path)
            if connection is None:
                with self._store.transaction() as transaction:
                    return self._save(
                        transaction,
                        safe_id,
                        fingerprint,
                        manifest_sha256,
                        items,
                        expected_revision,
                        actor,
                    )
            return self._save(
                connection,
                safe_id,
                fingerprint,
                manifest_sha256,
                items,
                expected_revision,
                actor,
            )

    @staticmethod
    def _save(
        connection: sqlite3.Connection,
        safe_id: str,
        fingerprint: str,
        manifest_sha256: str,
        items: list[dict[str, Any]],
        expected_revision: int,
        actor: str,
    ) -> int:
        existing = connection.execute(
            """
            SELECT data_fingerprint, manifest_sha256, revision
            FROM decision_sets
            WHERE run_id = ?
            """,
            (safe_id,),
        ).fetchone()
        if existing is None:
            if expected_revision != 0:
                raise DecisionConflict("decision_conflict")
            revision = 1
            connection.execute(
                """
                INSERT INTO decision_sets
                    (run_id, data_fingerprint, manifest_sha256, revision, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (safe_id, fingerprint, manifest_sha256, revision, time.time()),
            )
        else:
            if existing[0] != fingerprint or existing[1] != manifest_sha256:
                raise ValueError("decision model manifest has changed")
            current_revision = int(existing[2])
            if expected_revision != current_revision:
                raise DecisionConflict("decision_conflict")
            revision = current_revision + 1
            connection.execute(
                """
                UPDATE decision_sets
                SET revision = ?, updated_at = ?
                WHERE run_id = ? AND revision = ?
                """,
                (revision, time.time(), safe_id, current_revision),
            )
        connection.execute("DELETE FROM decisions WHERE run_id = ?", (safe_id,))
        connection.executemany(
            """
            INSERT INTO decisions(run_id, left_record_id, right_record_id, ts, reviewer)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    safe_id,
                    str(item.get("left", "")),
                    str(item.get("right", "")),
                    str(item.get("ts", "")),
                    str(item.get("reviewer", actor)),
                )
                for item in items
            ],
        )
        _record_event(connection, safe_id, revision, actor, "replace", items)
        return revision

    def clear(
        self,
        run_id: str,
        connection: sqlite3.Connection | None = None,
        actor: str = "",
    ) -> int:
        with self._lock:
            safe_id, manifest_path, manifest = self._binding(run_id)
            binding = (str(manifest["data_fingerprint"]), _sha256(manifest_path))
            if connection is None:
                with self._store.transaction() as transaction:
                    return self._clear(transaction, safe_id, binding, actor)
            return self._clear(connection, safe_id, binding, actor)

    @staticmethod
    def _clear(
        connection: sqlite3.Connection,
        safe_id: str,
        binding: tuple[str, str],
        actor: str,
    ) -> int:
        existing = connection.execute(
            """
            SELECT data_fingerprint, manifest_sha256, revision
            FROM decision_sets
            WHERE run_id = ?
            """,
            (safe_id,),
        ).fetchone()
        if existing is None:
            return 0
        if existing[0] != binding[0] or existing[1] != binding[1]:
            raise ValueError("decision model manifest has changed")
        current_revision = int(existing[2])
        revision = current_revision + 1
        connection.execute("DELETE FROM decisions WHERE run_id = ?", (safe_id,))
        connection.execute(
            "UPDATE decision_sets SET revision = ?, updated_at = ? WHERE run_id = ?",
            (revision, time.time(), safe_id),
        )
        _record_event(connection, safe_id, revision, actor, "clear", [])
        return revision

    def _binding(self, run_id: str) -> tuple[str, Path, dict[str, Any]]:
        requested = run_id.strip()
        if self._resolver is not None:
            snapshot = request_snapshot_for(requested)
            if snapshot is None:
                try:
                    snapshot = self._resolver.resolve(requested)
                except ReleaseError as exc:
                    raise ValueError("release is unavailable") from exc
                bind_snapshot(requested, snapshot)
            safe_id = snapshot.model_run_id
            candidates = [snapshot.model_dir]
        else:
            safe_id = safe_run_id(requested or self._current_run_id())
            candidates = [self.models_dir / "runs" / safe_id, self.models_dir / safe_id]
        if self._resolver is None:
            current = self.models_dir / "current"
            if current.exists() and current.resolve().name == safe_id:
                candidates.append(current.resolve())
        for run_dir in candidates:
            manifest_path = run_dir / "run_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("data_fingerprint"):
                    return safe_id, manifest_path, manifest
        raise ValueError("run manifest with data fingerprint is required")

    def _current_run_id(self) -> str:
        current = self.models_dir / "current"
        if not current.exists():
            raise ValueError("current run is unavailable")
        return current.resolve().name

    @staticmethod
    def _verify_binding(
        manifest: dict[str, Any], manifest_path: Path, binding: tuple[Any, ...]
    ) -> None:
        manifest_sha256 = _sha256(manifest_path)
        if binding[0] != manifest.get("data_fingerprint"):
            raise ValueError("decision data fingerprint does not match the model")
        if binding[1] != manifest_sha256:
            raise ValueError("decision model manifest has changed")


def _record_event(
    connection: sqlite3.Connection,
    run_id: str,
    revision: int,
    actor: str,
    operation: str,
    items: list[dict[str, Any]],
) -> None:
    connection.execute(
        """
        INSERT INTO decision_events
            (event_id, run_id, revision, actor, operation, decisions_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex,
            run_id,
            revision,
            actor,
            operation,
            json.dumps(items, ensure_ascii=False, separators=(",", ":")),
            datetime.now(UTC).isoformat(),
        ),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
