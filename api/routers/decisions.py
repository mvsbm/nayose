"""Review decision routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from api.application import ApplicationServices
from api.auth import require_scope
from api.decisions import DecisionConflict
from api.dependencies import actor_identity, get_services, timed_operation
from api.schemas import DecisionsIn

router = APIRouter(tags=["decisions"])


@router.get("/api/decisions")
def decisions_get(
    run_id: str = "", services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    try:
        snapshot = timed_operation(
            "decisions", lambda: services.decisions.load_snapshot(run_id), services
        )
    except ValueError as exc:
        raise HTTPException(422, "invalid run_id") from exc
    return {"items": snapshot.items, "revision": snapshot.revision}


@router.put("/api/decisions", dependencies=[Depends(require_scope("review"))])
def decisions_put(
    payload: DecisionsIn,
    x_reviewer: str | None = Header(default=None),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    reviewer = actor_identity(x_reviewer, services, services.config.default_reviewer)
    now = datetime.now(UTC).isoformat()
    items = [
        {
            "left": item.left,
            "right": item.right,
            "ts": item.ts or now,
            "reviewer": reviewer,
        }
        for item in payload.items
    ]

    def persist() -> int:
        with services.operational_store.transaction() as connection:
            revision = services.decisions.save(
                payload.run_id,
                items,
                expected_revision=payload.revision,
                actor=reviewer,
                connection=connection,
            )
            services.audit.record(
                reviewer,
                "decisions_save",
                {"run": payload.run_id, "count": len(items)},
                connection,
            )
            return revision

    try:
        revision = timed_operation("decisions_save", persist, services)
    except DecisionConflict as exc:
        raise HTTPException(409, "decision_conflict") from exc
    except ValueError as exc:
        raise HTTPException(422, "invalid run_id") from exc
    return {"saved": len(items), "revision": revision}
