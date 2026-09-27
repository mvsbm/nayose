"""Canonical Nayose configuration loader and immutable snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_generation import (
    CHANNEL_CONTACT_PROFILE,
    CHANNEL_NAMES,
    DEFAULT_EVENT_RATES,
    DEFAULT_LANDLINE_SHARE,
    DEFAULT_STALE_RATE,
    DIFFICULTY,
    EMAIL_DOMAINS,
    EVENT_NAMES,
    EXACT_MODES,
    GENERATION_DEFAULTS,
    MAX_ENTITY_SEEDS,
    MODEL_SPECS,
    NOISE_FIELDS,
    POLICY_PARAMS,
    PRODUCT_PROFILES,
    PROFILES,
    QUERY_DIFFICULTY,
    QUERY_MIX,
    STALE_EVENT_NAMES,
    TRAINING_DEFAULTS,
    DataSources,
    NoiseConfig,
    SyntheticConfig,
    TrainingConfig,
    noise_for_config,
)
from .config_views import (
    _DEFAULT_DOCUMENT,
    _FALSE_VALUES,
    _PIPELINE_MODEL_FAMILY,
    _TRUE_VALUES,
    EMBEDDING_BACKENDS,
    EMBEDDING_DEFAULTS,
    F2LLM_330M_MODEL_ID,
    F2LLM_MPS_BATCH_SIZE,
    F2LLM_MPS_DEVICE,
    F2LLM_MPS_PROCESS_WORKERS,
    F2LLM_MPS_TORCH_DTYPE,
    INFERENCE_DEFAULTS,
    PERFORMANCE_DEFAULTS,
    RELEASE_DEFAULTS,
    REPO_ROOT,
    RETRIEVAL_DEFAULTS,
    SERVING_DEFAULTS,
    STORAGE_DEFAULTS,
    SUPPORTED_MODEL_FAMILIES,
    TOOLING_DEFAULTS,
    ConfigurationError,
    EmbeddingConfig,
    InferenceConfig,
    PerformanceConfig,
    ReleaseConfig,
    RetrievalConfig,
    RuntimeConfig,
    StorageConfig,
    ToolingConfig,
    default_data_dir,
    repository_root,
    to_plain,
)
from .config_views import (
    PATH_DEFAULTS as _PATH_DEFAULTS,
)

PATH_DEFAULTS = _PATH_DEFAULTS

__all__ = [
    "CHANNEL_CONTACT_PROFILE",
    "CHANNEL_NAMES",
    "ConfigSnapshot",
    "ConfigurationError",
    "DataSources",
    "DEFAULT_EVENT_RATES",
    "DEFAULT_LANDLINE_SHARE",
    "DEFAULT_STALE_RATE",
    "DIFFICULTY",
    "EMAIL_DOMAINS",
    "EMBEDDING_BACKENDS",
    "EMBEDDING_DEFAULTS",
    "EmbeddingConfig",
    "EVENT_NAMES",
    "EXACT_MODES",
    "F2LLM_330M_MODEL_ID",
    "F2LLM_MPS_BATCH_SIZE",
    "F2LLM_MPS_DEVICE",
    "F2LLM_MPS_PROCESS_WORKERS",
    "F2LLM_MPS_TORCH_DTYPE",
    "InferenceConfig",
    "MAX_ENTITY_SEEDS",
    "MODEL_SPECS",
    "NOISE_FIELDS",
    "NoiseConfig",
    "PATH_DEFAULTS",
    "PERFORMANCE_DEFAULTS",
    "PerformanceConfig",
    "POLICY_PARAMS",
    "PRODUCT_PROFILES",
    "PROFILES",
    "QUERY_DIFFICULTY",
    "QUERY_MIX",
    "REPO_ROOT",
    "RETRIEVAL_DEFAULTS",
    "RuntimeConfig",
    "StorageConfig",
    "SUPPORTED_MODEL_FAMILIES",
    "STALE_EVENT_NAMES",
    "SyntheticConfig",
    "ToolingConfig",
    "TrainingConfig",
    "configuration_summary",
    "default_data_dir",
    "embedding_model_id",
    "is_f2llm_model",
    "load_config",
    "noise_for_config",
    "pipeline_model_family",
    "repository_root",
    "to_plain",
]


def _path(value: str, *, root: Path | None = None) -> Path:
    result = Path(value).expanduser()
    return (root / result).resolve() if root is not None and not result.is_absolute() else result


def _assert_keys(values: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigurationError(f"unknown configuration key(s) at {path}: {', '.join(unknown)}")


def _validate_document(document: Mapping[str, Any]) -> None:
    sections = {
        "paths",
        "serving",
        "embedding",
        "performance",
        "retrieval",
        "storage",
        "inference",
        "training",
        "release",
        "tooling",
        "generation",
        "sources",
        "schema_version",
    }
    _assert_keys(document, sections, "root")
    expected = {
        "paths": {"data_root", "data_dir", "models_dir", "frontend_dir", "operational_db"},
        "serving": {
            "bind_host",
            "port",
            "auth_mode",
            "trusted_auth_subject_header",
            "trusted_auth_scopes_header",
            "debug_sql",
            "warm_embed",
            "default_reviewer",
            "device",
            "exact_mode",
            "telemetry_interval_seconds",
        },
        "embedding": {
            "model_id",
            "backend",
            "device",
            "batch_size",
            "process_workers",
            "torch_dtype",
            "revision",
            "optimization",
        },
        "performance": {"process_workers", "cpu_threads", "model_threads", "native_thread_cap"},
        "storage": {"reserve_ratio", "reserve_minimum_bytes", "workspace_stale_seconds"},
        "retrieval": {
            "use_dense",
            "dense_views",
            "index_backend",
            "sparse_query_feature_cap",
            "sparse_batch_size",
            "inverted_memory_budget",
            "encode_chunk_size",
        },
        "inference": {
            "sparse_top_k",
            "exact_block_cap",
            "candidate_cap",
            "pair_auto_threshold",
            "entity_auto_threshold",
            "entity_review_threshold",
            "entity_margin_threshold",
            "minimum_evidence_families",
            "minimum_reference_density",
            "dense_profile",
            "entity_override",
        },
        "training": {
            "seed",
            "model_family",
            "use_dense",
            "exact_mode",
            "strict_backends",
            "decision_profile",
            "allow_svd_dense_fallback",
            "svd_dimensions",
            "low_memory_models",
            "pair_model_backend",
            "exact_block_cap",
            "sparse_top_k",
            "dense_top_k",
            "candidate_cap_per_record",
            "sparse_batch_size",
            "neural_batch_size",
            "rerank_top_k",
            "rerank_pair_limit",
            "embedding_dim",
            "training_negatives_per_positive",
            "pair_auto_precision_target",
            "pair_review_precision_target",
            "entity_auto_precision_target",
            "entity_review_threshold",
            "entity_margin_threshold",
            "minimum_evidence_families",
            "minimum_reference_density",
            "max_full_match_records",
            "pair_feature_batch",
            "save_all_candidate_pairs",
            "entity_scoring_mode",
            "model_specs",
        },
        "release": {"min_top1_gain", "min_recall5_gain", "min_hard_recall5_gain"},
        "tooling": {"metrics_dir", "architecture_ledger", "e2e"},
        "sources": {
            "persona_dataset",
            "persona_split",
            "persona_cache",
            "edinet_dataset",
            "edinet_split",
            "japan_post_zip_url",
            "postal_zip_name",
            "postal_csv_name",
        },
    }
    for name, allowed in expected.items():
        section = document.get(name)
        if not isinstance(section, dict):
            raise ConfigurationError(f"configuration section [{name}] must be a table")
        _assert_keys(section, allowed, name)
    embedding_optimization = document["embedding"].get("optimization")
    if not isinstance(embedding_optimization, dict):
        raise ConfigurationError("[embedding.optimization] must be a table")
    _assert_keys(
        embedding_optimization,
        {"max_length", "padding_strategy"},
        "embedding.optimization",
    )
    if document["embedding"].get("backend") not in EMBEDDING_BACKENDS:
        raise ConfigurationError(f"embedding backend must be one of {EMBEDDING_BACKENDS}")
    model_specs = document["training"].get("model_specs")
    if not isinstance(model_specs, dict):
        raise ConfigurationError("[training.model_specs] must be a table")
    _assert_keys(
        model_specs, {"off", "f2llm", "f2llm_0_6b", "ruri", "qwen"}, "training.model_specs"
    )
    for name, spec in model_specs.items():
        if not isinstance(spec, dict):
            raise ConfigurationError(f"[training.model_specs.{name}] must be a table")
        _assert_keys(
            spec,
            {"embedding", "reranker", "query_prefix", "document_prefix"},
            f"training.model_specs.{name}",
        )
    generation = document["generation"]
    _assert_keys(
        generation,
        {
            "seed",
            "max_entity_seeds",
            "default_stale_rate",
            "default_landline_share",
            "snapshot_date",
            "query_date",
            "profile",
            "default_difficulty",
            "n_agencies",
            "person_share",
            "query_share",
            "twin_rate",
            "lineage_mode",
            "profiles",
            "difficulty",
            "query_difficulty",
            "query_mix",
            "event_rates",
            "email_domains",
            "product_profiles",
            "policy_params",
            "channel_contact_profile",
            "noise",
        },
        "generation",
    )
    for table, allowed in {
        "profiles": {"preflight", "smoke", "mac", "large_mac", "generate_only"},
        "difficulty": {"debug", "realistic", "severe", "challenge"},
        "query_difficulty": {"debug", "realistic", "severe", "challenge"},
        "email_domains": {"modern", "isp"},
        "product_profiles": {"auto", "property_casualty", "personal_liability"},
        "channel_contact_profile": {"web", "agent_pc", "paper_ocr", "legacy_migration"},
    }.items():
        value = generation.get(table)
        if not isinstance(value, dict):
            raise ConfigurationError(f"[generation.{table}] must be a table")
        _assert_keys(value, allowed, f"generation.{table}")
    policy = generation.get("policy_params")
    if not isinstance(policy, dict):
        raise ConfigurationError("[generation.policy_params] must be a table")
    _assert_keys(
        policy,
        {
            "mean_policies",
            "product_mix",
            "start_range",
            "household_size_probs",
            "history_rate",
            "claim_close_years",
            "cancellation_reasons",
            "lapse_reasons",
            "household_sharing",
        },
        "generation.policy_params",
    )
    noise = generation.get("noise")
    if not isinstance(noise, dict):
        raise ConfigurationError("[generation.noise] must be a table")
    _assert_keys(
        noise,
        {
            "field_availability",
            "channel_events",
            "band_scale",
            "missing_rates",
            "channel_family",
            "vintage",
        },
        "generation.noise",
    )


_validate_document(_DEFAULT_DOCUMENT)


def _flag(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES or not normalized:
        return False
    raise ConfigurationError(f"invalid boolean value: {value!r}")


def _env_text(env: Mapping[str, str], name: str, default: str) -> str:
    return env.get(name, default).strip()


def _env_int(env: Mapping[str, str], name: str, default: int, minimum: int | None = None) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _env_optional_int(env: Mapping[str, str], name: str, default: int) -> int | None:
    raw = env.get(name, str(default)).strip()
    if not raw or raw.casefold() == "auto" or raw == "0":
        return None
    return _env_int(env, name, default, 1)


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Resolved, process-local settings with narrow subsystem views."""

    runtime: RuntimeConfig
    embedding: EmbeddingConfig
    performance: PerformanceConfig
    retrieval: RetrievalConfig
    inference: InferenceConfig
    storage: StorageConfig
    training: TrainingConfig
    generation: SyntheticConfig
    sources: DataSources
    release: ReleaseConfig
    tooling: ToolingConfig
    schema_version: int = int(_DEFAULT_DOCUMENT["schema_version"])

    @property
    def redacted(self) -> dict[str, Any]:
        value = to_plain(self)
        for token_name in ("api_token", "review_token", "admin_token"):
            if value["runtime"].get(token_name):
                value["runtime"][token_name] = "<redacted>"
        return value

    @property
    def config_hash(self) -> str:
        payload = json.dumps(self.redacted, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ConfigSnapshot:
        return load_config(environ)


KNOWN_ENV_VARS = frozenset(
    {
        "NAYOSE_MODELS_DIR",
        "NAYOSE_DATA_DIR",
        "NAYOSE_DATA_ROOT",
        "NAYOSE_RAW_DIR",
        "NAYOSE_OUTPUT_DIR",
        "NAYOSE_BIND_HOST",
        "NAYOSE_DASH_PORT",
        "NAYOSE_API_TOKEN",
        "NAYOSE_REVIEW_TOKEN",
        "NAYOSE_ADMIN_TOKEN",
        "NAYOSE_AUTH_MODE",
        "NAYOSE_AUTH_SUBJECT_HEADER",
        "NAYOSE_AUTH_SCOPES_HEADER",
        "NAYOSE_DEBUG_SQL",
        "NAYOSE_WARM_EMBED",
        "NAYOSE_REVIEWER_DEFAULT",
        "NAYOSE_DEVICE",
        "NAYOSE_EXACT_MODE",
        "NAYOSE_TELEMETRY_INTERVAL_SECONDS",
        "NAYOSE_EMBEDDING_MODEL",
        "NAYOSE_EMBEDDING_REVISION",
        "NAYOSE_MODEL_FAMILY",
        "NAYOSE_REQUESTED_MODEL_FAMILY",
        "NAYOSE_STRICT_BACKENDS",
        "NAYOSE_DECISION_PROFILE",
        "NAYOSE_USE_DENSE",
        "NAYOSE_DENSE_PROFILE",
        "NAYOSE_DENSE_VIEWS",
        "NAYOSE_DENSE_VIEW_BATCH",
        "NAYOSE_INDEX_BACKEND",
        "NAYOSE_SPARSE_QUERY_FEATURE_CAP",
        "NAYOSE_NATIVE_THREAD_CAP",
        "NAYOSE_ENTITY_OVERRIDE",
        "NAYOSE_ALLOW_SVD_FALLBACK",
        "NAYOSE_SVD_DIM",
        "NAYOSE_LOW_MEMORY_MODELS",
        "NAYOSE_PAIR_MODEL",
        "NAYOSE_EXACT_BLOCK_CAP",
        "NAYOSE_SPARSE_TOPK",
        "NAYOSE_DENSE_TOPK",
        "NAYOSE_CANDIDATE_CAP",
        "NAYOSE_SPARSE_BATCH",
        "NAYOSE_NEURAL_BATCH",
        "NAYOSE_RERANK_TOPK",
        "NAYOSE_RERANK_LIMIT",
        "NAYOSE_EMBED_DIM",
        "NAYOSE_NEG_PER_POS",
        "NAYOSE_PAIR_AUTO_PRECISION",
        "NAYOSE_PAIR_REVIEW_PRECISION",
        "NAYOSE_ENTITY_AUTO_PRECISION",
        "NAYOSE_ENTITY_REVIEW_THRESHOLD",
        "NAYOSE_ENTITY_MARGIN",
        "NAYOSE_MIN_EVIDENCE_FAMILIES",
        "NAYOSE_MIN_REFERENCE_DENSITY",
        "NAYOSE_MAX_FULL_RECORDS",
        "NAYOSE_PAIR_FEATURE_BATCH",
        "NAYOSE_SAVE_ALL_CANDIDATES",
        "NAYOSE_ENTITY_SCORING_MODE",
        "NAYOSE_MIN_TOP1_GAIN",
        "NAYOSE_MIN_RECALL5_GAIN",
        "NAYOSE_MIN_HARD_RECALL5_GAIN",
        "NAYOSE_BASELINE_RUN",
        "NAYOSE_PROCESS_WORKERS",
        "NAYOSE_CPU_THREADS",
        "NAYOSE_MODEL_THREADS",
        "NAYOSE_STORAGE_RESERVE_RATIO",
        "NAYOSE_STORAGE_RESERVE_MINIMUM_BYTES",
        "NAYOSE_STORAGE_WORKSPACE_STALE_SECONDS",
        "WEB_CONCURRENCY",
        "NAYOSE_UI",
        "NAYOSE_METRICS_DIR",
        "NAYOSE_ARCHITECTURE_LEDGER",
        "NAYOSE_E2E",
        "NAYOSE_E2E_BROWSER_PATH",
        "NAYOSE_E2E_CPU_THREADS",
        "NAYOSE_E2E_DATA",
        "NAYOSE_E2E_KEEP",
        "NAYOSE_E2E_METRICS",
        "NAYOSE_E2E_MODELS",
        "NAYOSE_E2E_MODEL_THREADS",
        "NAYOSE_E2E_OMP_THREADS",
        "NAYOSE_E2E_PORT",
        "NAYOSE_E2E_STORAGE_BUDGET",
        "NAYOSE_E2E_THREAD_CAP",
        "NAYOSE_E2E_WARM_EMBED",
        "NAYOSE_MACOS_RUNTIME_ALIGNED",
        "NAYOSE_OPERATIONAL_DB",
        "NAYOSE_SEED",
        "NAYOSE_PROFILE",
        "NAYOSE_DIFFICULTY",
        "NAYOSE_N_PARTIES",
        "NAYOSE_SNAPSHOT_DATE",
        "NAYOSE_QUERY_DATE",
        "NAYOSE_TWIN_RATE",
    }
)


def _validate_environment(env: Mapping[str, str]) -> None:
    unknown = sorted(key for key in env if key.startswith("NAYOSE_") and key not in KNOWN_ENV_VARS)
    if unknown:
        raise ConfigurationError(f"unknown environment configuration key(s): {', '.join(unknown)}")


def _runtime_from_env(env: Mapping[str, str]) -> RuntimeConfig:
    data_root = _path(_env_text(env, "NAYOSE_DATA_ROOT", str(_PATH_DEFAULTS["data_root"])))
    data_dir = _path(_env_text(env, "NAYOSE_DATA_DIR", str(_PATH_DEFAULTS["data_dir"])))
    models_dir = _path(_env_text(env, "NAYOSE_MODELS_DIR", str(_PATH_DEFAULTS["models_dir"])))
    frontend = _path(str(_PATH_DEFAULTS["frontend_dir"]), root=repository_root())
    operational = _path(
        _env_text(env, "NAYOSE_OPERATIONAL_DB", str(_PATH_DEFAULTS["operational_db"]))
    )
    host = _env_text(env, "NAYOSE_BIND_HOST", str(SERVING_DEFAULTS["bind_host"]))
    port = _env_int(env, "NAYOSE_DASH_PORT", int(SERVING_DEFAULTS["port"]), 1)
    if port > 65535:
        raise ConfigurationError("NAYOSE_DASH_PORT must be between 1 and 65535")
    return RuntimeConfig(
        models_dir=models_dir,
        data_dir=data_dir,
        data_root=data_root,
        frontend_dir=frontend,
        bind_host=host,
        port=port,
        api_token=env.get("NAYOSE_API_TOKEN", "").strip(),
        review_token=env.get("NAYOSE_REVIEW_TOKEN", "").strip(),
        admin_token=env.get("NAYOSE_ADMIN_TOKEN", "").strip(),
        auth_mode=_env_text(env, "NAYOSE_AUTH_MODE", str(SERVING_DEFAULTS["auth_mode"])).casefold(),
        trusted_auth_subject_header=_env_text(
            env, "NAYOSE_AUTH_SUBJECT_HEADER", str(SERVING_DEFAULTS["trusted_auth_subject_header"])
        ),
        trusted_auth_scopes_header=_env_text(
            env, "NAYOSE_AUTH_SCOPES_HEADER", str(SERVING_DEFAULTS["trusted_auth_scopes_header"])
        ),
        debug_sql=_flag(env.get("NAYOSE_DEBUG_SQL"), bool(SERVING_DEFAULTS["debug_sql"])),
        warm_embed=_flag(env.get("NAYOSE_WARM_EMBED"), bool(SERVING_DEFAULTS["warm_embed"])),
        default_reviewer=_env_text(
            env, "NAYOSE_REVIEWER_DEFAULT", str(SERVING_DEFAULTS["default_reviewer"])
        )
        or "web",
        device=_env_text(env, "NAYOSE_DEVICE", str(SERVING_DEFAULTS["device"])) or F2LLM_MPS_DEVICE,
        exact_mode=_env_text(env, "NAYOSE_EXACT_MODE", str(SERVING_DEFAULTS["exact_mode"])),
        telemetry_interval_seconds=_env_float(
            env,
            "NAYOSE_TELEMETRY_INTERVAL_SECONDS",
            float(SERVING_DEFAULTS["telemetry_interval_seconds"]),
        ),
        operational_db=operational,
    )


def _embedding_from_env(env: Mapping[str, str]) -> EmbeddingConfig:
    optimization = EMBEDDING_DEFAULTS["optimization"]
    return EmbeddingConfig(
        model_id=_env_text(env, "NAYOSE_EMBEDDING_MODEL", str(EMBEDDING_DEFAULTS["model_id"])),
        backend=str(EMBEDDING_DEFAULTS["backend"]),
        device=_env_text(env, "NAYOSE_DEVICE", str(EMBEDDING_DEFAULTS["device"])),
        batch_size=int(EMBEDDING_DEFAULTS["batch_size"]),
        process_workers=int(EMBEDDING_DEFAULTS["process_workers"]),
        torch_dtype=str(EMBEDDING_DEFAULTS["torch_dtype"]),
        revision=env.get("NAYOSE_EMBEDDING_REVISION", str(EMBEDDING_DEFAULTS["revision"])).strip(),
        max_length=int(optimization["max_length"]),
        padding_strategy=str(optimization["padding_strategy"]),
    )


def _performance_from_env(env: Mapping[str, str]) -> PerformanceConfig:
    worker_default = int(PERFORMANCE_DEFAULTS["process_workers"])
    if "NAYOSE_PROCESS_WORKERS" not in env and "WEB_CONCURRENCY" in env:
        worker_default = _env_int(env, "WEB_CONCURRENCY", worker_default, 1)
    return PerformanceConfig(
        process_workers=_env_optional_int(env, "NAYOSE_PROCESS_WORKERS", worker_default),
        cpu_threads=_env_optional_int(
            env, "NAYOSE_CPU_THREADS", int(PERFORMANCE_DEFAULTS["cpu_threads"])
        ),
        model_threads=_env_optional_int(
            env, "NAYOSE_MODEL_THREADS", int(PERFORMANCE_DEFAULTS["model_threads"])
        ),
        native_thread_cap=_env_optional_int(
            env, "NAYOSE_NATIVE_THREAD_CAP", int(PERFORMANCE_DEFAULTS["native_thread_cap"])
        ),
    )


def performance_from_environment(
    environ: Mapping[str, str] | None = None,
) -> PerformanceConfig:
    """Resolve process settings without applying serving-only device policy."""
    return _performance_from_env(os.environ if environ is None else environ)


def _storage_from_env(env: Mapping[str, str]) -> StorageConfig:
    return StorageConfig(
        reserve_ratio=_env_float(
            env, "NAYOSE_STORAGE_RESERVE_RATIO", float(STORAGE_DEFAULTS["reserve_ratio"])
        ),
        reserve_minimum_bytes=_env_int(
            env,
            "NAYOSE_STORAGE_RESERVE_MINIMUM_BYTES",
            int(STORAGE_DEFAULTS["reserve_minimum_bytes"]),
            0,
        ),
        workspace_stale_seconds=_env_int(
            env,
            "NAYOSE_STORAGE_WORKSPACE_STALE_SECONDS",
            int(STORAGE_DEFAULTS["workspace_stale_seconds"]),
            1,
        ),
    )


def _retrieval_from_env(env: Mapping[str, str]) -> RetrievalConfig:
    raw_cap = _env_optional_int(
        env, "NAYOSE_SPARSE_QUERY_FEATURE_CAP", int(RETRIEVAL_DEFAULTS["sparse_query_feature_cap"])
    )
    return RetrievalConfig(
        use_dense=_flag(env.get("NAYOSE_USE_DENSE"), bool(RETRIEVAL_DEFAULTS["use_dense"])),
        dense_views=_flag(env.get("NAYOSE_DENSE_VIEWS"), bool(RETRIEVAL_DEFAULTS["dense_views"])),
        index_backend=_env_text(
            env, "NAYOSE_INDEX_BACKEND", str(RETRIEVAL_DEFAULTS["index_backend"])
        ),
        sparse_query_feature_cap=raw_cap,
        sparse_batch_size=_env_int(
            env, "NAYOSE_SPARSE_BATCH", int(RETRIEVAL_DEFAULTS["sparse_batch_size"]), 1
        ),
        inverted_memory_budget=int(RETRIEVAL_DEFAULTS["inverted_memory_budget"]),
        encode_chunk_size=int(RETRIEVAL_DEFAULTS["encode_chunk_size"]),
    )


def _inference_from_env(env: Mapping[str, str]) -> InferenceConfig:
    return InferenceConfig(
        sparse_top_k=_env_int(
            env, "NAYOSE_SPARSE_TOPK", int(INFERENCE_DEFAULTS["sparse_top_k"]), 1
        ),
        exact_block_cap=_env_int(
            env, "NAYOSE_EXACT_BLOCK_CAP", int(INFERENCE_DEFAULTS["exact_block_cap"]), 1
        ),
        candidate_cap=_env_int(
            env, "NAYOSE_CANDIDATE_CAP", int(INFERENCE_DEFAULTS["candidate_cap"]), 1
        ),
        pair_auto_threshold=float(INFERENCE_DEFAULTS["pair_auto_threshold"]),
        entity_auto_threshold=float(INFERENCE_DEFAULTS["entity_auto_threshold"]),
        entity_review_threshold=_env_float(
            env,
            "NAYOSE_ENTITY_REVIEW_THRESHOLD",
            float(INFERENCE_DEFAULTS["entity_review_threshold"]),
        ),
        entity_margin_threshold=_env_float(
            env, "NAYOSE_ENTITY_MARGIN", float(INFERENCE_DEFAULTS["entity_margin_threshold"])
        ),
        minimum_evidence_families=_env_int(
            env,
            "NAYOSE_MIN_EVIDENCE_FAMILIES",
            int(INFERENCE_DEFAULTS["minimum_evidence_families"]),
            1,
        ),
        minimum_reference_density=_env_float(
            env,
            "NAYOSE_MIN_REFERENCE_DENSITY",
            float(INFERENCE_DEFAULTS["minimum_reference_density"]),
        ),
        dense_profile=_env_text(
            env, "NAYOSE_DENSE_PROFILE", str(INFERENCE_DEFAULTS["dense_profile"])
        ),
        entity_override=_flag(
            env.get("NAYOSE_ENTITY_OVERRIDE"), bool(INFERENCE_DEFAULTS["entity_override"])
        ),
    )


def _training_from_env(
    env: Mapping[str, str], embedding: EmbeddingConfig, runtime: RuntimeConfig
) -> TrainingConfig:
    family = _env_text(env, "NAYOSE_MODEL_FAMILY", str(TRAINING_DEFAULTS["model_family"]))
    if family == "f2llm_330m":
        family = "f2llm"
    return TrainingConfig(
        seed=_env_int(env, "NAYOSE_SEED", int(TRAINING_DEFAULTS["seed"]), 0),
        data_dir=_path(_env_text(env, "NAYOSE_DATA_DIR", str(runtime.data_dir))),
        output_dir=_path(_env_text(env, "NAYOSE_OUTPUT_DIR", str(runtime.models_dir))),
        device=_env_text(env, "NAYOSE_DEVICE", embedding.device),
        embedding_backend=embedding.backend,
        embedding_max_length=embedding.max_length,
        embedding_padding_strategy=embedding.padding_strategy,
        embedding_revision=embedding.revision,
        model_family=family,
        embedding_model=env.get("NAYOSE_EMBEDDING_MODEL") or None,
        strict_backends=_flag(
            env.get("NAYOSE_STRICT_BACKENDS"), bool(TRAINING_DEFAULTS["strict_backends"])
        ),
        decision_profile=_env_text(
            env, "NAYOSE_DECISION_PROFILE", str(TRAINING_DEFAULTS["decision_profile"])
        ),
        use_dense=_flag(env.get("NAYOSE_USE_DENSE"), bool(TRAINING_DEFAULTS["use_dense"])),
        dense_profile=_env_text(
            env, "NAYOSE_DENSE_PROFILE", str(INFERENCE_DEFAULTS["dense_profile"])
        ),
        exact_mode=_env_text(env, "NAYOSE_EXACT_MODE", str(TRAINING_DEFAULTS["exact_mode"])),
        allow_svd_dense_fallback=_flag(
            env.get("NAYOSE_ALLOW_SVD_FALLBACK"),
            bool(TRAINING_DEFAULTS["allow_svd_dense_fallback"]),
        ),
        svd_dimensions=_env_int(env, "NAYOSE_SVD_DIM", int(TRAINING_DEFAULTS["svd_dimensions"]), 2),
        low_memory_models=_flag(
            env.get("NAYOSE_LOW_MEMORY_MODELS"), bool(TRAINING_DEFAULTS["low_memory_models"])
        ),
        pair_model_backend=_env_text(
            env, "NAYOSE_PAIR_MODEL", str(TRAINING_DEFAULTS["pair_model_backend"])
        ),
        exact_block_cap=_env_int(
            env, "NAYOSE_EXACT_BLOCK_CAP", int(TRAINING_DEFAULTS["exact_block_cap"]), 1
        ),
        sparse_top_k=_env_int(env, "NAYOSE_SPARSE_TOPK", int(TRAINING_DEFAULTS["sparse_top_k"]), 1),
        dense_top_k=_env_int(env, "NAYOSE_DENSE_TOPK", int(TRAINING_DEFAULTS["dense_top_k"]), 1),
        candidate_cap_per_record=_env_int(
            env, "NAYOSE_CANDIDATE_CAP", int(TRAINING_DEFAULTS["candidate_cap_per_record"]), 1
        ),
        sparse_batch_size=_env_int(
            env, "NAYOSE_SPARSE_BATCH", int(TRAINING_DEFAULTS["sparse_batch_size"]), 1
        ),
        inverted_memory_budget=int(RETRIEVAL_DEFAULTS["inverted_memory_budget"]),
        neural_batch_size=embedding.batch_size,
        rerank_top_k=_env_int(env, "NAYOSE_RERANK_TOPK", int(TRAINING_DEFAULTS["rerank_top_k"]), 1),
        rerank_pair_limit=_env_int(
            env, "NAYOSE_RERANK_LIMIT", int(TRAINING_DEFAULTS["rerank_pair_limit"]), 1
        ),
        embedding_dim=_env_int(env, "NAYOSE_EMBED_DIM", int(TRAINING_DEFAULTS["embedding_dim"]), 2),
        training_negatives_per_positive=_env_int(
            env, "NAYOSE_NEG_PER_POS", int(TRAINING_DEFAULTS["training_negatives_per_positive"]), 1
        ),
        pair_auto_precision_target=_env_float(
            env,
            "NAYOSE_PAIR_AUTO_PRECISION",
            float(TRAINING_DEFAULTS["pair_auto_precision_target"]),
        ),
        pair_review_precision_target=_env_float(
            env,
            "NAYOSE_PAIR_REVIEW_PRECISION",
            float(TRAINING_DEFAULTS["pair_review_precision_target"]),
        ),
        entity_auto_precision_target=_env_float(
            env,
            "NAYOSE_ENTITY_AUTO_PRECISION",
            float(TRAINING_DEFAULTS["entity_auto_precision_target"]),
        ),
        entity_review_threshold=_env_float(
            env,
            "NAYOSE_ENTITY_REVIEW_THRESHOLD",
            float(TRAINING_DEFAULTS["entity_review_threshold"]),
        ),
        entity_margin_threshold=_env_float(
            env, "NAYOSE_ENTITY_MARGIN", float(TRAINING_DEFAULTS["entity_margin_threshold"])
        ),
        minimum_evidence_families=_env_int(
            env,
            "NAYOSE_MIN_EVIDENCE_FAMILIES",
            int(TRAINING_DEFAULTS["minimum_evidence_families"]),
            1,
        ),
        minimum_reference_density=_env_float(
            env,
            "NAYOSE_MIN_REFERENCE_DENSITY",
            float(TRAINING_DEFAULTS["minimum_reference_density"]),
        ),
        max_full_match_records=_env_int(
            env, "NAYOSE_MAX_FULL_RECORDS", int(TRAINING_DEFAULTS["max_full_match_records"]), 1
        ),
        pair_feature_batch=_env_int(
            env, "NAYOSE_PAIR_FEATURE_BATCH", int(TRAINING_DEFAULTS["pair_feature_batch"]), 1
        ),
        save_all_candidate_pairs=_flag(
            env.get("NAYOSE_SAVE_ALL_CANDIDATES"),
            bool(TRAINING_DEFAULTS["save_all_candidate_pairs"]),
        ),
        entity_scoring_mode=_env_text(
            env, "NAYOSE_ENTITY_SCORING_MODE", str(TRAINING_DEFAULTS["entity_scoring_mode"])
        ),
    )


def _generation_from_env(env: Mapping[str, str]) -> SyntheticConfig:
    data_root = _path(_env_text(env, "NAYOSE_DATA_ROOT", str(_PATH_DEFAULTS["data_root"])))
    return SyntheticConfig(
        seed=_env_int(env, "NAYOSE_SEED", int(GENERATION_DEFAULTS["seed"]), 0),
        profile=_env_text(env, "NAYOSE_PROFILE", str(GENERATION_DEFAULTS["profile"])),
        difficulty=_env_text(
            env, "NAYOSE_DIFFICULTY", str(GENERATION_DEFAULTS["default_difficulty"])
        ),
        output_dir=_path(_env_text(env, "NAYOSE_DATA_DIR", str(data_root))),
        raw_dir=_path(_env_text(env, "NAYOSE_RAW_DIR", str(data_root / "raw"))),
        n_parties_override=_env_int(env, "NAYOSE_N_PARTIES", 0, 0),
        snapshot_date=_env_text(
            env, "NAYOSE_SNAPSHOT_DATE", str(GENERATION_DEFAULTS["snapshot_date"])
        ),
        query_date=_env_text(env, "NAYOSE_QUERY_DATE", str(GENERATION_DEFAULTS["query_date"])),
        twin_rate=_env_float(env, "NAYOSE_TWIN_RATE", float(GENERATION_DEFAULTS["twin_rate"])),
    )


def load_config(environ: Mapping[str, str] | None = None) -> ConfigSnapshot:
    """Resolve the canonical document and environment overrides once.

    ``environ`` is injectable for tests and command adapters. Application code
    should call this at its composition root and pass only the relevant view.
    """
    env = os.environ if environ is None else environ
    _validate_environment(env)
    runtime = _runtime_from_env(env)
    embedding = _embedding_from_env(env)
    performance = _performance_from_env(env)
    retrieval = _retrieval_from_env(env)
    inference = _inference_from_env(env)
    storage = _storage_from_env(env)
    return ConfigSnapshot(
        runtime=runtime,
        embedding=embedding,
        performance=performance,
        retrieval=retrieval,
        inference=inference,
        storage=storage,
        training=_training_from_env(env, embedding, runtime),
        generation=_generation_from_env(env),
        sources=DataSources(),
        release=ReleaseConfig(
            min_top1_gain=_env_float(
                env, "NAYOSE_MIN_TOP1_GAIN", float(RELEASE_DEFAULTS["min_top1_gain"])
            ),
            min_recall5_gain=_env_float(
                env, "NAYOSE_MIN_RECALL5_GAIN", float(RELEASE_DEFAULTS["min_recall5_gain"])
            ),
            min_hard_recall5_gain=_env_float(
                env,
                "NAYOSE_MIN_HARD_RECALL5_GAIN",
                float(RELEASE_DEFAULTS["min_hard_recall5_gain"]),
            ),
            baseline_run=env.get("NAYOSE_BASELINE_RUN") or None,
        ),
        tooling=ToolingConfig(
            metrics_dir=_path(env["NAYOSE_METRICS_DIR"]) if env.get("NAYOSE_METRICS_DIR") else None,
            architecture_ledger=_path(env["NAYOSE_ARCHITECTURE_LEDGER"])
            if env.get("NAYOSE_ARCHITECTURE_LEDGER")
            else None,
            e2e=_flag(env.get("NAYOSE_E2E"), bool(TOOLING_DEFAULTS["e2e"])),
        ),
    )


def configuration_summary(snapshot: ConfigSnapshot) -> dict[str, Any]:
    """Build the redacted, section-shaped configuration read model.

    The dashboard can explain which configuration is active without receiving
    secrets or gaining a file-writing path. Interactive run settings remain
    separate request-scoped drafts.
    """
    plain = to_plain(snapshot)
    runtime = plain["runtime"]

    def pick(keys: tuple[str, ...]) -> dict[str, Any]:
        return {key: runtime[key] for key in keys if key in runtime}

    training = dict(plain["training"])
    training["model_specs"] = to_plain(MODEL_SPECS)
    return {
        "schema_version": snapshot.schema_version,
        "config_hash": snapshot.config_hash,
        "source": "nayose.toml + environment overrides",
        "editable": False,
        "sections": {
            "paths": pick(
                ("data_root", "data_dir", "models_dir", "frontend_dir", "operational_db")
            ),
            "serving": pick(
                (
                    "bind_host",
                    "port",
                    "auth_mode",
                    "trusted_auth_subject_header",
                    "trusted_auth_scopes_header",
                    "debug_sql",
                    "warm_embed",
                    "default_reviewer",
                    "device",
                    "exact_mode",
                )
            ),
            "embedding": plain["embedding"],
            "performance": plain["performance"],
            "retrieval": plain["retrieval"],
            "storage": plain["storage"],
            "inference": plain["inference"],
            "training": training,
            "generation": plain["generation"],
            "sources": plain["sources"],
            "release": plain["release"],
            "tooling": plain["tooling"],
        },
    }


def pipeline_model_family(family: str) -> str:
    if family in _PIPELINE_MODEL_FAMILY:
        return _PIPELINE_MODEL_FAMILY[family]
    if family not in SUPPORTED_MODEL_FAMILIES:
        raise ConfigurationError(f"unsupported model family {family!r}")
    return family


def is_f2llm_model(model_id: str) -> bool:
    return model_id.startswith("codefuse-ai/F2LLM-v2-")


def embedding_model_id(family: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if family in {"f2llm", "f2llm_330m"}:
        return F2LLM_330M_MODEL_ID
    return None
