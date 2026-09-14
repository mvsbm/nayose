from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from api.infrastructure.runtime.embedding_runtime import (
    close_query_encoder,
    configure_query_encoder,
    encode_queries,
)
from api.infrastructure.runtime.performance import require_mps_device, resolve_device
from nayose.config import ConfigSnapshot, load_config

_ENCODE_CHUNK_SIZE = 1024


def _resolve_device(requested: str) -> str:
    return resolve_device(requested)


class BundleRetriever:
    def __init__(
        self,
        engine: Any,
        manifest: dict[str, Any],
        run_dir: Path,
        exact_mode: str | None = None,
        config_snapshot: ConfigSnapshot | None = None,
    ):
        self.index = engine._index
        self.settings = manifest.get("config", {})
        self._config_snapshot = config_snapshot
        self._config = config_snapshot or load_config()
        self._exact_mode = exact_mode
        self.svd = None
        self.model_id = manifest.get("embedding_model")
        self.mode = "sparse_only"
        if self.index.reference_embeddings is not None:
            backend = manifest.get("backends", {}).get("dense_index", "")
            if backend == "tfidf_svd_degraded":
                self.svd = joblib.load(run_dir / "dense_svd.joblib")
                self.mode = "tfidf_svd"
            elif self.model_id:
                configure_query_encoder(
                    str(self.model_id),
                    device=require_mps_device(self._config.embedding.device),
                    batch_size=self._config.embedding.batch_size,
                )
                self.mode = "neural"
            else:
                raise RuntimeError("dense embeddings have no recorded query encoder")

    def set_exact_mode(self, mode: str) -> None:
        if mode not in {"hybrid", "fuzzy_only", "exact_only"}:
            raise ValueError("unsupported exact mode")
        self._exact_mode = mode

    @property
    def exact_mode(self) -> str:
        if self._exact_mode is not None:
            return self._exact_mode
        config = self._config_snapshot or load_config()
        return config.runtime.exact_mode or self.settings.get("exact_mode", "hybrid")

    @property
    def active_backend(self) -> str:
        if self.exact_mode == "exact_only":
            return "exact_only"
        if not self._config.retrieval.use_dense:
            return "sparse_only"
        return self.mode

    def encode_many(self, texts: list[str]) -> np.ndarray:
        if self.index.reference_embeddings is None:
            raise RuntimeError("dense query encoding is unavailable")
        if len(texts) > _ENCODE_CHUNK_SIZE:
            return np.vstack(
                [
                    self.encode_many(texts[start : start + _ENCODE_CHUNK_SIZE])
                    for start in range(0, len(texts), _ENCODE_CHUNK_SIZE)
                ]
            )
        if self.svd is not None:
            result = normalize(self.svd.transform(self.index.vectorizer.transform(texts)))
        else:
            result = encode_queries(texts)
        values = np.asarray(result, dtype=np.float32)
        expected = self.index.reference_embeddings.shape[1]
        if values.shape != (len(texts), expected) or not np.isfinite(values).all():
            raise ValueError("query embedding does not match reference dimensions")
        return values

    def encode(self, text: str) -> np.ndarray:
        return self.encode_many([text])

    def close(self) -> None:
        close_query_encoder()

    def retrieve(self, query: dict[str, str]) -> pd.DataFrame:
        mode = self.exact_mode
        if mode not in {"hybrid", "fuzzy_only", "exact_only"}:
            raise ValueError("unsupported exact mode")
        dense = mode != "exact_only" and self.active_backend != "sparse_only"
        return self.index.retrieve(
            query,
            exact_mode=mode,
            use_dense=dense,
            settings=self.settings,
        )
