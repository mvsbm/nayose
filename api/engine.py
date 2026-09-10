"""Load trained artifacts and run entity matching.

The module owns artifact loading, corpus fingerprint validation, retrieval
indexes, and the LightGBM booster adapter. Configuration is immutable so a
loaded engine cannot silently change its serving contract.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
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
from api.block_index import (
    BLOCK_INDEX_ARTIFACT,
    build_block_indexes,
    load_block_indexes,
)
from api.block_index import data_fingerprint as _artifact_data_fingerprint
from api.dense_index import (
    DENSE_INDEX_ARTIFACT,
    load_dense_index,
    target_platform,
    usearch_version,
)
from api.dense_retrieval import retrieve_dense_candidates
from api.dense_views import DenseView, load_dense_view_artifacts
from api.embedding_runtime import (
    close_query_encoder,
    configure_query_encoder,
    encode_queries,
    encode_query,
)
from api.features import (  # noqa: F401
    PAIR_FEATURE_COLS,
    _fuzzy,
    compute_pair_features,
    has_safe_identity_anchor,
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
from api.performance import cpu_thread_budget, cpu_thread_limit
from api.retrieval import _resolve_device  # noqa: F401
from api.retrieval_fusion import retrieval_fusion_score, retrieval_methods

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
    entity_margin_threshold: float = 0.06
    minimum_evidence_families: int = 2
    cpu_threads: int = field(default_factory=cpu_thread_budget)


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


def dense_enabled() -> bool:
    return os.getenv("NAYOSE_USE_DENSE", "1").strip().lower() not in {"0", "false", "no"}


# ─── Candidate index ─────────────────────────────────────────────────────────


class CandidateIndex:
    """Manages sparse index and block indexes for candidate retrieval."""

    def __init__(
        self,
        reference: pd.DataFrame,
        sparse_matrix,
        vectorizer,
        block_indexes: dict[str, dict[str, np.ndarray]],
        config: InferenceConfig,
        reference_embeddings: np.ndarray | None = None,
        dense_nn: Any | None = None,
        dense_svd: Any | None = None,
        dense_views: dict[str, DenseView] | None = None,
    ):
        self.reference = reference
        self.sparse_matrix: Any = sparse_matrix
        self.vectorizer: Any = vectorizer
        self.block_indexes: dict[str, dict[str, np.ndarray]] = block_indexes
        self.config = config
        self.reference_embeddings = reference_embeddings
        self.dense_nn = dense_nn
        self.dense_svd = dense_svd
        self.dense_views = dense_views or {}
        self.sparse_nn = NearestNeighbors(
            n_neighbors=min(config.sparse_top_k + 1, len(reference)),
            metric="cosine",
            algorithm="brute",
            n_jobs=config.cpu_threads,
        )
        self.sparse_nn.fit(sparse_matrix)

    def retrieve(self, query: dict[str, str]) -> pd.DataFrame:
        """Retrieve candidates using exact blocks + sparse retrieval."""
        states: dict[int, dict[str, Any]] = {}

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
        with cpu_thread_limit(self.config.cpu_threads):
            dists, idxs = self.sparse_nn.kneighbors(
                qvec, n_neighbors=min(self.config.sparse_top_k + 1, len(self.reference))
            )
        for rank, (tgt, score) in enumerate(zip(idxs[0], 1.0 - dists[0]), 1):
            s = states.setdefault(
                int(tgt), {"exact_blocks": set(), "sparse_score": 0.0, "dense_score": 0.0}
            )
            if float(score) > s["sparse_score"]:
                s["sparse_score"] = float(score)
                s["sparse_rank"] = rank

        # Dense candidates (F2LLM embeddings; ANN via usearch when present).
        # Record-level dense evidence remains separate from field-view evidence;
        # the trained pair model only consumes the former.
        if dense_enabled() and self.reference_embeddings is not None and self.dense_nn is not None:
            self._retrieve_dense(query, states)

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
                    "retrieval_fusion_score": retrieval_fusion_score(s, query=query),
                    "dense_view_count": len(s.get("dense_view_scores", {})),
                    "dense_view_scores": s.get("dense_view_scores", {}),
                    "dense_view_ranks": s.get("dense_view_ranks", {}),
                    "retrieval_methods": retrieval_methods(s),
                    "channel_count": int(ec > 0)
                    + int(s["sparse_score"] > 0)
                    + int(s["dense_score"] > 0),
                }
            )
        df = pd.DataFrame(rows)
        return (
            df.sort_values(
                ["retrieval_fusion_score", "retrieval_score", "candidate_index"],
                ascending=[False, False, True],
            )
            .head(self.config.candidate_cap)
            .reset_index(drop=True)
            if not df.empty
            else df
        )

    def _retrieve_dense(self, query: dict[str, str], states: dict[int, dict[str, Any]]) -> None:
        reference_embeddings = self.reference_embeddings
        dense_nn = self.dense_nn
        if reference_embeddings is None or dense_nn is None:
            raise RuntimeError("dense retrieval artifacts are unavailable")
        retrieve_dense_candidates(
            query,
            states,
            reference_embeddings=reference_embeddings,
            dense_nn=dense_nn,
            dense_svd=self.dense_svd,
            vectorizer=self.vectorizer,
            dense_views=self.dense_views,
            limit=min(self.config.candidate_cap, len(self.reference)),
            encode_query=encode_query,
            encode_queries=encode_queries,
        )

    def _block_key(self, query: dict[str, str], block_name: str) -> str:
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


def _load_dense_artifacts(
    config: InferenceConfig, reference: pd.DataFrame
) -> tuple[np.ndarray | None, Any | None, Any | None]:
    """Reference F2LLM embeddings + ANN index from the model bundle (optional)."""
    candidate_paths = [
        config.model_dir / "reference_embeddings.f16.npy",
        config.model_dir / "reference_embeddings.npy",
    ]
    emb_path = next((p for p in candidate_paths if p.exists()), None)
    if emb_path is None or len(reference) == 0:
        if emb_path is None:
            print("WARN: no reference embeddings in the bundle; dense retrieval disabled.")
        return None, None, None
    embeddings = np.load(emb_path)
    if embeddings.shape[0] != len(reference):
        raise RuntimeError(
            f"embedding file {emb_path} has {embeddings.shape[0]} rows but reference has "
            f"{len(reference)}; bundle is inconsistent with the reference"
        )
    index_path = config.model_dir / DENSE_INDEX_ARTIFACT
    if not index_path.is_file():
        raise RuntimeError(
            f"dense retrieval requires {index_path}; materialize the target-platform ANN "
            "artifact before serving"
        )
    manifest_path = config.model_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dense_svd = None
    if manifest.get("backends", {}).get("dense_index") == "tfidf_svd_degraded":
        svd_path = config.model_dir / "dense_svd.joblib"
        if not svd_path.is_file():
            raise RuntimeError(f"degraded dense retrieval requires {svd_path}")
        dense_svd = joblib.load(svd_path)
    artifact = manifest.get("runtime_artifacts", {}).get("dense_index")
    if not isinstance(artifact, dict):
        raise RuntimeError("run manifest has no dense_index runtime artifact identity")
    if artifact.get("platform") != target_platform():
        raise RuntimeError(
            f"dense ANN artifact targets {artifact.get('platform')!r}, "
            f"not {target_platform()!r}; materialize it on the serving platform"
        )
    if artifact.get("usearch_version") != usearch_version():
        raise RuntimeError(
            "dense ANN artifact usearch version does not match the serving environment"
        )
    try:
        index = load_dense_index(
            index_path,
            expected_rows=len(reference),
            expected_dimensions=embeddings.shape[1],
        )
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"unable to restore required dense ANN artifact {index_path}; "
            "serving refuses to fall back to a slower backend"
        ) from exc
    print(f"OK: dense retrieval enabled ({emb_path.name}, restored usearch HNSW)")
    return embeddings.astype(np.float32, copy=False), index, dense_svd


def _load_dense_views(
    config: InferenceConfig, reference: pd.DataFrame, fingerprint: str
) -> dict[str, DenseView]:
    manifest_path = config.model_dir / "run_manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    views = load_dense_view_artifacts(
        config.model_dir, manifest, len(reference), expected_fingerprint=fingerprint
    )
    if views:
        print(f"OK: restored field-aware dense views ({', '.join(sorted(views))})")
    return views


# ─── Nayose engine ───────────────────────────────────────────────────────────


# ─── Bundle loading and recommendation stages ────────────────────────────────


def _data_fingerprint(frame: pd.DataFrame) -> str:
    """Same canonical fingerprint nayose.ipynb writes into run/training manifests."""
    return _artifact_data_fingerprint(frame)


def _load_bundle(config: InferenceConfig) -> NayoseEngine:
    """Load a model bundle and its matching reference, then return a ready engine.

    Enforces dataset<->model generation linkage: a run/training manifest next to
    the model artifacts must fingerprint-match the loaded reference, otherwise the
    bundle is refused (a stale model silently matches the wrong corpus).
    """
    engine = NayoseEngine(config)

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
    reference = pd.read_parquet(ref_path)
    engine._reference = reference
    reference_fingerprint = _guard_generation_match(config, reference)

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

    # Exact indexes are build-time artifacts. Serving fails closed instead of
    # rebuilding them and turning a bad bundle into an unpredictable slow start.
    block_index_path = config.model_dir / BLOCK_INDEX_ARTIFACT
    if not block_index_path.is_file():
        raise RuntimeError(
            f"model bundle is missing required {block_index_path}; "
            "materialize the target bundle before serving"
        )
    try:
        engine._block_indexes = load_block_indexes(
            block_index_path,
            expected_fingerprint=reference_fingerprint,
            expected_row_count=len(reference),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"unable to load required exact indexes from {block_index_path}; "
            "serving refuses to rebuild them"
        ) from exc
    print(f"OK: loaded exact block indexes ({block_index_path.name})")

    # Load optional F2LLM dense embeddings + ANN index
    reference_embeddings, dense_nn, dense_svd = _load_dense_artifacts(config, reference)
    dense_views = _load_dense_views(config, reference, reference_fingerprint)

    # Create candidate index
    engine._index = CandidateIndex(
        reference,
        engine._sparse_matrix,
        engine._vectorizer,
        engine._block_indexes,
        config,
        reference_embeddings=reference_embeddings,
        dense_nn=dense_nn,
        dense_svd=dense_svd,
        dense_views=dense_views,
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


def _guard_generation_match(config: InferenceConfig, reference: pd.DataFrame) -> str:
    manifest_hits = [config.model_dir / n for n in ("run_manifest.json", "training_manifest.json")]
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
            "Model manifest has no data_fingerprint; refusing to load without generation linkage."
        )
    actual = _data_fingerprint(reference)
    if stored != actual:
        raise RuntimeError(
            f"Model bundle {config.model_dir} was trained on data generation fingerprint "
            f"{stored[:12]}... but the loaded reference ({len(reference):,} rows) has "
            f"fingerprint {actual[:12]}... Refusing to infer with a stale model. Regenerate "
            "the dataset and retrain, or point model_dir/data_dir at a matching pair."
        )
    print(f"OK: model bundle matches reference data (fingerprint {stored[:12]}...)")
    return actual


def _build_block_indexes(reference: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
    """Compatibility wrapper for the shared vectorized block-index builder."""
    return build_block_indexes(reference)


class RecommendationRunner:
    """Execute the loaded engine's retrieval, scoring, and aggregation stages."""

    def __init__(self, engine: NayoseEngine):
        self.engine = engine

    def run(self, raw_query: dict[str, Any], top_k: int = 8) -> Recommendation:
        """Run retrieval, scoring, aggregation, and decision construction."""
        engine = self._loaded_engine()
        assert engine._index is not None
        query = normalize_record(raw_query)
        query_id = str(raw_query.get("query_id", "MANUAL"))
        if not any(query.get(field) for field in ("n_name_loose", "n_kana", "n_phone", "n_email")):
            return Recommendation(
                query_id,
                "INSUFFICIENT_INPUT",
                None,
                0.0,
                0.0,
                pd.DataFrame(),
                {"message": "Insufficient input"},
            )
        retrieval = engine._index.retrieve(query)
        if retrieval.empty:
            return Recommendation(
                query_id,
                "NO_MATCH",
                None,
                0.0,
                0.0,
                pd.DataFrame(),
                {"message": "No candidates"},
            )
        raw, features, scores = self._score_candidates(query, retrieval)
        entities = self._aggregate(raw, scores, retrieval)
        return self._build_result(query_id, retrieval, entities, top_k)

    def _loaded_engine(self) -> NayoseEngine:
        engine = self.engine
        if (
            engine._index is None
            or engine._reference is None
            or engine._canonical is None
            or engine._pair_model is None
            or engine._pair_calibrator is None
        ):
            raise RuntimeError("engine is not loaded")
        return engine

    def _score_candidates(
        self, query: dict[str, str], retrieval: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
        engine = self._loaded_engine()
        assert engine._reference is not None
        assert engine._pair_model is not None
        assert engine._pair_calibrator is not None
        cand_indices = retrieval["candidate_index"].tolist()
        raw = engine._reference.iloc[cand_indices].reset_index(drop=True)
        normalized = pd.DataFrame([normalize_record(row.to_dict()) for _, row in raw.iterrows()])
        features = compute_pair_features(query, normalized, retrieval)
        try:
            base = np.asarray(engine._pair_model.predict_proba(features[PAIR_FEATURE_COLS])[:, 1])
            inputs = getattr(engine._pair_calibrator, "n_features_in_", 1)
            if inputs == 3:
                calibration = np.column_stack(
                    [logit_clip(base), np.zeros(len(base)), np.zeros(len(base))]
                )
            else:
                calibration = base.reshape(-1, 1)
            scores = engine._pair_calibrator.predict_proba(calibration)[:, 1]
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            print(f"WARN: pair scoring fallback ({type(exc).__name__}): {str(exc)[:80]}")
            scores = np.clip(retrieval["retrieval_score"].to_numpy(dtype=np.float64), 0.0, 1.0)
        return raw, features, np.clip(np.asarray(scores, dtype=np.float64), 0.0, 1.0)

    def _aggregate(
        self, raw: pd.DataFrame, scores: np.ndarray, retrieval: pd.DataFrame
    ) -> list[dict[str, Any]]:
        engine = self._loaded_engine()
        assert engine._reference is not None
        entity_scores: dict[str, list[float]] = {}
        for idx, probability in zip(retrieval["candidate_index"].tolist(), scores):
            record_id = str(engine._reference.iloc[idx].get("record_id", ""))
            entity_id = engine._record_to_nayose.get(record_id)
            if entity_id:
                entity_scores.setdefault(entity_id, []).append(float(probability))
        entities = [
            {
                "nayose_id": entity_id,
                "probability": np.mean(sorted(probabilities, reverse=True)[:3]),
                "support": len(probabilities),
            }
            for entity_id, probabilities in entity_scores.items()
        ]
        return sorted(
            entities,
            key=lambda item: float(item["probability"]),
            reverse=True,
        )

    def _build_result(
        self,
        query_id: str,
        retrieval: pd.DataFrame,
        entities: list[dict[str, Any]],
        top_k: int,
    ) -> Recommendation:
        engine = self._loaded_engine()
        assert engine._canonical is not None
        if not entities:
            return Recommendation(
                query_id,
                "NO_MATCH",
                None,
                0.0,
                0.0,
                pd.DataFrame(),
                {"message": "No entities"},
            )
        top = entities[0]
        second = float(entities[1]["probability"]) if len(entities) > 1 else 0.0
        top_probability = float(top["probability"])
        margin = top_probability - second
        action = (
            "AUTO_LINK"
            if top_probability >= engine.config.entity_auto_threshold
            else "REVIEW"
            if top_probability >= engine.config.entity_review_threshold
            else "NO_MATCH"
        )
        rows = []
        for rank, entity in enumerate(entities[:top_k], 1):
            canonical = engine._canonical[engine._canonical.nayose_id == entity["nayose_id"]]
            if not canonical.empty:
                row = canonical.iloc[0]
                rows.append(
                    {
                        "rank": rank,
                        "nayose_id": entity["nayose_id"],
                        "probability": entity["probability"],
                        "name": row.get("name_kanji", ""),
                        "name_kana": row.get("name_kana", ""),
                        "records": row.get("record_count", 0),
                    }
                )
        return Recommendation(
            query_id=query_id,
            action=action,
            top_nayose_id=str(top["nayose_id"]),
            top_probability=top_probability,
            margin=margin,
            candidates=pd.DataFrame(rows),
            diagnostics={
                "retrieved": len(retrieval),
                "entities": len(entities),
                "top": top_probability,
                "second": second,
                "margin": margin,
            },
        )


class NayoseEngine:
    """Entity matching inference over a validated model bundle."""

    def __init__(self, config: InferenceConfig):
        self.config = config
        self._index: CandidateIndex | None = None
        self._pair_model: Any | None = None
        self._pair_calibrator: Any | None = None
        self._reference: pd.DataFrame | None = None
        self._canonical: pd.DataFrame | None = None
        self._record_to_nayose: dict[str, str] = {}
        self._sparse_matrix: Any | None = None
        self._vectorizer: Any | None = None
        self._block_indexes: dict[str, dict[str, np.ndarray]] = {}
        self._runner = RecommendationRunner(self)

    @classmethod
    def _data_fingerprint(cls, frame: pd.DataFrame) -> str:
        return _data_fingerprint(frame)

    @classmethod
    def load(
        cls, model_dir: str | Path = "models/current", data_dir: str | Path = "data/current"
    ) -> NayoseEngine:
        config = InferenceConfig(model_dir=Path(model_dir), data_dir=Path(data_dir))
        return _load_bundle(config)

    @classmethod
    def _guard_generation_match(cls, config: Any, reference: pd.DataFrame) -> str:
        return _guard_generation_match(config, reference)

    def _build_block_indexes(self, reference: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
        return _build_block_indexes(reference)

    def recommend(self, raw_query: dict[str, Any], top_k: int = 8) -> Recommendation:
        return self._runner.run(raw_query, top_k)

    def close(self) -> None:
        close_query_encoder()


# ─── Convenience ──────────────────────────────────────────────────────────────


def _apply_manifest_embedding_model(config: InferenceConfig) -> None:
    """Use the EXACT embedding model the bundle was encoded with (serve parity)."""
    manifest_path = config.model_dir / "run_manifest.json"
    if manifest_path.exists():
        embedded = json.loads(manifest_path.read_text(encoding="utf-8")).get("embedding_model")
        if embedded:
            configure_query_encoder(str(embedded))
            print(f"embedding model from bundle: {embedded}")


def _apply_manifest_thresholds(config: InferenceConfig) -> None:
    """Use the trained thresholds from the bundle's run_manifest (env overrides win)."""
    manifest_path = config.model_dir / "run_manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    thresholds = manifest.get("thresholds", {})
    bundle_config = manifest.get("config", {})
    if isinstance(bundle_config, dict):
        for attr, key in (
            ("entity_margin_threshold", "entity_margin_threshold"),
            ("minimum_evidence_families", "minimum_evidence_families"),
        ):
            if key in bundle_config:
                object.__setattr__(config, attr, type(getattr(config, attr))(bundle_config[key]))
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


def load_engine(model_dir: str = "models/current", data_dir: str = "data/current") -> NayoseEngine:
    """Load and return a ready-to-use engine."""
    return NayoseEngine.load(model_dir, data_dir)
