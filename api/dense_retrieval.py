"""Dense candidate retrieval orchestration, isolated from bundle loading."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from sklearn.preprocessing import normalize

from api.dense_views import DenseView, dense_view_queries


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
) -> None:
    """Retrieve the full record and active field views in one encoder call."""
    record_text = str(query.get("retrieval_text", ""))
    view_requests = [
        (view, text) for view in dense_views for text in dense_view_queries(query, view)
    ]
    if dense_svd is not None:
        texts = [record_text, *(text for _, text in view_requests)]
        vectors = normalize(dense_svd.transform(vectorizer.transform(texts))).astype(
            np.float32, copy=False
        )
    elif view_requests:
        vectors = encode_queries(
            [record_text, *(text for _, text in view_requests)],
            reference_embeddings.shape[1],
        )
    else:
        vectors = encode_query(record_text)

    _apply_record_dense(dense_nn, vectors[0], states, limit)
    for vector, (view, _) in zip(vectors[1:], view_requests, strict=True):
        artifact = dense_views[view]
        matches = artifact.index.search(vector.reshape(1, -1), limit)
        keys = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
        scores = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
        _apply_view_dense(view, artifact, keys, scores, states)


def _apply_record_dense(
    dense_nn: Any, vector: np.ndarray, states: dict[int, dict[str, Any]], limit: int
) -> None:
    matches = dense_nn.search(vector.reshape(1, -1), limit)
    keys = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
    scores = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
    for rank, (target, score) in enumerate(zip(keys, scores, strict=True), 1):
        state = states.setdefault(
            int(target), {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
        )
        if float(score) > state["dense_score"]:
            state["dense_score"] = float(score)
            state["dense_rank"] = rank


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
