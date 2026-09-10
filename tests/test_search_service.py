from __future__ import annotations

from typing import Any

from api.search_service import (
    EntitySearchRequest,
    QueryInterpreter,
    SearchFilters,
    SearchService,
)


class FakeRetriever:
    name = "fake"

    def __init__(self, rows: list[dict[str, Any]] | None):
        self.rows = rows
        self.calls: list[tuple[str, str, SearchFilters]] = []

    def retrieve(self, run_id, plan, limit):
        self.calls.append((run_id, plan.raw_query, plan.filters))
        return self.rows


class FakePageRetriever:
    def __init__(self, page: dict[str, Any]):
        self.page = page
        self.calls: list[EntitySearchRequest] = []

    def retrieve(self, request: EntitySearchRequest) -> dict[str, Any]:
        self.calls.append(request)
        return self.page


def hit(entity_id: str, *, field: str, party_type: str = "person", score: float = 0.0):
    return {
        "nayose_id": entity_id,
        "name_kanji": entity_id,
        "party_type": party_type,
        "record_count": 1,
        "signals": [field],
        "score": score,
    }


def test_query_interpreter_selects_structured_intent():
    plan = QueryInterpreter().interpret("sato@example.jp")

    assert plan.likely_fields[:3] == ("name_kanji", "name_kana", "name_romaji")
    assert "email" in plan.likely_fields
    assert plan.field_weights["email"] > plan.field_weights["name_kanji"]


def test_search_service_merges_candidates_and_applies_filters():
    exact = FakeRetriever(
        [
            hit("address-hit", field="address", party_type="person"),
            hit("company-hit", field="name_kanji", party_type="company"),
        ]
    )
    semantic = FakeRetriever(
        [hit("semantic-company", field="name_kanji", party_type="company", score=0.99)]
    )
    fuzzy = FakeRetriever([hit("fuzzy-hit", field="name_kanji", score=0.8)])
    service = SearchService(
        exact=exact,
        fuzzy=fuzzy,
        semantic=semantic,
        suggest=lambda *_: None,
    )

    result = service.search(
        "run",
        "東京都",
        filters=SearchFilters(party_type="person"),
    )

    assert [row["nayose_id"] for row in result["rows"]] == ["address-hit"]
    assert result["rows"][0]["retrieval_sources"] == ["exact"]
    assert result["query_plan"]["likely_fields"][-1] == "address"
    assert result["search_trace"]["candidate_counts"]["exact"] == 1
    assert exact.calls[0][2].party_type == "person"


def test_search_service_explorer_uses_shared_plan_and_exhaustive_page():
    page = FakePageRetriever(
        {
            "rows": [hit("entity", field="name_kanji")],
            "total": 1,
            "offset": 0,
            "limit": 25,
            "query": "佐藤",
            "sort": "name",
            "filters": {"party_type": "person"},
        }
    )
    service = SearchService(
        exact=FakeRetriever([]),
        fuzzy=FakeRetriever([]),
        semantic=FakeRetriever([]),
        suggest=lambda *_: None,
        paged_exact=page,
    )

    result = service.explore(
        EntitySearchRequest(
            run_id="run",
            query="佐藤",
            sort="name",
            filters=SearchFilters(party_type="person"),
        )
    )

    assert result["search_mode"] == "exact"
    assert result["total_is_exact"] is True
    assert result["query_plan"]["filters"] == {"party_type": "person"}
    assert page.calls[0].sort == "name"


def test_search_service_explorer_reports_bounded_hybrid_fallback():
    page = FakePageRetriever(
        {
            "rows": [],
            "total": 0,
            "offset": 0,
            "limit": 25,
            "query": "佐藤",
            "sort": "relevance",
            "filters": {},
        }
    )
    service = SearchService(
        exact=FakeRetriever([]),
        fuzzy=FakeRetriever([hit("nearest", field="name_kanji", score=0.8)]),
        semantic=FakeRetriever(None),
        suggest=lambda *_: None,
        paged_exact=page,
    )

    result = service.explore(EntitySearchRequest(run_id="run", query="佐藤"))

    assert result["search_mode"] == "hybrid_fallback"
    assert result["total_is_exact"] is False
    assert result["rows"][0]["nayose_id"] == "nearest"


def test_search_service_preserves_fallback_mode_without_semantic_backend():
    service = SearchService(
        exact=FakeRetriever([]),
        fuzzy=FakeRetriever([hit("nearest", field="name_kanji", score=0.8)]),
        semantic=FakeRetriever(None),
        suggest=lambda *_: None,
    )

    result = service.search("run", "佐藤")

    assert result["semantic"] is False
    assert result["mode"] == "exact_plus_fuzzy"
    assert result["rows"][0]["nayose_id"] == "nearest"
