"""Composition root for the server-owned application graph."""

from __future__ import annotations

from functools import partial

from api.application import ApplicationServices
from api.application.admin import AdminService
from api.application.dataset import DatasetService
from api.application.matching import ExplainedMatcher, RecommendationService
from api.application.queries import QueryService
from api.application.review import ReviewService
from api.application.search import (
    ExactPageRetriever,
    ExactRetriever,
    FuzzyRetriever,
    SearchService,
    SemanticRetriever,
    SuggestionRetriever,
)
from api.infrastructure.bundles.release import ReleaseCatalog, ReleasePublisher
from api.infrastructure.bundles.repositories import RunRepository
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.audit import AuditLog
from api.infrastructure.persistence.decisions import DecisionsStore
from api.infrastructure.persistence.operational_store import OperationalStore
from api.infrastructure.runtime.embedding_runtime import configure_embedding_stats
from api.infrastructure.runtime.resource_manager import ResourceManager, resource_state_dir
from nayose.config import ConfigSnapshot, RuntimeConfig, load_config


def build_search_service(queries: QueryService, recommend: RecommendationService) -> SearchService:
    """Compose the server-owned search pipeline."""
    return SearchService(
        exact=ExactRetriever(queries),
        fuzzy=FuzzyRetriever(queries),
        semantic=SemanticRetriever(recommend),
        suggest=SuggestionRetriever(queries),
        paged_exact=ExactPageRetriever(queries),
    )


def build_application(
    config: RuntimeConfig, config_snapshot: ConfigSnapshot | None = None
) -> ApplicationServices:
    """Construct all dependencies for one independently owned app instance."""
    snapshot = config_snapshot or load_config()
    stats = Stats()
    configure_embedding_stats(stats)
    resource_manager = ResourceManager(resource_state_dir(config.models_dir))
    operational_store = OperationalStore(
        config.operational_db or config.data_root / "operational.sqlite3", stats=stats
    )
    release_catalog = ReleaseCatalog(config.data_root, config.models_dir)
    release_publisher = ReleasePublisher(release_catalog, operational_store)
    runs = RunRepository(config.models_dir, release_catalog)
    queries = QueryService(runs, config.data_dir, operational_store, stats=stats)
    recommend = RecommendationService(
        runs,
        config.data_dir,
        loader=partial(ExplainedMatcher.load, config_snapshot=snapshot),
        exact_mode=config.exact_mode,
        operational_store=operational_store,
        stats=stats,
    )
    decisions = DecisionsStore(config.models_dir, operational_store, release_catalog)
    audit = AuditLog(operational_store)
    review = ReviewService(decisions, operational_store, audit)
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
            warm_embed=config.warm_embed,
            release_publish=release_publisher.publish,
            decisions_clear=review.clear,
            operational_store=operational_store,
            audit=audit,
            stats=stats,
            config_snapshot=snapshot,
        ),
        search=build_search_service(queries, recommend),
        stats=stats,
        review=review,
        resource_manager=resource_manager,
    )
