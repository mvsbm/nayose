from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from api.domain.evidence import visible_record
from api.domain.features import compute_pair_features
from api.domain.normalize import normalize_record
from api.infrastructure.matching.scoring import (
    CandidateAggregator,
    DecisionPolicy,
    PairScorer,
    PipelineTraceBuilder,
    empty_result,
)
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.operational_store import OperationalStore
from api.infrastructure.retrieval.bundle_retrieval import BundleRetriever
from api.infrastructure.runtime.engine import load_engine

LOGGER = logging.getLogger(__name__)


class ExplainedMatcher:
    def __init__(self, engine: Any, retriever: Any = None):
        self.engine = engine
        self.retriever = retriever if retriever is not None else engine._index
        self.scorer = PairScorer(engine)
        self.aggregator = CandidateAggregator(engine)
        self.trace_builder = PipelineTraceBuilder()
        self.decision_policy = DecisionPolicy(engine, self.retriever)

    @classmethod
    def load(cls, run_dir: Path, data_dir: Path) -> ExplainedMatcher:
        engine = load_engine(model_dir=str(run_dir), data_dir=str(data_dir))
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        return cls(engine, BundleRetriever(engine, manifest, run_dir))

    def set_exact_mode(self, mode: str) -> None:
        setter = getattr(self.retriever, "set_exact_mode", None)
        if setter is not None:
            setter(mode)

    def close(self) -> None:
        close = getattr(self.retriever, "close", None)
        if close is not None:
            close()
            return
        close_engine = getattr(self.engine, "close", None)
        if close_engine is not None:
            close_engine()

    def recommend(
        self,
        record: dict[str, str],
        stage: Callable[[str], None],
        overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        engine = self.engine
        query = normalize_record(record)
        stage("retrieving")
        retrieval = self.retriever.retrieve(query).reset_index(drop=True)
        if retrieval.empty:
            result = empty_result("NO_MATCH", "no_candidates")
            result["pipeline"] = self._pipeline(record, query, retrieval, result)
            return result
        raw = engine._reference.iloc[retrieval["candidate_index"].tolist()].reset_index(drop=True)
        normalized = pd.DataFrame(
            [normalize_record(visible_record(r)) for r in raw.to_dict("records")]
        )
        stage("scoring")
        features = compute_pair_features(query, normalized, retrieval)
        scores = self._score(features)
        stage("aggregating")
        candidates = self._candidates(record, raw, features, scores, retrieval)
        result = self._decision(candidates, len(retrieval), overrides=overrides)
        result["input_normalized"] = {
            key: query.get(norm, "")
            for key, norm in {
                "name_kanji": "n_name_loose",
                "name_kana": "n_kana",
                "phone": "n_phone",
                "email": "n_email",
                "postal_code": "n_postal",
                "date_birth": "n_dob",
                "address": "n_address",
            }.items()
        }
        result["pipeline"] = self._pipeline(record, query, retrieval, result)
        return result

    def _score(self, features: pd.DataFrame) -> np.ndarray:
        return self.scorer.score(features)

    def _candidates(
        self,
        record: dict[str, str],
        raw: pd.DataFrame,
        features: pd.DataFrame,
        scores: np.ndarray,
        retrieval: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        return self.aggregator.build(record, raw, features, scores, retrieval)

    def _pipeline(
        self,
        record: dict[str, str],
        query: dict[str, str],
        retrieval: pd.DataFrame,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self.trace_builder.build(record, query, retrieval, result)

    @staticmethod
    def _normalized_fields(query: dict[str, str]) -> dict[str, str]:
        return PipelineTraceBuilder.normalized_fields(query)

    def _decision(
        self,
        candidates: list[dict[str, Any]],
        retrieved: int,
        overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        return self.decision_policy.decide(candidates, retrieved, overrides)


class RecommendationService:
    """Coordinate the engine session and bounded recommendation jobs."""

    def __init__(
        self,
        repo: Any,
        data_dir: Path,
        loader: Callable = ExplainedMatcher.load,
        exact_mode: str = "hybrid",
        operational_store: OperationalStore | None = None,
        stats: Stats | None = None,
    ):
        from api.application.matching_runtime import EngineSession, MatchingJobRunner

        self.session = EngineSession(
            repo,
            data_dir,
            loader,
            exact_mode=exact_mode,
            operational_store=operational_store,
            stats=stats,
        )
        self.jobs = MatchingJobRunner(self.session, operational_store=operational_store)

    @property
    def loader(self) -> Callable:
        return self.session.loader

    @loader.setter
    def loader(self, value: Callable) -> None:
        self.session.loader = value

    def engine_ready(self, run_id: str) -> bool:
        return self.session.engine_ready(run_id)

    def reset_engine(self) -> None:
        self.session.reset_engine()

    def set_exact_mode(self, mode: str) -> None:
        self.session.set_exact_mode(mode)

    def search_entities(
        self, run_id: str, text: str, limit: int = 20
    ) -> list[dict[str, Any]] | None:
        return self.session.search_entities(run_id, text, limit)

    def recommend(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        return self.session.recommend(run_id, record, overrides)

    def start(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        return self.jobs.start(run_id, record, overrides)

    def status(self, job_id: str) -> dict[str, Any]:
        return self.jobs.status(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.jobs.cancel(job_id)

    def wait(self, job_id: str, timeout: float) -> None:
        self.jobs.wait(job_id, timeout)

    @staticmethod
    def _score_context(run_dir: Path, score: float) -> dict[str, Any]:
        from api.application.matching_runtime import MatchingJobRunner

        return MatchingJobRunner._score_context(run_dir, score)

    def close(self) -> None:
        self.jobs.close()
