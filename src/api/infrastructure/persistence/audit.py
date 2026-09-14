"""Append-only audit repository backed by the operational store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from api.infrastructure.persistence.operational_store import OperationalStore


class AuditLog:
    """Persist tamper-evident operational events in commit order."""

    def __init__(self, store: OperationalStore):
        self.store = store

    def record(
        self,
        actor: str,
        action: str,
        detail: dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        event = {
            "ts": datetime.now(UTC).isoformat(),
            "event_id": uuid4().hex,
            "actor": actor,
            "action": action,
            **detail,
        }
        if connection is None:
            with self.store.transaction() as transaction:
                event_hash = self._insert(transaction, event, actor, action, detail)
        else:
            event_hash = self._insert(connection, event, actor, action, detail)
        event["event_hash"] = event_hash
        return event

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        event: dict[str, Any],
        actor: str,
        action: str,
        detail: dict[str, Any],
    ) -> str:
        previous = connection.execute(
            "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        event["prev_hash"] = str(previous[0]) if previous else ""
        unsigned = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        event_hash = hashlib.sha256(unsigned.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO audit_events
                (event_id, ts, actor, action, detail_json, prev_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["ts"],
                actor,
                action,
                json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                event["prev_hash"],
                event_hash,
            ),
        )
        return event_hash

    def verify(self) -> bool:
        try:
            with self.store.read_connection() as connection:
                rows = connection.execute(
                    """
                    SELECT event_id, ts, actor, action, detail_json, prev_hash, event_hash
                    FROM audit_events
                    ORDER BY sequence ASC
                    """
                ).fetchall()
        except Exception:
            return False
        previous_hash = ""
        for event_id, timestamp, actor, action, detail_json, prev_hash, event_hash in rows:
            try:
                detail = json.loads(str(detail_json))
                event = {
                    "ts": timestamp,
                    "event_id": event_id,
                    "actor": actor,
                    "action": action,
                    **detail,
                    "prev_hash": prev_hash,
                }
                unsigned = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, json.JSONDecodeError):
                return False
            if prev_hash != previous_hash:
                return False
            if hashlib.sha256(unsigned.encode("utf-8")).hexdigest() != event_hash:
                return False
            previous_hash = str(event_hash)
        return True

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self.store.read_connection() as connection:
            rows = connection.execute(
                """
                SELECT event_id, ts, actor, action, detail_json, prev_hash, event_hash
                FROM audit_events
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        events = []
        for event_id, timestamp, actor, action, detail_json, prev_hash, event_hash in reversed(
            rows
        ):
            detail = json.loads(str(detail_json))
            events.append(
                {
                    "ts": timestamp,
                    "event_id": event_id,
                    "actor": actor,
                    "action": action,
                    **detail,
                    "prev_hash": prev_hash,
                    "event_hash": event_hash,
                }
            )
        return events
