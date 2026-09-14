"""Evaluation and review read routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from api.application import ApplicationServices
from api.http.dependencies import get_services, timed_operation

router = APIRouter(tags=["evaluation"])


@router.get("/api/review")
def review(
    run_id: str = "",
    min_p: float = 0.0,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    nayose: str = "",
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "review",
        lambda: services.queries.review(run_id, min_p, offset, limit, nayose),
        services,
    )


@router.get("/api/metrics")
def metrics(
    run_id: str = "", services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    return timed_operation("metrics", lambda: services.queries.metrics(run_id), services)


@router.get("/api/confusion")
def confusion(
    run_id: str = "", services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    return timed_operation("confusion", lambda: services.queries.confusion(run_id), services)


@router.get("/api/compare")
def compare(
    run_id: str = "",
    left: str = "",
    right: str = "",
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "compare",
        lambda: {"rows": services.queries.compare(run_id, left, right)},
        services,
    )
