import numpy as np

from api.retrieval import BundleRetriever


class _Index:
    reference_embeddings: np.ndarray = np.zeros((1, 2), dtype=np.float32)
    vectorizer = None


def test_bundle_retriever_chunks_large_query_encoding(monkeypatch) -> None:
    calls: list[int] = []

    def encode_queries(texts: list[str]) -> np.ndarray:
        calls.append(len(texts))
        return np.ones((len(texts), 2), dtype=np.float32)

    monkeypatch.setattr("api.retrieval.encode_queries", encode_queries)
    retriever = object.__new__(BundleRetriever)
    retriever.index = _Index()
    retriever.svd = None

    values = retriever.encode_many(["query"] * 1025)

    assert calls == [1024, 1]
    assert values.shape == (1025, 2)
