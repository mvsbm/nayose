import numpy as np
import pytest

from api.embedding_cache import cache_paths, load, save, text_fingerprint


def test_text_fingerprint_is_order_sensitive_without_exposing_text() -> None:
    first = text_fingerprint(["alpha", "beta"])
    second = text_fingerprint(["beta", "alpha"])

    assert first != second
    assert "alpha" not in first
    assert text_fingerprint(["alpha", "beta"]) == first


def test_embedding_cache_requires_all_provenance_fields(tmp_path) -> None:
    vector_path, metadata_path = cache_paths(tmp_path, "codefuse-ai/F2LLM-v2-330M", "query")
    values: np.ndarray = np.eye(2, dtype=np.float32)
    metadata = {
        "model_id": "codefuse-ai/F2LLM-v2-330M",
        "data_fingerprint": "data-fp",
        "query_text_fingerprint": "query-fp",
        "rows": 2,
        "device": "mps",
        "torch_dtype": "float16",
        "batch_size": 32,
    }
    save(vector_path, metadata_path, values, metadata)

    loaded = load(vector_path, metadata_path, metadata)

    assert loaded is not None
    np.testing.assert_array_equal(loaded, values)
    assert load(vector_path, metadata_path, {**metadata, "data_fingerprint": "other"}) is None


def test_embedding_cache_rejects_non_finite_values(tmp_path) -> None:
    vector_path, metadata_path = cache_paths(tmp_path, "model", "query")

    with pytest.raises(ValueError, match="finite"):
        save(vector_path, metadata_path, np.array([[np.nan]], dtype=np.float32), {})
