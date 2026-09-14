"""Review decision use cases."""

from __future__ import annotations

import sqlite3
from typing import Any

from api.infrastructure.persistence.audit import AuditLog
from api.infrastructure.persistence.decisions import DecisionSnapshot, DecisionsStore
from api.infrastructure.persistence.operational_store import OperationalStore


class ReviewService:
    """Coordinate bound decision persistence and its audit transaction."""

    def __init__(
        self,
        decisions: DecisionsStore,
        operational_store: OperationalStore,
        audit: AuditLog,
    ):
        self._decisions = decisions
        self._operational_store = operational_store
        self._audit = audit

    def load_snapshot(self, run_id: str) -> DecisionSnapshot:
        return self._decisions.load_snapshot(run_id)

    def save(
        self,
        run_id: str,
        items: list[dict[str, Any]],
        expected_revision: int,
        actor: str,
    ) -> int:
        with self._operational_store.transaction() as connection:
            revision = self._decisions.save(
                run_id,
                items,
                expected_revision=expected_revision,
                actor=actor,
                connection=connection,
            )
            self._audit.record(
                actor,
                "decisions_save",
                {"run": run_id, "count": len(items)},
                connection,
            )
            return revision

    def clear(
        self,
        run_id: str,
        connection: sqlite3.Connection | None = None,
        actor: str = "",
    ) -> int:
        return self._decisions.clear(run_id, connection=connection, actor=actor)
