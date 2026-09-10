"""Application service for Entity Explorer graph projections."""

from __future__ import annotations

from api.entity_graph_projector import EntityGraphProjector
from api.entity_graph_repository import EntityGraphRepository


class EntityGraphService:
    """Coordinate graph snapshot loading and pure DTO projection."""

    def __init__(self, repository: EntityGraphRepository, projector: EntityGraphProjector):
        self.repository = repository
        self.projector = projector

    def graph(
        self,
        run_id: str,
        entity_id: str,
        record_limit: int = 50,
        potential_limit: int = 20,
        include_potential: bool = False,
    ) -> dict[str, object]:
        record_cap = min(max(1, int(record_limit)), 100)
        potential_cap = min(max(1, int(potential_limit)), 50)
        snapshot = self.repository.snapshot(
            run_id,
            entity_id,
            record_cap,
            potential_cap,
            include_potential,
        )
        return self.projector.project(snapshot)
