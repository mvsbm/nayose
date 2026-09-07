"""
Nayose Inference Engine — load trained artifacts and run entity matching.

Hosted at api/engine.py (migrated from the repo-root legacy module). This is
an engine FACADE: it owns trained-artifact loading, the corpus fingerprint
guard, retrieval indexes and the LightGBM booster wrapper.

Design:
- Facade Pattern: Single entry point for complex subsystem
- SRP: Each class has one responsibility
- Configuration Object: Frozen dataclass for all settings
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.sparse import load_npz
from sklearn.neighbors import NearestNeighbors

# Shared primitives (single source of truth): normalization, pair features and
# device policy live in api/* — the engine re-exports them so historical import
# sites keep working without owning a second copy.
from api.features import (  # noqa: F401
    PAIR_FEATURE_COLS,
    _fuzzy,
    compute_pair_features,
    logit_clip,
)
from api.normalize import (  # noqa: F401
    DIGITS_RE,
    LEGAL_FORMS,
    OLD_KANJI,
    PUNCT_RE,
    _compact,
    normalize_address,
    normalize_dob,
    normalize_email,
    normalize_kana,
    normalize_name,
    normalize_phone,
    normalize_postal,
    normalize_record,
)
from api.retrieval import _resolve_device  # noqa: F401

# ─── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    """Immutable configuration for inference."""

    model_dir: Path = Path("models/current")
    data_dir: Path = Path("data/current")
    sparse_top_k: int = 16
    exact_block_cap: int = 50
    candidate_cap: int = 50
    pair_auto_threshold: float = 0.105
    entity_auto_threshold: float = 0.016
    entity_review_threshold: float = 0.016


class LightGBMBoosterAdapter:
    """Binary classifier view over a native LightGBM booster (no pickle)."""

    def __init__(self, booster):
        self.booster = booster

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        positive = np.asarray(self.booster.predict(features, raw_score=False), dtype=np.float64)
        return np.column_stack([1.0 - positive, positive])


def load_pair_model(model_dir: Path) -> Any:
    """Pair verifier: native LightGBM text booster (current) or legacy joblib pickle."""
    native = model_dir / "pair_model.txt"
    if native.exists():
        import lightgbm as lgb

        return LightGBMBoosterAdapter(lgb.Booster(model_file=str(native)))
    legacy = next(
        (
            model_dir / n
            for n in ("pair_model.joblib", "pair_verifier.joblib")
            if (model_dir / n).exists()
        ),
        model_dir / "pair_verifier.joblib",
    )
    return joblib.load(legacy)


_QUERY_ENCODER: Any = None
_QUERY_MODEL_ID: str | None = None  # embedding model recorded in the bundle manifest


def _load_query_encoder() -> Any:
    from sentence_transformers import SentenceTransformer

    device = _resolve_device(os.getenv("NAYOSE_DEVICE", "auto"))
    model_id = _QUERY_MODEL_ID or os.getenv("NAYOSE_EMBEDDING_MODEL", "cl-nagoya/ruri-v3-130m")
    kwargs: dict[str, Any] = {"device": device}
    if device in {"mps", "cuda"}:
        import torch

        kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
    return SentenceTransformer(model_id, **kwargs)


def dense_enabled() -> bool:
    return os.getenv("NAYOSE_USE_DENSE", "1").strip().lower() not in {"0", "false", "no"}


def encode_query(text: str) -> np.ndarray:
    global _QUERY_ENCODER
    if _QUERY_ENCODER is None:
        _QUERY_ENCODER = _load_query_encoder()
    return _QUERY_ENCODER.encode([text], normalize_embeddings=True).astype(np.float32, copy=False)


# ─── Candidate Index (SRP: retrieval only) ───────────────────────────────────


class CandidateIndex:
    """Manages sparse index and block indexes for candidate retrieval."""

    def __init__(
        self,
        reference: pd.DataFrame,
        sparse_matrix,
        vectorizer,
        block_indexes: dict,
        config: InferenceConfig,
        reference_embeddings: np.ndarray | None = None,
        dense_nn: Any | None = None,
    ):
        self.reference = reference
        self.sparse_matrix = sparse_matrix
        self.vectorizer = vectorizer
        self.block_indexes = block_indexes
        self.config = config
        self.reference_embeddings = reference_embeddings
        self.dense_nn = dense_nn
        self.sparse_nn = NearestNeighbors(
            n_neighbors=min(config.sparse_top_k + 1, len(reference)),
            metric="cosine",
            algorithm="brute",
        )
        self.sparse_nn.fit(sparse_matrix)

    def retrieve(self, query: dict[str, str]) -> pd.DataFrame:
        """Retrieve candidates using exact blocks + sparse retrieval."""
        states: dict[int, dict] = {}

        # Exact block candidates
        for block_name, groups in self.block_indexes.items():
            key = self._block_key(query, block_name)
            if key and key in groups and len(groups[key]) <= self.config.exact_block_cap:
                for idx in groups[key]:
                    states.setdefault(
                        idx, {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
                    )
                    states[idx]["exact_blocks"].add(block_name)

        # Sparse candidates
        qvec = self.vectorizer.transform([query.get("retrieval_text", "")])
        dists, idxs = self.sparse_nn.kneighbors(
            qvec, n_neighbors=min(self.config.sparse_top_k + 1, len(self.reference))
        )
        for rank, (tgt, score) in enumerate(zip(idxs[0], 1.0 - dists[0]), 1):
            s = states.setdefault(
                int(tgt), {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
            )
            if float(score) > s["sparse_score"]:
                s["sparse_score"] = float(score)

        # Dense candidates (F2LLM embeddings; ANN via usearch when present)
        if dense_enabled() and self.reference_embeddings is not None and self.dense_nn is not None:
            q_vec = encode_query(str(query.get("retrieval_text", "")))
            k_d = min(self.config.candidate_cap, len(self.reference))
            try:
                matches = self.dense_nn.search(q_vec, k_d)
                dense_keys = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
                dense_scores = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
            except Exception:
                distances, idxs = self.dense_nn.kneighbors(q_vec, n_neighbors=k_d)
                dense_keys, dense_scores = idxs.reshape(-1), (1.0 - distances).reshape(-1)
            for rank, (tgt, score) in enumerate(zip(dense_keys, dense_scores), 1):
                s = states.setdefault(
                    int(tgt), {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
                )
                if float(score) > s["dense_score"]:
                    s["dense_score"] = float(score)

        # Build result
        rows = []
        for idx, s in states.items():
            ec = len(s["exact_blocks"])
            rows.append(
                {
                    "candidate_index": idx,
                    "exact_count": ec,
                    "sparse_score": s["sparse_score"],
                    "dense_score": s["dense_score"],
                    "retrieval_score": max(s["sparse_score"], s["dense_score"]) + 0.08 * min(ec, 3),
                    "channel_count": int(ec > 0)
                    + int(s["sparse_score"] > 0)
                    + int(s["dense_score"] > 0),
                }
            )
        df = pd.DataFrame(rows)
        return (
            df.sort_values("retrieval_score", ascending=False)
            .head(self.config.candidate_cap)
            .reset_index(drop=True)
            if not df.empty
            else df
        )

    def _block_key(self, query: dict, block_name: str) -> str:
        specs = {
            "exact_email": ("n_email",),
            "exact_phone": ("n_phone",),
            "name_dob": ("n_name_loose", "n_dob"),
            "kana_dob": ("n_kana", "n_dob"),
            "signature_postal": ("n_name_signature", "n_postal"),
            "kana_postal": ("n_kana", "n_postal"),
            "name_address": ("n_name_loose", "n_address"),
            "phone_suffix_name": ("n_phone_suffix8", "n_name_loose"),
        }
        cols = specs.get(block_name, ())
        vals = [query.get(c, "") for c in cols]
        return "¦".join(vals) if all(vals) else ""


# ─── Recommendation Result ────────────────────────────────────────────────────


@dataclass
class Recommendation:
    """Immutable result of entity matching."""

    query_id: str
    action: str  # AUTO_LINK, REVIEW, NO_MATCH, INSUFFICIENT_INPUT
    top_nayose_id: str | None
    top_probability: float
    margin: float
    candidates: pd.DataFrame
    diagnostics: dict[str, Any]


def _load_dense_artifacts(config, reference: pd.DataFrame) -> tuple[np.ndarray | None, Any | None]:
    """Reference F2LLM embeddings + ANN index from the model bundle (optional)."""
    candidate_paths = [
        config.model_dir / "reference_embeddings.f16.npy",
        config.model_dir / "reference_embeddings.npy",
    ]
    emb_path = next((p for p in candidate_paths if p.exists()), None)
    if emb_path is None or len(reference) == 0:
        if emb_path is None:
            print("WARN: no reference embeddings in the bundle; dense retrieval disabled.")
        return None, None
    embeddings = np.load(emb_path)
    if embeddings.shape[0] != len(reference):
        raise RuntimeError(
            f"embedding file {emb_path} has {embeddings.shape[0]} rows but reference has "
            f"{len(reference)}; bundle is inconsistent with the reference"
        )
    try:
        from usearch.index import Index

        index = Index(ndim=embeddings.shape[1], metric="cos", dtype="f16")
        index.add(np.arange(len(reference), dtype=np.int64), embeddings)
        print(f"OK: dense retrieval enabled ({emb_path.name}, {embeddings.shape}, usearch HNSW)")
        return embeddings.astype(np.float32, copy=False), index
    except Exception:
        nn = NearestNeighbors(metric="cosine", algorithm="brute", n_jobs=-1).fit(embeddings)
        print(
            f"OK: dense retrieval enabled ({emb_path.name}); usearch unavailable -> sklearn brute"
        )
        return embeddings.astype(np.float32, copy=False), nn


# ─── Nayose Engine (Facade) ───────────────────────────────────────────────────


class NayoseEngine:
    """
    Facade for entity matching inference.

    Usage:
        engine = NayoseEngine.load("nayose_output_v2")
        result = engine.recommend({"name_kanji": "山田太郎", "phone": "090-1234-5678"})
    """

    def __init__(self, config: InferenceConfig):
        self.config = config
        self._index: CandidateIndex | None = None
        self._pair_model = None
        self._pair_calibrator = None
        self._reference: pd.DataFrame | None = None
        self._canonical: pd.DataFrame | None = None
        self._record_to_nayose: dict[str, str] = {}

    @classmethod
    def _data_fingerprint(cls, frame: pd.DataFrame) -> str:
        """Same canonical fingerprint nayose.ipynb writes into run/training manifests."""
        return hashlib.sha256(
            pd.util.hash_pandas_object(frame, index=False).values.tobytes()
        ).hexdigest()

    @classmethod
    def load(cls, model_dir: str | Path = "models", data_dir: str | Path = "data") -> NayoseEngine:
        """Load a model bundle and its matching reference, then return a ready engine.

        Enforces dataset<->model generation linkage: a run/training manifest next to
        the model artifacts must fingerprint-match the loaded reference, otherwise the
        bundle is refused (a stale model silently matches the wrong corpus).
        """
        config = InferenceConfig(model_dir=Path(model_dir), data_dir=Path(data_dir))
        engine = cls(config)

        # Load reference data (current contract; legacy name tolerated)
        ref_path = config.data_dir / "reference_records.parquet"
        if not ref_path.exists():
            legacy = config.data_dir / "reference_snapshot.parquet"
            if legacy.exists():
                print(
                    "WARN: loading legacy reference_snapshot.parquet; regenerate data with the current pipeline."
                )
                ref_path = legacy
            else:
                raise FileNotFoundError(
                    f"Missing {ref_path}: generate data first "
                    "(python scripts/generate_data.py --profile mac)"
                )
        engine._reference = pd.read_parquet(ref_path)
        cls._guard_generation_match(config, engine._reference)

        # Load sparse artifacts (current bundle names; legacy names tolerated)
        sparse_path = next(
            (
                config.model_dir / n
                for n in ("reference_tfidf.npz", "reference_sparse_matrix.npz")
                if (config.model_dir / n).exists()
            ),
            config.model_dir / "reference_tfidf.npz",
        )
        engine._sparse_matrix = load_npz(sparse_path)
        vectorizer_path = next(
            (
                config.model_dir / n
                for n in ("sparse_vectorizer.joblib", "tfidf_vectorizer.joblib")
                if (config.model_dir / n).exists()
            ),
            config.model_dir / "tfidf_vectorizer.joblib",
        )
        engine._vectorizer = joblib.load(vectorizer_path)

        # Build block indexes
        engine._block_indexes = engine._build_block_indexes()

        # Load optional F2LLM dense embeddings + ANN index
        reference_embeddings, dense_nn = _load_dense_artifacts(config, engine._reference)

        # Create candidate index
        engine._index = CandidateIndex(
            engine._reference,
            engine._sparse_matrix,
            engine._vectorizer,
            engine._block_indexes,
            config,
            reference_embeddings=reference_embeddings,
            dense_nn=dense_nn,
        )

        # Load models (native LightGBM booster; legacy joblib tolerated)
        engine._pair_model = load_pair_model(config.model_dir)
        engine._pair_calibrator = joblib.load(config.model_dir / "pair_calibrator.joblib")

        _apply_manifest_thresholds(config)
        _apply_manifest_embedding_model(config)

        # Load entity mappings (current bundle names; legacy names tolerated)
        canonical_path = next(
            (
                config.model_dir / n
                for n in ("canonical_entities.parquet", "entity_index.parquet")
                if (config.model_dir / n).exists()
            ),
            config.model_dir / "entity_index.parquet",
        )
        mapping_path = next(
            (
                config.model_dir / n
                for n in ("record_to_nayose.parquet", "record_entity_mapping.parquet")
                if (config.model_dir / n).exists()
            ),
            config.model_dir / "record_entity_mapping.parquet",
        )
        engine._canonical = pd.read_parquet(canonical_path)
        mapping = pd.read_parquet(mapping_path)
        engine._record_to_nayose = dict(zip(mapping.record_id.astype(str), mapping.nayose_id))

        return engine

    @classmethod
    def _guard_generation_match(cls, config, reference: pd.DataFrame) -> None:
        manifest_hits = [
            config.model_dir / n for n in ("run_manifest.json", "training_manifest.json")
        ]
        manifest_path = next((p for p in manifest_hits if p.exists()), None)
        if manifest_path is None:
            raise RuntimeError(
                "Model bundle has no run/training manifest; refusing to load without "
                "a data fingerprint."
            )
        import json as _json

        stored = _json.loads(manifest_path.read_text(encoding="utf-8")).get("data_fingerprint")
        if not stored:
            raise RuntimeError(
                "Model manifest has no data_fingerprint; refusing to load without "
                "generation linkage."
            )
        actual = cls._data_fingerprint(reference)
        if stored != actual:
            raise RuntimeError(
                f"Model bundle {config.model_dir} was trained on data generation fingerprint "
                f"{stored[:12]}... but the loaded reference ({len(reference):,} rows) has "
                f"fingerprint {actual[:12]}... Refusing to infer with a stale model. Regenerate "
                "the dataset and retrain, or point model_dir/data_dir at a matching pair."
            )
        print(f"OK: model bundle matches reference data (fingerprint {stored[:12]}...)")

    def _build_block_indexes(self) -> dict[str, dict[str, list[int]]]:
        """Build block indexes from reference data."""
        ref = self._reference
        specs = {
            "exact_email": ("email",),
            "exact_phone": ("phone",),
            "name_dob": ("name_kanji", "date_birth"),
            "kana_dob": ("name_kana", "date_birth"),
            "signature_postal": ("name_kanji", "postal_code"),
            "kana_postal": ("name_kana", "postal_code"),
            "name_address": ("name_kanji", "address"),
            "phone_suffix_name": ("phone", "name_kanji"),
        }
        indexes = {}
        for block_name, raw_cols in specs.items():
            groups: dict[str, list[int]] = {}
            for idx, row in ref.iterrows():
                # Normalize the key values
                vals = []
                for col in raw_cols:
                    v = str(row.get(col, ""))
                    if col == "name_kanji":
                        vals.append(normalize_name(v))
                    elif col == "name_kana":
                        vals.append(normalize_kana(v))
                    elif col == "phone":
                        vals.append(
                            normalize_phone(v)[-8:]
                            if "suffix" in block_name
                            else normalize_phone(v)
                        )
                    elif col == "postal_code":
                        vals.append(normalize_postal(v))
                    elif col == "address":
                        vals.append(normalize_address(v))
                    elif col == "date_birth":
                        vals.append(normalize_dob(v))
                    elif col == "email":
                        vals.append(normalize_email(v))
                    else:
                        vals.append(_compact(v))
                key = "¦".join(vals)
                if all(vals):
                    groups.setdefault(key, []).append(idx)
            indexes[block_name] = groups
        return indexes

    def recommend(self, raw_query: dict[str, Any], top_k: int = 8) -> Recommendation:
        """Main inference method."""
        query = normalize_record(raw_query)
        query_id = raw_query.get("query_id", "MANUAL")

        # Check sufficiency
        populated = sum(1 for f in ["n_name_loose", "n_kana", "n_phone", "n_email"] if query.get(f))
        if populated < 1:
            return Recommendation(
                query_id,
                "INSUFFICIENT_INPUT",
                None,
                0.0,
                0.0,
                pd.DataFrame(),
                {"message": "Insufficient input"},
            )

        # Retrieve candidates
        retrieval = self._index.retrieve(query)
        if retrieval.empty:
            return Recommendation(
                query_id, "NO_MATCH", None, 0.0, 0.0, pd.DataFrame(), {"message": "No candidates"}
            )

        # Get candidate records, normalize them, and compute features
        cand_indices = retrieval["candidate_index"].tolist()
        cand_records_raw = self._reference.iloc[cand_indices].reset_index(drop=True)
        # Normalize candidate records to get n_name_loose, n_kana, etc.
        cand_records = pd.DataFrame(
            [normalize_record(row.to_dict()) for _, row in cand_records_raw.iterrows()]
        )
        features = compute_pair_features(query, cand_records, retrieval)

        # Score with pair model: base probabilities -> calibrator (3 inputs in the
        # current bundle: logit(base), reranker_score, reranker_present).
        try:
            base = np.asarray(self._pair_model.predict_proba(features[PAIR_FEATURE_COLS])[:, 1])
            n_in = getattr(self._pair_calibrator, "n_features_in_", 1)
            if n_in == 3:  # current bundle contract (reranker disabled at serve time)
                cal_x = np.column_stack(
                    [logit_clip(base), np.zeros(len(base)), np.zeros(len(base))]
                )
            elif n_in == 1:  # legacy calibrator trained directly on base probability
                cal_x = base.reshape(-1, 1)
            else:
                cal_x = base.reshape(-1, 1)
            pair_probs = self._pair_calibrator.predict_proba(cal_x)[:, 1]
        except Exception as exc:
            print(f"WARN: pair scoring fallback ({type(exc).__name__}): {str(exc)[:80]}")
            pair_probs = np.clip(retrieval["retrieval_score"].to_numpy(dtype=np.float64), 0.0, 1.0)
        pair_probs = np.clip(np.asarray(pair_probs, dtype=np.float64), 0.0, 1.0)

        # Aggregate to entities
        entity_scores: dict[str, list[float]] = {}
        for idx, prob in zip(cand_indices, pair_probs):
            rid = str(self._reference.iloc[idx].get("record_id", ""))
            nid = self._record_to_nayose.get(rid)
            if nid:
                entity_scores.setdefault(nid, []).append(float(prob))

        entities = sorted(
            [
                {
                    "nayose_id": nid,
                    "probability": np.mean(sorted(ps, reverse=True)[:3]),
                    "support": len(ps),
                }
                for nid, ps in entity_scores.items()
            ],
            key=lambda x: x["probability"],
            reverse=True,
        )

        if not entities:
            return Recommendation(
                query_id, "NO_MATCH", None, 0.0, 0.0, pd.DataFrame(), {"message": "No entities"}
            )

        top = entities[0]
        second = entities[1]["probability"] if len(entities) > 1 else 0.0
        margin = top["probability"] - second

        action = (
            "AUTO_LINK"
            if top["probability"] >= self.config.entity_auto_threshold
            else "REVIEW"
            if top["probability"] >= self.config.entity_review_threshold
            else "NO_MATCH"
        )

        # Build candidate table
        cand_rows = []
        for rank, e in enumerate(entities[:top_k], 1):
            canon = self._canonical[self._canonical.nayose_id == e["nayose_id"]]
            if len(canon) > 0:
                c = canon.iloc[0]
                cand_rows.append(
                    {
                        "rank": rank,
                        "nayose_id": e["nayose_id"],
                        "probability": e["probability"],
                        "name": c.get("name_kanji", ""),
                        "name_kana": c.get("name_kana", ""),
                        "records": c.get("record_count", 0),
                    }
                )

        return Recommendation(
            query_id=query_id,
            action=action,
            top_nayose_id=top["nayose_id"],
            top_probability=top["probability"],
            margin=margin,
            candidates=pd.DataFrame(cand_rows),
            diagnostics={
                "retrieved": len(retrieval),
                "entities": len(entities),
                "top": top["probability"],
                "second": second,
                "margin": margin,
            },
        )


# ─── Convenience ──────────────────────────────────────────────────────────────


def _apply_manifest_embedding_model(config) -> None:
    """Use the EXACT embedding model the bundle was encoded with (serve parity)."""
    global _QUERY_MODEL_ID
    manifest_path = config.model_dir / "run_manifest.json"
    if manifest_path.exists():
        embedded = json.loads(manifest_path.read_text(encoding="utf-8")).get("embedding_model")
        if embedded:
            _QUERY_MODEL_ID = embedded
            print(f"embedding model from bundle: {embedded}")


def _apply_manifest_thresholds(config) -> None:
    """Use the trained thresholds from the bundle's run_manifest (env overrides win)."""
    manifest_path = config.model_dir / "run_manifest.json"
    if not manifest_path.exists():
        return
    thresholds = json.loads(manifest_path.read_text(encoding="utf-8")).get("thresholds", {})
    for attr, key in (
        ("entity_auto_threshold", "entity_auto"),
        ("entity_review_threshold", "entity_review"),
    ):
        if key in thresholds and os.getenv("NAYOSE_ENTITY_OVERRIDE", "0") != "1":
            object.__setattr__(config, attr, float(thresholds[key]))
    if "entity_auto" in thresholds:
        print(
            f"thresholds from manifest: entity_auto={thresholds['entity_auto']:.4f} "
            f"entity_review={thresholds.get('entity_review', thresholds.get('entity_auto', 0.0)):.4f}"
        )


def load_engine(model_dir: str = "models", data_dir: str = "data") -> NayoseEngine:
    """Load and return a ready-to-use engine."""
    return NayoseEngine.load(model_dir, data_dir)
