from __future__ import annotations

import copy
from collections.abc import MutableMapping
from pathlib import Path
from typing import cast

import pytest

import nayose.config as config_module
from nayose.config import (
    ConfigurationError,
    EmbeddingConfig,
    InferenceConfig,
    RetrievalConfig,
    RuntimeConfig,
    TrainingConfig,
    configuration_summary,
    load_config,
)


def test_default_toml_document_has_no_unknown_keys() -> None:
    config_module._validate_document(config_module._DEFAULT_DOCUMENT)


def test_unknown_toml_keys_fail_at_their_section() -> None:
    document = copy.deepcopy(config_module._DEFAULT_DOCUMENT)
    document["inference"]["unexpected"] = True

    with pytest.raises(ConfigurationError, match=r"unknown configuration key\(s\) at inference"):
        config_module._validate_document(document)


def test_unknown_nested_model_key_fails() -> None:
    document = copy.deepcopy(config_module._DEFAULT_DOCUMENT)
    document["training"]["model_specs"]["off"]["unexpected"] = "value"

    with pytest.raises(
        ConfigurationError, match=r"unknown configuration key\(s\) at training\.model_specs\.off"
    ):
        config_module._validate_document(document)


def test_snapshot_resolves_environment_overrides_once() -> None:
    snapshot = load_config(
        {
            "NAYOSE_DASH_PORT": "8123",
            "NAYOSE_USE_DENSE": "0",
            "NAYOSE_EXACT_MODE": "exact_only",
            "NAYOSE_CANDIDATE_CAP": "7",
            "NAYOSE_TELEMETRY_INTERVAL_SECONDS": "2.5",
        }
    )

    assert snapshot.runtime.port == 8123
    assert snapshot.runtime.exact_mode == "exact_only"
    assert snapshot.retrieval.use_dense is False
    assert snapshot.inference.candidate_cap == 7
    assert snapshot.embedding.backend == "pytorch_mps"
    assert snapshot.embedding.batch_size == 32
    assert snapshot.training.embedding_backend == "pytorch_mps"
    assert snapshot.storage.reserve_minimum_bytes == 20 << 30
    assert snapshot.runtime.telemetry_interval_seconds == pytest.approx(2.5)


def test_storage_policy_environment_overrides_are_validated() -> None:
    snapshot = load_config(
        {
            "NAYOSE_STORAGE_RESERVE_RATIO": "0.2",
            "NAYOSE_STORAGE_RESERVE_MINIMUM_BYTES": "1024",
            "NAYOSE_STORAGE_WORKSPACE_STALE_SECONDS": "30",
        }
    )

    assert snapshot.storage.reserve_ratio == pytest.approx(0.2)
    assert snapshot.storage.reserve_minimum_bytes == 1024
    assert snapshot.storage.workspace_stale_seconds == 30


def test_embedding_optimization_values_are_toml_only() -> None:
    snapshot = load_config(
        {
            "NAYOSE_NEURAL_BATCH": "64",
            "NAYOSE_PROCESS_WORKERS": "4",
            "NAYOSE_MODEL_THREADS": "2",
        }
    )

    assert snapshot.embedding.batch_size == 32
    assert snapshot.embedding.process_workers == 1
    assert snapshot.embedding.max_length == 0
    assert snapshot.embedding.padding_strategy == "longest"


def test_runtime_embedding_overrides_cannot_bypass_toml_settings() -> None:
    from api.infrastructure.runtime.embedding_runtime import (
        close_query_encoder,
        configure_query_encoder,
    )

    settings = EmbeddingConfig(backend="mlx_cpu")
    try:
        with pytest.raises(ValueError, match="canonical configuration"):
            configure_query_encoder("example/model", batch_size=64, settings=settings)
    finally:
        close_query_encoder()


def test_training_cpu_override_does_not_bypass_serving_mps_lock() -> None:
    training = TrainingConfig.from_env({"NAYOSE_DEVICE": "cpu", "NAYOSE_MODEL_FAMILY": "off"})

    assert training.device == "cpu"
    assert training.model_family == "off"


def test_internal_macos_runtime_marker_is_accepted() -> None:
    snapshot = load_config({"NAYOSE_MACOS_RUNTIME_ALIGNED": "1"})

    assert snapshot.runtime.exact_mode == "hybrid"


def test_unknown_nayose_environment_key_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match="NAYOSE_NOT_KNOWN"):
        load_config({"NAYOSE_NOT_KNOWN": "1"})


