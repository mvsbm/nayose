"""Application-owned service graph and shutdown lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from api.admin import AdminService
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
    dataset: DatasetService
    admin: AdminService
    search: SearchService
    stats: Stats

    def close(self) -> None:
        """Release application resources exactly at application shutdown."""
        try:
            self.admin.close()
        finally:
            self.recommend.close()
