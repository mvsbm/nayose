"""Authoritative Nayose configuration contract and snapshot loader.

The versioned ``nayose.toml`` document is the only source of defaults. This
module owns parsing, environment injection, validation, redaction, and the
immutable subsystem views consumed by the application and generator.
"""

from __future__ import annotations

import ipaddress
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypedDict

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
EXACT_MODES = ("hybrid", "exact_only", "fuzzy_only")
F2LLM_330M_MODEL_ID = "codefuse-ai/F2LLM-v2-330M"
F2LLM_MPS_DEVICE = "mps"
F2LLM_MPS_BATCH_SIZE = 32
F2LLM_MPS_PROCESS_WORKERS = 1
F2LLM_MPS_TORCH_DTYPE = "float16"
SUPPORTED_MODEL_FAMILIES = (
    "off",
    "ruri",
    "f2llm_330m",
    "f2llm",
    "f2llm_0.6b",
    "qwen",
)
_PIPELINE_MODEL_FAMILY = {"f2llm_330m": "f2llm"}


class ModelSpec(TypedDict):
    embedding: str | None
    reranker: str | None
    query_prefix: str
    document_prefix: str


class ConfigurationError(ValueError):
    """Raised when the configuration document or its overrides are invalid."""


_CONFIG_PATH = Path(__file__).with_name("nayose.toml")


