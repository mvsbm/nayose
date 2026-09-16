from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

from api.infrastructure.retrieval.sparse_retrieval import (
    DEFAULT_QUERY_FEATURE_CAP,
    SparseCandidateBackend,
)
from api.infrastructure.runtime.engine import CandidateIndex, InferenceConfig


def test_sparse_backend_matches_cosine_nearest_neighbors() -> None:
    texts = ["alpha beta", "beta gamma", "gamma delta", "unrelated phrase"]
    vectorizer = TfidfVectorizer().fit(texts)
    matrix = vectorizer.transform(texts)
    backend = SparseCandidateBackend(matrix, vectorizer)

    indices, scores = backend.retrieve("alpha beta", 3)
    distances, expected_indices = (
        NearestNeighbors(metric="cosine", algorithm="brute", n_jobs=1)
        .fit(matrix)
        .kneighbors(vectorizer.transform(["alpha beta"]), n_neighbors=3)
    )

    np.testing.assert_array_equal(indices, expected_indices[0])
    np.testing.assert_allclose(scores, 1.0 - distances[0], rtol=1e-6, atol=1e-6)


def test_sparse_backend_uses_direct_strategy_when_budget_is_exceeded() -> None:
    vectorizer = TfidfVectorizer().fit(["alpha beta", "beta gamma"])
    matrix = vectorizer.transform(["alpha beta", "beta gamma"])
    backend = SparseCandidateBackend(matrix, vectorizer, inverted_memory_budget=0)

    assert backend.strategy == "direct"
    indices, scores = backend.retrieve("alpha beta", 1)
    assert indices.tolist() == [0]
    assert scores[0] == pytest.approx(1.0)


def test_direct_and_inverted_sparse_strategies_have_identical_order() -> None:
    texts = ["alpha beta", "beta gamma", "gamma delta", "alpha delta"]
    vectorizer = TfidfVectorizer().fit(texts)
    matrix = vectorizer.transform(texts)
    direct = SparseCandidateBackend(matrix, vectorizer, inverted_memory_budget=0)
    inverted = SparseCandidateBackend(matrix, vectorizer, inverted_memory_budget=10**9)

    direct_results = direct.retrieve_many(texts, 4)
    inverted_results = inverted.retrieve_many(texts, 4)

    for (direct_indices, direct_scores), (inverted_indices, inverted_scores) in zip(
        direct_results, inverted_results, strict=True
    ):
        np.testing.assert_array_equal(direct_indices, inverted_indices)
        np.testing.assert_allclose(direct_scores, inverted_scores, atol=1e-6)


def test_sparse_backend_batches_with_bounded_memory() -> None:
    texts = ["alpha beta", "beta gamma", "gamma delta"]
    vectorizer = TfidfVectorizer().fit(texts)
    backend = SparseCandidateBackend(vectorizer.transform(texts), vectorizer, batch_size=2)

    batched = backend.retrieve_many(["alpha beta", "gamma delta", "beta gamma"], 2)
    individual = [backend.retrieve(text, 2) for text in ["alpha beta", "gamma delta", "beta gamma"]]

    assert len(batched) == len(individual) == 3
    for (actual_indices, actual_scores), (expected_indices, expected_scores) in zip(
        batched, individual, strict=True
    ):
        np.testing.assert_array_equal(actual_indices, expected_indices)
        np.testing.assert_allclose(actual_scores, expected_scores)


def test_candidate_index_batch_matches_single_sparse_retrieval() -> None:
    texts = ["alpha beta", "beta gamma", "gamma delta"]
    vectorizer = TfidfVectorizer().fit(texts)
    reference = pd.DataFrame({"record_id": ["R1", "R2", "R3"]})
    index = CandidateIndex(
        reference,
        vectorizer.transform(texts),
        vectorizer,
        {},
        InferenceConfig(sparse_top_k=2, candidate_cap=2),
    )
    queries = [{"retrieval_text": text} for text in ["alpha beta", "gamma delta"]]

    batched = index.retrieve_sparse_many(queries)
    individual = [
        index.retrieve(query, exact_mode="fuzzy_only", use_dense=False) for query in queries
    ]

    for actual, expected in zip(batched, individual, strict=True):
        pd.testing.assert_frame_equal(actual, expected)


