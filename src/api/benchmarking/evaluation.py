"""Ranking evaluation independent of the production matching runtime."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from api.benchmarking.contracts import BenchmarkDataset, BenchmarkEntity, BenchmarkQuery
from api.domain.normalize import normalize_search_field


class BenchmarkScorer(Protocol):
    def score(self, query: BenchmarkQuery, entity: BenchmarkEntity) -> float: ...


class CandidateProvider(Protocol):
    def candidates(
        self, query: BenchmarkQuery, entities: Sequence[BenchmarkEntity]
    ) -> Iterable[BenchmarkEntity]: ...


class AllCandidateProvider:
    def candidates(
        self, query: BenchmarkQuery, entities: Sequence[BenchmarkEntity]
    ) -> Iterable[BenchmarkEntity]:
        return entities


class NormalizedFieldCandidateProvider:
    """Exact normalized blocking with an explicit all-records fallback."""

    _FIELDS = (
        "phone",
        "email",
        "postal_code",
        "date_birth",
        "name_kanji",
        "name_kana",
        "name_romaji",
    )

    def __init__(self, entities: Sequence[BenchmarkEntity]):
        self._entities = tuple(entities)
        self._index: dict[str, dict[str, set[str]]] = {field: {} for field in self._FIELDS}
        for entity in self._entities:
            for field in self._FIELDS:
                value = normalize_search_field(field, entity.fields.get(field, ""))
                if value:
                    self._index[field].setdefault(value, set()).add(entity.entity_id)

    def candidates(
        self, query: BenchmarkQuery, entities: Sequence[BenchmarkEntity]
    ) -> Iterable[BenchmarkEntity]:
        candidate_ids: set[str] = set()
        for field in self._FIELDS:
            value = normalize_search_field(field, query.fields.get(field, ""))
            if value:
                candidate_ids.update(self._index[field].get(value, set()))
        if not candidate_ids:
            return entities
        return (entity for entity in entities if entity.entity_id in candidate_ids)


class NormalizedFieldScorer:
    """Transparent baseline for validating an external dataset adapter."""

    _WEIGHTS = {
        "phone": 3.0,
        "email": 3.0,
        "postal_code": 2.0,
        "date_birth": 2.0,
        "name_kanji": 1.0,
        "name_kana": 1.0,
        "name_romaji": 1.0,
        "address": 1.0,
        "party_type": 0.5,
    }

    def score(self, query: BenchmarkQuery, entity: BenchmarkEntity) -> float:
        total = 0.0
        weight = 0.0
        for field, field_weight in self._WEIGHTS.items():
            left = normalize_search_field(field, query.fields.get(field, ""))
            right = normalize_search_field(field, entity.fields.get(field, ""))
            if not left or not right:
                continue
            total += field_weight * (
                1.0 if left == right else SequenceMatcher(None, left, right).ratio()
            )
            weight += field_weight
        return total / weight if weight else 0.0


@dataclass(frozen=True, slots=True)
class SplitMetrics:
    split: str
    queries: int
    candidate_recall: float
    top1_accuracy: float
    recall_at: dict[str, float]
    mean_reciprocal_rank: float
    mean_candidates: float

    def as_dict(self) -> dict[str, object]:
        return {
            "queries": self.queries,
            "candidate_recall": self.candidate_recall,
            "top1_accuracy": self.top1_accuracy,
            "recall_at": self.recall_at,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "mean_candidates": self.mean_candidates,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    dataset: str
    version: str
    metadata: dict[str, str]
    metrics: dict[str, SplitMetrics]
    slices: dict[str, dict[str, SplitMetrics]]

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "version": self.version,
            "metadata": self.metadata,
            "splits": {split: metric.as_dict() for split, metric in self.metrics.items()},
            "slices": {
                name: {split: metric.as_dict() for split, metric in values.items()}
                for name, values in self.slices.items()
            },
        }


def evaluate_ranking(
    dataset: BenchmarkDataset,
    scorer: BenchmarkScorer,
    candidate_provider: CandidateProvider | None = None,
    ks: tuple[int, ...] = (1, 5, 10),
) -> BenchmarkReport:
    dataset.validate()
    if not ks or any(k < 1 for k in ks) or tuple(sorted(set(ks))) != ks:
        raise ValueError("ks must be positive and sorted without duplicates")
    provider = candidate_provider or AllCandidateProvider()
    entities = dataset.entities
    by_split: dict[str, list[BenchmarkQuery]] = {}
    by_slice: dict[str, dict[str, list[BenchmarkQuery]]] = {}
    for query in dataset.queries:
        by_split.setdefault(query.split, []).append(query)
        by_slice.setdefault(query.slice_name, {}).setdefault(query.split, []).append(query)
    metrics = {
        split: _evaluate_group(queries, entities, scorer, provider, ks)
        for split, queries in by_split.items()
    }
    slices = {
        name: {
            split: _evaluate_group(queries, entities, scorer, provider, ks)
            for split, queries in split_groups.items()
        }
        for name, split_groups in by_slice.items()
        if name != "overall"
    }
    return BenchmarkReport(
        dataset.name,
        dataset.version,
        {str(key): str(value) for key, value in dataset.metadata.items()},
        metrics,
        slices,
    )


def _evaluate_group(
    queries: Sequence[BenchmarkQuery],
    entities: Sequence[BenchmarkEntity],
    scorer: BenchmarkScorer,
    provider: CandidateProvider,
    ks: tuple[int, ...],
) -> SplitMetrics:
    hits = {k: 0 for k in ks}
    candidate_hits = 0
    top1 = 0
    reciprocal = 0.0
    candidates_total = 0
    for query in queries:
        candidates = list(provider.candidates(query, entities))
        candidates_total += len(candidates)
        ranked = sorted(
            candidates,
            key=lambda entity: (-float(scorer.score(query, entity)), entity.entity_id),
        )
        target_rank = next(
            (
                index
                for index, entity in enumerate(ranked, start=1)
                if entity.entity_id == query.target_entity_id
            ),
            0,
        )
        if target_rank:
            candidate_hits += 1
            reciprocal += 1.0 / target_rank
            for k in ks:
                hits[k] += target_rank <= k
            top1 += target_rank == 1
    count = len(queries)
    denominator = float(count or 1)
    return SplitMetrics(
        split=queries[0].split if queries else "",
        queries=count,
        candidate_recall=candidate_hits / denominator,
        top1_accuracy=top1 / denominator,
        recall_at={str(k): hits[k] / denominator for k in ks},
        mean_reciprocal_rank=reciprocal / denominator,
        mean_candidates=candidates_total / denominator,
    )
