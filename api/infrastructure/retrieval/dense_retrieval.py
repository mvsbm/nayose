"""Dense candidate retrieval orchestration, isolated from bundle loading."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from sklearn.preprocessing import normalize

from api.infrastructure.retrieval.dense_views import (
    DenseView,
    dense_view_queries,
    selected_dense_views,
)


def retrieve_dense_candidates(
    query: dict[str, str],
    states: dict[int, dict[str, Any]],
    *,
    reference_embeddings: np.ndarray,
    dense_nn: Any,
    dense_svd: Any | None,
    vectorizer: Any,
    dense_views: dict[str, DenseView],
    limit: int,
    encode_query: Callable[[str], np.ndarray],
    encode_queries: Callable[[list[str], int | None], np.ndarray],
    include_record_dense: bool = True,
) -> None:
    """Retrieve the record and active field views in one encoder call."""
    record_text = str(query.get("retrieval_text", ""))
    view_requests = [
        (view, text)
        for view in selected_dense_views(query, dense_views)
        for text in dense_view_queries(query, view)
    ]
    texts = ([record_text] if include_record_dense else []) + [text for _, text in view_requests]
    if not texts:
        return
    if dense_svd is not None:
        vectors = normalize(dense_svd.transform(vectorizer.transform(texts))).astype(
            np.float32, copy=False
        )
    else:
        vectors = encode_queries(texts, reference_embeddings.shape[1])

    view_offset = 1 if include_record_dense else 0
    if include_record_dense:
        _apply_record_dense(dense_nn, vectors[0], states, limit)
    for vector, (view, _) in zip(vectors[view_offset:], view_requests, strict=True):
        artifact = dense_views[view]
        matches = artifact.index.search(vector.reshape(1, -1), limit)
        keys = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
        scores = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
        _apply_view_dense(view, artifact, keys, scores, states)


def retrieve_dense_candidates_many(
    queries: list[dict[str, str]],
    states: list[dict[int, dict[str, Any]]],
    *,
    reference_embeddings: np.ndarray,
    dense_nn: Any,
    dense_svd: Any | None,
    vectorizer: Any,
    dense_views: dict[str, DenseView],
    limit: int,
    encode_queries: Callable[[list[str], int | None], np.ndarray],
    include_record_dense: bool = True,
) -> None:
    """Encode and search record and selected field views for one bounded batch."""
    if len(queries) != len(states):
        raise ValueError("dense query batch and state count differ")
    if not queries:
        return
    record_texts = [str(query.get("retrieval_text", "")) for query in queries]
    view_requests: dict[str, list[tuple[int, int]]] = {}
    texts = list(record_texts) if include_record_dense else []
    for query_index, query in enumerate(queries):
        for view in selected_dense_views(query, dense_views):
            for text in dense_view_queries(query, view):
                view_requests.setdefault(view, []).append((query_index, len(texts)))
                texts.append(text)
    if dense_svd is not None:
        vectors = normalize(dense_svd.transform(vectorizer.transform(texts))).astype(
            np.float32, copy=False
        )
    else:
        vectors = encode_queries(texts, reference_embeddings.shape[1])
    if vectors.ndim != 2 or vectors.shape != (len(texts), reference_embeddings.shape[1]):
        raise ValueError("dense query batch has an invalid embedding shape")
    if include_record_dense:
        record_keys, record_scores = _search_many(dense_nn, vectors[: len(queries)], limit)
        for query_index, (keys, scores) in enumerate(zip(record_keys, record_scores, strict=True)):
            _apply_record_dense_scores(states[query_index], keys, scores)
    for view, requests in view_requests.items():
        artifact = dense_views[view]
        positions = [position for _, position in requests]
        keys, scores = _search_many(artifact.index, vectors[positions], limit)
        for (query_index, _), query_keys, query_scores in zip(requests, keys, scores, strict=True):
            _apply_view_dense(view, artifact, query_keys, query_scores, states[query_index])


def _search_many(index: Any, vectors: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    matches = index.search(vectors, limit)
    keys = np.asarray(matches.keys, dtype=np.int32)
    scores = 1.0 - np.asarray(matches.distances, dtype=np.float32)
    if keys.ndim == 1 and len(vectors) == 1:
        keys = keys.reshape(1, -1)
        scores = scores.reshape(1, -1)
    if keys.ndim != 2 or scores.shape != keys.shape or keys.shape[0] != len(vectors):
        raise ValueError("dense batch search returned an invalid result shape")
    return keys, scores


def _apply_record_dense_scores(
    states: dict[int, dict[str, Any]], keys: np.ndarray, scores: np.ndarray
) -> None:
    for rank, (idx, score) in enumerate(zip(keys, scores, strict=True), 1):
        state = states.setdefault(
            int(idx), {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
        )
        if float(score) > float(state.get("dense_score", 0.0)):
            state["dense_score"] = float(score)
            state["dense_rank"] = rank


def _apply_record_dense(
    dense_nn: Any, vector: np.ndarray, states: dict[int, dict[str, Any]], limit: int
) -> None:
    matches = dense_nn.search(vector.reshape(1, -1), limit)
    keys = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
    scores = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
    _apply_record_dense_scores(states, keys, scores)


def _apply_view_dense(
    view: str,
    artifact: DenseView,
    keys: np.ndarray,
    scores: np.ndarray,
    states: dict[int, dict[str, Any]],
) -> None:
    for rank, (target, score) in enumerate(zip(keys, scores, strict=True), 1):
        candidate_index = (
            int(artifact.row_indices[int(target)])
            if artifact.row_indices is not None
            else int(target)
        )
        state = states.setdefault(
            candidate_index, {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
        )
        view_scores = state.setdefault("dense_view_scores", {})
        view_scores[view] = max(float(score), float(view_scores.get(view, 0.0)))
        view_ranks = state.setdefault("dense_view_ranks", {})
        view_ranks[view] = min(rank, int(view_ranks.get(view, rank)))
