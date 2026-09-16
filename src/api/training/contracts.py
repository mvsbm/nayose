from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd


class ModelSpec(TypedDict):
    embedding: str | None
    reranker: str | None
    query_prefix: str
    document_prefix: str


@dataclass(frozen=True, slots=True)
class VisibleSnapshot:
    reference: pd.DataFrame
    incoming: pd.DataFrame
    generation_manifest: dict[str, Any]
    data_fingerprint: str


@dataclass(frozen=True, slots=True)
class NormalizedCorpus:
    reference: pd.DataFrame
    incoming: pd.DataFrame
    value_frequency: dict[str, dict[str, int]]


@dataclass(frozen=True, slots=True)
class TruthSnapshot:
    record_truth: pd.DataFrame
    evaluation_truth: pd.DataFrame
    query_truth: pd.DataFrame
    party_truth: pd.DataFrame
    relation_truth: pd.DataFrame
    field_lineage: pd.DataFrame


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    device: str
    diagnostics: dict[str, Any]
    backends: dict[str, bool]
    neural_enabled: bool
    embedding_backend: str = "pytorch_mps"
    embedding_model_revision: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateGraphResult:
    candidate_pair_recall: float
    entity_candidate_recall: float
    hardness: float


@dataclass(frozen=True, slots=True)
class PairTrainingResult:
    model: Any
    calibrator: Any
    backend: str
    candidate_pairs: pd.DataFrame
    auto_threshold: float
    review_threshold: float
    auto_status: str
    review_status: str
    metrics: dict[str, float]
    confusion_test: pd.DataFrame


@dataclass(frozen=True, slots=True)
class ClusterResult:
    mapping: pd.DataFrame
    canonical_entities: pd.DataFrame
    attribute_history: pd.DataFrame
    diagnostics: pd.DataFrame
    review_queue: pd.DataFrame
    union_log: pd.DataFrame
    truth_sets: dict[str, set[str]]
    metrics: dict[str, float]


@dataclass(frozen=True, slots=True)
class EntityTrainingResult:
    scaler: Any
    model: Any
    calibrator: Any
    entity_features: dict[str, pd.DataFrame]
    auto_threshold: float
    review_threshold: float
    threshold_status: str
    metrics: dict[str, float]


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    query_results: pd.DataFrame
    scenario_metrics: pd.DataFrame
    query_confusion: pd.DataFrame
    metrics: dict[str, float]


@dataclass(frozen=True, slots=True)
class RunIdentity:
    run_id: str
    output_dir: Path
    config_hash: str


@dataclass(frozen=True, slots=True)
class TrainingRunResult:
    identity: RunIdentity
    manifest_path: Path
    data_fingerprint: str


@dataclass(frozen=True, slots=True)
class StageRecord:
    name: str
    seconds: float
    rss_before_mib: float
    rss_after_mib: float
    rss_delta_mib: float
    resource_sampled: bool


class ArtifactRecord(TypedDict):
    name: str
    path: str
    format: str
    rows: int
    sha256: str
