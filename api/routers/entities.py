"""Entity detail and graph routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.application import ApplicationServices
from api.contracts import EntityDetail, EntityGraph, SampleRecord
from api.dependencies import get_services, timed_operation
from api.entity_graph_repository import EntityNotFoundError

router = APIRouter(tags=["entities"])


@router.get("/api/entity/{entity_id}/graph", response_model=EntityGraph)
def entity_graph(
    entity_id: str,
    run_id: str = "",
    record_limit: int = Query(50, ge=1, le=100),
    potential_limit: int = Query(20, ge=1, le=50),
    include_potential: bool = False,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        return timed_operation(
            "entity_graph",
            lambda: services.queries.entity_graph(
                run_id, entity_id, record_limit, potential_limit, include_potential
            ),
            services,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/entity/{entity_id}", response_model=EntityDetail)
def entity(
    entity_id: str,
    run_id: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "entity",
        lambda: services.queries.entity(run_id, entity_id, offset, limit),
        services,
    )


@router.get("/api/match-sample", response_model=SampleRecord)
def match_sample(
    run_id: str = "", services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    return timed_operation("match_sample", lambda: services.queries.match_sample(run_id), services)
