"""Recommendation routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.application import ApplicationServices
from api.auth import require_scope
from api.contracts import MatchJob, MatchResult
from api.dependencies import get_services, timed_operation
from api.schemas import RecommendIn

router = APIRouter(tags=["matching"])


@router.post("/api/recommendations", status_code=202, response_model=MatchJob)
def start_recommendation(
    payload: RecommendIn,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "recommendation_start",
        lambda: services.recommend.start(payload.run_id, payload.record, payload.thresholds),
        services,
    )


@router.post(
    "/api/recommendations/{job_id}/cancel",
    response_model=MatchJob,
    dependencies=[Depends(require_scope("review"))],
)
def cancel_recommendation(
    job_id: str, services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    return timed_operation(
        "recommendation_cancel", lambda: services.recommend.cancel(job_id), services
    )


@router.get("/api/recommendations/{job_id}", response_model=MatchJob)
def recommendation_status(
    job_id: str, services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    return timed_operation(
        "recommendation_status", lambda: services.recommend.status(job_id), services
    )


@router.post("/api/recommend", response_model=MatchResult)
def recommend(
    payload: RecommendIn,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "recommend",
        lambda: services.recommend.recommend(payload.run_id, payload.record, payload.thresholds),
        services,
    )
