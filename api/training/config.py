from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from api.embedding_config import (
    F2LLM_330M_MODEL_ID,
    F2LLM_MPS_BATCH_SIZE,
    F2LLM_MPS_DEVICE,
    F2LLM_MPS_TORCH_DTYPE,
)
from api.training.contracts import ModelSpec, RunIdentity

MODEL_SPECS: dict[str, ModelSpec] = {
    "off": {
        "embedding": None,
        "reranker": None,
        "query_prefix": "",
        "document_prefix": "",
    },
    "f2llm": {
        "embedding": F2LLM_330M_MODEL_ID,
        "reranker": None,
        "query_prefix": "",
        "document_prefix": "",
    },
    "f2llm_0.6b": {
        "embedding": "codefuse-ai/F2LLM-v2-0.6B",
        "reranker": None,
        "query_prefix": "",
        "document_prefix": "",
    },
    "ruri": {
        "embedding": "cl-nagoya/ruri-v3-130m",
        "reranker": "cl-nagoya/ruri-v3-reranker-310m",
        "query_prefix": "",
        "document_prefix": "",
    },
    "qwen": {
        "embedding": "Qwen/Qwen3-Embedding-0.6B",
        "reranker": "Qwen/Qwen3-Reranker-0.6B",
        "query_prefix": (
            "Instruct: Match this Japanese insurance customer to an existing party.\nQuery: "
        ),
        "document_prefix": "Document: ",
    },
}


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    value = int(env.get(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    return float(env.get(name, str(default)))


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    return env.get(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    seed: int = 42
    data_dir: Path = Path("data/current")
    output_dir: Path = Path("models")
    device: str = F2LLM_MPS_DEVICE
    model_family: str = "off"
    embedding_model: str | None = None
    strict_backends: bool = False
    decision_profile: str = "audit"
    use_dense: bool = True
    dense_profile: str = "record_hybrid"
    exact_mode: str = "hybrid"
    allow_svd_dense_fallback: bool = True
    svd_dimensions: int = 32
    low_memory_models: bool = True
    pair_model_backend: str = "auto"
    exact_block_cap: int = 80
    sparse_top_k: int = 24
    dense_top_k: int = 16
    candidate_cap_per_record: int = 80
    sparse_batch_size: int = 512
    neural_batch_size: int = F2LLM_MPS_BATCH_SIZE
    rerank_top_k: int = 5
    rerank_pair_limit: int = 12_000
    embedding_dim: int = 256
    training_negatives_per_positive: int = 6
    pair_auto_precision_target: float = 0.985
    pair_review_precision_target: float = 0.80
    entity_auto_precision_target: float = 0.97
    entity_review_threshold: float = 0.20
    entity_margin_threshold: float = 0.06
    minimum_evidence_families: int = 2
    minimum_reference_density: float = 0.0
    max_full_match_records: int = 100_000
    pair_feature_batch: int = 100_000
    save_all_candidate_pairs: bool = False
    entity_scoring_mode: str = "logistic_v1"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> TrainingConfig:
        env = os.environ if environ is None else environ
        family = env.get("NAYOSE_MODEL_FAMILY", "off").strip()
        if family == "f2llm_330m":
            family = "f2llm"
        config = cls(
            seed=_int(env, "NAYOSE_SEED", 42),
            data_dir=Path(env.get("NAYOSE_DATA_DIR", "data/current")),
            output_dir=Path(env.get("NAYOSE_OUTPUT_DIR", "models")),
            device=env.get("NAYOSE_DEVICE", F2LLM_MPS_DEVICE),
            model_family=family,
            embedding_model=env.get("NAYOSE_EMBEDDING_MODEL") or None,
            strict_backends=_bool(env, "NAYOSE_STRICT_BACKENDS", False),
            decision_profile=env.get("NAYOSE_DECISION_PROFILE", "audit"),
            use_dense=_bool(env, "NAYOSE_USE_DENSE", True),
            dense_profile=env.get("NAYOSE_DENSE_PROFILE", "record_hybrid"),
            exact_mode=env.get("NAYOSE_EXACT_MODE", "hybrid"),
            allow_svd_dense_fallback=_bool(env, "NAYOSE_ALLOW_SVD_FALLBACK", True),
            svd_dimensions=_int(env, "NAYOSE_SVD_DIM", 32, 2),
            low_memory_models=_bool(env, "NAYOSE_LOW_MEMORY_MODELS", True),
            pair_model_backend=env.get("NAYOSE_PAIR_MODEL", "auto"),
            exact_block_cap=_int(env, "NAYOSE_EXACT_BLOCK_CAP", 80, 1),
            sparse_top_k=_int(env, "NAYOSE_SPARSE_TOPK", 24, 1),
            dense_top_k=_int(env, "NAYOSE_DENSE_TOPK", 16, 1),
            candidate_cap_per_record=_int(env, "NAYOSE_CANDIDATE_CAP", 80, 1),
            sparse_batch_size=_int(env, "NAYOSE_SPARSE_BATCH", 512, 1),
            neural_batch_size=_int(env, "NAYOSE_NEURAL_BATCH", F2LLM_MPS_BATCH_SIZE, 1),
            rerank_top_k=_int(env, "NAYOSE_RERANK_TOPK", 5, 1),
            rerank_pair_limit=_int(env, "NAYOSE_RERANK_LIMIT", 12_000, 1),
            embedding_dim=_int(env, "NAYOSE_EMBED_DIM", 256, 2),
            training_negatives_per_positive=_int(env, "NAYOSE_NEG_PER_POS", 6, 1),
            pair_auto_precision_target=_float(env, "NAYOSE_PAIR_AUTO_PRECISION", 0.985),
            pair_review_precision_target=_float(env, "NAYOSE_PAIR_REVIEW_PRECISION", 0.80),
            entity_auto_precision_target=_float(env, "NAYOSE_ENTITY_AUTO_PRECISION", 0.97),
            entity_review_threshold=_float(env, "NAYOSE_ENTITY_REVIEW_THRESHOLD", 0.20),
            entity_margin_threshold=_float(env, "NAYOSE_ENTITY_MARGIN", 0.06),
            minimum_evidence_families=_int(env, "NAYOSE_MIN_EVIDENCE_FAMILIES", 2, 1),
            minimum_reference_density=_float(env, "NAYOSE_MIN_REFERENCE_DENSITY", 0.0),
            max_full_match_records=_int(env, "NAYOSE_MAX_FULL_RECORDS", 100_000, 1),
            pair_feature_batch=_int(env, "NAYOSE_PAIR_FEATURE_BATCH", 100_000, 1),
            save_all_candidate_pairs=_bool(env, "NAYOSE_SAVE_ALL_CANDIDATES", False),
            entity_scoring_mode=env.get("NAYOSE_ENTITY_SCORING_MODE", "logistic_v1"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.model_family not in MODEL_SPECS:
            raise ValueError(f"unknown model_family={self.model_family!r}")
        if self.decision_profile not in {"audit", "conservative"}:
            raise ValueError("decision_profile must be audit or conservative")
        if self.dense_profile not in {"record_hybrid", "field_aware"}:
            raise ValueError("dense_profile must be record_hybrid or field_aware")
        if self.exact_mode not in {"hybrid", "fuzzy_only", "exact_only"}:
            raise ValueError("exact_mode must be hybrid, fuzzy_only, or exact_only")
        if self.pair_model_backend not in {"auto", "lightgbm", "sklearn"}:
            raise ValueError("pair_model_backend must be auto, lightgbm, or sklearn")
        if self.entity_scoring_mode not in {"legacy_mean_top3", "logistic_v1"}:
            raise ValueError("unsupported entity_scoring_mode")
        if not 0.0 <= self.minimum_reference_density <= 1.0:
            raise ValueError("minimum_reference_density must be between 0 and 1")
        if self.model_family == "f2llm":
            if self.device != F2LLM_MPS_DEVICE:
                raise ValueError("F2LLM-v2-330M is locked to Apple MPS")
            if self.neural_batch_size != F2LLM_MPS_BATCH_SIZE:
                raise ValueError("F2LLM-v2-330M requires neural batch size 32")
            if not self.use_dense or self.allow_svd_dense_fallback:
                raise ValueError("F2LLM-v2-330M requires dense retrieval without SVD fallback")

    def model_spec(self) -> ModelSpec:
        spec = MODEL_SPECS[self.model_family].copy()
        if self.embedding_model and self.model_family != "f2llm":
            spec["embedding"] = self.embedding_model
        return spec

    def payload(self) -> dict[str, object]:
        return {**asdict(self), "data_dir": str(self.data_dir), "output_dir": str(self.output_dir)}

    def run_identity(self, timestamp: str, source: str = "") -> RunIdentity:
        payload = {**self.payload(), "source": source}
        config_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:8]
        return RunIdentity(
            run_id=f"run_{timestamp}_{config_hash}",
            output_dir=self.output_dir,
            config_hash=config_hash,
        )

    @property
    def torch_dtype(self) -> str:
        return F2LLM_MPS_TORCH_DTYPE if self.device == F2LLM_MPS_DEVICE else "float32"