def test_cross_field_validation_rejects_invalid_combinations() -> None:
    with pytest.raises(ConfigurationError, match="between 1 and 65535"):
        RuntimeConfig(port=0)
    with pytest.raises(ConfigurationError, match="telemetry_interval_seconds must be positive"):
        RuntimeConfig(telemetry_interval_seconds=0)
    with pytest.raises(ConfigurationError, match="must not bind to loopback"):
        RuntimeConfig(auth_mode="trusted_proxy", bind_host="127.0.0.1")
    with pytest.raises(ConfigurationError, match="locked to MPS"):
        EmbeddingConfig(device="cpu")
    with pytest.raises(ConfigurationError, match="embedding backend"):
        EmbeddingConfig(backend="unsupported")
    with pytest.raises(ConfigurationError, match="max_length padding"):
        EmbeddingConfig(padding_strategy="max_length")
    with pytest.raises(ConfigurationError, match="must be positive"):
        RetrievalConfig(sparse_batch_size=0)
    with pytest.raises(ConfigurationError, match="between 0 and 1"):
        InferenceConfig(minimum_reference_density=1.1)
    with pytest.raises(ConfigurationError, match="without SVD fallback"):
        TrainingConfig(model_family="f2llm", allow_svd_dense_fallback=True)


def test_secret_is_redacted_and_excluded_from_identity() -> None:
    first = load_config({"NAYOSE_API_TOKEN": "first-secret"})
    second = load_config({"NAYOSE_API_TOKEN": "second-secret"})

    assert first.redacted["runtime"]["api_token"] == "<redacted>"
    assert "first-secret" not in repr(first.redacted)
    assert "second-secret" not in repr(second.redacted)
    assert first.config_hash == second.config_hash


def test_snapshot_views_are_immutable_and_plain_conversion_is_serializable() -> None:
    snapshot = load_config()
    query_mix = cast(MutableMapping[str, float], snapshot.generation.query_mix)

    with pytest.raises(TypeError):
        query_mix["new_customer"] = 0.0
    with pytest.raises((AttributeError, TypeError)):
        snapshot.runtime.port = 9000  # type: ignore[misc]

    plain = config_module.to_plain(snapshot)
    assert isinstance(plain["generation"]["query_mix"], dict)
    assert isinstance(plain["runtime"]["models_dir"], str)


def test_manifest_thresholds_return_a_new_engine_settings_snapshot(tmp_path: Path) -> None:
    from api.infrastructure.runtime.engine import _apply_manifest_thresholds
    from api.infrastructure.runtime.engine_config import EngineSettings

    (tmp_path / "run_manifest.json").write_text(
        '{"thresholds":{"entity_auto":0.8,"entity_review":0.6},'
        '"config":{"entity_margin_threshold":0.2,"minimum_evidence_families":3, '
        '"minimum_reference_density":0.4,"dense_profile":"field_aware"}}',
        encoding="utf-8",
    )
    original = EngineSettings(model_dir=tmp_path, config_snapshot=load_config({}))
    updated = _apply_manifest_thresholds(original)

    assert updated is not original
    assert original.entity_auto_threshold != updated.entity_auto_threshold
    assert updated.entity_auto_threshold == pytest.approx(0.8)
    assert updated.entity_review_threshold == pytest.approx(0.6)
    assert updated.entity_margin_threshold == pytest.approx(0.2)
    assert updated.minimum_evidence_families == 3
    assert updated.minimum_reference_density == pytest.approx(0.4)
    assert updated.dense_profile == "field_aware"


def test_configuration_summary_is_section_shaped_redacted_and_read_only() -> None:
    snapshot = load_config({"NAYOSE_API_TOKEN": "secret-not-for-the-browser"})
    summary = configuration_summary(snapshot)

    assert summary["editable"] is False
    assert summary["source"] == "nayose.toml + environment overrides"
    assert summary["config_hash"] == snapshot.config_hash
    assert "secret-not-for-the-browser" not in repr(summary)
    assert set(summary["sections"]) == {
        "paths",
        "serving",
        "embedding",
        "performance",
        "retrieval",
        "storage",
        "inference",
        "training",
        "generation",
        "sources",
        "release",
        "tooling",
    }
    assert "api_token" not in summary["sections"]["serving"]
    assert summary["sections"]["training"]["model_specs"]


def test_runtime_consumers_do_not_read_environment_directly() -> None:
    paths = (
        Path("src/api/infrastructure/retrieval/bundle_retrieval.py"),
        Path("src/api/infrastructure/runtime/engine.py"),
        Path("src/api/infrastructure/runtime/embedding_cache.py"),
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "os.getenv" not in source
        assert "os.environ.get" not in source
