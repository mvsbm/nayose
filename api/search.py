"""Entity search over the trained embeddings — semantic ranking on top of the
exact LIKE search (api/main.py merges both). SRP: this module knows how to turn
the engine's per-record embeddings into entity centroids and rank them; it does
NOT talk to the network, the DB, or the frontend.

Honest scope: the embeddings come from TF-IDF over retrieval_text (encoded via
the bundle's SVD transform), so this tolerates typos/substrings/partial names
but does NOT cross-map kanji<->kana (different character tokens).
"""

from __future__ import annotations

from typing import Any

import numpy as np


class EntitySearch:
    def __init__(self, matcher: Any):
        self.matcher = matcher
        self._centroids: np.ndarray | None = None
        self._entity_ids: np.ndarray | None = None
        self._canon_lookup: dict[str, dict[str, Any]] | None = None
        self._sparse_norm: Any = None  # sklearn normalize of entity TF-IDF centroid matrix
        self._sparse_entity_ids: np.ndarray | None = None

    def prepare(self) -> None:
        if self._canon_lookup is None:
            self._build()

    def _build(self) -> None:
        engine = self.matcher.engine
        embeddings = engine._index.reference_embeddings
        if embeddings is not None:
            self._build_dense(engine, embeddings)
        self._build_sparse(engine)
        self._canon_lookup = {}
        twin_counts: dict[str, int] = {}
        for _, row in engine._canonical.iterrows():
            nayose = str(row["nayose_id"])
            address = str(row.get("address", ""))
            self._canon_lookup[nayose] = {
                "nayose_id": nayose,
                "name_kanji": str(row.get("name_kanji", "")),
                "name_kana": str(row.get("name_kana", "")),
                "name_romaji": str(row.get("name_romaji", "")),
                "date_birth": str(row.get("date_birth", "")),
                "party_type": str(row.get("party_type", "")),
                "address": str(row.get("address", "")),
                "email": str(row.get("email", "")),
                "phone": str(row.get("phone", "")),
                "postal_code": str(row.get("postal_code", "")),
                "record_count": int(_num(row.get("record_count"))),
                "prefecture": _prefecture(address),
            }
            kana = str(row.get("name_kana", "")).replace(" ", "")
            if kana:
                twin_counts[kana] = twin_counts.get(kana, 0) + 1
        self._twin_counts = twin_counts

    def _build_dense(self, engine: Any, embeddings: np.ndarray) -> None:
        record_ids = engine._reference["record_id"].astype(str).to_numpy()
        entity_ids = np.array(
            [engine._record_to_nayose.get(rid, "") for rid in record_ids], dtype=object
        )
        unique, inverse = np.unique(entity_ids, return_inverse=True)
        sums = np.zeros((len(unique), embeddings.shape[1]), dtype=np.float64)
        np.add.at(sums, inverse, embeddings.astype(np.float64, copy=False))
        counts = np.bincount(inverse)
        centroids = sums / np.maximum(counts[:, None], 1)
        norms = np.linalg.norm(centroids, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._centroids = (centroids / norms).astype(np.float32)
        self._entity_ids = unique

    def _build_sparse(self, engine: Any) -> None:
        """Step 5: entity-level TF-IDF centroids for sparse term similarity.
        Sparse-only math (indicator transpose) — NEVER densify the corpus
        matrix (374k x vocab would OOM); nnz stays bounded by the matrix."""
        matrix = getattr(engine, "_sparse_matrix", None)
        if matrix is None:
            return
        import scipy.sparse as sp

        record_ids = engine._reference["record_id"].astype(str).to_numpy()
        entity_ids = np.array(
            [engine._record_to_nayose.get(rid, "") for rid in record_ids], dtype=object
        )
        unique, inverse = np.unique(entity_ids, return_inverse=True)
        counts = np.bincount(inverse)
        n = len(record_ids)
        indicator = sp.coo_matrix(
            (np.ones(n, dtype=np.float64), (np.arange(n), inverse)),
            shape=(n, len(unique)),
        )
        sums = indicator.T @ matrix.tocsr()  # (n_entities x vocab), nnz bounded
        means = sums.multiply(1.0 / np.maximum(counts, 1)[:, None]).tocsr()
        squared = means.multiply(means).sum(axis=1)
        norms = np.sqrt(squared)
        norms[norms == 0] = 1.0
        self._sparse_norm = means.multiply(1.0 / norms).tocsr()
        self._sparse_entity_ids = unique

    def search(self, text: str, top_k: int = 20) -> list[dict[str, Any]] | None:
        """Rank entities by fused sparse+dense similarity. Returns None when the
        bundle has no embeddings (semantic search unavailable)."""
        if not text.strip():
            return []
        self.prepare()
        if self._canon_lookup is None:
            return None
        scores = self._fused_scores(text)
        if scores is None:
            return None
        similarity, ids = scores
        order = np.argsort(-similarity)[:top_k]
        rows = []
        for idx in order:
            entity_id = str(ids[idx])
            row = self._canon_lookup.get(entity_id)
            if row is None:
                continue
            kana = row["name_kana"].replace(" ", "")
            rows.append(
                {
                    **row,
                    "score": round(float(similarity[idx]), 4),
                    "kind": "semantic",
                    "signals": ["semantic"],
                    "search_backend": getattr(self.matcher.retriever, "active_backend", "unknown"),
                    "twin_count": max(0, self._twin_counts.get(kana, 1) - 1) if kana else 0,
                }
            )
        return rows

    def _fused_scores(self, text: str) -> tuple[np.ndarray, np.ndarray] | None:
        dense = (
            self._centroids @ self.matcher.retriever.encode(text).reshape(-1)
            if self._centroids is not None
            else None
        )
        sparse = None
        if self._sparse_norm is not None and self.matcher.retriever.svd is not None:
            q_sparse = self.matcher.retriever.index.vectorizer.transform([text])
            sparse = np.asarray(
                self._sparse_norm @ q_sparse.toarray().reshape(-1), dtype=np.float64
            )
        if dense is None and sparse is None:
            return None
        if dense is None:
            ids = self._sparse_entity_ids
            assert sparse is not None and ids is not None
            return sparse, ids
        if sparse is None:
            ids = self._entity_ids
            assert ids is not None
            return dense, ids
        # fuse: dense drives ranking, sparse term overlap breaks near-ties
        ids = self._entity_ids
        assert ids is not None
        fused = dense + 0.35 * np.clip(sparse, 0, 1)
        return fused, ids


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


_PREFECTURES = (
    "北海道",
    "青森県",
    "岩手県",
    "宮城県",
    "秋田県",
    "山形県",
    "福島県",
    "茨城県",
    "栃木県",
    "群馬県",
    "埼玉県",
    "千葉県",
    "東京都",
    "神奈川県",
    "新潟県",
    "富山県",
    "石川県",
    "福井県",
    "山梨県",
    "長野県",
    "岐阜県",
    "静岡県",
    "愛知県",
    "三重県",
    "滋賀県",
    "京都府",
    "大阪府",
    "兵庫県",
    "奈良県",
    "和歌山県",
    "鳥取県",
    "島根県",
    "岡山県",
    "広島県",
    "山口県",
    "徳島県",
    "香川県",
    "愛媛県",
    "高知県",
    "福岡県",
    "佐賀県",
    "長崎県",
    "熊本県",
    "大分県",
    "宮崎県",
    "鹿児島県",
    "沖縄県",
)


def _prefecture(address: str) -> str:
    """Leading prefecture token of an address (authoritative list)."""
    for name in _PREFECTURES:
        if address.startswith(name):
            return name
    return ""