def repository_root() -> Path:
    """Locate the checkout root without depending on the caller's cwd."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


with _CONFIG_PATH.open("rb") as _stream:
    _DEFAULT_DOCUMENT: dict[str, Any] = tomllib.load(_stream)


def _section(name: str) -> dict[str, Any]:
    return _DEFAULT_DOCUMENT[name]


REPO_ROOT = repository_root()


def to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: to_plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _path(value: str, *, root: Path | None = None) -> Path:
    result = Path(value).expanduser()
    return (root / result).resolve() if root is not None and not result.is_absolute() else result


def default_data_dir() -> Path:
    return repository_root() / _section("paths")["data_root"]


_PATH_DEFAULTS = _section("paths")
SERVING_DEFAULTS = _section("serving")
EMBEDDING_DEFAULTS = _section("embedding")
PERFORMANCE_DEFAULTS = _section("performance")
RETRIEVAL_DEFAULTS = _section("retrieval")
INFERENCE_DEFAULTS = _section("inference")
TRAINING_DEFAULTS = _section("training")
RELEASE_DEFAULTS = _section("release")
TOOLING_DEFAULTS = _section("tooling")
GENERATION_DEFAULTS = _section("generation")
SOURCE_DEFAULTS = _section("sources")
PATH_DEFAULTS = _PATH_DEFAULTS


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Narrow API-serving view of the resolved configuration."""

    models_dir: Path = field(default_factory=lambda: Path(_PATH_DEFAULTS["models_dir"]))
    data_dir: Path = field(default_factory=lambda: Path(_PATH_DEFAULTS["data_dir"]))
    data_root: Path = field(default_factory=lambda: Path(_PATH_DEFAULTS["data_root"]))
    frontend_dir: Path = field(
        default_factory=lambda: repository_root() / _PATH_DEFAULTS["frontend_dir"]
    )
    bind_host: str = str(SERVING_DEFAULTS["bind_host"])
    port: int = int(SERVING_DEFAULTS["port"])
    api_token: str = ""
    auth_mode: str = str(SERVING_DEFAULTS["auth_mode"])
    trusted_auth_subject_header: str = str(SERVING_DEFAULTS["trusted_auth_subject_header"])
    trusted_auth_scopes_header: str = str(SERVING_DEFAULTS["trusted_auth_scopes_header"])
    debug_sql: bool = bool(SERVING_DEFAULTS["debug_sql"])
    warm_embed: bool = bool(SERVING_DEFAULTS["warm_embed"])
    default_reviewer: str = str(SERVING_DEFAULTS["default_reviewer"])
    device: str = str(SERVING_DEFAULTS["device"])
    exact_mode: str = str(SERVING_DEFAULTS["exact_mode"])
    operational_db: Path | None = field(
        default_factory=lambda: Path(_PATH_DEFAULTS["operational_db"])
    )

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("port must be between 1 and 65535")
        if self.auth_mode not in {"local_bearer", "trusted_proxy"}:
            raise ConfigurationError("auth_mode must be local_bearer or trusted_proxy")
        if self.device != F2LLM_MPS_DEVICE:
            raise ConfigurationError("Nayose neural serving is locked to MPS")
        if self.exact_mode not in EXACT_MODES:
            raise ConfigurationError(f"exact_mode must be one of {EXACT_MODES}")
        if self.auth_mode == "trusted_proxy" and _is_loopback(self.bind_host):
            raise ConfigurationError("trusted_proxy authentication must not bind to loopback")
        if (
            self.auth_mode == "local_bearer"
            and not _is_loopback(self.bind_host)
            and not self.api_token
        ):
            raise ConfigurationError(
                "authentication is required when NAYOSE_BIND_HOST is not loopback"
            )
        if not self.trusted_auth_subject_header or not self.trusted_auth_scopes_header:
            raise ConfigurationError("trusted authentication headers must not be empty")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RuntimeConfig:
        from .config import load_config

        return load_config(environ).runtime


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Narrow embedding-model view shared by training and serving."""

    model_id: str = str(EMBEDDING_DEFAULTS["model_id"])
    device: str = str(EMBEDDING_DEFAULTS["device"])
    batch_size: int = int(EMBEDDING_DEFAULTS["batch_size"])
    process_workers: int = int(EMBEDDING_DEFAULTS["process_workers"])
    torch_dtype: str = str(EMBEDDING_DEFAULTS["torch_dtype"])
    revision: str = str(EMBEDDING_DEFAULTS["revision"])

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ConfigurationError("embedding model_id must not be empty")
        if self.batch_size < 1 or self.process_workers < 1:
            raise ConfigurationError("embedding batch_size and process_workers must be positive")
        if self.device != F2LLM_MPS_DEVICE:
            raise ConfigurationError("neural embedding is locked to MPS")
        if self.torch_dtype != F2LLM_MPS_TORCH_DTYPE:
            raise ConfigurationError("neural embedding is locked to FP16")


@dataclass(frozen=True, slots=True)
class PerformanceConfig:
    """Narrow process and native-thread policy view."""

    process_workers: int | None = None
    cpu_threads: int | None = None
    model_threads: int | None = None
    native_thread_cap: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("process_workers", self.process_workers),
            ("cpu_threads", self.cpu_threads),
            ("model_threads", self.model_threads),
            ("native_thread_cap", self.native_thread_cap),
        ):
            if value is not None and value < 1:
                raise ConfigurationError(f"{name} must be positive when configured")


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Narrow candidate-retrieval and artifact materialization view."""

    use_dense: bool = bool(RETRIEVAL_DEFAULTS["use_dense"])
    dense_views: bool = bool(RETRIEVAL_DEFAULTS["dense_views"])
    index_backend: str = str(RETRIEVAL_DEFAULTS["index_backend"])
    sparse_query_feature_cap: int | None = int(RETRIEVAL_DEFAULTS["sparse_query_feature_cap"])
    sparse_batch_size: int = int(RETRIEVAL_DEFAULTS["sparse_batch_size"])
    inverted_memory_budget: int = int(RETRIEVAL_DEFAULTS["inverted_memory_budget"])
    encode_chunk_size: int = int(RETRIEVAL_DEFAULTS["encode_chunk_size"])

    def __post_init__(self) -> None:
        for name in ("sparse_batch_size", "inverted_memory_budget", "encode_chunk_size"):
            if getattr(self, name) < 1:
                raise ConfigurationError(f"{name} must be positive")
        if self.sparse_query_feature_cap is not None and self.sparse_query_feature_cap < 1:
            raise ConfigurationError("sparse_query_feature_cap must be positive")
        if self.index_backend not in {"polars", "pandas"}:
            raise ConfigurationError("index_backend must be polars or pandas")


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    """Narrow serving decision-policy view."""

    sparse_top_k: int = int(INFERENCE_DEFAULTS["sparse_top_k"])
    exact_block_cap: int = int(INFERENCE_DEFAULTS["exact_block_cap"])
    candidate_cap: int = int(INFERENCE_DEFAULTS["candidate_cap"])
    pair_auto_threshold: float = float(INFERENCE_DEFAULTS["pair_auto_threshold"])
    entity_auto_threshold: float = float(INFERENCE_DEFAULTS["entity_auto_threshold"])
    entity_review_threshold: float = float(INFERENCE_DEFAULTS["entity_review_threshold"])
    entity_margin_threshold: float = float(INFERENCE_DEFAULTS["entity_margin_threshold"])
    minimum_evidence_families: int = int(INFERENCE_DEFAULTS["minimum_evidence_families"])
    minimum_reference_density: float = float(INFERENCE_DEFAULTS["minimum_reference_density"])
    dense_profile: str = str(INFERENCE_DEFAULTS["dense_profile"])
    entity_override: bool = bool(INFERENCE_DEFAULTS["entity_override"])

    def __post_init__(self) -> None:
        for name in (
            "sparse_top_k",
            "exact_block_cap",
            "candidate_cap",
            "minimum_evidence_families",
        ):
            if getattr(self, name) < 1:
                raise ConfigurationError(f"{name} must be positive")
        if self.dense_profile not in {"record_hybrid", "field_aware"}:
            raise ConfigurationError("dense_profile must be record_hybrid or field_aware")
        if not 0.0 <= self.minimum_reference_density <= 1.0:
            raise ConfigurationError("minimum_reference_density must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ReleaseConfig:
    """Release promotion thresholds."""

    min_top1_gain: float = float(RELEASE_DEFAULTS["min_top1_gain"])
    min_recall5_gain: float = float(RELEASE_DEFAULTS["min_recall5_gain"])
    min_hard_recall5_gain: float = float(RELEASE_DEFAULTS["min_hard_recall5_gain"])
    baseline_run: str | None = None

    def __post_init__(self) -> None:
        for name in ("min_top1_gain", "min_recall5_gain", "min_hard_recall5_gain"):
            if getattr(self, name) < 0:
                raise ConfigurationError(f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class ToolingConfig:
    """Non-runtime quality-gate settings injected into command adapters."""

    metrics_dir: Path | None = None
    architecture_ledger: Path | None = None
    e2e: bool = bool(TOOLING_DEFAULTS["e2e"])