def test_candidate_index_batches_dense_and_sparse_channels(monkeypatch) -> None:
    texts = ["alpha beta", "beta gamma", "gamma delta"]
    vectorizer = TfidfVectorizer().fit(texts)

    class DenseIndex:
        def search(self, vectors, limit):
            count = len(vectors)
            return SimpleNamespace(
                keys=np.tile(np.asarray([[0, 1]], dtype=np.int32), (count, 1)),
                distances=np.tile(np.asarray([[0.1, 0.2]], dtype=np.float32), (count, 1)),
            )

    monkeypatch.setattr(
        "api.infrastructure.runtime.engine.encode_queries",
        lambda values, expected_dimensions: np.ones(
            (len(values), expected_dimensions), dtype=np.float32
        ),
    )
    index = CandidateIndex(
        pd.DataFrame({"record_id": ["R1", "R2", "R3"]}),
        vectorizer.transform(texts),
        vectorizer,
        {},
        InferenceConfig(sparse_top_k=2, candidate_cap=2),
        reference_embeddings=np.ones((3, 2), dtype=np.float32),
        dense_nn=DenseIndex(),
    )
    queries = [{"retrieval_text": text} for text in ["alpha beta", "gamma delta"]]

    frames = index.retrieve_many(queries, exact_mode="hybrid", use_dense=True)

    assert len(frames) == 2
    assert all(len(frame) == 2 for frame in frames)
    assert all("dense_score" in frame and "sparse_score" in frame for frame in frames)


def test_materializer_writes_inverted_sparse_artifact(tmp_path) -> None:
    from scipy.sparse import load_npz, save_npz

    from api.infrastructure.bundles.materialization import _materialize_sparse_inverted_artifact

    vectorizer = TfidfVectorizer().fit(["alpha beta", "beta gamma"])
    save_npz(
        tmp_path / "reference_sparse_matrix.npz", vectorizer.transform(["alpha beta", "beta gamma"])
    )

    artifact = _materialize_sparse_inverted_artifact(tmp_path, 2, "fingerprint")

    assert artifact is not None
    assert artifact["path"] == "reference_sparse_inverted.npz"
    assert artifact["query_feature_cap"] == DEFAULT_QUERY_FEATURE_CAP
    assert load_npz(tmp_path / str(artifact["path"])).getformat() == "csc"


def test_sparse_backend_rejects_invalid_inputs() -> None:
    vectorizer = TfidfVectorizer().fit(["alpha"])
    backend = SparseCandidateBackend(vectorizer.transform(["alpha"]), vectorizer)

    with pytest.raises(ValueError, match="top_k"):
        backend.retrieve("alpha", 0)
    with pytest.raises(ValueError, match="batch_size"):
        SparseCandidateBackend(backend.reference_matrix, vectorizer, batch_size=0)
    assert backend.retrieve_many([], 1) == []


def test_training_candidate_graph_merges_channels_without_pair_objects() -> None:
    from api.training.retrieval import CandidateGraphConfig, build_candidate_graph

    class Index:
        reference = pd.DataFrame(
            {
                "record_id": ["R1", "R2", "R3"],
                "retrieval_text": ["a", "b", "c"],
            }
        )
        block_indexes: dict[str, dict[str, np.ndarray]] = {
            "name_dob": {"same": np.asarray([0, 1], dtype=np.int32)},
            "exact_phone": {"same": np.asarray([0, 1], dtype=np.int32)},
        }
        reference_embeddings: np.ndarray | None = None

        def sparse_candidates(self, query_texts, k):
            del query_texts, k
            return (
                np.asarray([[0, 2], [1, 2], [2, 1]], dtype=np.int32),
                np.asarray([[1.0, 0.8], [1.0, 0.7], [1.0, 0.6]], dtype=np.float32),
            )

        def dense_reference_candidates(self, start, stop, k):
            del start, stop, k
            return np.empty((3, 0), dtype=np.int32), np.empty((3, 0), dtype=np.float32)

    graph = build_candidate_graph(
        Index(),
        CandidateGraphConfig(
            dense_top_k=0,
            sparse_top_k=1,
            sparse_batch_size=3,
            candidate_cap_per_record=2,
        ),
    )

    pair = graph.set_index(["left_record_id", "right_record_id"])
    assert pair.loc[("R1", "R2"), "exact_blocks"] == "exact_phone,name_dob"
    assert pair.loc[("R1", "R2"), "exact_count"] == 2
    assert pair.loc[("R1", "R3"), "sparse_score"] == pytest.approx(0.8)
