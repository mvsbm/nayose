"""Thread-safe lazy query encoding shared by serving and search paths."""

from __future__ import annotations

import multiprocessing as mp
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from multiprocessing.connection import Connection
from threading import RLock
from typing import Any

import numpy as np

from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.runtime.performance import (
    configure_mps_runtime,
    configure_torch_threads,
    model_thread_budget,
    require_mps_device,
    resolve_device,
)
from nayose.config import (
    EMBEDDING_BACKENDS,
    EMBEDDING_DEFAULTS,
    F2LLM_330M_MODEL_ID,
    F2LLM_MPS_BATCH_SIZE,
    EmbeddingConfig,
    is_f2llm_model,
    load_config,
)


class _EmbeddingWorker:
    def __init__(
        self,
        model_id: str,
        device: str,
        batch_size: int,
        timeout: float,
        backend: str,
        max_length: int,
        padding_strategy: str,
        revision: str,
    ):
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        self._connection = parent
        self._process = context.Process(
            target=_embedding_worker_main,
            args=(
                child,
                model_id,
                device,
                batch_size,
                backend,
                max_length,
                padding_strategy,
                revision,
            ),
            daemon=True,
        )
        self._timeout = timeout
        self._process.start()
        child.close()
        self._wait_for("ready")

    def _wait_for(self, expected: str) -> Any:
        if not self._connection.poll(self._timeout):
            exited = not self._process.is_alive()
            self.close()
            if exited:
                raise RuntimeError("embedding worker exited before responding")
            raise TimeoutError("embedding worker did not respond before its deadline")
        status, payload = self._connection.recv()
        if status != expected:
            self.close()
            raise RuntimeError(f"embedding worker failed: {payload}")
        return payload

    def encode(self, texts: list[str]) -> Any:
        try:
            self._connection.send(texts)
            return self._wait_for("ok")
        except (EOFError, OSError) as exc:
            self.close()
            raise RuntimeError("embedding worker exited unexpectedly") from exc

    def close(self) -> None:
        if self._process.is_alive():
            with suppress(BrokenPipeError, EOFError, OSError):
                self._connection.send(None)
            self._process.join(timeout=2.0)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2.0)
        self._connection.close()


