from types import SimpleNamespace

import numpy as np

from api.infrastructure.retrieval.dense_retrieval import retrieve_dense_candidates
from api.infrastructure.retrieval.dense_views import DenseView


class _SearchIndex:
    def search(self, vectors, limit):
        count = len(np.asarray(vectors))
        return SimpleNamespace(
            keys=np.zeros((count, min(limit, 1)), dtype=np.int32),
            distances=np.zeros((count, min(limit, 1)), dtype=np.float32),
        )


class _EmptySearchIndex:
    def search(self, vectors, limit):
        count = len(np.asarray(vectors))
        return SimpleNamespace(
            keys=np.empty((count, 0), dtype=np.int32),
            distances=np.empty((count, 0), dtype=np.float32),
        )


def test_field_aware_profile_can_skip_full_record_encoding() -> None:
    calls: list[list[str]] = []
    query = {
        "party_type": "person",
        "n_party_type": "person",
        "n_name_loose": "高橋太郎",
        "n_kana": "タカハシタロウ",
        "n_romaji": "tarotakahashi",
    }
    states: dict[int, dict[str, object]] = {}

    def encode_queries(texts: list[str], _: int | None) -> np.ndarray:
        calls.append(texts)
        return np.ones((len(texts), 2), dtype=np.float32)

    retrieve_dense_candidates(
        query,
        states,
        reference_embeddings=np.zeros((1, 2), dtype=np.float32),
        dense_nn=_SearchIndex(),
        dense_svd=None,
        vectorizer=None,
        dense_views={"name": DenseView("name", np.zeros((1, 2)), _SearchIndex())},
        limit=1,
        encode_query=lambda _: np.zeros(2, dtype=np.float32),
        encode_queries=encode_queries,
        include_record_dense=False,
    )

    assert calls == [
        ["party_type=person name_kanji=高橋太郎 name_kana=タカハシタロウ name_romaji=tarotakahashi"]
    ]
    assert "dense_rank" not in states[0]
    assert states[0]["dense_view_scores"] == {"name": 1.0}


def test_field_aware_profile_handles_an_empty_view_result() -> None:
    query = {"party_type": "person", "n_party_type": "person"}
    states: dict[int, dict[str, object]] = {}

    retrieve_dense_candidates(
        query,
        states,
        reference_embeddings=np.zeros((1, 2), dtype=np.float32),
        dense_nn=_EmptySearchIndex(),
        dense_svd=None,
        vectorizer=None,
        dense_views={"name": DenseView("name", np.zeros((1, 2)), _EmptySearchIndex())},
        limit=1,
        encode_query=lambda _: np.zeros(2, dtype=np.float32),
        encode_queries=lambda texts, _: np.ones((len(texts), 2), dtype=np.float32),
        include_record_dense=False,
    )

    assert states == {}
