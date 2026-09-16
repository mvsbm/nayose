"""Generation and training views for the Nayose configuration contract."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from math import isclose
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .config_views import (
    EMBEDDING_BACKENDS,
    EMBEDDING_DEFAULTS,
    EXACT_MODES,
    F2LLM_MPS_BATCH_SIZE,
    F2LLM_MPS_DEVICE,
    F2LLM_MPS_TORCH_DTYPE,
    GENERATION_DEFAULTS,
    INFERENCE_DEFAULTS,
    RETRIEVAL_DEFAULTS,
    SOURCE_DEFAULTS,
    TRAINING_DEFAULTS,
    ConfigurationError,
    ModelSpec,
    default_data_dir,
    to_plain,
)
from .config_views import (
    PATH_DEFAULTS as _PATH_DEFAULTS,
)

# These are deliberately derived from the TOML document, not a second default registry.
MAX_ENTITY_SEEDS = int(GENERATION_DEFAULTS["max_entity_seeds"])
PROFILES: dict[str, dict[str, float]] = {
    name: {key: float(value) for key, value in values.items()}
    for name, values in GENERATION_DEFAULTS["profiles"].items()
}
DIFFICULTY: dict[str, dict[str, float]] = {
    name: {key: float(value) for key, value in values.items()}
    for name, values in GENERATION_DEFAULTS["difficulty"].items()
}
QUERY_DIFFICULTY: dict[str, dict[str, float]] = {
    name: {key: float(value) for key, value in values.items()}
    for name, values in GENERATION_DEFAULTS["query_difficulty"].items()
}
QUERY_MIX: dict[str, float] = {
    name: float(value) for name, value in GENERATION_DEFAULTS["query_mix"].items()
}
DEFAULT_EVENT_RATES: dict[str, float] = {
    name: float(value) for name, value in GENERATION_DEFAULTS["event_rates"].items()
}
EVENT_NAMES: tuple[str, ...] = tuple(DEFAULT_EVENT_RATES)
STALE_EVENT_NAMES: frozenset[str] = frozenset(
    {"rename_stale", "move_stale", "contact_change", "full_snapshot"}
)
DEFAULT_STALE_RATE = float(GENERATION_DEFAULTS["default_stale_rate"])
CHANNEL_NAMES: tuple[str, ...] = ("web", "agent_pc", "paper_ocr", "legacy_migration")
NOISE_FIELDS: tuple[str, ...] = (
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "postal_code",
    "address",
    "phone",
    "email",
)
EMAIL_DOMAINS: dict[str, tuple[str, ...]] = {
    name: tuple(values) for name, values in GENERATION_DEFAULTS["email_domains"].items()
}
DEFAULT_LANDLINE_SHARE = float(GENERATION_DEFAULTS["default_landline_share"])

PRODUCT_PROFILES: dict[str, dict[str, Any]] = {}
for _name, _values in GENERATION_DEFAULTS["product_profiles"].items():
    PRODUCT_PROFILES[_name] = {
        key: tuple(value) if key in {"risk_fields", "property_types", "occupancies"} else value
        for key, value in _values.items()
    }

_POLICY_DEFAULTS = GENERATION_DEFAULTS["policy_params"]
POLICY_PARAMS: dict[str, Any] = {
    key: tuple(value)
    if key
    in {
        "product_mix",
        "start_range",
        "household_size_probs",
        "claim_close_years",
        "cancellation_reasons",
        "lapse_reasons",
    }
    else value
    for key, value in _POLICY_DEFAULTS.items()
}
POLICY_PARAMS["product_mix"] = tuple(POLICY_PARAMS["product_mix"])
POLICY_PARAMS["household_sharing"] = {
    key: float(value) for key, value in _POLICY_DEFAULTS["household_sharing"].items()
}
CHANNEL_CONTACT_PROFILE: dict[str, dict[str, float]] = {
    channel: {key: float(value) for key, value in values.items()}
    for channel, values in GENERATION_DEFAULTS["channel_contact_profile"].items()
}
_NOISE_DEFAULTS = GENERATION_DEFAULTS["noise"]


@dataclass(frozen=True, slots=True)
class SyntheticConfig:
    """Immutable generation run settings backed by the canonical document."""

    seed: int = int(GENERATION_DEFAULTS["seed"])
    profile: str = str(GENERATION_DEFAULTS["profile"])
    difficulty: str = str(GENERATION_DEFAULTS["default_difficulty"])
    output_dir: Path | None = None
    raw_dir: Path | None = None
    n_parties_override: int = 0
    mean_records_override: float | None = None
    poor_override: float | None = None
    catastrophic_override: float | None = None
    noise_event_rates: dict[str, float] = field(default_factory=dict)
    noise_band_scale: dict[str, float] = field(default_factory=dict)
    noise_missing_rates: dict[str, float] = field(default_factory=dict)
    noise_field_availability: dict[str, dict[str, float]] = field(default_factory=dict)
    noise_stale_rate: float | None = None
    snapshot_date: str = str(GENERATION_DEFAULTS["snapshot_date"])
    query_date: str = str(GENERATION_DEFAULTS["query_date"])
    n_agencies: int = int(GENERATION_DEFAULTS["n_agencies"])
    person_share: float = float(GENERATION_DEFAULTS["person_share"])
    query_share: float = float(GENERATION_DEFAULTS["query_share"])
    twin_rate: float = float(GENERATION_DEFAULTS["twin_rate"])
    lineage_mode: str = str(GENERATION_DEFAULTS["lineage_mode"])
    query_mix: dict[str, float] = field(default_factory=lambda: dict(QUERY_MIX))
    n_parties: int = 0
    mean_records: float = float(PROFILES["generate_only"]["mean_records"])
    poor: float = 0.33
    catastrophic: float = 0.04
    copy: float = 0.09
    stale: float = 0.34

    @property
    def snapshot_year(self) -> int:
        return int(self.snapshot_date[:4])

    def __post_init__(self) -> None:
        if self.output_dir is None:
            object.__setattr__(self, "output_dir", default_data_dir())
        if self.raw_dir is None:
            object.__setattr__(self, "raw_dir", default_data_dir() / "raw")
        if self.profile not in PROFILES:
            raise ConfigurationError(
                f"Unknown profile {self.profile!r}; choose from {list(PROFILES)}"
            )
        if self.difficulty not in DIFFICULTY:
            raise ConfigurationError(
                f"Unknown difficulty {self.difficulty!r}; choose from {list(DIFFICULTY)}"
            )
        if not 0 <= self.n_parties_override <= MAX_ENTITY_SEEDS:
            raise ConfigurationError(
                f"n_parties_override must be between 0 and {MAX_ENTITY_SEEDS:,}"
            )
        if self.mean_records_override is not None and not 1.0 <= self.mean_records_override <= 20.0:
            raise ConfigurationError("mean_records_override must be between 1 and 20")
        try:
            snapshot = date.fromisoformat(self.snapshot_date)
            query = date.fromisoformat(self.query_date)
        except ValueError as exc:
            raise ConfigurationError("snapshot_date and query_date must be ISO dates") from exc
        if query < snapshot:
            raise ConfigurationError("query_date must not precede snapshot_date")
        if not 1 <= self.n_agencies <= 100:
            raise ConfigurationError("n_agencies must be between 1 and 100")
        for name, value in (
            ("person_share", self.person_share),
            ("query_share", self.query_share),
            ("twin_rate", self.twin_rate),
        ):
            if not 0.0 <= value <= 1.0:
                raise ConfigurationError(f"{name} must be between 0 and 1")
        if set(self.query_mix) != set(QUERY_MIX) or any(
            not 0.0 <= value <= 1.0 for value in self.query_mix.values()
        ):
            raise ConfigurationError(
                f"query_mix keys must be {sorted(QUERY_MIX)} with values between 0 and 1"
            )
        if not isclose(sum(self.query_mix.values()), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ConfigurationError("query_mix probabilities must sum to 1")
        for name, values, low, high, allowed in (
            ("noise_event_rates", self.noise_event_rates, 0.0, 1.0, set(EVENT_NAMES)),
            (
                "noise_band_scale",
                self.noise_band_scale,
                0.0,
                3.0,
                {"average", "poor", "catastrophic"},
            ),
            (
                "noise_missing_rates",
                self.noise_missing_rates,
                0.0,
                1.0,
                {"average", "poor", "catastrophic"},
            ),
        ):
            unknown = set(values) - allowed
            if unknown or any(not low <= value <= high for value in values.values()):
                raise ConfigurationError(
                    f"{name} contains unknown or out-of-range values: {sorted(unknown)}"
                )
        for channel, values in self.noise_field_availability.items():
            if (
                channel not in CHANNEL_NAMES
                or set(values) - set(NOISE_FIELDS)
                or any(not 0.0 <= value <= 1.0 for value in values.values())
            ):
                raise ConfigurationError(
                    "noise_field_availability contains an unknown or out-of-range "
                    f"channel/field: {channel}"
                )
        if self.noise_stale_rate is not None and not 0.0 <= self.noise_stale_rate <= 1.0:
            raise ConfigurationError("noise_stale_rate must be between 0 and 1")
        profile = PROFILES[self.profile]
        difficulty = DIFFICULTY[self.difficulty]
        poor = self.poor_override if self.poor_override is not None else float(difficulty["poor"])
        catastrophic = (
            self.catastrophic_override
            if self.catastrophic_override is not None
            else float(difficulty["catastrophic"])
        )
        if not 0.0 <= catastrophic <= poor <= 1.0:
            raise ConfigurationError(
                "catastrophic_override must be <= poor_override and both between 0 and 1"
            )
        for name in (
            "noise_event_rates",
            "noise_band_scale",
            "noise_missing_rates",
            "noise_field_availability",
            "query_mix",
        ):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        object.__setattr__(self, "n_parties", self.n_parties_override or int(profile["n_parties"]))
        object.__setattr__(
            self, "mean_records", self.mean_records_override or float(profile["mean_records"])
        )
        object.__setattr__(self, "poor", poor)
        object.__setattr__(self, "catastrophic", catastrophic)
        object.__setattr__(self, "copy", float(difficulty["copy"]))
        object.__setattr__(self, "stale", float(difficulty["stale"]))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SyntheticConfig:
        from .config import load_config

        return load_config(environ).generation


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class NoiseConfig:
    """Correlated dirty-data event settings."""

    event_rates: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_EVENT_RATES))
    field_availability: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            channel: {key: float(value) for key, value in values.items()}
            for channel, values in _NOISE_DEFAULTS["field_availability"].items()
        }
    )
    channel_events: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            channel: tuple(values) for channel, values in _NOISE_DEFAULTS["channel_events"].items()
        }
    )
    band_scale: dict[str, float] = field(
        default_factory=lambda: {
            key: float(value) for key, value in _NOISE_DEFAULTS["band_scale"].items()
        }
    )
    missing_rates: dict[str, float] = field(
        default_factory=lambda: {
            key: float(value) for key, value in _NOISE_DEFAULTS["missing_rates"].items()
        }
    )
    channel_family: dict[str, str] = field(
        default_factory=lambda: {
            key: str(value) for key, value in _NOISE_DEFAULTS["channel_family"].items()
        }
    )
    vintage: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {
            key: tuple(value["values"]) for key, value in _NOISE_DEFAULTS["vintage"].items()
        }
    )
    stale_rate: float = DEFAULT_STALE_RATE
    query_profile: dict[str, float] = field(
        default_factory=lambda: dict(QUERY_DIFFICULTY["realistic"])
    )


def noise_for_config(cfg: SyntheticConfig) -> NoiseConfig:
    base = NoiseConfig()
    realistic_stale = DIFFICULTY["realistic"]["stale"]
    query_profile = dict(QUERY_DIFFICULTY[cfg.difficulty])
    stale_rate = (
        cfg.noise_stale_rate
        if cfg.noise_stale_rate is not None
        else min(1.0, DEFAULT_STALE_RATE * cfg.stale / realistic_stale)
    )
    return NoiseConfig(
        event_rates={**base.event_rates, **cfg.noise_event_rates},
        field_availability={
            channel: {
                **base.field_availability.get(channel, {}),
                **cfg.noise_field_availability.get(channel, {}),
            }
            for channel in CHANNEL_NAMES
        },
        channel_events=base.channel_events,
        band_scale={**base.band_scale, **cfg.noise_band_scale},
        missing_rates={**base.missing_rates, **cfg.noise_missing_rates},
        channel_family=base.channel_family,
        vintage=base.vintage,
        stale_rate=stale_rate,
        query_profile=query_profile,
    )


@dataclass(frozen=True, slots=True)
class DataSources:
    """External data source identifiers and cache names."""

    persona_dataset: str = str(SOURCE_DEFAULTS["persona_dataset"])
    persona_split: str = str(SOURCE_DEFAULTS["persona_split"])
    persona_cache: str = str(SOURCE_DEFAULTS["persona_cache"])
    edinet_dataset: str = str(SOURCE_DEFAULTS["edinet_dataset"])
    edinet_split: str = str(SOURCE_DEFAULTS["edinet_split"])
    japan_post_zip_url: str = str(SOURCE_DEFAULTS["japan_post_zip_url"])
    postal_zip_name: str = str(SOURCE_DEFAULTS["postal_zip_name"])
    postal_csv_name: str = str(SOURCE_DEFAULTS["postal_csv_name"])


MODEL_SPECS: dict[str, ModelSpec] = {}
for _name, _spec in TRAINING_DEFAULTS["model_specs"].items():
    _family = "f2llm_0.6b" if _name == "f2llm_0_6b" else _name
    MODEL_SPECS[_family] = {
        "embedding": str(_spec["embedding"]) or None,
        "reranker": str(_spec["reranker"]) or None,
        "query_prefix": str(_spec["query_prefix"]),
        "document_prefix": str(_spec["document_prefix"]),
    }


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    """Narrow model-training view."""

    seed: int = int(TRAINING_DEFAULTS["seed"])
    data_dir: Path = field(default_factory=lambda: Path(_PATH_DEFAULTS["data_dir"]))
    output_dir: Path = field(default_factory=lambda: Path(_PATH_DEFAULTS["models_dir"]))
    device: str = str(EMBEDDING_DEFAULTS["device"])
    embedding_backend: str = str(EMBEDDING_DEFAULTS["backend"])
    embedding_max_length: int = int(EMBEDDING_DEFAULTS["optimization"]["max_length"])
    embedding_padding_strategy: str = str(EMBEDDING_DEFAULTS["optimization"]["padding_strategy"])
    embedding_revision: str = str(EMBEDDING_DEFAULTS["revision"])
    model_family: str = str(TRAINING_DEFAULTS["model_family"])
    embedding_model: str | None = None
    strict_backends: bool = bool(TRAINING_DEFAULTS["strict_backends"])
    decision_profile: str = str(TRAINING_DEFAULTS["decision_profile"])
    use_dense: bool = bool(TRAINING_DEFAULTS["use_dense"])
    dense_profile: str = str(INFERENCE_DEFAULTS["dense_profile"])
    exact_mode: str = str(TRAINING_DEFAULTS["exact_mode"])
    allow_svd_dense_fallback: bool = bool(TRAINING_DEFAULTS["allow_svd_dense_fallback"])
    svd_dimensions: int = int(TRAINING_DEFAULTS["svd_dimensions"])
    low_memory_models: bool = bool(TRAINING_DEFAULTS["low_memory_models"])
    pair_model_backend: str = str(TRAINING_DEFAULTS["pair_model_backend"])
    exact_block_cap: int = int(TRAINING_DEFAULTS["exact_block_cap"])
    sparse_top_k: int = int(TRAINING_DEFAULTS["sparse_top_k"])
    dense_top_k: int = int(TRAINING_DEFAULTS["dense_top_k"])
    candidate_cap_per_record: int = int(TRAINING_DEFAULTS["candidate_cap_per_record"])
    sparse_batch_size: int = int(TRAINING_DEFAULTS["sparse_batch_size"])
    inverted_memory_budget: int = int(RETRIEVAL_DEFAULTS["inverted_memory_budget"])
    neural_batch_size: int = int(TRAINING_DEFAULTS["neural_batch_size"])
    rerank_top_k: int = int(TRAINING_DEFAULTS["rerank_top_k"])
    rerank_pair_limit: int = int(TRAINING_DEFAULTS["rerank_pair_limit"])
    embedding_dim: int = int(TRAINING_DEFAULTS["embedding_dim"])
    training_negatives_per_positive: int = int(TRAINING_DEFAULTS["training_negatives_per_positive"])
    pair_auto_precision_target: float = float(TRAINING_DEFAULTS["pair_auto_precision_target"])
    pair_review_precision_target: float = float(TRAINING_DEFAULTS["pair_review_precision_target"])
    entity_auto_precision_target: float = float(TRAINING_DEFAULTS["entity_auto_precision_target"])
    entity_review_threshold: float = float(TRAINING_DEFAULTS["entity_review_threshold"])
    entity_margin_threshold: float = float(TRAINING_DEFAULTS["entity_margin_threshold"])
    minimum_evidence_families: int = int(TRAINING_DEFAULTS["minimum_evidence_families"])
    minimum_reference_density: float = float(TRAINING_DEFAULTS["minimum_reference_density"])
    max_full_match_records: int = int(TRAINING_DEFAULTS["max_full_match_records"])
    pair_feature_batch: int = int(TRAINING_DEFAULTS["pair_feature_batch"])
    save_all_candidate_pairs: bool = bool(TRAINING_DEFAULTS["save_all_candidate_pairs"])
    entity_scoring_mode: str = str(TRAINING_DEFAULTS["entity_scoring_mode"])

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> TrainingConfig:
        from .config import load_config

        source = os.environ if environ is None else environ
        requested_device = source.get("NAYOSE_DEVICE", "").strip()
        if requested_device and requested_device.casefold() != F2LLM_MPS_DEVICE:
            safe_source = dict(source)
            safe_source["NAYOSE_DEVICE"] = F2LLM_MPS_DEVICE
            return replace(load_config(safe_source).training, device=requested_device)
        return load_config(environ).training

    def validate(self) -> None:
        if self.model_family not in MODEL_SPECS:
            raise ConfigurationError(f"unknown model_family={self.model_family!r}")
        if self.decision_profile not in {"audit", "conservative"}:
            raise ConfigurationError("decision_profile must be audit or conservative")
        if self.dense_profile not in {"record_hybrid", "field_aware"}:
            raise ConfigurationError("dense_profile must be record_hybrid or field_aware")
        if self.exact_mode not in EXACT_MODES:
            raise ConfigurationError("exact_mode must be hybrid, fuzzy_only, or exact_only")
        if self.pair_model_backend not in {"auto", "lightgbm", "sklearn"}:
            raise ConfigurationError("pair_model_backend must be auto, lightgbm, or sklearn")
        if self.entity_scoring_mode not in {"legacy_mean_top3", "logistic_v1"}:
            raise ConfigurationError("unsupported entity_scoring_mode")
        if self.embedding_backend not in EMBEDDING_BACKENDS:
            raise ConfigurationError(f"embedding backend must be one of {EMBEDDING_BACKENDS}")
        if self.embedding_max_length < 0:
            raise ConfigurationError("embedding_max_length must not be negative")
        if self.embedding_padding_strategy not in {"longest", "max_length"}:
            raise ConfigurationError("embedding_padding_strategy must be longest or max_length")
        if self.embedding_padding_strategy == "max_length" and self.embedding_max_length < 1:
            raise ConfigurationError("max_length padding requires a positive max_length")
        if not 0.0 <= self.minimum_reference_density <= 1.0:
            raise ConfigurationError("minimum_reference_density must be between 0 and 1")
        for name in (
            "svd_dimensions",
            "exact_block_cap",
            "sparse_top_k",
            "dense_top_k",
            "candidate_cap_per_record",
            "sparse_batch_size",
            "inverted_memory_budget",
            "neural_batch_size",
            "rerank_top_k",
            "rerank_pair_limit",
            "embedding_dim",
            "training_negatives_per_positive",
            "minimum_evidence_families",
            "max_full_match_records",
            "pair_feature_batch",
        ):
            if getattr(self, name) < 1:
                raise ConfigurationError(f"{name} must be positive")
        for name in (
            "pair_auto_precision_target",
            "pair_review_precision_target",
            "entity_auto_precision_target",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ConfigurationError(f"{name} must be between 0 and 1")
        if self.model_family == "f2llm":
            if self.device != F2LLM_MPS_DEVICE:
                raise ConfigurationError("F2LLM-v2-330M is locked to Apple MPS")
            if self.neural_batch_size != F2LLM_MPS_BATCH_SIZE:
                raise ConfigurationError("F2LLM-v2-330M requires neural batch size 32")
            if not self.use_dense or self.allow_svd_dense_fallback:
                raise ConfigurationError(
                    "F2LLM-v2-330M requires dense retrieval without SVD fallback"
                )

    def model_spec(self) -> ModelSpec:
        spec = MODEL_SPECS[self.model_family].copy()
        if self.embedding_model and self.model_family != "f2llm":
            spec["embedding"] = self.embedding_model
        return spec

    def payload(self) -> dict[str, object]:
        return to_plain(self)

    def run_identity(self, timestamp: str, source: str = "") -> Any:
        payload = {**self.payload(), "source": source}
        config_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:8]
        from api.training.contracts import RunIdentity

        return RunIdentity(f"run_{timestamp}_{config_hash}", self.output_dir, config_hash)

    @property
    def torch_dtype(self) -> str:
        return F2LLM_MPS_TORCH_DTYPE if self.device == F2LLM_MPS_DEVICE else "float32"
