"""Entity search, detail, and graph routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.application import ApplicationServices
from api.application.search import EntitySearchRequest, SearchFilters
from api.http.contracts import EntityDetail, EntityExplorerPage, EntityGraph, SampleRecord
from api.http.dependencies import get_services, timed_operation
from api.infrastructure.read_model.entity_graph_repository import EntityNotFoundError
from api.infrastructure.retrieval.entity_index import EntityCursorError

router = APIRouter(tags=["entities"])


@router.get("/api/entities/explorer", response_model=EntityExplorerPage)
def entity_explorer(
    q: str = "",
    run_id: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
    sort: Literal["relevance", "records", "name", "last_seen"] = "relevance",
    party_type: str = "",
    min_record_count: int = Query(0, ge=0),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    filters = SearchFilters(
        party_type=party_type.strip() or None,
        min_record_count=min_record_count if min_record_count > 0 else None,
    )
    request = EntitySearchRequest(
        run_id=run_id,
        query=q,
        offset=offset,
        limit=limit,
        sort=sort,
        filters=filters,
        cursor=cursor,
    )
    try:
        return timed_operation(
            "entity_explorer", lambda: services.search.explore(request), services
        )
    except EntityCursorError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/entities")
def entities(
    q: str = "",
    run_id: str = "",
    limit: int = Query(50, ge=1, le=200),
    semantic: bool = True,
    party_type: str = "",
    min_record_count: int = Query(0, ge=0),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    filters = SearchFilters(
        party_type=party_type.strip() or None,
        min_record_count=min_record_count if min_record_count > 0 else None,
    )
    return timed_operation(
        "entities",
        lambda: services.search.search(run_id, q, limit, filters=filters, semantic=semantic),
        services,
    )


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
