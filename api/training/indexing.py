from __future__ import annotations

import gc
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize as l2_normalize

from api.embedding_cache import (
    EMBEDDING_FIELD_SCHEMA_VERSION,
    EMBEDDING_PREPROCESSING_VERSION,
    cache_paths,
    model_revision,
    text_fingerprint,
)
from api.embedding_cache import (
    load as load_embedding_cache,
)
from api.embedding_cache import (
    save as save_embedding_cache,
)
from api.performance import require_mps_device
from api.sparse_retrieval import DEFAULT_QUERY_FEATURE_CAP, SparseCandidateBackend
from api.training.config import TrainingConfig
from api.training.contracts import NormalizedCorpus, RuntimeInfo
from api.training_retrieval import CandidateGraphConfig, build_candidate_graph

BLOCK_SPECS: dict[str, tuple[str, ...]] = {
    "exact_email": ("n_email",),
    "exact_phone": ("n_phone",),
    "name_dob": ("n_name_loose", "n_dob"),
    "kana_dob": ("n_kana", "n_dob"),
    "signature_postal": ("n_name_signature", "n_postal"),
    "kana_postal": ("n_kana", "n_postal"),
    "name_address": ("n_name_loose", "n_address"),
    "phone_suffix_name": ("n_phone_suffix8", "n_name_loose"),
}


@dataclass(slots=True)
class TrainingCandidateIndex:
    reference: pd.DataFrame
    block_indexes: dict[str, dict[str, np.ndarray]]
    vectorizer: TfidfVectorizer
    sparse_matrix: csr_matrix
    sparse_backend: SparseCandidateBackend
    config: TrainingConfig
    runtime: RuntimeInfo
    reference_embeddings: np.ndarray | None = None
    dense_nn: Any | None = None
    dense_backend: str = "disabled"
    svd: Any | None = None
    _query_model: Any | None = None

    def exact_candidates(self, query: pd.Series) -> dict[int, set[str]]:
        found: dict[int, set[str]] = {}
        for name, columns in BLOCK_SPECS.items():
            values = [str(query.get(column, "") or "") for column in columns]
            if not all(values):
                continue
            key = "¦".join(values)
            positions = self.block_indexes[name].get(key, np.empty(0, dtype=np.int32))
            if len(positions) > self.config.exact_block_cap:
                continue
            for position in positions:
                found.setdefault(int(position), set()).add(name)
        return found

    def sparse_candidates(self, texts: list[str], k: int) -> tuple[np.ndarray, np.ndarray]:
        if not texts or k < 1:
            return np.empty((len(texts), 0), dtype=np.int32), np.empty(
                (len(texts), 0), dtype=np.float32
            )
        matches = self.sparse_backend.retrieve_many(texts, k)
        width = max((len(ids) for ids, _ in matches), default=0)
        ids: np.ndarray = np.full((len(matches), width), -1, dtype=np.int32)
        scores: np.ndarray = np.zeros((len(matches), width), dtype=np.float32)
        for row, (row_ids, row_scores) in enumerate(matches):
            ids[row, : len(row_ids)] = row_ids
            scores[row, : len(row_scores)] = row_scores
        return ids, scores

    def dense_reference_candidates(
        self, start: int, stop: int, k: int
    ) -> tuple[np.ndarray, np.ndarray]:
        if self.reference_embeddings is None or self.dense_nn is None:
            return np.empty((stop - start, 0), dtype=np.int32), np.empty(
                (stop - start, 0), dtype=np.float32
            )
        vectors = self.reference_embeddings[start:stop]
        if self.dense_backend == "usearch":
            matches = self.dense_nn.search(vectors, min(k, len(self.reference)))
            ids = np.asarray(matches.keys, dtype=np.int32)
            scores = 1.0 - np.asarray(matches.distances, dtype=np.float32)
        else:
            distances, ids = self.dense_nn.kneighbors(
                vectors, n_neighbors=min(k, len(self.reference))
            )
            scores = 1.0 - distances
        if ids.ndim == 1:
            ids, scores = ids.reshape(1, -1), scores.reshape(1, -1)
        return ids, scores

    def dense_candidates(self, texts: list[str], k: int) -> tuple[np.ndarray, np.ndarray]:
        if not texts or k < 1 or self.reference_embeddings is None or self.dense_nn is None:
            return np.empty((len(texts), 0), dtype=np.int32), np.empty(
                (len(texts), 0), dtype=np.float32
            )
        if self.dense_backend == "tfidf_svd_degraded":
            if self.svd is None:
                raise RuntimeError("degraded dense retrieval has no SVD model")
            vectors = l2_normalize(self.svd.transform(self.vectorizer.transform(texts))).astype(
                np.float32
            )
        else:
            model = self._query_model or _load_embedding_model(self.config, self.runtime.device)
            self._query_model = model
            vectors = _encode_texts(model, texts, self.config, query=True)
        if self.dense_backend == "usearch":
            matches = self.dense_nn.search(vectors, min(k, len(self.reference)))
            ids = np.asarray(matches.keys, dtype=np.int32)
            scores = 1.0 - np.asarray(matches.distances, dtype=np.float32)
        else:
            distances, ids = self.dense_nn.kneighbors(
                vectors, n_neighbors=min(k, len(self.reference))
            )
            scores = 1.0 - distances
        if self.config.low_memory_models and self._query_model is not None:
            del self._query_model
            self._query_model = None
            _release_accelerator()
        return ids.astype(np.int32), scores.astype(np.float32)

    def release_query_model(self) -> None:
        self._query_model = None
        gc.collect()


