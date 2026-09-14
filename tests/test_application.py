import os
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from api.admin import AdminService
from api.app import create_app
from api.application import ApplicationServices
from api.audit import AuditLog
from api.dataset_service import DatasetService
from api.decisions import DecisionsStore
from api.matching import RecommendationService
from api.operational_store import OperationalStore
from api.query_service import QueryService
from api.release import ReleaseCatalog, ReleasePublisher
from api.repositories import RunRepository
from api.runtime_config import RuntimeConfig
from api.search_service import SearchService
from api.telemetry import Stats


class Closeable:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class Runs:
    def list(self) -> list[dict[str, str]]:
        return [{"id": "injected"}]


def test_runtime_config_reads_one_validated_configuration(tmp_path: Path) -> None:
    config = RuntimeConfig.from_env(
        {
            "NAYOSE_MODELS_DIR": str(tmp_path / "models"),
            "NAYOSE_DATA_DIR": str(tmp_path / "data" / "current"),
            "NAYOSE_DATA_ROOT": str(tmp_path / "data"),
            "NAYOSE_BIND_HOST": "127.0.0.1",
            "NAYOSE_DASH_PORT": "8899",
            "NAYOSE_DEBUG_SQL": "1",
            "NAYOSE_WARM_EMBED": "true",
            "NAYOSE_REVIEWER_DEFAULT": "reviewer",
            "NAYOSE_DEVICE": "mps",
        }
    )

    assert config.models_dir == tmp_path / "models"
    assert config.data_dir == tmp_path / "data" / "current"
    assert config.data_root == tmp_path / "data"
    assert config.port == 8899
    assert config.debug_sql is True
    assert config.warm_embed is True
    assert config.default_reviewer == "reviewer"
    assert config.device == "mps"


def test_runtime_config_rejects_non_mps_serving() -> None:
    with pytest.raises(ValueError, match="locked to MPS"):
        RuntimeConfig(device="cpu")


def test_runtime_config_rejects_shared_bind_without_authentication() -> None:
    with pytest.raises(ValueError, match="authentication"):
        RuntimeConfig.from_env({"NAYOSE_BIND_HOST": "0.0.0.0"})


def test_runtime_config_allows_authenticated_shared_bind() -> None:
    config = RuntimeConfig.from_env(
        {"NAYOSE_BIND_HOST": "0.0.0.0", "NAYOSE_API_TOKEN": "local-test-token"}
    )

    assert config.bind_host == "0.0.0.0"
    assert config.api_token == "local-test-token"


def test_application_services_close_owned_resources(tmp_path: Path) -> None:
    admin = Closeable()
    recommend = Closeable()
    services = ApplicationServices(
        config=RuntimeConfig(),
        operational_store=OperationalStore(tmp_path / "operational.sqlite3"),
        audit=cast(AuditLog, object()),
        release_catalog=ReleaseCatalog(Path("."), Path(".")),
        release_publisher=ReleasePublisher(ReleaseCatalog(Path("."), Path("."))),
        runs=cast(RunRepository, Runs()),
        queries=cast(QueryService, object()),
        recommend=cast(RecommendationService, recommend),
        decisions=cast(DecisionsStore, object()),
        dataset=DatasetService(Path(".")),
        admin=cast(AdminService, admin),
        search=cast(SearchService, object()),
        stats=Stats(),
    )

    services.close()

    assert admin.closed == 1
    assert recommend.closed == 1


def test_runtime_config_can_be_overridden_without_mutating_the_original() -> None:
    original = RuntimeConfig()
    updated = replace(original, port=9000)

    assert original.port != updated.port
    assert original.bind_host == updated.bind_host


def test_admin_settings_update_the_owned_matching_service(monkeypatch, tmp_path: Path) -> None:
    from api.admin import AdminService

    selected: list[str] = []
    monkeypatch.delenv("NAYOSE_EXACT_MODE", raising=False)
    admin = AdminService(
        tmp_path / "data",
        tmp_path / "models",
        exact_mode_set=selected.append,
    )
    try:
        result = admin.action("settings", {"exact_mode": "exact_only"}, "test")
    finally:
        admin.close()

    assert result["exact_mode"] == "exact_only"
    assert selected == ["exact_only"]
    assert "NAYOSE_EXACT_MODE" not in os.environ


def test_application_lifespan_closes_services(tmp_path: Path) -> None:
    admin = Closeable()
    recommend = Closeable()
    services = ApplicationServices(
        config=RuntimeConfig(),
        operational_store=OperationalStore(tmp_path / "operational.sqlite3"),
        audit=cast(AuditLog, object()),
        release_catalog=ReleaseCatalog(Path("."), Path(".")),
        release_publisher=ReleasePublisher(ReleaseCatalog(Path("."), Path("."))),
        runs=cast(RunRepository, Runs()),
        queries=cast(QueryService, object()),
        recommend=cast(RecommendationService, recommend),
        decisions=cast(DecisionsStore, object()),
        dataset=DatasetService(Path(".")),
        admin=cast(AdminService, admin),
        search=cast(SearchService, object()),
        stats=Stats(),
    )

    with TestClient(create_app(services, APIRouter())):
        pass

    assert admin.closed == 1
    assert recommend.closed == 1


def test_routes_use_the_application_service_container(tmp_path: Path) -> None:
    from api import main

    services = ApplicationServices(
        config=RuntimeConfig(),
        operational_store=OperationalStore(tmp_path / "operational.sqlite3"),
        audit=cast(AuditLog, object()),
        release_catalog=ReleaseCatalog(Path("."), Path(".")),
        release_publisher=ReleasePublisher(ReleaseCatalog(Path("."), Path("."))),
        runs=cast(RunRepository, Runs()),
        queries=cast(QueryService, object()),
        recommend=cast(RecommendationService, Closeable()),
        decisions=cast(DecisionsStore, object()),
        dataset=DatasetService(Path(".")),
        admin=cast(AdminService, Closeable()),
        search=cast(SearchService, object()),
        stats=Stats(),
    )

    with TestClient(create_app(services, main.router)) as client:
        response = client.get("/api/runs")

    assert response.json() == [{"id": "injected"}]
