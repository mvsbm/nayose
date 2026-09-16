"""Application-owned service graph and shutdown lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from api.application.admin import AdminService
from api.application.dataset import DatasetService
from api.application.matching import RecommendationService
from api.application.queries import QueryService
from api.application.review import ReviewService
from api.application.search import SearchService
from api.infrastructure.bundles.release import ReleaseCatalog, ReleasePublisher
from api.infrastructure.bundles.repositories import RunRepository
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.audit import AuditLog
from api.infrastructure.persistence.decisions import DecisionsStore
from api.infrastructure.persistence.operational_store import OperationalStore
from api.infrastructure.runtime.resource_manager import ResourceManager
from nayose.config import ConfigSnapshot, RuntimeConfig


@dataclass(slots=True)
class ApplicationServices:
    """Concrete services owned by one FastAPI application instance."""

    config: RuntimeConfig
    operational_store: OperationalStore
    audit: AuditLog
    release_catalog: ReleaseCatalog
    release_publisher: ReleasePublisher
    runs: RunRepository
    queries: QueryService
    recommend: RecommendationService
    decisions: DecisionsStore
    review: ReviewService
    dataset: DatasetService
    admin: AdminService
    search: SearchService
    stats: Stats
    config_snapshot: ConfigSnapshot | None = None
    resource_manager: ResourceManager | None = None

    def close(self) -> None:
        """Release application resources exactly at application shutdown."""
        try:
            self.admin.close()
        finally:
            self.recommend.close()
