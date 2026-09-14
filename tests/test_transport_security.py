from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from api.application import ApplicationServices
from api.application.admin import AdminService
from api.application.dataset import DatasetService
from api.application.matching import RecommendationService
from api.application.queries import QueryService
from api.application.review import ReviewService
from api.application.search import SearchService
from api.http.app import create_app
from api.http.auth import require_scope
from api.http.dependencies import actor_identity
from api.infrastructure.bundles.release import ReleaseCatalog, ReleasePublisher
from api.infrastructure.bundles.repositories import RunRepository
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.audit import AuditLog
from api.infrastructure.persistence.decisions import DecisionsStore
from api.infrastructure.persistence.operational_store import OperationalStore
from api.runtime_config import RuntimeConfig


class _Closable:
    def close(self) -> None:
        pass


def _services(config: RuntimeConfig) -> ApplicationServices:
    return ApplicationServices(
        config=config,
        operational_store=OperationalStore(config.data_root / "operational.sqlite3"),
        audit=cast(AuditLog, _Closable()),
        release_catalog=ReleaseCatalog(config.data_root, config.models_dir),
        release_publisher=ReleasePublisher(ReleaseCatalog(config.data_root, config.models_dir)),
        runs=cast(RunRepository, _Closable()),
        queries=cast(QueryService, _Closable()),
        recommend=cast(RecommendationService, _Closable()),
        decisions=cast(DecisionsStore, _Closable()),
        review=cast(ReviewService, _Closable()),
        dataset=DatasetService(config.data_root),
        admin=cast(AdminService, _Closable()),
        search=cast(SearchService, _Closable()),
        stats=Stats(),
    )


def test_token_authenticated_attribution_does_not_trust_a_spoofed_header(
    tmp_path: Path,
) -> None:
    services = _services(RuntimeConfig(frontend_dir=tmp_path, api_token="secret-token"))

    assert actor_identity("spoofed-user", services, "admin") == "web"


def test_api_requests_get_a_safe_correlation_id_even_when_rejected(tmp_path: Path) -> None:
    config = RuntimeConfig(
        frontend_dir=tmp_path,
        api_token="secret-token",
    )

    with TestClient(create_app(_services(config), APIRouter())) as client:
        response = client.get("/api/protected")

    assert response.status_code == 401
    UUID(response.headers["x-request-id"])


def test_unhandled_errors_have_a_generic_body_and_correlation_id(tmp_path: Path) -> None:
    router = APIRouter()

    @router.get("/api/fail")
    def fail() -> None:
        raise RuntimeError("private detail")

    with TestClient(
        create_app(_services(RuntimeConfig(frontend_dir=tmp_path)), router),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/api/fail")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal_server_error"}
    UUID(response.headers["x-request-id"])


def test_trusted_proxy_scopes_control_mutations(tmp_path: Path) -> None:
    router = APIRouter()
    config = RuntimeConfig(
        frontend_dir=tmp_path,
        bind_host="0.0.0.0",
        auth_mode="trusted_proxy",
    )
    services = _services(config)

    @router.post("/api/admin-only", dependencies=[Depends(require_scope("admin"))])
    def admin_only() -> dict[str, str]:
        return {"actor": actor_identity(None, services, "fallback")}

    with TestClient(create_app(services, router)) as client:
        headers = {"X-Authenticated-User": "reviewer", "X-Authenticated-Scopes": "read review"}
        assert client.post("/api/admin-only", headers=headers).status_code == 403
        headers["X-Authenticated-Scopes"] = "read review admin"
        assert client.post("/api/admin-only", headers=headers).json() == {"actor": "reviewer"}


def test_api_preserves_a_valid_client_correlation_id(tmp_path: Path) -> None:
    config = RuntimeConfig(frontend_dir=tmp_path)

    with TestClient(create_app(_services(config), APIRouter())) as client:
        response = client.get("/not-found", headers={"X-Request-ID": "request-123"})

    assert response.status_code == 404
    assert response.headers["x-request-id"] == "request-123"