def _load_embedding_model(config: TrainingConfig, device: str) -> Any:
    from sentence_transformers import SentenceTransformer

    model_id = config.model_spec()["embedding"]
    if not model_id:
        return None
    kwargs: dict[str, Any] = {
        "device": require_mps_device(device) if device == "mps" else device,
        "model_kwargs": {"torch_dtype": _torch_dtype(device)},
    }
    if config.model_family == "qwen":
        kwargs["truncate_dim"] = config.embedding_dim
    return SentenceTransformer(model_id, **kwargs)


def _torch_dtype(device: str) -> Any:
    import torch

    return torch.float16 if device == "mps" else torch.float32


def _encode_texts(
    model: Any, texts: list[str], config: TrainingConfig, *, query: bool
) -> np.ndarray:
    spec = config.model_spec()
    values = list(texts)
    prefix = spec["query_prefix"] if query else spec["document_prefix"]
    values = [prefix + value for value in values]
    return np.asarray(
        model.encode(
            values,
            batch_size=min(config.neural_batch_size, max(len(values), 1)),
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ),
        dtype=np.float32,
    )


def _release_accelerator() -> None:
    try:
        import torch

        torch.mps.empty_cache()
    except (AttributeError, ImportError, RuntimeError):
        return


def _block_indexes(frame: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
    positions: np.ndarray = np.arange(len(frame), dtype=np.int32)
    indexes: dict[str, dict[str, np.ndarray]] = {}
    for name, columns in BLOCK_SPECS.items():
        keys = frame[list(columns)].astype(str).agg("¦".join, axis=1)
        keys = keys.where(frame[list(columns)].ne("").all(axis=1), "")
        valid = pd.DataFrame({"key": keys, "position": positions}).query("key != ''")
        groups = valid.groupby("key", sort=False).position.apply(
            lambda values: values.to_numpy(np.int32)
        )
        indexes[name] = groups.to_dict()
    return indexes


def _load_or_encode_embeddings(
    corpus: NormalizedCorpus,
    config: TrainingConfig,
    runtime: RuntimeInfo,
    fingerprint: str,
) -> tuple[np.ndarray | None, str | None]:
    if not (config.use_dense and runtime.neural_enabled):
        return None, None
    model_id = config.model_spec()["embedding"]
    if not model_id:
        return None, None
    vector_path, metadata_path = cache_paths(config.data_dir, model_id, "reference")
    if not config.strict_backends:
        cached = _load_legacy_cache(
            vector_path, metadata_path, model_id, fingerprint, len(corpus.reference)
        )
        if cached is None:
            legacy_vector = config.data_dir / f"reference_embeddings_{Path(model_id).name}.npy"
            legacy_metadata = (
                config.data_dir / f"reference_embeddings_{Path(model_id).name}_meta.json"
            )
            cached = _load_legacy_cache(
                legacy_vector, legacy_metadata, model_id, fingerprint, len(corpus.reference)
            )
        if cached is not None:
            return cached, None
    model = _load_embedding_model(config, runtime.device)
    dimensions = int(model.get_embedding_dimension())
    texts = corpus.reference.retrieval_text.astype(str).tolist()
    provenance = {
        "model_id": model_id,
        "model_revision": model_revision(model),
        "data_fingerprint": fingerprint,
        "text_fingerprint": text_fingerprint(texts),
        "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
        "field_schema_version": EMBEDDING_FIELD_SCHEMA_VERSION,
        "rows": len(texts),
        "dimensions": dimensions,
        "device": runtime.device,
        "torch_dtype": config.torch_dtype,
        "batch_size": config.neural_batch_size,
    }
    cached = load_embedding_cache(vector_path, metadata_path, provenance)
    if cached is None:
        values: np.ndarray = np.empty((len(texts), dimensions), dtype=np.float32)
        for start in range(0, len(texts), config.neural_batch_size):
            stop = min(start + config.neural_batch_size, len(texts))
            values[start:stop] = _encode_texts(model, texts[start:stop], config, query=False)
        save_embedding_cache(vector_path, metadata_path, values, provenance)
        cached = values
    del model
    _release_accelerator()
    return np.asarray(cached), str(provenance["model_revision"])


def _load_legacy_cache(
    vector_path: Path,
    metadata_path: Path,
    model_id: str,
    fingerprint: str,
    rows: int,
) -> np.ndarray | None:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            metadata.get("model_id") != model_id
            or metadata.get("data_fingerprint") != fingerprint
            or metadata.get("rows") != rows
        ):
            return None
        values = np.load(vector_path, mmap_mode="r", allow_pickle=False)
        if values.ndim != 2 or values.shape[0] != rows or not np.isfinite(values).all():
            return None
        return np.asarray(values)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def build_index(
    corpus: NormalizedCorpus,
    config: TrainingConfig,
    runtime: RuntimeInfo,
    fingerprint: str,
) -> tuple[TrainingCandidateIndex, str | None]:
    reference = corpus.reference
    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(2, 4),
        min_df=1,
        max_df=0.995,
        max_features=300_000,
        sublinear_tf=True,
        dtype=np.float32,
        norm="l2",
    )
    sparse_matrix = vectorizer.fit_transform(reference.retrieval_text)
    backend = SparseCandidateBackend(
        sparse_matrix,
        vectorizer,
        batch_size=config.sparse_batch_size,
        query_feature_cap=DEFAULT_QUERY_FEATURE_CAP,
    )
    embeddings, revision = _load_or_encode_embeddings(corpus, config, runtime, fingerprint)
    dense_nn: Any | None = None
    dense_backend = "disabled"
    svd: Any | None = None
    if embeddings is not None:
        if runtime.backends.get("usearch", False):
            from usearch.index import Index

            dense_nn = Index(ndim=embeddings.shape[1], metric="cos", dtype="f16")
            dense_nn.add(np.arange(len(embeddings), dtype=np.int64), embeddings)
            dense_backend = "usearch"
        else:
            dense_nn = NearestNeighbors(metric="cosine", algorithm="brute").fit(embeddings)
            dense_backend = "sklearn_exact_neural"
    elif config.use_dense and config.allow_svd_dense_fallback:
        dimensions = max(
            2, min(config.svd_dimensions, sparse_matrix.shape[0] - 1, sparse_matrix.shape[1] - 1)
        )
        svd = TruncatedSVD(n_components=dimensions, random_state=config.seed)
        embeddings = l2_normalize(svd.fit_transform(sparse_matrix)).astype(np.float32)
        dense_nn = NearestNeighbors(metric="cosine", algorithm="brute").fit(embeddings)
        dense_backend = "tfidf_svd_degraded"
    index = TrainingCandidateIndex(
        reference,
        _block_indexes(reference),
        vectorizer,
        sparse_matrix,
        backend,
        config,
        runtime,
        embeddings,
        dense_nn,
        dense_backend,
        svd,
    )
    return index, revision


def build_graph(index: TrainingCandidateIndex, config: TrainingConfig) -> pd.DataFrame:
    return build_candidate_graph(
        index,
        CandidateGraphConfig(
            exact_mode=config.exact_mode,
            exact_block_cap=config.exact_block_cap,
            sparse_top_k=config.sparse_top_k,
            dense_top_k=config.dense_top_k,
            sparse_batch_size=config.sparse_batch_size,
            candidate_cap_per_record=config.candidate_cap_per_record,
        ),
    )
