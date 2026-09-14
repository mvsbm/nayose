"""Development-only diagnostic routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.application import ApplicationServices
from api.http.auth import require_scope
from api.http.dependencies import get_services
from api.http.schemas import QueryIn

router = APIRouter(tags=["debug"], dependencies=[Depends(require_scope("admin"))])


@router.post("/api/query", response_model=dict[str, Any])
def debug_sql(
    payload: QueryIn,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    if not services.config.debug_sql:
        raise HTTPException(403, "raw SQL is disabled (set NAYOSE_DEBUG_SQL=1 to enable)")
    try:
        return services.queries.run_sql(payload.run_id, payload.sql)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
