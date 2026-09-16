"""FastAPI application construction and transport middleware."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from api.application import ApplicationServices
from api.http.auth import AuthPolicy, bind_auth, clear_auth
from api.infrastructure.bundles.request_context import clear_snapshot_context
from api.infrastructure.read_model.bundle_reader import BundleSnapshotError

LOGGER = logging.getLogger(__name__)


def create_app(services: ApplicationServices, router: APIRouter) -> FastAPI:
    """Build one independently testable application instance."""
    config = services.config
    auth = AuthPolicy(
        config.auth_mode,
        config.api_token,
        config.default_reviewer,
        config.trusted_auth_subject_header,
        config.trusted_auth_scopes_header,
        getattr(config, "review_token", ""),
        getattr(config, "admin_token", ""),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config.warm_embed:
            _warm_recommendation(services)
        try:
            yield
        finally:
            app.state.services.close()

    app = FastAPI(
        title="Nayose — 照合監査ダッシュボード",
        version="3.0.0",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.services = services
    app.mount("/static", StaticFiles(directory=str(config.frontend_dir)), name="static")
    app.include_router(router)

    @app.exception_handler(BundleSnapshotError)
    async def bundle_snapshot_error(_request: Request, exc: BundleSnapshotError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.middleware("http")
    async def transport(request: Request, call_next):
        clear_snapshot_context()
        clear_auth()
        request_id = _request_id(request)
        request.state.request_id = request_id
        started = time.perf_counter()
        context = auth.authenticate(request)
        request.state.auth = context
        bind_auth(context)
        if _requires_auth(config, request) and context is None:
            response = JSONResponse({"detail": "unauthorized"}, status_code=401)
            response.headers["X-Request-ID"] = request_id
            _set_security_headers(response)
            services.stats.record("http", time.perf_counter() - started, error=True)
            clear_snapshot_context()
            clear_auth()
            return response
        try:
            try:
                response = await call_next(request)
            except Exception:
                services.stats.record("http_error", time.perf_counter() - started, error=True)
                LOGGER.exception("request failed request_id=%s", request_id)
                response = JSONResponse({"detail": "internal_server_error"}, status_code=500)
            response.headers["X-Request-ID"] = request_id
            _set_security_headers(response)
            services.stats.record("http", time.perf_counter() - started)
            return response
        finally:
            clear_snapshot_context()
            clear_auth()

    return app


def _set_security_headers(response: Response) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; "
        "connect-src 'self'; img-src 'self' data:; style-src 'self'; "
        "font-src 'self'; form-action 'self'; frame-ancestors 'none'"
    )


def _requires_auth(config: object, request: Request) -> bool:
    mode = getattr(config, "auth_mode", "local_bearer")
    tokens = (
        getattr(config, "api_token", ""),
        getattr(config, "review_token", ""),
        getattr(config, "admin_token", ""),
    )
    return bool(
        request.url.path.startswith("/api/")
        and request.url.path != "/api/health"
        and (mode == "trusted_proxy" or any(tokens))
    )


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    if (
        supplied
        and len(supplied) <= 128
        and all(char.isascii() and (char.isalnum() or char in "-_.:") for char in supplied)
    ):
        return supplied
    return str(uuid4())


def _warm_recommendation(services: ApplicationServices) -> None:
    current = next((run for run in services.runs.list() if run.get("current")), None)
    if current is None:
        return
    started = time.perf_counter()
    try:
        services.recommend.recommend(current["id"], {"name_kanji": "ウォームアップ"})
    except Exception as exc:  # startup remains available for read-only endpoints
        LOGGER.warning("recommendation warmup failed (%s): %s", type(exc).__name__, exc)
    else:
        LOGGER.info(
            "recommendation engine warmed run=%s elapsed_ms=%.1f",
            current["id"],
            (time.perf_counter() - started) * 1000,
        )
