"""Typed query facade over immutable model bundles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from api.entity_detail_queries import EntityDetailQueries
from api.entity_graph_projector import EntityGraphProjector
from api.entity_graph_queries import EntityGraphService
from api.entity_graph_repository import DuckDbEntityGraphRepository
from api.entity_queries import EntityQueries
from api.evaluation_queries import EvaluationQueries
from api.query_context import BundleReader, BundleSnapshot
from api.repositories import RunRepository
from api.search_service import SearchFilters


class QueryService:
    """Expose the server query surface without owning query implementations."""

    def __init__(self, repo: RunRepository, data_dir: Path):
        self.context = BundleReader(repo, data_dir)
        self.entities_query = EntityQueries(self.context)
        self.entity_detail_query = EntityDetailQueries(self.context)
        self.entity_graph_query = EntityGraphService(
            DuckDbEntityGraphRepository(self.context), EntityGraphProjector()
        )
        self.evaluation_query = EvaluationQueries(self.context)

    def _cached(self, key: str, operation: Callable[[], Any]) -> Any:
        return self.context.cached(key, operation)

    def _open(self, run_id: str) -> tuple[Any, BundleSnapshot]:
        return self.context.open(run_id)

    def entities(
        self,
        run_id: str,
        q: str,
        limit: int = 50,
        filters: SearchFilters | None = None,
    ) -> list[dict[str, Any]]:
        return self.entities_query.entities(run_id, q, limit, filters)

    def entity_page(
        self,
        run_id: str,
        q: str,
        offset: int = 0,
        limit: int = 50,
        sort: str = "relevance",
        filters: SearchFilters | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return self.entities_query.entity_page(run_id, q, offset, limit, sort, filters, cursor)

    def normalized_query(self, q: str) -> dict[str, str]:
        return self.entities_query.normalized_query(q)

    def _nearest_choices(self, run_dir: Path, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return self.entities_query._nearest_choices(run_dir, fields)

    def nearest_entities(self, run_id: str, q: str, limit: int = 8) -> list[dict[str, Any]]:
        return self.entities_query.nearest_entities(
            run_id, q, limit, nearest_choices=self._nearest_choices
        )

    def suggest(
        self, run_id: str, q: str, nearest: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        if nearest is None:
            nearest = self.nearest_entities(run_id, q, 1)
        return self.entities_query.suggest(run_id, q, nearest)

    def entity(
        self, run_id: str, entity_id: str, offset: int = 0, limit: int = 25
    ) -> dict[str, Any]:
        return self.entity_detail_query.entity(run_id, entity_id, offset, limit)

    def entity_graph(
        self,
        run_id: str,
        entity_id: str,
        record_limit: int = 50,
        potential_limit: int = 20,
        include_potential: bool = False,
    ) -> dict[str, Any]:
        return self.entity_graph_query.graph(
            run_id, entity_id, record_limit, potential_limit, include_potential
        )

    def match_sample(self, run_id: str) -> dict[str, Any]:
        return self.entity_detail_query.match_sample(run_id)

    def review(
        self, run_id: str, min_p: float, offset: int, limit: int, nayose: str = ""
    ) -> dict[str, Any]:
        return self.evaluation_query.review(run_id, min_p, offset, limit, nayose)

    def metrics(self, run_id: str) -> dict[str, Any]:
        return self.evaluation_query.metrics(run_id)

    def confusion(self, run_id: str) -> dict[str, Any]:
        return self.evaluation_query.confusion(run_id)

    def compare(self, run_id: str, left: str, right: str) -> list[dict[str, Any]]:
        return self.evaluation_query.compare(run_id, left, right)

    def run_sql(self, run_id: str, sql: str) -> dict[str, Any]:
        return self.context.run_sql(run_id, sql)
