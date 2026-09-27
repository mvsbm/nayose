from __future__ import annotations

from typing import Any

import pytest

from api.application.search import (
    EntitySearchRequest,
    IntentAwareRanker,
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


def test_structured_query_intent_is_high_confidence():
    interpreter = QueryInterpreter()

    phone = interpreter.interpret("+81 (90) 1234-5678")
    postal = interpreter.interpret("１００−０００１")

    assert phone.structured_fields == (("phone", "09012345678"),)
    assert postal.structured_fields == (("postal_code", "1000001"),)
    assert phone.field_weights["phone"] > phone.field_weights["name_kanji"]
    assert postal.field_weights["postal_code"] > postal.field_weights["name_kanji"]


@pytest.mark.parametrize(
    ("split", "query", "field", "value"),
    [
        ("train", "090-1234-5678", "phone", "09012345678"),
        ("validation", "+81 90 1234 5678", "phone", "09012345678"),
        ("test", "100-0001", "postal_code", "1000001"),
    ],
)
def test_structured_evidence_beats_high_semantic_score(
    split: str, query: str, field: str, value: str
):
    plan = QueryInterpreter().interpret(query)
    hits = [
        hit("semantic-name", field="name_kanji", score=0.99),
        {
            **hit("structured-match", field="semantic", score=0.10),
            field: value,
        },
    ]

    ranked = IntentAwareRanker().rank(hits, plan, "semantic")

    assert split in {"train", "validation", "test"}
    assert [row["nayose_id"] for row in ranked] == ["structured-match", "semantic-name"]


def test_candidate_merger_keeps_provenance_after_reaching_limit():
    from api.application.search import CandidateMerger

    rows = CandidateMerger().merge(
        [
            ("exact", [hit("same", field="name_kanji")]),
            ("semantic", [hit("same", field="semantic", score=0.9)]),
            ("fuzzy", [hit("other", field="name_kanji", score=0.8)]),
        ],
        limit=1,
    )

    assert [row["nayose_id"] for row in rows] == ["same"]
    assert rows[0]["retrieval_sources"] == ["exact", "semantic"]
    assert [item["source"] for item in rows[0]["retrieval_evidence"]] == ["exact", "semantic"]


def test_exact_candidates_are_never_displaced_by_semantic_candidates():
    exact = FakeRetriever([hit(f"exact-{index}", field="phone") for index in range(10)])
    semantic = FakeRetriever([hit("semantic", field="name_kanji", score=0.99)])
    service = SearchService(
        exact=exact,
        fuzzy=FakeRetriever([]),
        semantic=semantic,
        suggest=lambda *_: None,
    )

    rows = service.search("run", "09012345678", limit=20)["rows"]

    assert [row["nayose_id"] for row in rows[:10]] == [f"exact-{index}" for index in range(10)]
    assert rows[10]["nayose_id"] == "semantic"


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
