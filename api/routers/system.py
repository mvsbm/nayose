"""System and read-only health routes."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from api.application import ApplicationServices
from api.auth import require_scope
from api.dependencies import get_services
from api.normalize import normalize_query_fields
from api.performance import RuntimeProfile
from api.release import release_status

router = APIRouter(tags=["system"])


@router.get("/", include_in_schema=False)
def index(request: Request) -> FileResponse:
    return FileResponse(request.app.state.config.frontend_dir / "index.html")


@router.get("/api/runs")
def list_runs(
    services: ApplicationServices = Depends(get_services),
) -> list[dict[str, Any]]:
    return services.runs.list()


@router.get("/api/normalize")
def normalize_preview(text: str = "") -> dict[str, str]:
    normalized = normalize_query_fields(text)
    return {
        field: normalized[field]
        for field in (
            "name_kanji",
            "name_kana",
            "phone",
            "postal_code",
            "address",
            "email",
            "date_birth",
        )
    }


@router.get("/api/stats")
def stats(
    services: ApplicationServices = Depends(get_services),
) -> list[dict[str, Any]]:
    return services.stats.snapshot()


@router.get("/api/health")
def health(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    release = release_status(services.config.data_root, services.config.models_dir)
    return {
        "status": "ok" if release.compatible else "degraded",
        "ready": release.compatible,
        "now_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }


@router.get("/api/health/details", dependencies=[Depends(require_scope("read"))])
def health_details(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    runs = services.runs.list()
    current = next((run for run in runs if run.get("current")), runs[0] if runs else None)
    release = release_status(services.config.data_root, services.config.models_dir)
    runtime_error = ""
    try:
        runtime: dict[str, Any] = asdict(RuntimeProfile.resolve(services.config.device))
    except (RuntimeError, ValueError) as exc:
        runtime_error = str(exc)
        runtime = {"device": services.config.device, "available": False, "error": runtime_error}
    return {
        "status": (
            "ok"
            if current is not None and release.compatible and not runtime_error
            else "degraded"
            if current
            else "no_run"
        ),
        "run": current["id"] if current else None,
        "fingerprint": release.model_fingerprint[:12] if release.model_fingerprint else "",
        "data_fingerprint": release.data_fingerprint,
        "model_fingerprint": release.model_fingerprint,
        "fingerprint_match": release.compatible,
        "release": release.as_dict(),
        "run_count": len(runs),
        "data_dir": str(services.config.data_dir),
        "models_dir": str(services.config.models_dir),
        "now_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "stats": services.stats.snapshot(),
        "runtime": runtime,
        "runtime_error": runtime_error,
        "engine_warm": services.recommend.engine_ready(current["id"] if current else "")
        if release.compatible
        else False,
    }
