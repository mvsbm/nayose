"""Administrative and manual data-entry routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from api.application import ApplicationServices
from api.auth import require_scope
from api.contracts import AdminJob, DatasetRowsOut, GenerationPreviewIn
from api.dependencies import actor_identity, get_services, timed_operation
from api.schemas import AdminActionIn, AdminJobIn, ImportIn, RegisterIn, UndoIn

router = APIRouter(tags=["admin"], dependencies=[Depends(require_scope("admin"))])


@router.get("/api/admin/capable")
def admin_capable(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, bool]:
    return {"capable": services.admin.capable()}


@router.get("/api/admin/state")
def admin_state(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "admin_state",
        lambda: services.admin.state(services.recommend.engine_ready),
        services,
    )


@router.get("/api/admin/generation-config")
def admin_generation_config(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation("admin_generation_config", services.admin.generation_config, services)


@router.post("/api/admin/generation-preview")
def admin_generation_preview(
    payload: GenerationPreviewIn,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "admin_generation_preview",
        lambda: services.admin.generation_preview(payload.controls.model_dump()),
        services,
    )


@router.get("/api/admin/generations")
def admin_generations(
    services: ApplicationServices = Depends(get_services),
) -> list[dict[str, Any]]:
    return timed_operation(
        "admin_generations",
        services.dataset.generations,
        services,
    )


@router.get("/api/admin/generations/{generation_id}/summary")
def admin_generation_summary(
    generation_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "admin_generation_summary",
        lambda: services.dataset.summary(generation_id),
        services,
    )


@router.get("/api/admin/generations/{generation_id}/records", response_model=DatasetRowsOut)
def admin_generation_records(
    generation_id: str,
    table: str = Query(default="reference"),
    q: str = Query(default="", max_length=100),
    filters: str = Query(default="", max_length=2_000),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    count: bool = Query(default=True),
    after: str | None = Query(default=None, max_length=200),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    column_filters: dict[str, str] | None = None
    if filters.strip():
        try:
            decoded = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "invalid_dataset_filters") from exc
        if not isinstance(decoded, dict):
            raise HTTPException(422, "dataset_filters_must_be_object")
        column_filters = {str(key): str(value) for key, value in decoded.items()}
    return timed_operation(
        "admin_generation_records",
        lambda: services.dataset.records(
            generation_id,
            table,
            q,
            offset,
            limit,
            column_filters,
            include_total=count,
            after=after,
        ),
        services,
    )


@router.post("/api/admin/jobs", status_code=202, response_model=AdminJob)
def admin_job(
    payload: AdminJobIn,
    x_actor: str | None = Header(default=None),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "admin_job",
        lambda: services.admin.start(
            payload.action, payload.params, actor_identity(x_actor, services, "admin")
        ),
        services,
    )


@router.post("/api/admin/jobs/{job_id}/cancel", response_model=AdminJob)
def cancel_admin_job(
    job_id: str,
    x_actor: str | None = Header(default=None),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "admin_job_cancel",
        lambda: services.admin.cancel(job_id, actor_identity(x_actor, services, "admin")),
        services,
    )


@router.get("/api/admin/jobs/{job_id}", response_model=AdminJob)
def admin_job_status(
    job_id: str, services: ApplicationServices = Depends(get_services)
) -> dict[str, Any]:
    return timed_operation("admin_job_status", lambda: services.admin.status(job_id), services)


@router.post("/api/admin/actions")
def admin_action(
    payload: AdminActionIn,
    x_actor: str | None = Header(default=None),
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return timed_operation(
        "admin_action",
        lambda: services.admin.action(
            payload.action, payload.params, actor_identity(x_actor, services, "admin")
        ),
        services,
    )


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
