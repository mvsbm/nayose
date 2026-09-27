"""Stable contracts for evaluating noisy entity-resolution datasets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

VISIBLE_FIELDS = frozenset(
    {
        "party_type",
        "name_kanji",
        "name_kana",
        "name_romaji",
        "date_birth",
        "postal_code",
        "address",
        "phone",
        "email",
    }
)
HIDDEN_FIELDS = frozenset(
    {
        "truth_id",
        "target_truth_id",
        "relation_id",
        "scenario",
        "hard_case",
        "split",
        "record_quality",
        "quality_band",
        "transformation",
    }
)
SPLIT_ALIASES = {
    "train": "train",
    "val": "validation",
    "validation": "validation",
    "test": "test",
    "calibration": "validation",
}
REQUIRED_SPLITS = frozenset({"train", "validation", "test"})
EXPECTED_ACTIONS = frozenset(
    {
        "AUTO_LINK",
        "REVIEW",
        "NO_MATCH",
        "NEW_CUSTOMER",
        "EXISTING",
        "MATCH_OR_REVIEW",
        "REVIEW_OR_MINIMAL",
    }
)
NO_MATCH_ACTIONS = frozenset({"NO_MATCH", "NEW_CUSTOMER"})


@dataclass(frozen=True, slots=True)
class BenchmarkEntity:
    entity_id: str
    fields: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class BenchmarkQuery:
    query_id: str
    target_entity_id: str | None
    split: str
    fields: Mapping[str, str]
    slice_name: str = "overall"
    expected_action: str = "EXISTING"
    target_entity_ids: frozenset[str] = frozenset()
    related_entity_ids: frozenset[str] = frozenset()

    @property
    def target_ids(self) -> frozenset[str]:
        if self.target_entity_ids:
            return self.target_entity_ids
        return frozenset({self.target_entity_id}) if self.target_entity_id else frozenset()


@dataclass(frozen=True, slots=True)
class BenchmarkDataset:
    name: str
    version: str
    license: str
    entities: tuple[BenchmarkEntity, ...]
    queries: tuple[BenchmarkQuery, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)

    def validate(self, *, require_all_splits: bool = True) -> None:
        if not self.name.strip() or not self.version.strip() or not self.license.strip():
            raise ValueError("benchmark name, version, and license are required")
        entity_ids = [entity.entity_id for entity in self.entities]
        query_ids = [query.query_id for query in self.queries]
        if not entity_ids or len(entity_ids) != len(set(entity_ids)):
            raise ValueError("benchmark entity IDs must be non-empty and unique")
        if not query_ids or len(query_ids) != len(set(query_ids)):
            raise ValueError("benchmark query IDs must be non-empty and unique")
        known_entities = set(entity_ids)
        for entity in self.entities:
            _validate_fields(entity.fields, "entity")
        for query in self.queries:
            _validate_fields(query.fields, "query")
            if query.expected_action not in EXPECTED_ACTIONS:
                raise ValueError(f"unsupported expected action: {query.expected_action}")
            unknown_targets = query.target_ids - known_entities
            if unknown_targets:
                raise ValueError(f"query targets are not present: {sorted(unknown_targets)}")
            unknown_related = query.related_entity_ids - known_entities
            if unknown_related:
                raise ValueError(f"related entities are not present: {sorted(unknown_related)}")
            if not query.target_ids and query.expected_action not in NO_MATCH_ACTIONS:
                raise ValueError(f"query has no target: {query.query_id}")
            if query.target_ids and query.expected_action in NO_MATCH_ACTIONS:
                raise ValueError(f"no-match query has targets: {query.query_id}")
            if query.split not in REQUIRED_SPLITS:
                raise ValueError(f"unsupported benchmark split: {query.split}")
        split_targets: dict[str, set[str]] = {}
        for query in self.queries:
            split_targets.setdefault(query.split, set()).update(query.target_ids)
        target_sets = list(split_targets.values())
        for index, left in enumerate(target_sets):
            for right in target_sets[index + 1 :]:
                if left & right:
                    raise ValueError("benchmark targets must be entity-disjoint across splits")
        if require_all_splits and set(split_targets) != REQUIRED_SPLITS:
            missing = sorted(REQUIRED_SPLITS - set(split_targets))
            raise ValueError(f"benchmark is missing required splits: {missing}")


def canonical_split(raw: str) -> str:
    try:
        return SPLIT_ALIASES[raw.strip().casefold()]
    except KeyError as exc:
        raise ValueError(f"unsupported benchmark split: {raw!r}") from exc


def _validate_fields(fields: Mapping[str, str], kind: str) -> None:
    unknown = set(fields) - VISIBLE_FIELDS
    if unknown:
        raise ValueError(f"{kind} contains unsupported fields: {sorted(unknown)}")
    leaked = set(fields) & HIDDEN_FIELDS
    if leaked:
        raise ValueError(f"hidden benchmark fields leaked into {kind}: {sorted(leaked)}")
