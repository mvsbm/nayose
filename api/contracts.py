from typing import Any, Literal

from pydantic import BaseModel, Field


class GenerationControlsIn(BaseModel):
    profile: str = Field(default="smoke", max_length=32)
    difficulty: str = Field(default="realistic", max_length=32)
    seed: int = Field(default=42, ge=0)
    n_parties: int = Field(default=0, ge=0, le=2_000_000)
    mean_records: float | None = Field(default=None, ge=1.0, le=20.0)
    n_agencies: int = Field(default=18, ge=1, le=100)
    person_share: float = Field(default=0.90, ge=0.0, le=1.0)
    query_share: float = Field(default=0.16, ge=0.0, le=1.0)
    twin_rate: float = Field(default=0.15, ge=0.0, le=1.0)
    poor: float | None = Field(default=None, ge=0.0, le=1.0)
    catastrophic: float | None = Field(default=None, ge=0.0, le=1.0)
    snapshot_date: str = Field(default="2025-12-31", max_length=10)
    query_date: str = Field(default="2026-08-25", max_length=10)
    event_rates: dict[str, float] = Field(default_factory=dict)
    band_scale: dict[str, float] = Field(default_factory=dict)
    missing_rates: dict[str, float] = Field(default_factory=dict)
    field_availability: dict[str, dict[str, float]] = Field(default_factory=dict)
    stale_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    query_mix: dict[str, float] = Field(default_factory=dict)


class GenerationPreviewIn(BaseModel):
    controls: GenerationControlsIn


Stage = Literal[
    "queued",
    "validating",
    "loading",
    "retrieving",
    "scoring",
    "aggregating",
    "complete",
    "failed",
    "generating",
    "training",
]


class FieldComparison(BaseModel):
    left: str
    right: str
    left_normalized: str
    right_normalized: str
    status: Literal["exact", "normalized", "different", "missing"]


class EntityDetail(BaseModel):
    entity: dict[str, Any]
    members: list[dict[str, str]]
    total: int
    offset: int
    limit: int
    comparisons: dict[str, dict[str, FieldComparison]]
    merge_evidence_available: bool


class SampleRecord(BaseModel):
    record: dict[str, str]


class CandidateEvidence(BaseModel):
    record: dict[str, str]
    score: float = Field(ge=0, le=1)
    comparisons: dict[str, FieldComparison]
    features: dict[str, float]
    retrieval: dict[str, Any] = Field(default_factory=dict)


class PipelineNode(BaseModel):
    id: str
    kind: str
    status: Literal["complete", "warning", "skipped"]
    metrics: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class DecisionTrace(BaseModel):
    selected_candidate: str | None = None
    nodes: list[PipelineNode]
    edges: list[list[str]]


class Candidate(BaseModel):
    rank: int
    nayose_id: str
    name: str
    probability: float = Field(ge=0, le=1)
    support: int
    evidence: list[CandidateEvidence]


class Thresholds(BaseModel):
    auto: float
    review: float


class MatchResult(BaseModel):
    action: Literal["AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"]
    reason: str
    top_nayose_id: str | None
    top_probability: float = Field(ge=0, le=1)
    margin: float
    candidates: list[Candidate]
    thresholds: Thresholds | None
    threshold_source: str = "manifest"
    nearest: list[Candidate] | None = None
    input_normalized: dict[str, str] | None = None
    score_context: dict[str, Any] | None = None
    diagnostics: dict[str, Any]
    scoring_mode: str
    pipeline: DecisionTrace | None = None


class StageTime(BaseModel):
    stage: Stage
    elapsed_ms: int


class MatchJob(BaseModel):
    id: str
    run_id: str
    stage: Stage
    elapsed_ms: int
    timeline: list[StageTime]
    progress: dict[str, Any] | None = None
    error: str | None
    result: MatchResult | None


class AdminJob(BaseModel):
    """Job shape for admin ops: result is an arbitrary generated/trained payload
    (not a MatchResult) — LSP: a free-form result, not a match verdict."""

    id: str
    run_id: str
    stage: Stage
    elapsed_ms: int
    timeline: list[StageTime]
    progress: dict[str, Any] | None = None
    error: str | None
    result: dict[str, Any] | None
