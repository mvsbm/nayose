"""Production-resolution adapters and decision-quality metrics."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from api.benchmarking.contracts import BenchmarkDataset, BenchmarkQuery


class RecommendationPort(Protocol):
    def recommend(self, run_id: str, record: dict[str, str]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    action: str
    top_entity_id: str | None
    top_probability: float
    margin: float
    ranked_entity_ids: tuple[str, ...]
    diagnostics: Mapping[str, Any]


class NayoseResolutionAdapter:
    """Adapt the existing offline recommendation service to the benchmark port."""

    def __init__(self, service: RecommendationPort):
        self.service = service

    def resolve(self, run_id: str, query: BenchmarkQuery) -> ResolutionResult:
        raw = self.service.recommend(run_id, dict(query.fields))
        candidates = raw.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("recommendation candidates must be a list")
        ranked = tuple(
            str(candidate["nayose_id"])
            for candidate in candidates
            if isinstance(candidate, Mapping) and candidate.get("nayose_id")
        )
        top = raw.get("top_nayose_id")
        top_id = str(top) if top else (ranked[0] if ranked else None)
        action = raw.get("action")
        if not isinstance(action, str):
            raise ValueError("recommendation action is missing")
        return ResolutionResult(
            action=action,
            top_entity_id=top_id,
            top_probability=_finite_number(raw.get("top_probability")),
            margin=_finite_number(raw.get("margin")),
            ranked_entity_ids=ranked,
            diagnostics=raw.get("diagnostics", {})
            if isinstance(raw.get("diagnostics", {}), Mapping)
            else {},
        )


ResolutionFactory = Callable[[str, BenchmarkQuery], ResolutionResult]


@dataclass(frozen=True, slots=True)
class DecisionMetrics:
    split: str
    queries: int
    candidate_recall: float
    top1_identity_accuracy: float
    mean_reciprocal_rank: float
    auto_link_precision: float
    false_auto_link_rate: float
    safe_no_auto_rate: float
    action_accuracy: float

    def as_dict(self) -> dict[str, object]:
        return {
            "queries": self.queries,
            "candidate_recall": self.candidate_recall,
            "top1_identity_accuracy": self.top1_identity_accuracy,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "auto_link_precision": self.auto_link_precision,
            "false_auto_link_rate": self.false_auto_link_rate,
            "safe_no_auto_rate": self.safe_no_auto_rate,
            "action_accuracy": self.action_accuracy,
        }


@dataclass(frozen=True, slots=True)
class ResolutionReport:
    dataset: str
    version: str
    metadata: dict[str, str]
    metrics: dict[str, DecisionMetrics]

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "version": self.version,
            "metadata": self.metadata,
            "splits": {split: metric.as_dict() for split, metric in self.metrics.items()},
        }


def evaluate_resolution(
    dataset: BenchmarkDataset,
    resolver: ResolutionFactory,
    run_id: str,
) -> ResolutionReport:
    dataset.validate()
    grouped: dict[str, list[BenchmarkQuery]] = {}
    for query in dataset.queries:
        grouped.setdefault(query.split, []).append(query)
    metrics = {
        split: _evaluate_split(queries, resolver, run_id) for split, queries in grouped.items()
    }
    return ResolutionReport(
        dataset.name,
        dataset.version,
        {str(key): str(value) for key, value in dataset.metadata.items()},
        metrics,
    )


def _evaluate_split(
    queries: Sequence[BenchmarkQuery], resolver: ResolutionFactory, run_id: str
) -> DecisionMetrics:
    candidate_hits = top1 = action_hits = 0
    reciprocal = 0.0
    auto_links = correct_auto_links = false_auto_links = safe_no_auto = 0
    for query in queries:
        result = resolver(run_id, query)
        targets = query.target_ids
        rank = next(
            (
                index
                for index, entity_id in enumerate(result.ranked_entity_ids, 1)
                if entity_id in targets
            ),
            0,
        )
        has_target = bool(targets)
        if rank:
            candidate_hits += 1
            reciprocal += 1.0 / rank
            top1 += rank == 1
        elif not has_target:
            safe_no_auto += result.action != "AUTO_LINK"
        if has_target and result.top_entity_id in targets and result.action == "AUTO_LINK":
            correct_auto_links += 1
        if result.action == "AUTO_LINK":
            auto_links += 1
            false_auto_links += not has_target or result.top_entity_id not in targets
        action_hits += _action_matches(result.action, query.expected_action)
    count = len(queries)
    denominator = float(count or 1)
    return DecisionMetrics(
        split=queries[0].split if queries else "",
        queries=count,
        candidate_recall=candidate_hits / denominator,
        top1_identity_accuracy=top1 / denominator,
        mean_reciprocal_rank=reciprocal / denominator,
        auto_link_precision=correct_auto_links / float(auto_links or 1),
        false_auto_link_rate=false_auto_links / denominator,
        safe_no_auto_rate=safe_no_auto / denominator,
        action_accuracy=action_hits / denominator,
    )


def _action_matches(actual: str, expected: str) -> bool:
    if expected == "MATCH_OR_REVIEW":
        return actual in {"AUTO_LINK", "REVIEW"}
    if expected == "REVIEW_OR_MINIMAL":
        return actual in {"REVIEW", "NO_MATCH"}
    return _action_family(actual) == _action_family(expected)


def _action_family(action: str) -> str:
    return "NO_MATCH" if action in {"NO_MATCH", "NEW_CUSTOMER"} else action


def _finite_number(value: object) -> float:
    if value is None:
        number = 0.0
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        raise ValueError("recommendation contains a non-numeric value")
    if number != number or number in {float("inf"), float("-inf")}:
        raise ValueError("recommendation contains a non-finite number")
    return number
