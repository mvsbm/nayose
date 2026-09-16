"""System and read-only health routes."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from api.application import ApplicationServices
from api.domain.normalize import normalize_query_fields
from api.http.auth import require_scope
from api.http.dependencies import get_services
from api.infrastructure.bundles.release import release_status
from api.infrastructure.runtime.performance import RuntimeProfile

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
    storage = _storage_health(services)
    ready = release.compatible and not bool(storage.get("low_space"))
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "storage": storage,
        "now_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }


def _storage_health(services: ApplicationServices) -> dict[str, Any]:
    manager = services.resource_manager
    if manager is None:
        return {}
    try:
        snapshot = manager.storage_probe.inspect(services.config.data_root)
    except OSError:
        return {"low_space": True, "error": "storage_unavailable"}
    reserve = max(
        manager.storage_policy.reserve_minimum_bytes,
        int(snapshot.total_bytes * manager.storage_policy.reserve_ratio),
    )
    return {
        "path": str(snapshot.path),
        "available_bytes": snapshot.available_bytes,
        "reserve_bytes": reserve,
        "low_space": snapshot.available_bytes <= reserve,
    }


@router.get("/api/health/details", dependencies=[Depends(require_scope("read"))])
def health_details(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    runs = services.runs.list()
    current = next((run for run in runs if run.get("current")), runs[0] if runs else None)
    release = release_status(services.config.data_root, services.config.models_dir)
    runtime_error = ""
    runtime_device = services.config.device
    if current is not None:
        try:
            manifest = services.runs.resolve(current["id"]) / "run_manifest.json"
            bundle = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(bundle, dict) and not bundle.get("embedding_model"):
                runtime_device = "cpu"
        except (OSError, TypeError, ValueError, KeyError):
            runtime_device = services.config.device
    try:
        runtime: dict[str, Any] = asdict(RuntimeProfile.resolve(runtime_device))
    except (RuntimeError, ValueError) as exc:
        runtime_error = str(exc)
        runtime = {"device": runtime_device, "available": False, "error": runtime_error}
    resources: dict[str, Any] = {}
    if services.resource_manager is not None:
        resources = services.resource_manager.inspect().as_dict()
        resources["storage"] = services.resource_manager.inspect_storage(services.config.data_root)
    storage = _storage_health(services)
    return {
        "status": (
            "ok"
            if current is not None
            and release.compatible
            and not runtime_error
            and not storage.get("low_space")
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
        "resources": resources,
        "runtime_error": runtime_error,
        "engine_warm": services.recommend.engine_ready(current["id"] if current else "")
        if release.compatible
        else False,
    }
