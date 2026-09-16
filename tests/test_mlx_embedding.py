from __future__ import annotations

import numpy as np
import pytest

from api.infrastructure.runtime.mlx_embedding import MLXEmbeddingModel

mx = pytest.importorskip("mlx.core")


class _FakeTokenizer:
    def __call__(self, texts, **kwargs):
        assert kwargs["padding"] in {False, "longest"}
        assert kwargs["truncation"] is True
        assert kwargs["max_length"] == 8
        rows = [[1, 2, 3] if text == "one" else [4, 5, 0] for text in texts]
        masks = [[1, 1, 1] if text == "one" else [1, 1, 0] for text in texts]
        return {
            "input_ids": np.asarray(rows, dtype=np.int32),
            "attention_mask": np.asarray(masks, dtype=np.int32),
        }


class _FakeBody:
    def __call__(self, input_ids):
        batch, length = input_ids.shape
        values = mx.arange(length * 2, dtype=mx.float32).reshape((1, length, 2))
        return mx.broadcast_to(values, (batch, length, 2))


class _FakeModel:
    def __init__(self):
        self.model = _FakeBody()

    def __call__(self, input_ids):
        return self.model(input_ids)


def test_mlx_model_pools_last_non_padding_token_and_normalizes() -> None:
    model = MLXEmbeddingModel(
        _FakeModel(),
        _FakeTokenizer(),
        {"model_type": "qwen3", "hidden_size": 2},
        backend="mlx_cpu",
        max_length=8,
        padding_strategy="longest",
        mlx_module=mx,
    )

    values = model.encode(["one", "two"], batch_size=2)

    expected: np.ndarray = np.asarray([[4.0, 5.0], [2.0, 3.0]], dtype=np.float32)
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    np.testing.assert_allclose(values, expected, rtol=1e-5, atol=1e-5)


def test_mlx_model_deduplicates_inputs_and_restores_order() -> None:
    model = MLXEmbeddingModel(
        _FakeModel(),
        _FakeTokenizer(),
        {"model_type": "qwen3", "hidden_size": 2},
        backend="mlx_cpu",
        max_length=8,
        padding_strategy="longest",
        mlx_module=mx,
    )

    values = model.encode(["two", "one", "two"], batch_size=1)

    assert values.shape == (3, 2)
    np.testing.assert_array_equal(values[0], values[2])


def test_mlx_model_batches_and_supports_raw_embeddings() -> None:
    model = MLXEmbeddingModel(
        _FakeModel(),
        _FakeTokenizer(),
        {"model_type": "qwen3", "hidden_size": 2},
        backend="mlx_cpu",
        max_length=8,
        padding_strategy="longest",
        mlx_module=mx,
    )

    values = model.encode(["one", "two"], batch_size=1, normalize_embeddings=False)

    np.testing.assert_allclose(values, [[4.0, 5.0], [2.0, 3.0]], rtol=1e-6, atol=1e-6)


def test_mlx_model_rejects_unsupported_architecture() -> None:
    with pytest.raises(RuntimeError, match="does not support model_type"):
        MLXEmbeddingModel(
            _FakeModel(),
            _FakeTokenizer(),
            {"model_type": "bert", "hidden_size": 2},
            backend="mlx_cpu",
            max_length=8,
            padding_strategy="longest",
            mlx_module=mx,
        )
