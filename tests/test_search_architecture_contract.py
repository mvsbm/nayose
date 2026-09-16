"""Strict architecture contracts for the server-owned search pipeline."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEARCH = REPO / "src" / "api" / "application" / "search.py"
SEARCH_ROUTES = REPO / "src" / "api" / "http" / "routers" / "entities.py"
MAIN = REPO / "src" / "api" / "composition.py"
MATCH_VIEW = REPO / "frontend" / "ts" / "match-view.ts"
INFRA = REPO / "frontend" / "ts" / "infra.ts"
MEMO_PROXY = REPO / "frontend" / "ts" / "memo-proxy.ts"

REQUIRED_SEARCH_COMPONENTS = {
    "QueryInterpreter",
    "QueryIntentRule",
    "DefaultQueryNormalizer",
    "ExactRetriever",
    "FuzzyRetriever",
    "SemanticRetriever",
    "CandidateMerger",
    "IntentAwareRanker",
    "SearchResponseBuilder",
    "SuggestionRetriever",
    "SearchService",
}


def _top_level_function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"top-level function {name!r} not found")


def _class(tree: ast.AST, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name!r} not found")


def test_search_components_are_present():
    tree = ast.parse(SEARCH.read_text(encoding="utf-8"))
    classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert classes >= REQUIRED_SEARCH_COMPONENTS


def test_search_service_is_composed_from_ports():
    source = SEARCH.read_text(encoding="utf-8")
    service = _class(ast.parse(source), "SearchService")
    init = next(
        node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    parameters = {arg.arg for arg in init.args.args}
    assert {"exact", "fuzzy", "semantic", "suggest"} <= parameters
    assert "duckdb" not in source
    assert "fetch(" not in source
    assert "if source ==" not in source
    assert "if source in" not in source


def test_search_intent_and_ranking_are_extension_points():
    tree = ast.parse(SEARCH.read_text(encoding="utf-8"))
    interpreter = _class(tree, "QueryInterpreter")
    ranker = _class(tree, "IntentAwareRanker")
    interpreter_init = next(
        node
        for node in interpreter.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    ranker_init = next(
        node
        for node in ranker.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    interpreter_init_source = ast.unparse(interpreter_init)
    ranker_init_source = ast.unparse(ranker_init)
    assert "rules" in interpreter_init_source
    assert "strategies" in ranker_init_source
    assert "ExactFieldRankStrategy" in ranker_init_source
    assert "ScoreRankStrategy" in ranker_init_source


def test_composition_root_has_a_search_factory():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    factory = _top_level_function(tree, "build_search_service")
    source = ast.unparse(factory)
    assert "SearchService" in source
    assert "ExactRetriever" in source
    assert "SemanticRetriever" in source


def test_api_search_endpoint_uses_service_not_retrievers():
    tree = ast.parse(SEARCH_ROUTES.read_text(encoding="utf-8"))
    endpoint = _top_level_function(tree, "entities")
    source = ast.unparse(endpoint)
    assert "get_services" in source
    assert "services.search" in source
    assert "QUERIES" not in source
    assert "RECOMMEND" not in source


def test_search_view_uses_port_and_server_filters():
    source = MATCH_VIEW.read_text(encoding="utf-8")
    assert re.search(r"port\.entities\(value, 50, activeFilters\(\), controller\.signal\)", source)
    assert "fetch(" not in source
    assert "localStorage" not in source
    assert "activeFilters" in source
    assert "party_type" in source
    assert "min_record_count" in source


def test_transport_and_cache_preserve_filter_parameter():
    source = "\n".join(path.read_text(encoding="utf-8") for path in (INFRA, MEMO_PROXY))
    assert re.search(
        r"entities\(q: string, limit\?: number, filters\?: SearchFilters, signal\?: AbortSignal\)",
        source,
    )
    assert "filters?.party_type" in source
    assert "filters?.min_record_count" in source
    assert "JSON.stringify(filters ?? {})" in source
