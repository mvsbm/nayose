from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from api.dense_views import dense_view_queries
from api.embedding_runtime import (
    close_query_encoder,
    configure_query_encoder,
    configured_batch_size,
    encode_queries,
)
from api.performance import resolve_device
from api.retrieval_fusion import retrieval_fusion_score, retrieval_methods


def _resolve_device(requested: str) -> str:
    return resolve_device(requested)


class BundleRetriever:
    def __init__(self, engine: Any, manifest: dict[str, Any], run_dir: Path):
        self.index = engine._index
        self.settings = manifest.get("config", {})
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
                    device=os.getenv("NAYOSE_DEVICE", "cpu"),
                    batch_size=configured_batch_size(
                        int(self.settings.get("neural_batch_size", 16))
                    ),
                )
                self.mode = "neural"
            else:
                raise RuntimeError("dense embeddings have no recorded query encoder")

    @property
    def exact_mode(self) -> str:
        return os.getenv("NAYOSE_EXACT_MODE", self.settings.get("exact_mode", "hybrid"))

    @property
    def active_backend(self) -> str:
        if self.exact_mode == "exact_only":
            return "exact_only"
        if os.getenv("NAYOSE_USE_DENSE", "1").lower() in {"0", "false", "no"}:
            return "sparse_only"
        return self.mode

    def encode_many(self, texts: list[str]) -> np.ndarray:
        if self.index.reference_embeddings is None:
            raise RuntimeError("dense query encoding is unavailable")
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
        states: dict[int, dict[str, Any]] = {}
        if mode != "fuzzy_only":
            cap = int(self.settings.get("exact_block_cap", self.index.config.exact_block_cap))
            for block, groups in self.index.block_indexes.items():
                key = self.index._block_key(query, block)
                if key and key in groups and len(groups[key]) <= cap:
                    for idx in groups[key]:
                        row = states.setdefault(int(idx), {})
                        row["exact_count"] = row.get("exact_count", 0) + 1
                        row.setdefault("exact_blocks", []).append(block)
        if mode != "exact_only":
            self._sparse(query, states)
            if self.active_backend != "sparse_only" and self.index.dense_nn is not None:
                self._dense_and_views(query, states)
        rows = []
        for idx, state in states.items():
            exact, sparse, dense = (
                state.get(key, 0.0) for key in ("exact_count", "sparse_score", "dense_score")
            )
            rows.append(
                {
                    "candidate_index": idx,
                    "exact_count": exact,
                    "sparse_score": sparse,
                    "dense_score": dense,
                    "channel_count": int(exact > 0) + int(sparse > 0) + int(dense > 0),
                    "retrieval_score": max(sparse, dense) + 0.08 * min(exact, 3),
                    "retrieval_fusion_score": retrieval_fusion_score(state, query=query),
                    "dense_view_count": len(state.get("dense_view_scores", {})),
                    "dense_view_scores": state.get("dense_view_scores", {}),
                    "dense_view_ranks": state.get("dense_view_ranks", {}),
                    "retrieval_methods": retrieval_methods(state),
                    "exact_blocks": state.get("exact_blocks", []),
                }
            )
        frame = pd.DataFrame(rows)
        cap = int(self.settings.get("candidate_cap_per_record", self.index.config.candidate_cap))
        return (
            frame.sort_values(
                ["retrieval_fusion_score", "retrieval_score", "candidate_index"],
                ascending=[False, False, True],
            )
            .head(cap)
            .reset_index(drop=True)
            if len(frame)
            else frame
        )

    def _sparse(self, query: dict[str, str], states: dict[int, dict[str, Any]]) -> None:
        vector = self.index.vectorizer.transform([query.get("retrieval_text", "")])
        k = min(
            int(self.settings.get("sparse_top_k", self.index.config.sparse_top_k)) + 1,
            len(self.index.reference),
        )
        distances, indices = self.index.sparse_nn.kneighbors(vector, n_neighbors=k)
        for rank, (idx, score) in enumerate(zip(indices[0], 1 - distances[0], strict=True), 1):
            state = states.setdefault(int(idx), {})
            state["sparse_score"] = max(0.0, float(score))
            state["sparse_rank"] = rank

    def _dense_and_views(self, query: dict[str, str], states: dict[int, dict[str, Any]]) -> None:
        k = min(
            int(self.settings.get("dense_top_k", self.index.config.candidate_cap)),
            len(self.index.reference),
        )
        requests = [
            (view, text)
            for view in getattr(self.index, "dense_views", {})
            for text in dense_view_queries(query, view)
        ]
        vectors = self.encode_many(
            [query.get("retrieval_text", ""), *(text for _, text in requests)]
        )
        matches = self.index.dense_nn.search(vectors[0].reshape(1, -1), k)
        indices = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
        distances = np.asarray(matches.distances, dtype=np.float32).reshape(-1)
        for rank, (idx, distance) in enumerate(zip(indices, distances, strict=True), 1):
            state = states.setdefault(int(idx), {})
            state["dense_score"] = max(0.0, float(1.0 - distance))
            state["dense_rank"] = rank

        for vector, (view, _) in zip(vectors[1:], requests, strict=True):
            artifact = self.index.dense_views[view]
            view_matches = artifact.index.search(vector.reshape(1, -1), k)
            view_indices = np.asarray(view_matches.keys, dtype=np.int32).reshape(-1)
            view_distances = np.asarray(view_matches.distances, dtype=np.float32).reshape(-1)
            for rank, (idx, distance) in enumerate(
                zip(view_indices, view_distances, strict=True), 1
            ):
                candidate_index = (
                    int(artifact.row_indices[int(idx)])
                    if artifact.row_indices is not None
                    else int(idx)
                )
                state = states.setdefault(candidate_index, {})
                scores = state.setdefault("dense_view_scores", {})
                score = max(0.0, float(1.0 - distance))
                scores[view] = max(score, float(scores.get(view, 0.0)))
                ranks = state.setdefault("dense_view_ranks", {})
                ranks[view] = min(rank, int(ranks.get(view, rank)))
