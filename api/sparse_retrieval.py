"""Fast sparse candidate retrieval over a persisted normalized matrix."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
from scipy.sparse import issparse

DEFAULT_BATCH_SIZE = 32
DEFAULT_QUERY_FEATURE_CAP = 64
DEFAULT_INVERTED_MEMORY_BUDGET = 1_000_000_000
SPARSE_INVERTED_ARTIFACT = "reference_sparse_inverted.npz"


class SparseSimilarity(Protocol):
    reference_matrix: Any

    def score_many(self, query_matrix: Any) -> list[np.ndarray]:
        """Return one full reference-score vector for each query row."""


def _limit_query_features(query_matrix: Any, feature_cap: int | None) -> Any:
    if feature_cap is None:
        return query_matrix
    matrix = query_matrix.tocsr(copy=True)
    for row in range(matrix.shape[0]):
        start, stop = matrix.indptr[row : row + 2]
        if stop - start <= feature_cap:
            continue
        order = np.lexsort((matrix.indices[start:stop], -matrix.data[start:stop]))[:feature_cap]
        keep = np.zeros(stop - start, dtype=bool)
        keep[order] = True
        matrix.data[start:stop][~keep] = 0.0
    matrix.eliminate_zeros()
    return matrix


class DirectSparseSimilarity:
    """Compute normalized cosine scores with sparse matrix multiplication."""

    def __init__(self, reference_matrix: Any) -> None:
        self.reference_matrix = reference_matrix

    def score_many(self, query_matrix: Any) -> list[np.ndarray]:
        similarities = self.reference_matrix @ query_matrix.T
        return [
            np.asarray(
                similarities.getcol(row).toarray()
                if issparse(similarities)
                else similarities[:, row]
            ).reshape(-1)
            for row in range(query_matrix.shape[0])
        ]


class InvertedSparseSimilarity:
    """Accumulate query n-gram contributions through a CSC postings matrix."""

    def __init__(self, reference_matrix: Any) -> None:
        self.reference_matrix = reference_matrix.tocsc(copy=False)
        self.reference_matrix.sum_duplicates()

    def score_many(self, query_matrix: Any) -> list[np.ndarray]:
        return [self._score(query_matrix.getrow(row)) for row in range(query_matrix.shape[0])]

    def top_k_many(self, query_matrix: Any, top_k: int) -> list[tuple[np.ndarray, np.ndarray]]:
        return [
            self._top_k(
                self._sparse_scores(query_matrix.getrow(row)),
                top_k,
                self.reference_matrix.shape[0],
            )
            for row in range(query_matrix.shape[0])
        ]

    def _sparse_scores(self, query: Any) -> tuple[np.ndarray, np.ndarray]:
        row_parts: list[np.ndarray] = []
        score_parts: list[np.ndarray] = []
        for column, value in zip(query.indices, query.data, strict=True):
            start, stop = self.reference_matrix.indptr[column : column + 2]
            if start == stop:
                continue
            row_parts.append(self.reference_matrix.indices[start:stop])
            score_parts.append(
                np.multiply(
                    self.reference_matrix.data[start:stop], np.float32(value), dtype=np.float32
                )
            )
        if not row_parts:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)
        rows = np.concatenate(row_parts).astype(np.int32, copy=False)
        contributions = np.concatenate(score_parts)
        order = np.argsort(rows, kind="stable")
        rows = rows[order]
        contributions = contributions[order]
        starts = np.r_[0, np.flatnonzero(np.diff(rows)) + 1]
        return rows[starts], np.add.reduceat(contributions, starts).astype(np.float32, copy=False)

    def _score(self, query: Any) -> np.ndarray:
        rows, values = self._sparse_scores(query)
        scores = np.zeros(self.reference_matrix.shape[0], dtype=np.float32)
        scores[rows] = values
        return scores

    @staticmethod
    def _top_k(
        scores: tuple[np.ndarray, np.ndarray], top_k: int, reference_count: int
    ) -> tuple[np.ndarray, np.ndarray]:
        indices, values = scores
        limit = min(top_k, reference_count)
        if len(indices) >= limit:
            cutoff = np.partition(values, len(values) - limit)[len(values) - limit]
            above = np.flatnonzero(values > cutoff)
            ties = np.flatnonzero(values == cutoff)
            selected = np.concatenate((above, ties[: limit - len(above)]))
            order = np.lexsort((indices[selected], -values[selected]))
            return indices[selected][order], values[selected][order]
        missing = np.setdiff1d(
            np.arange(reference_count, dtype=np.int32), indices, assume_unique=True
        )[: limit - len(indices)]
        selected_indices = np.concatenate((indices, missing))
        selected_values = np.concatenate((values, np.zeros(len(missing), dtype=np.float32)))
        order = np.lexsort((selected_indices, -selected_values))
        return selected_indices[order], selected_values[order]


def _estimated_inverted_bytes(reference_matrix: Any) -> int:
    return int(
        reference_matrix.data.nbytes
        + reference_matrix.indices.nbytes
        + (reference_matrix.shape[1] + 1) * np.dtype(np.int64).itemsize
    )


def inverted_within_budget(
    reference_matrix: Any, memory_budget: int = DEFAULT_INVERTED_MEMORY_BUDGET
) -> bool:
    """Return whether the inverted representation fits the configured budget."""
    return _estimated_inverted_bytes(reference_matrix) <= memory_budget


def _similarity_strategy(reference_matrix: Any, memory_budget: int) -> tuple[SparseSimilarity, str]:
    if memory_budget < 0:
        raise ValueError("inverted memory budget must not be negative")
    if inverted_within_budget(reference_matrix, memory_budget):
        return InvertedSparseSimilarity(reference_matrix), "inverted"
    return DirectSparseSimilarity(reference_matrix), "direct"


class SparseCandidateBackend:
    """Retrieve cosine candidates with a memory-aware sparse Strategy."""

    def __init__(
        self,
        reference_matrix: Any,
        vectorizer: Any,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        query_feature_cap: int | None = DEFAULT_QUERY_FEATURE_CAP,
        inverted_memory_budget: int = DEFAULT_INVERTED_MEMORY_BUDGET,
    ) -> None:
        if getattr(reference_matrix, "ndim", None) != 2:
            raise ValueError("reference sparse matrix must be two-dimensional")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if query_feature_cap is not None and query_feature_cap < 1:
            raise ValueError("query_feature_cap must be positive")
        self.query_feature_cap = query_feature_cap
        self._similarity, self.strategy = _similarity_strategy(
            reference_matrix, inverted_memory_budget
        )
        self.vectorizer = vectorizer
        self.batch_size = batch_size

    @property
    def reference_matrix(self) -> Any:
        return self._similarity.reference_matrix

    @property
    def reference_count(self) -> int:
        return int(self.reference_matrix.shape[0])

    def retrieve(self, text: str, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        """Return candidate row indices and descending cosine scores."""
        self._validate_top_k(top_k)
        query_matrix = _limit_query_features(
            self.vectorizer.transform([text]), self.query_feature_cap
        )
        return self._retrieve_top_k(query_matrix, top_k)[0]

    def retrieve_many(
        self, texts: Sequence[str], top_k: int
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Retrieve bounded batches without materializing the whole query set."""
        self._validate_top_k(top_k)
        values = list(texts)
        results: list[tuple[np.ndarray, np.ndarray]] = []
        for start in range(0, len(values), self.batch_size):
            batch = values[start : start + self.batch_size]
            query_matrix = _limit_query_features(
                self.vectorizer.transform(batch), self.query_feature_cap
            )
            results.extend(self._retrieve_top_k(query_matrix, top_k))
        return results

    def _retrieve_top_k(self, query_matrix: Any, top_k: int) -> list[tuple[np.ndarray, np.ndarray]]:
        top_k_many = getattr(self._similarity, "top_k_many", None)
        if top_k_many is not None:
            return top_k_many(query_matrix, top_k)
        return [self._top_k(scores, top_k) for scores in self._similarity.score_many(query_matrix)]

    def _top_k(self, scores: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        scores = np.asarray(scores).reshape(-1)
        if not np.isfinite(scores).all():
            raise ValueError("sparse retrieval produced non-finite scores")
        limit = min(top_k, len(scores))
        cutoff = np.partition(scores, len(scores) - limit)[len(scores) - limit]
        above = np.flatnonzero(scores > cutoff)
        ties = np.flatnonzero(scores == cutoff)
        selected = np.concatenate((above, ties[: limit - len(above)]))
        order = np.lexsort((selected, -scores[selected]))
        indices = selected[order].astype(np.int32, copy=False)
        return indices, scores[indices].astype(np.float32, copy=False)

    def _validate_top_k(self, top_k: int) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if self.reference_count == 0:
            raise ValueError("reference sparse matrix must not be empty")
