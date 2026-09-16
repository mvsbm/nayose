"""Engine configuration adapter over the canonical Nayose settings views."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from api.infrastructure.runtime.performance import cpu_thread_budget
from nayose.config import (
    ConfigSnapshot,
    EmbeddingConfig,
    PerformanceConfig,
    RetrievalConfig,
    load_config,
)
from nayose.config import InferenceConfig as PolicyConfig


@dataclass(frozen=True, slots=True, init=False)
class EngineSettings:
    """Compose canonical policy, retrieval, and process settings for the engine.

    The public ``InferenceConfig`` alias below is retained for callers that
    construct the historical engine adapter directly. Defaults come from one
    resolved ``ConfigSnapshot``; this adapter owns only bundle paths and the
    compatibility constructor, not subsystem defaults.
    """

    model_dir: Path
    data_dir: Path
    inference: PolicyConfig
    embedding: EmbeddingConfig
    retrieval: RetrievalConfig
    performance: PerformanceConfig
    cpu_threads: int

    def __init__(
        self,
        model_dir: Path | str = Path("models/current"),
        data_dir: Path | str = Path("data/current"),
        *,
        config_snapshot: ConfigSnapshot | None = None,
        inference: PolicyConfig | None = None,
        embedding: EmbeddingConfig | None = None,
        retrieval: RetrievalConfig | None = None,
        performance: PerformanceConfig | None = None,
        sparse_top_k: int | None = None,
        exact_block_cap: int | None = None,
        candidate_cap: int | None = None,
        pair_auto_threshold: float | None = None,
        entity_auto_threshold: float | None = None,
        entity_review_threshold: float | None = None,
        entity_margin_threshold: float | None = None,
        minimum_evidence_families: int | None = None,
        minimum_reference_density: float | None = None,
        dense_profile: str | None = None,
        sparse_query_feature_cap: int | None = None,
        cpu_threads: int | None = None,
    ) -> None:
        snapshot = config_snapshot
        if snapshot is None and (inference is None or retrieval is None or performance is None):
            snapshot = load_config()
        if embedding is None:
            if snapshot is None:
                snapshot = load_config()
            resolved_embedding = snapshot.embedding
        else:
            resolved_embedding = embedding
        if inference is None:
            if snapshot is None:
                raise ValueError("inference or config_snapshot is required")
            policy = snapshot.inference
        else:
            policy = inference
        policy = replace(
            policy,
            sparse_top_k=policy.sparse_top_k if sparse_top_k is None else sparse_top_k,
            exact_block_cap=policy.exact_block_cap if exact_block_cap is None else exact_block_cap,
            candidate_cap=policy.candidate_cap if candidate_cap is None else candidate_cap,
            pair_auto_threshold=policy.pair_auto_threshold
            if pair_auto_threshold is None
            else pair_auto_threshold,
            entity_auto_threshold=policy.entity_auto_threshold
            if entity_auto_threshold is None
            else entity_auto_threshold,
            entity_review_threshold=policy.entity_review_threshold
            if entity_review_threshold is None
            else entity_review_threshold,
            entity_margin_threshold=policy.entity_margin_threshold
            if entity_margin_threshold is None
            else entity_margin_threshold,
            minimum_evidence_families=policy.minimum_evidence_families
            if minimum_evidence_families is None
            else minimum_evidence_families,
            minimum_reference_density=policy.minimum_reference_density
            if minimum_reference_density is None
            else minimum_reference_density,
            dense_profile=policy.dense_profile if dense_profile is None else dense_profile,
        )
        if retrieval is None:
            if snapshot is None:
                raise ValueError("retrieval or config_snapshot is required")
            resolved_retrieval = snapshot.retrieval
        else:
            resolved_retrieval = retrieval
        if sparse_query_feature_cap is not None:
            resolved_retrieval = replace(
                resolved_retrieval, sparse_query_feature_cap=sparse_query_feature_cap
            )
        if performance is None:
            if snapshot is None:
                raise ValueError("performance or config_snapshot is required")
            resolved_performance = snapshot.performance
        else:
            resolved_performance = performance
        selected_threads = (
            cpu_thread_budget(settings=resolved_performance) if cpu_threads is None else cpu_threads
        )
        if selected_threads < 1:
            raise ValueError("cpu_threads must be positive")
        object.__setattr__(self, "model_dir", Path(model_dir))
        object.__setattr__(self, "data_dir", Path(data_dir))
        object.__setattr__(self, "inference", policy)
        object.__setattr__(self, "embedding", resolved_embedding)
        object.__setattr__(self, "retrieval", resolved_retrieval)
        object.__setattr__(self, "performance", resolved_performance)
        object.__setattr__(self, "cpu_threads", selected_threads)

    @property
    def sparse_top_k(self) -> int:
        return self.inference.sparse_top_k

    @property
    def exact_block_cap(self) -> int:
        return self.inference.exact_block_cap

    @property
    def candidate_cap(self) -> int:
        return self.inference.candidate_cap

    @property
    def pair_auto_threshold(self) -> float:
        return self.inference.pair_auto_threshold

    @property
    def entity_auto_threshold(self) -> float:
        return self.inference.entity_auto_threshold

    @property
    def entity_review_threshold(self) -> float:
        return self.inference.entity_review_threshold

    @property
    def entity_margin_threshold(self) -> float:
        return self.inference.entity_margin_threshold

    @property
    def minimum_evidence_families(self) -> int:
        return self.inference.minimum_evidence_families

    @property
    def minimum_reference_density(self) -> float:
        return self.inference.minimum_reference_density

    @property
    def dense_profile(self) -> str:
        return self.inference.dense_profile

    @property
    def entity_override(self) -> bool:
        return self.inference.entity_override

    @property
    def sparse_query_feature_cap(self) -> int | None:
        return self.retrieval.sparse_query_feature_cap

    def with_inference(self, inference: PolicyConfig) -> EngineSettings:
        return EngineSettings(
            model_dir=self.model_dir,
            data_dir=self.data_dir,
            inference=inference,
            embedding=self.embedding,
            retrieval=self.retrieval,
            performance=self.performance,
            cpu_threads=self.cpu_threads,
        )

    def with_sparse_query_feature_cap(self, value: int | None) -> EngineSettings:
        return EngineSettings(
            model_dir=self.model_dir,
            data_dir=self.data_dir,
            inference=self.inference,
            embedding=self.embedding,
            retrieval=replace(self.retrieval, sparse_query_feature_cap=value),
            performance=self.performance,
            cpu_threads=self.cpu_threads,
        )


InferenceConfig = EngineSettings
