from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize


def _resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class BundleRetriever:
    def __init__(self, engine: Any, manifest: dict[str, Any], run_dir: Path):
        self.index = engine._index
        self.settings = manifest.get("config", {})
        self.svd = None
        self.encoder: Any = None
        self.model_id = manifest.get("embedding_model")
        self.mode = "sparse_only"
        if self.index.reference_embeddings is not None:
            backend = manifest.get("backends", {}).get("dense_index", "")
            if backend == "tfidf_svd_degraded":
                self.svd = joblib.load(run_dir / "dense_svd.joblib")
                self.mode = "tfidf_svd"
            elif self.model_id:
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

    def encode(self, text: str) -> np.ndarray:
        if self.svd is not None:
            result = normalize(self.svd.transform(self.index.vectorizer.transform([text])))
        else:
            if self.encoder is None:
                from sentence_transformers import SentenceTransformer

                self.encoder = SentenceTransformer(
                    self.model_id, device=_resolve_device(os.getenv("NAYOSE_DEVICE", "cpu"))
                )
            result = self.encoder.encode([text], normalize_embeddings=True)
        result = np.asarray(result, dtype=np.float32)
        expected = self.index.reference_embeddings.shape[1]
        if result.shape != (1, expected) or not np.isfinite(result).all():
            raise ValueError("query embedding does not match reference dimensions")
        return result

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
                self._dense(query, states)
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
                    "exact_blocks": state.get("exact_blocks", []),
                }
            )
        frame = pd.DataFrame(rows)
        cap = int(self.settings.get("candidate_cap_per_record", self.index.config.candidate_cap))
        return (
            frame.sort_values(["retrieval_score", "candidate_index"], ascending=[False, True])
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
        for idx, score in zip(indices[0], 1 - distances[0], strict=True):
            states.setdefault(int(idx), {})["sparse_score"] = max(0.0, float(score))

    def _dense(self, query: dict[str, str], states: dict[int, dict[str, Any]]) -> None:
        vector = self.encode(query.get("retrieval_text", ""))
        k = min(
            int(self.settings.get("dense_top_k", self.index.config.candidate_cap)),
            len(self.index.reference),
        )
        nn = self.index.dense_nn
        if hasattr(nn, "search"):
            matches = nn.search(vector, k)
            indices, distances = (
                np.asarray(matches.keys).reshape(-1),
                np.asarray(matches.distances).reshape(-1),
            )
        else:
            distances, indices = nn.kneighbors(vector, n_neighbors=k)
            indices, distances = indices.reshape(-1), distances.reshape(-1)
        for idx, score in zip(indices, 1 - distances, strict=True):
            states.setdefault(int(idx), {})["dense_score"] = max(0.0, float(score))
