"""Entity search and explorer routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.application import ApplicationServices
from api.contracts import EntityExplorerPage
from api.dependencies import get_services, timed_operation
from api.entity_index import EntityCursorError
from api.search_service import EntitySearchRequest, SearchFilters

router = APIRouter(tags=["search"])


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
