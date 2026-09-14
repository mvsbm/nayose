"""Review decision routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from api.application import ApplicationServices
from api.http.auth import require_scope
from api.http.dependencies import actor_identity, get_services, timed_operation
from api.http.schemas import DecisionsIn
from api.infrastructure.persistence.decisions import DecisionConflict

router = APIRouter(tags=["decisions"])


@router.get("/api/decisions")
def decisions_get(
    run_id: str = "", services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    try:
        snapshot = timed_operation(
            "decisions", lambda: services.review.load_snapshot(run_id), services
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

    try:
        revision = timed_operation(
            "decisions_save",
            lambda: services.review.save(payload.run_id, items, payload.revision, reviewer),
            services,
        )
    except DecisionConflict as exc:
        raise HTTPException(409, "decision_conflict") from exc
    except ValueError as exc:
        raise HTTPException(422, "invalid run_id") from exc
    return {"saved": len(items), "revision": revision}
