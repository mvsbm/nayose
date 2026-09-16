"""MLX embedding adapter for the Qwen3-based F2LLM models."""

from __future__ import annotations

import gc
import platform
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

_SUPPORTED_MODEL_TYPES = frozenset({"qwen3"})


class MLXEmbeddingModel:
    """Expose MLX Qwen3 inference through the SentenceTransformer encode shape."""

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        model_config: dict[str, Any],
        *,
        backend: str,
        max_length: int,
        padding_strategy: str,
        mlx_module: Any,
    ) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self._model_config = model_config
        self._backend = backend
        self._max_length = max_length or int(model_config.get("max_position_embeddings", 0))
        self._padding_strategy = padding_strategy
        self._mx = mlx_module
        self._dimensions = int(model_config.get("hidden_size", 0))
        self._validate_model()

    @classmethod
    def from_pretrained(
        cls,
        model_id: str,
        *,
        backend: str,
        revision: str = "",
        max_length: int = 0,
        padding_strategy: str = "longest",
    ) -> MLXEmbeddingModel:
        cls._require_host(backend)
        try:
            import mlx.core as mx
            from huggingface_hub import snapshot_download
            from mlx.utils import tree_map
            from mlx_lm.models.qwen3 import Model, ModelArgs
            from mlx_lm.utils import load_config, load_model, load_tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "MLX embedding requires the optional 'nayose[mlx]' installation"
            ) from exc

        mx.set_default_device(mx.gpu if backend == "mlx_gpu" else mx.cpu)
        model_path = Path(model_id).expanduser()
        if not model_path.is_dir():
            model_path = Path(snapshot_download(model_id, revision=revision or None))
        model_config = load_config(model_path)
        if model_config.get("model_type") not in _SUPPORTED_MODEL_TYPES:
            raise RuntimeError(
                f"MLX embedding does not support model_type={model_config.get('model_type')!r}"
            )

        class _F2LLMQwen3Model(Model):
            def sanitize(self, weights: dict[str, Any]) -> dict[str, Any]:
                if not any(key.startswith("model.") for key in weights):
                    weights = {f"model.{key}": value for key, value in weights.items()}
                return super().sanitize(weights)

        try:
            model, model_config = load_model(
                model_path,
                lazy=False,
                get_model_classes=lambda config: (_F2LLMQwen3Model, ModelArgs),
            )
            tokenizer = load_tokenizer(
                model_path,
                eos_token_ids=model_config.get("eos_token_id"),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"failed to load MLX embedding model {model_id!r}") from exc
        model.update(tree_map(lambda value: value.astype(mx.float16), model.parameters()))
        mx.eval(model.parameters())
        return cls(
            model,
            tokenizer,
            model_config,
            backend=backend,
            max_length=max_length,
            padding_strategy=padding_strategy,
            mlx_module=mx,
        )

    @staticmethod
    def _require_host(backend: str) -> None:
        if backend not in {"mlx_gpu", "mlx_cpu"}:
            raise ValueError(f"unsupported MLX backend {backend!r}")
        if platform.system() != "Darwin" or platform.machine() not in {"arm64", "aarch64"}:
            raise RuntimeError("MLX embedding requires Apple Silicon macOS")

    def _validate_model(self) -> None:
        model_type = str(self._model_config.get("model_type", ""))
        if model_type not in _SUPPORTED_MODEL_TYPES:
            raise RuntimeError(
                f"MLX embedding does not support model_type={model_type!r}; "
                f"supported types are {sorted(_SUPPORTED_MODEL_TYPES)}"
            )
        if self._dimensions < 1:
            raise RuntimeError("MLX embedding model has no positive hidden size")
        if self._max_length < 1:
            raise RuntimeError("MLX embedding model has no positive maximum sequence length")
        if self._padding_strategy == "max_length" and self._max_length < 1:
            raise ValueError("max_length padding requires a positive max_length")
        if not callable(self._model):
            raise RuntimeError("MLX embedding model is not callable")
        if not callable(getattr(self._model, "model", None)):
            raise RuntimeError("MLX embedding model does not expose its transformer body")

    def get_embedding_dimension(self) -> int:
        return self._dimensions

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int = 32,
        show_progress_bar: bool = False,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
        **_: Any,
    ) -> np.ndarray | Any:
        del show_progress_bar
        values = list(texts)
        if not values:
            empty: np.ndarray = np.empty((0, self._dimensions), dtype=np.float32)
            return empty if convert_to_numpy else self._mx.array(empty)
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if any(not isinstance(value, str) for value in values):
            raise TypeError("embedding texts must be strings")
        unique_values, inverse = np.unique(np.asarray(values, dtype=object), return_inverse=True)
        if len(unique_values) != len(values):
            unique_result = self.encode(
                unique_values.tolist(),
                batch_size=batch_size,
                normalize_embeddings=normalize_embeddings,
                convert_to_numpy=convert_to_numpy,
            )
            if convert_to_numpy:
                return np.asarray(unique_result, dtype=np.float32)[inverse]
            return unique_result[self._mx.array(inverse, dtype=self._mx.int32)]

        tokenized = self._tokenize_unpadded(values)
        lengths: np.ndarray = np.asarray([len(row[0]) for row in tokenized], dtype=np.int32)
        order: np.ndarray = np.arange(len(values), dtype=np.int32)
        if self._padding_strategy == "longest" and len(values) > batch_size:
            order = np.argsort(lengths, kind="stable").astype(np.int32)
        batches = []
        for start in range(0, len(order), batch_size):
            positions = order[start : start + batch_size]
            input_ids, attention_mask = self._pad_batch(
                [tokenized[int(position)] for position in positions]
            )
            batches.append(self._encode_arrays(input_ids, attention_mask, normalize_embeddings))
        result = self._mx.concatenate(batches, axis=0) if len(batches) > 1 else batches[0]
        if not np.array_equal(order, np.arange(len(values), dtype=np.int32)):
            restore_order: np.ndarray = np.empty(len(order), dtype=np.int32)
            restore_order[order] = np.arange(len(order), dtype=np.int32)
            result = result[self._mx.array(restore_order, dtype=self._mx.int32)]
        if convert_to_numpy:
            self._mx.eval(result)
            return np.asarray(result, dtype=np.float32)
        self._mx.eval(result)
        return result
        return result

    def _tokenize_unpadded(self, texts: list[str]) -> list[tuple[np.ndarray, np.ndarray]]:
        tokenizer = getattr(self._tokenizer, "_tokenizer", self._tokenizer)
        tokenized = tokenizer(
            texts,
            padding=False,
            truncation=True,
            max_length=self._max_length,
        )
        try:
            input_ids = tokenized["input_ids"]
            attention_mask = tokenized["attention_mask"]
            rows = [
                (
                    np.asarray(ids, dtype=np.int32),
                    np.asarray(mask, dtype=np.int32),
                )
                for ids, mask in zip(input_ids, attention_mask, strict=True)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("MLX tokenizer did not return unpadded token rows") from exc
        if len(rows) != len(texts) or any(
            ids.ndim != 1 or mask.shape != ids.shape or not mask.any() for ids, mask in rows
        ):
            raise RuntimeError("MLX tokenizer returned incompatible unpadded rows")
        return rows

    def _pad_batch(
        self, rows: list[tuple[np.ndarray, np.ndarray]]
    ) -> tuple[np.ndarray, np.ndarray]:
        target = (
            self._max_length
            if self._padding_strategy == "max_length"
            else max(len(ids) for ids, _ in rows)
        )
        tokenizer = getattr(self._tokenizer, "_tokenizer", self._tokenizer)
        pad_token_id = int(getattr(tokenizer, "pad_token_id", 0) or 0)
        input_ids: np.ndarray = np.full((len(rows), target), pad_token_id, dtype=np.int32)
        attention_mask: np.ndarray = np.zeros((len(rows), target), dtype=np.int32)
        padding_side = getattr(tokenizer, "padding_side", "right")
        for row, (ids, mask) in enumerate(rows):
            width = len(ids)
            start = target - width if padding_side == "left" else 0
            input_ids[row, start : start + width] = ids
            attention_mask[row, start : start + width] = mask
        return input_ids, attention_mask

    def _encode_batch(self, texts: list[str], normalize_embeddings: bool) -> Any:
        encoded = self._tokenizer_inputs(texts)
        return self._encode_arrays(
            encoded["input_ids"], encoded["attention_mask"], normalize_embeddings
        )

    def _encode_arrays(
        self, input_ids: np.ndarray, attention_mask: np.ndarray, normalize_embeddings: bool
    ) -> Any:
        input_ids_mx = self._mx.array(input_ids, dtype=self._mx.int32)
        attention_mask_mx = self._mx.array(attention_mask, dtype=self._mx.int32)
        hidden = self._model.model(input_ids_mx)
        rows = self._mx.arange(input_ids_mx.shape[0], dtype=self._mx.int32)
        positions = self._mx.sum(attention_mask_mx, axis=1) - 1
        pooled = hidden[rows, positions]
        if normalize_embeddings:
            pooled = self._normalize(pooled)
        return pooled.astype(self._mx.float32)

    def _tokenizer_inputs(self, texts: list[str]) -> dict[str, np.ndarray]:
        padding: str | bool = self._padding_strategy
        values: dict[str, Any] = {
            "padding": padding,
            "truncation": True,
            "max_length": self._max_length,
            "return_tensors": "np",
        }
        tokenizer = getattr(self._tokenizer, "_tokenizer", self._tokenizer)
        tokenized = tokenizer(texts, **values)
        try:
            input_ids = np.asarray(tokenized["input_ids"], dtype=np.int32)
            attention_mask = np.asarray(tokenized["attention_mask"], dtype=np.int32)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("MLX tokenizer did not return input_ids and attention_mask") from exc
        if input_ids.ndim != 2 or attention_mask.shape != input_ids.shape:
            raise RuntimeError("MLX tokenizer returned incompatible batch shapes")
        if np.any(attention_mask.sum(axis=1) < 1):
            raise RuntimeError("MLX tokenizer returned an empty input")
        return {"input_ids": input_ids, "attention_mask": attention_mask}

    def _normalize(self, values: Any) -> Any:
        mx = self._mx
        values_f32 = values.astype(mx.float32)
        norm = mx.sqrt(mx.sum(values_f32 * values_f32, axis=-1, keepdims=True))
        return values_f32 / mx.maximum(norm, mx.array(np.finfo(np.float32).tiny, dtype=mx.float32))

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        gc.collect()
