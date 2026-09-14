from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_engine_is_an_artifact_loader_not_a_second_matcher() -> None:
    source = (REPO / "api" / "infrastructure" / "runtime" / "engine.py").read_text(encoding="utf-8")

    assert "class RecommendationRunner" not in source
    assert "class Recommendation" not in source
    assert "self._runner" not in source
    assert "def recommend(" not in source


def test_matching_pipeline_owns_recommendation_orchestration() -> None:
    source = (REPO / "api" / "application" / "matching.py").read_text(encoding="utf-8")

    assert "class ExplainedMatcher" in source
    assert "class RecommendationService" in source
    assert "DecisionPolicy" in source


def test_bundle_retriever_delegates_candidate_discovery_to_the_index() -> None:
    source = (REPO / "api" / "infrastructure" / "retrieval" / "bundle_retrieval.py").read_text(
        encoding="utf-8"
    )

    assert "return self.index.retrieve(" in source
    assert "def _sparse(" not in source
    assert "def _dense_and_views(" not in source
