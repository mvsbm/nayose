from api.infrastructure.read_model.entity_graph_projector import EntityGraphProjector
from api.infrastructure.read_model.entity_graph_repository import (
    EntityGraphSnapshot,
    EntitySummary,
    PotentialMatch,
    SourceRecord,
)


def test_graph_projector_labels_observed_and_potential_edges_without_hidden_fields():
    snapshot = EntityGraphSnapshot(
        entity=EntitySummary("N1", "佐藤 太郎", 1, "person"),
        records=(SourceRecord("R1", "佐藤 太郎", "web", "2026-01-01", "P1", "A1"),),
        records_truncated=False,
        potential_matches=(PotentialMatch("N2", "鈴木 花子", "R1", "R2", 0.81, 2),),
        potential_matches_available=True,
        potential_matches_truncated=False,
    )

    graph = EntityGraphProjector().project(snapshot)

    assert {edge["kind"] for edge in graph["edges"]} == {"observed", "potential"}
    assert any(edge["evidence"]["source"] == "reference_records" for edge in graph["edges"])
    assert any(edge["evidence"]["source"] == "review_queue" for edge in graph["edges"])
    assert all("truth_id" not in node for node in graph["nodes"])