def _embedding_worker_main(
    connection: Connection,
    model_id: str,
    device: str,
    batch_size: int,
    backend: str,
    max_length: int,
    padding_strategy: str,
    revision: str,
) -> None:
    try:
        model = _load_model(
            model_id,
            device,
            backend=backend,
            max_length=max_length,
            padding_strategy=padding_strategy,
            revision=revision,
        )
        connection.send(("ready", None))
        while True:
            texts = connection.recv()
            if texts is None:
                return
            try:
                values = model.encode(
                    texts,
                    batch_size=min(batch_size, len(texts)),
                    show_progress_bar=False,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )
                connection.send(("ok", np.asarray(values, dtype=np.float32)))
            except Exception as exc:
                connection.send(("error", f"{type(exc).__name__}: {exc}"))
    except Exception as exc:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send(("startup_error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


class QueryEmbeddingEncoder:
    """Lazily load one encoder and batch all texts for a request.

    Neural inference runs in a spawned worker process by default. This keeps
    PyTorch's runtime isolated from LightGBM/OpenMP, which can otherwise crash
    or deadlock on some CPU and Apple Silicon combinations. A supplied model
    factory remains in-process for deterministic unit-test doubles.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "mps",
        batch_size: int = F2LLM_MPS_BATCH_SIZE,
        model_factory: Callable[[str, str], Any] | None = None,
        backend: str = str(EMBEDDING_DEFAULTS["backend"]),
        max_length: int = int(EMBEDDING_DEFAULTS["optimization"]["max_length"]),
        padding_strategy: str = str(EMBEDDING_DEFAULTS["optimization"]["padding_strategy"]),
        revision: str = str(EMBEDDING_DEFAULTS["revision"]),
        timeout_seconds: float = 120.0,
        stats: Stats | None = None,
    ):
        if not model_id:
            raise ValueError("model_id must not be empty")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if backend not in EMBEDDING_BACKENDS:
            raise ValueError(f"unsupported embedding backend {backend!r}")
        if max_length < 0:
            raise ValueError("max_length must not be negative")
        if padding_strategy not in {"longest", "max_length"}:
            raise ValueError("padding_strategy must be longest or max_length")
        if padding_strategy == "max_length" and max_length < 1:
            raise ValueError("max_length padding requires a positive max_length")
        if model_factory is None and is_f2llm_model(model_id):
            if backend == "pytorch_mps" and device != "mps":
                raise RuntimeError("F2LLM-v2-330M PyTorch serving is locked to Apple MPS")
            if batch_size != F2LLM_MPS_BATCH_SIZE:
                raise ValueError(
                    f"F2LLM-v2-330M serving requires batch size {F2LLM_MPS_BATCH_SIZE}"
                )
        self.model_id = model_id
        if model_factory is not None:
            self.device = resolve_device(device)
        elif backend == "pytorch_mps":
            self.device = require_mps_device(device)
        else:
            self.device = device
        self.batch_size = batch_size
        self.backend = backend
        self.max_length = max_length
        self.padding_strategy = padding_strategy
        self.revision = revision
        self._model_factory = model_factory or _load_model
        self._isolated = model_factory is None
        self._timeout_seconds = timeout_seconds
        self._stats = stats
        self._model: Any = None
        self._worker: _EmbeddingWorker | None = None
        self._lock = RLock()

    def encode(self, texts: Sequence[str], expected_dimensions: int | None = None) -> np.ndarray:
        values = list(texts)
        if not values:
            dimensions = expected_dimensions or 0
            return np.empty((0, dimensions), dtype=np.float32)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("embedding texts must be strings")
        started = time.perf_counter()
        try:
            with self._lock:
                encoded = self._encode_locked(values)
        except BaseException:
            self._record("embedding.encode", started, error=True)
            raise
        else:
            self._record("embedding.encode", started)
        result = np.asarray(encoded, dtype=np.float32)
        if result.ndim != 2 or result.shape[0] != len(values):
            raise ValueError("query encoder returned an invalid shape")
        if expected_dimensions is not None and result.shape[1] != expected_dimensions:
            raise ValueError("query embedding does not match reference dimensions")
        if not np.isfinite(result).all():
            raise ValueError("query encoder returned non-finite values")
        return result

    def _encode_locked(self, values: list[str]) -> Any:
        if self._isolated:
            if self._worker is None:
                self._worker = _EmbeddingWorker(
                    self.model_id,
                    self.device,
                    self.batch_size,
                    self._timeout_seconds,
                    self.backend,
                    self.max_length,
                    self.padding_strategy,
                    self.revision,
                )
            try:
                return self._worker.encode(values)
            except (RuntimeError, TimeoutError):
                self._worker = None
                raise
        if self._model is None:
            self._model = self._model_factory(self.model_id, self.device)
        return self._model.encode(
            values,
            batch_size=min(self.batch_size, len(values)),
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

    def _record(self, name: str, started: float, *, error: bool = False) -> None:
        if self._stats is not None:
            self._stats.record(name, time.perf_counter() - started, error=error)

    def close(self) -> None:
        with self._lock:
            if self._worker is not None:
                self._worker.close()
                self._worker = None
            self._model = None


def load_embedding_model(
    model_id: str,
    device: str,
    *,
    backend: str = "pytorch_mps",
    max_length: int = 0,
    padding_strategy: str = "longest",
    revision: str = "",
    truncate_dim: int | None = None,
) -> Any:
    """Load one configured embedding backend for batch-oriented callers."""
    return _load_model(
        model_id,
        device,
        backend=backend,
        max_length=max_length,
        padding_strategy=padding_strategy,
        revision=revision,
        truncate_dim=truncate_dim,
    )


def _load_model(
    model_id: str,
    device: str,
    *,
    backend: str = "pytorch_mps",
    max_length: int = 0,
    padding_strategy: str = "longest",
    revision: str = "",
    truncate_dim: int | None = None,
) -> Any:
    if backend != "pytorch_mps":
        from api.infrastructure.runtime.mlx_embedding import MLXEmbeddingModel

        return MLXEmbeddingModel.from_pretrained(
            model_id,
            backend=backend,
            max_length=max_length,
            padding_strategy=padding_strategy,
            revision=revision,
        )
    import torch
    from sentence_transformers import SentenceTransformer

    if device == "mps":
        configure_mps_runtime()
        require_mps_device(device)
        dtype = torch.float16
    else:
        dtype = torch.float32
    configure_torch_threads(model_thread_budget())
    kwargs: dict[str, Any] = {
        "device": device,
        "model_kwargs": {"torch_dtype": dtype},
    }
    if truncate_dim is not None:
        kwargs["truncate_dim"] = truncate_dim
    return SentenceTransformer(model_id, **kwargs)


_DEFAULT_ENCODER: QueryEmbeddingEncoder | None = None
_DEFAULT_MODEL_ID: str | None = None
_DEFAULT_DEVICE: str | None = None
_DEFAULT_BATCH_SIZE: int | None = None
_DEFAULT_BACKEND: str | None = None
_DEFAULT_MAX_LENGTH: int | None = None
_DEFAULT_PADDING_STRATEGY: str | None = None
_DEFAULT_REVISION: str | None = None
_DEFAULT_STATS: Stats | None = None
_DEFAULT_LOCK = RLock()


def configure_embedding_stats(stats: Stats | None) -> None:
    """Attach process-local telemetry to lazily created query encoders."""
    global _DEFAULT_STATS
    with _DEFAULT_LOCK:
        _DEFAULT_STATS = stats


def configure_query_encoder(
    model_id: str | None,
    *,
    device: str | None = None,
    batch_size: int | None = None,
    settings: EmbeddingConfig | None = None,
) -> None:
    """Bind the process-wide encoder to the currently loaded bundle."""
    global _DEFAULT_ENCODER, _DEFAULT_MODEL_ID, _DEFAULT_DEVICE, _DEFAULT_BATCH_SIZE
    global _DEFAULT_BACKEND, _DEFAULT_MAX_LENGTH, _DEFAULT_PADDING_STRATEGY, _DEFAULT_REVISION
    resolved = settings if settings is not None else load_config().embedding
    requested_device = device if device is not None else resolved.device
    selected_backend = resolved.backend
    selected_max_length = resolved.max_length
    selected_padding_strategy = resolved.padding_strategy
    selected_revision = resolved.revision
    selected_device = (
        resolve_device(requested_device) if selected_backend == "pytorch_mps" else requested_device
    )
    if settings is not None:
        if device is not None and device != resolved.device:
            raise ValueError("embedding device must come from the canonical configuration")
        if batch_size is not None and batch_size != resolved.batch_size:
            raise ValueError("embedding batch size must come from the canonical configuration")
    selected_batch_size = (
        configured_batch_size(settings=resolved) if batch_size is None else batch_size
    )
    if selected_batch_size < 1:
        raise ValueError("batch_size must be positive")
    with _DEFAULT_LOCK:
        if (
            model_id == _DEFAULT_MODEL_ID
            and selected_device == _DEFAULT_DEVICE
            and selected_batch_size == _DEFAULT_BATCH_SIZE
            and selected_backend == _DEFAULT_BACKEND
            and selected_max_length == _DEFAULT_MAX_LENGTH
            and selected_padding_strategy == _DEFAULT_PADDING_STRATEGY
            and selected_revision == _DEFAULT_REVISION
        ):
            return
        close_query_encoder()
        _DEFAULT_MODEL_ID = model_id
        _DEFAULT_DEVICE = selected_device
        _DEFAULT_BATCH_SIZE = selected_batch_size
        _DEFAULT_BACKEND = selected_backend
        _DEFAULT_MAX_LENGTH = selected_max_length
        _DEFAULT_PADDING_STRATEGY = selected_padding_strategy
        _DEFAULT_REVISION = selected_revision


def close_query_encoder() -> None:
    global _DEFAULT_ENCODER, _DEFAULT_MODEL_ID, _DEFAULT_DEVICE, _DEFAULT_BATCH_SIZE
    global _DEFAULT_BACKEND, _DEFAULT_MAX_LENGTH, _DEFAULT_PADDING_STRATEGY, _DEFAULT_REVISION
    with _DEFAULT_LOCK:
        if _DEFAULT_ENCODER is not None:
            _DEFAULT_ENCODER.close()
        _DEFAULT_ENCODER = None
        _DEFAULT_MODEL_ID = None
        _DEFAULT_DEVICE = None
        _DEFAULT_BATCH_SIZE = None
        _DEFAULT_BACKEND = None
        _DEFAULT_MAX_LENGTH = None
        _DEFAULT_PADDING_STRATEGY = None
        _DEFAULT_REVISION = None


def encode_queries(
    texts: list[str],
    expected_dimensions: int | None = None,
    settings: EmbeddingConfig | None = None,
) -> np.ndarray:
    global _DEFAULT_ENCODER
    resolved = settings if settings is not None else load_config().embedding
    configured_model_id = resolved.model_id or F2LLM_330M_MODEL_ID
    with _DEFAULT_LOCK:
        if _DEFAULT_MODEL_ID is None:
            configure_query_encoder(
                configured_model_id,
                device=resolved.device,
                batch_size=resolved.batch_size,
                settings=resolved,
            )
        if _DEFAULT_ENCODER is None:
            if _DEFAULT_MODEL_ID is None or _DEFAULT_DEVICE is None or _DEFAULT_BATCH_SIZE is None:
                raise RuntimeError("query encoder is not configured")
            encoder_kwargs: dict[str, Any] = {
                "device": _DEFAULT_DEVICE,
                "batch_size": _DEFAULT_BATCH_SIZE,
                "stats": _DEFAULT_STATS,
            }
            if (
                _DEFAULT_BACKEND != "pytorch_mps"
                or _DEFAULT_MAX_LENGTH
                or (_DEFAULT_PADDING_STRATEGY != "longest")
            ):
                encoder_kwargs.update(
                    {
                        "backend": _DEFAULT_BACKEND,
                        "max_length": _DEFAULT_MAX_LENGTH,
                        "padding_strategy": _DEFAULT_PADDING_STRATEGY,
                        "revision": _DEFAULT_REVISION,
                    }
                )
            _DEFAULT_ENCODER = QueryEmbeddingEncoder(_DEFAULT_MODEL_ID, **encoder_kwargs)
        return _DEFAULT_ENCODER.encode(texts, expected_dimensions)


def encode_query(text: str) -> np.ndarray:
    return encode_queries([text])


def configured_batch_size(
    default: int = F2LLM_MPS_BATCH_SIZE, settings: EmbeddingConfig | None = None
) -> int:
    resolved = settings if settings is not None else load_config().embedding
    value = resolved.batch_size if settings is not None else default
    if value < 1:
        raise ValueError("embedding batch size must be positive")
    return value
