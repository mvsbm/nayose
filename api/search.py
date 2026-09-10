"""Entity search over immutable record and alias retrieval artifacts.

The module maps retrieved records back to entities without averaging conflicting
observations. It does NOT talk to the network, the DB, or the frontend.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from api.dense_views import dense_view_queries
from api.normalize import normalize_record
from api.retrieval_fusion import DEFAULT_RETRIEVAL_FUSION_POLICY


class EntitySearch:
    def __init__(self, matcher: Any):
        self.matcher = matcher
        self._record_embeddings: np.ndarray | None = None
        self._record_entity_ids: np.ndarray | None = None
        self._dense_views: dict[str, Any] = {}
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
        self._record_embeddings = embeddings
        self._dense_views = getattr(engine._index, "dense_views", {})
        record_ids = engine._reference["record_id"].astype(str).to_numpy()
        self._record_entity_ids = np.array(
            [engine._record_to_nayose.get(record_id, "") for record_id in record_ids],
            dtype=object,
        )
        self._entity_ids = np.unique(self._record_entity_ids)
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
        scores = self._fused_scores(text, self._query_record(text))
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

    def _query_record(self, text: str) -> dict[str, str]:
        raw = text.strip()
        digits = "".join(character for character in raw if character.isdigit())
        address_like = any(
            token in raw.casefold()
            for token in (
                "都",
                "道",
                "府",
                "県",
                "市",
                "区",
                "町",
                "丁目",
                "番地",
                "号",
                "street",
                "avenue",
            )
        )
        values: dict[str, str] = {"party_type": ""}
        if "@" in raw:
            values["email"] = raw
        elif address_like:
            values["address"] = raw
            if len(digits) == 7:
                values["postal_code"] = raw
        elif digits and len(digits) == 7:
            values["postal_code"] = raw
        elif digits and len(digits) == 8:
            values["date_birth"] = raw
        elif digits:
            values["phone"] = raw
        else:
            values.update({"name_kanji": raw, "name_kana": raw, "name_romaji": raw})
        return normalize_record(values)

    def _fused_scores(
        self, text: str, query_record: dict[str, str]
    ) -> tuple[np.ndarray, np.ndarray] | None:
        dense_by_entity = self._dense_entity_scores(query_record)
        sparse = None
        if self._sparse_norm is not None and hasattr(self.matcher.retriever.index, "vectorizer"):
            q_sparse = self.matcher.retriever.index.vectorizer.transform(
                [query_record["retrieval_text"]]
            )
            sparse_values = np.asarray(
                self._sparse_norm @ q_sparse.toarray().reshape(-1), dtype=np.float64
            )
            sparse = (
                dict(zip(self._sparse_entity_ids, sparse_values, strict=True))
                if self._sparse_entity_ids is not None
                else {}
            )
        ids = self._entity_ids if dense_by_entity is not None else self._sparse_entity_ids
        if ids is None or (dense_by_entity is None and sparse is None):
            return None
        dense = (
            np.array(
                [dense_by_entity.get(str(entity_id), 0.0) for entity_id in ids],
                dtype=np.float64,
            )
            if dense_by_entity is not None
            else None
        )
        sparse_array = (
            np.array([sparse.get(entity_id, 0.0) for entity_id in ids], dtype=np.float64)
            if sparse is not None
            else None
        )
        if dense is None:
            assert sparse_array is not None
            return sparse_array, ids
        if sparse_array is None:
            return dense, ids
        return dense + 0.35 * np.clip(sparse_array, 0, 1), ids

    def _dense_entity_scores(self, query_record: dict[str, str]) -> dict[str, float] | None:
        if self._record_entity_ids is None:
            return None
        requests: list[tuple[str, str, Any | None]] = [
            ("record", query_record["retrieval_text"], None)
        ]
        address_like = bool(query_record.get("n_address"))
        for view, artifact in self._dense_views.items():
            if view == "address" and not address_like:
                continue
            requests.extend(
                (view, text, artifact) for text in dense_view_queries(query_record, view)
            )
        vectors = self._encode_many([text for _, text, _ in requests])
        scores: dict[str, float] = {}
        dense_nn = getattr(self.matcher.engine._index, "dense_nn", None)
        if dense_nn is not None and hasattr(dense_nn, "search"):
            matches = dense_nn.search(
                vectors[0].reshape(1, -1), min(256, len(self._record_entity_ids))
            )
            indices = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
            values = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
            for index, value in zip(indices, values, strict=True):
                entity_id = str(self._record_entity_ids[int(index)])
                scores[entity_id] = max(float(value), scores.get(entity_id, 0.0))
        elif self._record_embeddings is not None:
            values = self._record_embeddings @ vectors[0].reshape(-1)
            for record_index in np.argsort(-values):
                entity_id = str(self._record_entity_ids[int(record_index)])
                scores[entity_id] = max(
                    float(values[int(record_index)]), scores.get(entity_id, 0.0)
                )
        else:
            return None

        for vector, (view, _, artifact) in zip(vectors[1:], requests[1:], strict=True):
            assert artifact is not None
            matches = artifact.index.search(
                vector.reshape(1, -1), min(256, len(artifact.embeddings))
            )
            indices = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
            values = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
            weight = DEFAULT_RETRIEVAL_FUSION_POLICY.view_weight(view, query_record)
            for index, value in zip(indices, values, strict=True):
                record_index = (
                    int(artifact.row_indices[int(index)])
                    if artifact.row_indices is not None
                    else int(index)
                )
                entity_id = str(self._record_entity_ids[record_index])
                scores[entity_id] = max(float(value) * weight, scores.get(entity_id, 0.0))
        return scores

    def _encode_many(self, texts: list[str]) -> np.ndarray:
        encoder = getattr(self.matcher.retriever, "encode_many", None)
        if callable(encoder):
            return np.asarray(encoder(texts), dtype=np.float32)
        return np.vstack([self.matcher.retriever.encode(text) for text in texts]).astype(
            np.float32, copy=False
        )


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
