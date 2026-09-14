"""Nayose API process entrypoint."""

from __future__ import annotations

import uvicorn

from api.application import ApplicationServices
from api.application.admin import AdminService
from api.application.dataset import DatasetService
from api.application.matching import RecommendationService as RecommendService
from api.application.queries import QueryService
from api.application.search import SearchService
from api.composition import build_application, build_search_service
from api.http.app import create_app
from api.http.contracts import REQUIRED_COLUMNS
from api.http.routers import api_router
from api.infrastructure.bundles.repositories import RunRepository
from nayose.config import load_config

CONFIG_SNAPSHOT = load_config()
CONFIG = CONFIG_SNAPSHOT.runtime
MODELS_DIR = CONFIG.models_dir
DATA_DIR = CONFIG.data_dir
DATA_ROOT = CONFIG.data_root
FRONTEND_DIR = CONFIG.frontend_dir
DEBUG_SQL = CONFIG.debug_sql
WARM_EMBED = CONFIG.warm_embed
DEFAULT_REVIEWER = CONFIG.default_reviewer

APP_SERVICES = build_application(CONFIG, CONFIG_SNAPSHOT)

STATS = APP_SERVICES.stats
RUNS = APP_SERVICES.runs
QUERIES = APP_SERVICES.queries
RECOMMEND = APP_SERVICES.recommend
DECISIONS = APP_SERVICES.decisions
ADMIN = APP_SERVICES.admin
SEARCH_SERVICE = APP_SERVICES.search
router = api_router


def current_search_service() -> SearchService:
    """Return the default app's search service for legacy callers."""
    return APP_SERVICES.search


app = create_app(APP_SERVICES, api_router)


if __name__ == "__main__":
    uvicorn.run(app, host=CONFIG.bind_host, port=CONFIG.port)


__all__ = [
    "ADMIN",
    "APP_SERVICES",
    "CONFIG_SNAPSHOT",
    "DATA_DIR",
    "DATA_ROOT",
    "DEBUG_SQL",
    "DEFAULT_REVIEWER",
    "FRONTEND_DIR",
    "MODELS_DIR",
    "QUERIES",
    "REQUIRED_COLUMNS",
    "RECOMMEND",
    "RUNS",
    "SEARCH_SERVICE",
    "STATS",
    "WARM_EMBED",
    "AdminService",
    "ApplicationServices",
    "DatasetService",
    "QueryService",
    "RecommendService",
    "RunRepository",
    "app",
    "build_application",
    "build_search_service",
    "current_search_service",
    "router",
]
