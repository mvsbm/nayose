"""Nayose API application bootstrap.

Route handlers live in ``api.routers``. This module owns only configuration,
dependency composition, compatibility exports, and the process entrypoint.
"""

from __future__ import annotations

import uvicorn

from api.admin import AdminService
from api.app import create_app
from api.application import ApplicationServices
from api.audit import AuditLog
from api.dataset_service import DatasetService
from api.decisions import DecisionsStore
from api.embedding_runtime import configure_embedding_stats
from api.matching import RecommendationService as RecommendService
from api.operational_store import OperationalStore
from api.query_service import QueryService
from api.release import ReleaseCatalog, ReleasePublisher
from api.repositories import RunRepository
from api.routers import api_router
from api.runtime_config import RuntimeConfig
from api.search_service import (
    ExactPageRetriever,
    ExactRetriever,
    FuzzyRetriever,
    SearchService,
    SemanticRetriever,
    SuggestionRetriever,
)
from api.telemetry import Stats

CONFIG = RuntimeConfig.from_env()
MODELS_DIR = CONFIG.models_dir
DATA_DIR = CONFIG.data_dir
DATA_ROOT = CONFIG.data_root
FRONTEND_DIR = CONFIG.frontend_dir
DEBUG_SQL = CONFIG.debug_sql
WARM_EMBED = CONFIG.warm_embed
DEFAULT_REVIEWER = CONFIG.default_reviewer

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "full_dataset_summary": ["metric", "value"],
    "full_dataset_metrics": ["section", "metric", "value"],
    "pair_confusion_test": ["metric", "value"],
    "query_confusion": ["scenario", "action", "expected_no", "expected_yes"],
    "review_queue": ["left_record_id", "right_record_id"],
    "canonical_entities": ["nayose_id", "name_kanji", "name_kana", "record_count"],
    "record_to_nayose": ["record_id", "nayose_id"],
}


def build_search_service(queries: QueryService, recommend: RecommendService) -> SearchService:
    """Compose the server-owned search pipeline."""
    return SearchService(
        exact=ExactRetriever(queries),
        fuzzy=FuzzyRetriever(queries),
        semantic=SemanticRetriever(recommend),
        suggest=SuggestionRetriever(queries),
        paged_exact=ExactPageRetriever(queries),
    )


def build_application(config: RuntimeConfig) -> ApplicationServices:
    """Construct all dependencies for one independently owned app instance."""
    stats = Stats()
    configure_embedding_stats(stats)
    operational_store = OperationalStore(
        config.operational_db or config.data_root / "operational.sqlite3", stats=stats
    )
    release_catalog = ReleaseCatalog(config.data_root, config.models_dir)
    release_publisher = ReleasePublisher(release_catalog, operational_store)
    runs = RunRepository(config.models_dir, release_catalog)
    queries = QueryService(runs, config.data_dir, operational_store, stats=stats)
    recommend = RecommendService(
        runs,
        config.data_dir,
        exact_mode=config.exact_mode,
        operational_store=operational_store,
        stats=stats,
    )
    decisions = DecisionsStore(config.models_dir, operational_store, release_catalog)
    audit = AuditLog(operational_store)
    return ApplicationServices(
        config=config,
        operational_store=operational_store,
        audit=audit,
        release_catalog=release_catalog,
        release_publisher=release_publisher,
        runs=runs,
        queries=queries,
        recommend=recommend,
        decisions=decisions,
        dataset=DatasetService(config.data_root, stats=stats),
        admin=AdminService(
            config.data_root,
            config.models_dir,
            engine_reset=recommend.reset_engine,
            exact_mode_set=recommend.set_exact_mode,
            exact_mode=config.exact_mode,
            release_publish=release_publisher.publish,
            decisions_clear=decisions.clear,
            operational_store=operational_store,
            audit=audit,
            stats=stats,
        ),
        search=build_search_service(queries, recommend),
        stats=stats,
    )


APP_SERVICES = build_application(CONFIG)

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
