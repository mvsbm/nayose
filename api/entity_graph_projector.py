"""Pure projection from relationship snapshots to graph DTOs."""

from __future__ import annotations

from typing import Any

from api.entity_graph_repository import EntityGraphSnapshot, PotentialMatch, SourceRecord


class EntityGraphProjector:
    """Build a bounded graph while preserving relationship provenance."""

    def project(self, snapshot: EntityGraphSnapshot) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        entity_node = f"entity:{snapshot.entity.entity_id}"
        self._node(
            nodes,
            node_ids,
            entity_node,
            "entity",
            snapshot.entity.name or snapshot.entity.entity_id,
            {
                "nayose_id": snapshot.entity.entity_id,
                "record_count": snapshot.entity.record_count,
                "party_type": snapshot.entity.party_type,
            },
        )
        for record in snapshot.records:
            self._record(nodes, edges, node_ids, entity_node, record)
        for potential in snapshot.potential_matches:
            self._potential(nodes, edges, node_ids, entity_node, potential)
        return {
            "entity_id": snapshot.entity.entity_id,
            "nodes": nodes,
            "edges": edges,
            "truncated": snapshot.records_truncated or snapshot.potential_matches_truncated,
            "potential_matches_available": snapshot.potential_matches_available,
        }

    def _record(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        node_ids: set[str],
        entity_node: str,
        record: SourceRecord,
    ) -> None:
        record_node = f"record:{record.record_id}"
        self._node(
            nodes,
            node_ids,
            record_node,
            "record",
            record.record_id,
            {"name_kanji": record.name, "observed_at": record.observed_at},
        )
        evidence = {"source": "reference_records", "record_id": record.record_id}
        self._edge(edges, entity_node, record_node, "observed", "member_record", evidence)
        for kind, value, relation in (
            ("policy", record.policy_id, "observed_policy"),
            ("agency", record.agency_id, "observed_agency"),
            ("channel", record.channel, "source_channel"),
        ):
            if not value:
                continue
            target = f"{kind}:{value}"
            self._node(nodes, node_ids, target, kind, value, {})
            self._edge(edges, record_node, target, "observed", relation, evidence)

    def _potential(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        node_ids: set[str],
        entity_node: str,
        potential: PotentialMatch,
    ) -> None:
        target = f"entity:{potential.other_entity_id}"
        self._node(
            nodes,
            node_ids,
            target,
            "entity",
            potential.other_entity_name or potential.other_entity_id,
            {"nayose_id": potential.other_entity_id},
        )
        self._edge(
            edges,
            entity_node,
            target,
            "potential",
            "potential_match",
            {
                "source": "review_queue",
                "method": "candidate_pair",
                "pair_probability": potential.pair_probability,
                "exact_count": potential.exact_count,
                "left_record_id": potential.left_record_id,
                "right_record_id": potential.right_record_id,
            },
        )

    @staticmethod
    def _node(
        nodes: list[dict[str, Any]],
        node_ids: set[str],
        node_id: str,
        kind: str,
        label: str,
        metadata: dict[str, Any],
    ) -> None:
        if node_id in node_ids:
            return
        node_ids.add(node_id)
        nodes.append({"id": node_id, "kind": kind, "label": label, "metadata": metadata})

    @staticmethod
    def _edge(
        edges: list[dict[str, Any]],
        source: str,
        target: str,
        kind: str,
        relation: str,
        evidence: dict[str, Any],
    ) -> None:
        edges.append(
            {
                "source": source,
                "target": target,
                "kind": kind,
                "relation": relation,
                "evidence": evidence,
            }
        )
