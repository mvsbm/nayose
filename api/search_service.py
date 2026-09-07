"""Composable server-owned search orchestration.

The service separates query interpretation, candidate retrieval, candidate merging,
and response construction. Concrete data services are injected through small
retriever strategies so new retrieval backends do not change the API facade.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from api.normalize import normalize_query_fields


@dataclass(frozen=True)
class SearchFilters:
    party_type: str | None = None
    min_record_count: int | None = None

    def matches(self, hit: dict[str, Any]) -> bool:
        party_matches = not self.party_type or (
            str(hit.get("party_type") or "").casefold() == self.party_type.casefold()
        )
        count_matches = self.min_record_count is None or (
            int(hit.get("record_count") or 0) >= self.min_record_count
        )
        return party_matches and count_matches

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "party_type": self.party_type,
                "min_record_count": self.min_record_count,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class QueryPlan:
    raw_query: str
    tokens: tuple[str, ...]
    normalized_fields: dict[str, str]
    likely_fields: tuple[str, ...]
    field_weights: dict[str, float]
    filters: SearchFilters

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.raw_query,
            "tokens": list(self.tokens),
            "normalized_fields": self.normalized_fields,
            "likely_fields": list(self.likely_fields),
            "field_weights": self.field_weights,
            "filters": self.filters.as_dict(),
        }


@dataclass(frozen=True)
class IntentSignal:
    fields: tuple[str, ...]
    weights: dict[str, float]


class QueryNormalizer(Protocol):
    def normalize(self, query: str) -> dict[str, str]: ...


class DefaultQueryNormalizer:
    def normalize(self, query: str) -> dict[str, str]:
        return normalize_query_fields(query)


class QueryIntentRule(Protocol):
    def detect(self, query: str) -> IntentSignal | None: ...


class EmailIntentRule:
    def detect(self, query: str) -> IntentSignal | None:
        return IntentSignal(("email",), {"email": 1.4}) if "@" in query else None


class NumericIntentRule:
    def detect(self, query: str) -> IntentSignal | None:
        if not any(char.isdigit() for char in query):
            return None
        return IntentSignal(
            ("phone", "postal_code", "date_birth"),
            {"phone": 1.35, "postal_code": 1.3, "date_birth": 1.2},
        )


class AddressIntentRule:
    _TOKENS = ("住所", "都", "道", "府", "県", "市", "区", "町", "street", "avenue")

    def detect(self, query: str) -> IntentSignal | None:
        if not any(token in query.casefold() for token in self._TOKENS):
            return None
        return IntentSignal(("address",), {"address": 1.25})


class PartyTypeIntentRule:
    _TOKENS = ("個人", "法人", "person", "company", "corporation")

    def detect(self, query: str) -> IntentSignal | None:
        if not any(token in query.casefold() for token in self._TOKENS):
            return None
        return IntentSignal(("party_type",), {"party_type": 1.25})


class QueryInterpreter:
    """Compose query normalization and intent rules into an immutable plan."""

    _STOPWORDS = frozenset(
        {
            "a",
            "an",
            "and",
            "for",
            "in",
            "of",
            "or",
            "the",
            "with",
            "customer",
            "customers",
            "顧客",
            "の",
            "に",
            "で",
            "を",
        }
    )

    def __init__(
        self,
        normalizer: QueryNormalizer | None = None,
        rules: tuple[QueryIntentRule, ...] | None = None,
    ):
        self.normalizer = normalizer or DefaultQueryNormalizer()
        self.rules = (
            rules
            if rules is not None
            else (
                EmailIntentRule(),
                NumericIntentRule(),
                AddressIntentRule(),
                PartyTypeIntentRule(),
            )
        )

    def interpret(self, query: str, filters: SearchFilters | None = None) -> QueryPlan:
        raw = query.strip()
        tokens = tuple(token for token in raw.split() if token)
        useful = (
            tuple(token for token in tokens if token.casefold() not in self._STOPWORDS) or tokens
        )
        fields = ["name_kanji", "name_kana", "name_romaji"]
        weights = {field: 1.0 for field in fields}
        for rule in self.rules:
            signal = rule.detect(raw)
            if signal:
                fields.extend(signal.fields)
                weights.update(signal.weights)
        return QueryPlan(
            raw_query=raw,
            tokens=useful,
            normalized_fields=self.normalizer.normalize(raw),
            likely_fields=tuple(dict.fromkeys(fields)),
            field_weights=weights,
            filters=filters or SearchFilters(),
        )


class ExactService(Protocol):
    def entities(
        self,
        run_id: str,
        query: str,
        limit: int,
        filters: SearchFilters,
    ) -> list[dict[str, Any]]: ...


class FuzzyService(Protocol):
    def nearest_entities(self, run_id: str, query: str, limit: int) -> list[dict[str, Any]]: ...


class SemanticService(Protocol):
    def search_entities(
        self, run_id: str, query: str, limit: int
    ) -> list[dict[str, Any]] | None: ...


class SuggestionService(Protocol):
    def suggest(
        self,
        run_id: str,
        query: str,
        nearest: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None: ...


class SuggestionProvider(Protocol):
    def __call__(
        self, run_id: str, query: str, nearest: list[dict[str, Any]] | None
    ) -> dict[str, Any] | None: ...


class SuggestionRetriever:
    """Adapter that exposes the legacy suggestion service as a provider."""

    def __init__(self, service: SuggestionService):
        self.service = service

    def __call__(
        self, run_id: str, query: str, nearest: list[dict[str, Any]] | None
    ) -> dict[str, Any] | None:
        return self.service.suggest(run_id, query, nearest)


class CandidateRetriever(Protocol):
    name: str

    def retrieve(self, run_id: str, plan: QueryPlan, limit: int) -> list[dict[str, Any]] | None: ...


class ExactRetriever:
    name = "exact"

    def __init__(self, service: ExactService):
        self.service = service

    def retrieve(self, run_id: str, plan: QueryPlan, limit: int) -> list[dict[str, Any]]:
        return self.service.entities(run_id, plan.raw_query, limit, plan.filters)


class FuzzyRetriever:
    name = "fuzzy"

    def __init__(self, service: FuzzyService):
        self.service = service

    def retrieve(self, run_id: str, plan: QueryPlan, limit: int) -> list[dict[str, Any]]:
        return self.service.nearest_entities(run_id, plan.raw_query, limit)


class SemanticRetriever:
    name = "semantic"

    def __init__(self, service: SemanticService):
        self.service = service

    def retrieve(self, run_id: str, plan: QueryPlan, limit: int) -> list[dict[str, Any]] | None:
        return self.service.search_entities(run_id, plan.raw_query, limit)


class CandidateMerger:
    """Composite that unions ordered candidate streams by entity identity."""

    def merge(
        self,
        groups: list[tuple[str, list[dict[str, Any]]]],
        limit: int,
    ) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        ordered: list[dict[str, Any]] = []
        for source, group in groups:
            for hit in group:
                entity_id = str(hit.get("nayose_id") or "")
                if not entity_id:
                    continue
                evidence = {
                    "source": source,
                    "field": hit.get("match_field") or (hit.get("signals") or [None])[0],
                    "score": hit.get("score"),
                }
                current = by_id.get(entity_id)
                if current is not None:
                    if source not in current["retrieval_sources"]:
                        current["retrieval_sources"].append(source)
                        current["retrieval_evidence"].append(evidence)
                    continue
                current = {
                    **hit,
                    "retrieval_sources": [source],
                    "retrieval_evidence": [evidence],
                }
                by_id[entity_id] = current
                ordered.append(current)
                if len(ordered) >= limit:
                    return ordered
        return ordered


class RankStrategy(Protocol):
    def rank(self, hits: list[dict[str, Any]], plan: QueryPlan) -> list[dict[str, Any]]: ...


class IdentityRankStrategy:
    def rank(self, hits: list[dict[str, Any]], plan: QueryPlan) -> list[dict[str, Any]]:
        return hits


class ExactFieldRankStrategy:
    def rank(self, hits: list[dict[str, Any]], plan: QueryPlan) -> list[dict[str, Any]]:
        return sorted(
            hits,
            key=lambda hit: (
                -self._field_priority(hit, plan),
                str(hit.get("name_kanji") or ""),
                str(hit.get("nayose_id") or ""),
            ),
        )

    @staticmethod
    def _field_priority(hit: dict[str, Any], plan: QueryPlan) -> float:
        fields = list(hit.get("signals") or [])
        match_field = hit.get("match_field")
        if match_field:
            fields.append(str(match_field))
        return max((plan.field_weights.get(field, 0.0) for field in fields), default=0.0)


class ScoreRankStrategy:
    def rank(self, hits: list[dict[str, Any]], plan: QueryPlan) -> list[dict[str, Any]]:
        return sorted(
            hits,
            key=lambda hit: (
                -float(hit.get("score") or 0),
                -int(hit.get("record_count") or 0),
                str(hit.get("nayose_id") or ""),
            ),
        )


class IntentAwareRanker:
    """Strategy registry for source-specific candidate ranking."""

    def __init__(self, strategies: dict[str, RankStrategy] | None = None):
        self.strategies: dict[str, RankStrategy] = (
            strategies
            if strategies is not None
            else {
                "exact": ExactFieldRankStrategy(),
                "fuzzy": ScoreRankStrategy(),
                "semantic": ScoreRankStrategy(),
            }
        )
        self.fallback: RankStrategy = IdentityRankStrategy()

    def rank(
        self,
        hits: list[dict[str, Any]],
        plan: QueryPlan,
        source: str,
    ) -> list[dict[str, Any]]:
        return self.strategies.get(source, self.fallback).rank(hits, plan)


class SearchTelemetry:
    """Per-request timing collector used by the response builder."""

    def __init__(self):
        self.latency_ms: dict[str, float] = {}

    def measure(self, name: str, operation: Callable[[], Any]) -> Any:
        started = perf_counter()
        result = operation()
        self.latency_ms[name] = _milliseconds(started)
        return result


class SearchResponseBuilder:
    """Builder that keeps the public response stable while adding diagnostics."""

    def build(
        self,
        plan: QueryPlan,
        rows: list[dict[str, Any]],
        exact: list[dict[str, Any]],
        nearest: list[dict[str, Any]],
        semantic_available: bool,
        telemetry: SearchTelemetry,
        semantic_results: list[dict[str, Any]] | None = None,
        suggestion: dict[str, Any] | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        return {
            "rows": rows,
            "exact": exact,
            "semantic_results": semantic_results or [],
            "nearest": nearest,
            "semantic": semantic_available,
            "mode": self._mode(plan, semantic_available),
            "embedding_backend": backend,
            "query": plan.raw_query,
            "normalized_query": plan.normalized_fields,
            "query_plan": plan.as_dict(),
            "search_trace": {
                "latency_ms": telemetry.latency_ms,
                "candidate_counts": {
                    "rows": len(rows),
                    "exact": len(exact),
                    "nearest": len(nearest),
                    "semantic": len(semantic_results or []),
                },
            },
            "suggestion": suggestion,
        }

    @staticmethod
    def _mode(plan: QueryPlan, semantic_available: bool) -> str:
        if not plan.raw_query:
            return "browse"
        return "hybrid" if semantic_available else "exact_plus_fuzzy"


class QueryPlanner(Protocol):
    def interpret(self, query: str, filters: SearchFilters | None = None) -> QueryPlan: ...


class CandidateCombiner(Protocol):
    def merge(
        self,
        groups: list[tuple[str, list[dict[str, Any]]]],
        limit: int,
    ) -> list[dict[str, Any]]: ...


class CandidateRanker(Protocol):
    def rank(
        self,
        hits: list[dict[str, Any]],
        plan: QueryPlan,
        source: str,
    ) -> list[dict[str, Any]]: ...


class ResponseBuilder(Protocol):
    def build(
        self,
        plan: QueryPlan,
        rows: list[dict[str, Any]],
        exact: list[dict[str, Any]],
        nearest: list[dict[str, Any]],
        semantic_available: bool,
        telemetry: SearchTelemetry,
        semantic_results: list[dict[str, Any]] | None = None,
        suggestion: dict[str, Any] | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]: ...


class SearchService:
    """Facade for the complete search pipeline."""

    def __init__(
        self,
        exact: CandidateRetriever,
        fuzzy: CandidateRetriever,
        semantic: CandidateRetriever,
        suggest: SuggestionProvider,
        interpreter: QueryPlanner | None = None,
        merger: CandidateCombiner | None = None,
        ranker: CandidateRanker | None = None,
        response_builder: ResponseBuilder | None = None,
    ):
        self.exact = exact
        self.fuzzy = fuzzy
        self.semantic = semantic
        self.suggest = suggest
        self.interpreter = interpreter if interpreter is not None else QueryInterpreter()
        self.merger = merger if merger is not None else CandidateMerger()
        self.ranker = ranker if ranker is not None else IntentAwareRanker()
        self.response_builder = (
            response_builder if response_builder is not None else SearchResponseBuilder()
        )

    def search(
        self,
        run_id: str,
        query: str,
        limit: int = 50,
        filters: SearchFilters | None = None,
        semantic: bool = True,
    ) -> dict[str, Any]:
        plan = self.interpreter.interpret(query, filters)
        telemetry = SearchTelemetry()
        exact = telemetry.measure(
            "exact", lambda: self.exact.retrieve(run_id, plan, limit) or []
        )
        exact = self._apply_filters(exact, plan.filters)
        exact = self.ranker.rank(exact, plan, "exact")
        if not plan.raw_query:
            return self.response_builder.build(
                plan, exact[:limit], exact[:limit], [], False, telemetry
            )

        ranked = (
            telemetry.measure(
                "semantic", lambda: self.semantic.retrieve(run_id, plan, limit)
            )
            if semantic
            else None
        )
        ranked = self._apply_filters(ranked, plan.filters) if ranked is not None else None
        if ranked is not None:
            ranked = self.ranker.rank(ranked, plan, "semantic")
        fuzzy: list[dict[str, Any]] = []
        if not exact:
            fuzzy = telemetry.measure(
                "fuzzy", lambda: self.fuzzy.retrieve(run_id, plan, min(limit, 8)) or []
            )
            fuzzy = self.ranker.rank(self._apply_filters(fuzzy, plan.filters), plan, "fuzzy")
        nearest = fuzzy
        exact_head = exact[:8]
        merged = self.merger.merge(
            [
                ("exact", exact_head),
                ("semantic", ranked or []),
                ("fuzzy", fuzzy),
                ("exact", exact),
            ],
            limit,
        )
        suggestion = self.suggest(run_id, plan.raw_query, fuzzy[:1]) if not exact else None
        backend = (ranked or [{}])[0].get("search_backend") if ranked else None
        return self.response_builder.build(
            plan,
            merged,
            exact[:limit],
            nearest[:limit],
            ranked is not None,
            telemetry,
            semantic_results=ranked[:limit] if ranked else [],
            suggestion=suggestion,
            backend=backend,
        )

    @staticmethod
    def _apply_filters(
        hits: list[dict[str, Any]], filters: SearchFilters
    ) -> list[dict[str, Any]]:
        return [hit for hit in hits if filters.matches(hit)]


def _milliseconds(started: float) -> float:
    return round((perf_counter() - started) * 1000, 1)
