"""Manual data-entry routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header

from api.application import ApplicationServices
from api.http.auth import require_scope
from api.http.dependencies import actor_identity, get_services, timed_operation
from api.http.schemas import ImportIn, RegisterIn, UndoIn

router = APIRouter(tags=["admin"], dependencies=[Depends(require_scope("admin"))])


@router.get("/api/data-entry")
def manual_list(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "manual_list",
        lambda: {"count": services.admin.manual_count(), "rows": services.admin.manual_list()},
        services,
    )


@router.post("/api/data-entry/register")
def manual_register(
    payload: RegisterIn,
    x_actor: str | None = Header(default=None),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "manual_register",
        lambda: services.admin.manual_register(
            payload.record, actor_identity(x_actor, services, "admin")
        ),
        services,
    )


@router.post("/api/data-entry/import")
def manual_import(
    payload: ImportIn,
    x_actor: str | None = Header(default=None),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "manual_import",
        lambda: services.admin.manual_import(
            payload.rows, payload.dry_run, actor_identity(x_actor, services, "admin")
        ),
        services,
    )


@router.post("/api/data-entry/undo")
def manual_undo(
    payload: UndoIn,
    x_actor: str | None = Header(default=None),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "manual_undo",
        lambda: services.admin.manual_undo(
            payload.record_ids, actor_identity(x_actor, services, "admin")
        ),
        services,
    )
