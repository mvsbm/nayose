"""Load trained artifacts and run entity matching.

The module owns artifact loading, corpus fingerprint validation, retrieval
indexes, and the LightGBM booster adapter. Configuration is immutable so a
loaded engine cannot silently change its serving contract.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.sparse import load_npz

from api.domain.features import (  # noqa: F401
    ENTITY_FEATURE_COLS,
    PAIR_FEATURE_COLS,
    _fuzzy,
    compute_pair_features,
    has_safe_identity_anchor,
    logit_clip,
)
from api.domain.fingerprint import data_fingerprint as _artifact_data_fingerprint
from api.domain.normalize import (  # noqa: F401
    DIGITS_RE,
    LEGAL_FORMS,
    OLD_KANJI,
    PUNCT_RE,
    _compact,
    normalize_address,
    normalize_dob,
    normalize_email,
    normalize_frame,
    normalize_kana,
    normalize_name,
    normalize_name_signature,
    normalize_name_strict,
    normalize_phone,
    normalize_postal,
    normalize_record,
    normalize_romaji,
)
from api.infrastructure.bundles.release import validate_artifact_checksums
from api.infrastructure.retrieval.block_index import (
    BLOCK_INDEX_ARTIFACT,
    build_block_indexes,
    load_block_indexes,
)
from api.infrastructure.retrieval.bundle_retrieval import _resolve_device  # noqa: F401
from api.infrastructure.retrieval.dense_index import (
    DENSE_INDEX_ARTIFACT,
    load_dense_index,
    target_platform,
    usearch_version,
)
from api.infrastructure.retrieval.dense_retrieval import (
    retrieve_dense_candidates,
    retrieve_dense_candidates_many,
)
from api.infrastructure.retrieval.dense_views import DenseView, load_dense_view_artifacts
from api.infrastructure.retrieval.fusion import retrieval_fusion_score, retrieval_methods
from api.infrastructure.retrieval.sparse_retrieval import (
    DEFAULT_QUERY_FEATURE_CAP,
    SPARSE_INVERTED_ARTIFACT,
    SparseCandidateBackend,
)
from api.infrastructure.runtime.embedding_runtime import (
    close_query_encoder,
    configure_query_encoder,
    encode_queries,
    encode_query,
)
from api.infrastructure.runtime.engine_config import EngineSettings, InferenceConfig
from api.infrastructure.runtime.performance import cpu_thread_budget, cpu_thread_limit
from nayose.config import ConfigSnapshot, RetrievalConfig, load_config

__all__ = ["EngineSettings", "InferenceConfig"]


class LightGBMBoosterAdapter:
    """Binary classifier view over a native LightGBM booster (no pickle)."""

    def __init__(self, booster, threads: int | None = None):
        self.booster = booster
        self.threads = threads or cpu_thread_budget()

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        with cpu_thread_limit(self.threads):
            positive = np.asarray(
                self.booster.predict(features, raw_score=False, num_threads=self.threads),
                dtype=np.float64,
            )
        return np.column_stack([1.0 - positive, positive])


def load_pair_model(model_dir: Path, threads: int | None = None) -> Any:
    """Pair verifier: native LightGBM text booster (current) or legacy joblib pickle."""
    native = model_dir / "pair_model.txt"
    if native.exists():
        import lightgbm as lgb

        return LightGBMBoosterAdapter(lgb.Booster(model_file=str(native)), threads)
    legacy = next(
        (
            model_dir / n
            for n in ("pair_model.joblib", "pair_verifier.joblib")
            if (model_dir / n).exists()
        ),
        model_dir / "pair_verifier.joblib",
    )
    return joblib.load(legacy)


def dense_enabled(settings: RetrievalConfig | None = None) -> bool:
    return (settings or load_config().retrieval).use_dense


# ─── Candidate index ─────────────────────────────────────────────────────────


class CandidateIndex:
    """Manages sparse index and block indexes for candidate retrieval."""

    def __init__(
        self,
        reference: pd.DataFrame,
        sparse_matrix,
        vectorizer,
        block_indexes: dict[str, dict[str, np.ndarray]],
        config: EngineSettings,
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
        self.sparse_backend = SparseCandidateBackend(
            sparse_matrix,
            vectorizer,
            query_feature_cap=config.sparse_query_feature_cap,
        )

    def retrieve(
        self,
        query: dict[str, str],
        *,
        exact_mode: str = "hybrid",
        use_dense: bool | None = None,
        settings: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """Retrieve one query through the batch-capable retrieval pipeline."""
        return self.retrieve_many(
            [query], exact_mode=exact_mode, use_dense=use_dense, settings=settings
        )[0]

    def retrieve_many(
        self,
        queries: Sequence[dict[str, str]],
        *,
        exact_mode: str = "hybrid",
        use_dense: bool | None = None,
        settings: dict[str, Any] | None = None,
    ) -> list[pd.DataFrame]:
        """Retrieve exact, sparse, and dense candidates for a bounded query batch."""
        if exact_mode not in {"hybrid", "fuzzy_only", "exact_only"}:
            raise ValueError("unsupported exact mode")
        settings = settings or {}
        exact_cap = int(settings.get("exact_block_cap", self.config.exact_block_cap))
        sparse_top_k = int(settings.get("sparse_top_k", self.config.sparse_top_k))
        candidate_cap = int(settings.get("candidate_cap_per_record", self.config.candidate_cap))
        query_values = list(queries)
        states_list: list[dict[int, dict[str, Any]]] = [{} for _ in query_values]

        if exact_mode != "fuzzy_only":
            for query, states in zip(query_values, states_list, strict=True):
                for block_name, groups in self.block_indexes.items():
                    key = self._block_key(query, block_name)
                    if key and key in groups and len(groups[key]) <= exact_cap:
                        for idx in groups[key]:
                            states.setdefault(int(idx), {})
                            states[int(idx)].setdefault("exact_blocks", []).append(block_name)

        if exact_mode != "exact_only" and query_values:
            top_k = min(sparse_top_k + 1, len(self.reference))
            sparse_matches = self.sparse_backend.retrieve_many(
                [query.get("retrieval_text", "") for query in query_values], top_k
            )
            for states, (indices, scores) in zip(states_list, sparse_matches, strict=True):
                self._add_sparse_results(states, indices, scores)

            dense_allowed = dense_enabled(self.config.retrieval) if use_dense is None else use_dense
            dense_limit = min(int(settings.get("dense_top_k", candidate_cap)), len(self.reference))
            if (
                dense_allowed
                and self.reference_embeddings is not None
                and self.dense_nn is not None
            ):
                self._retrieve_dense_many(query_values, states_list, dense_limit)

        return [
            self._frame_from_states(states, query, candidate_cap)
            for query, states in zip(query_values, states_list, strict=True)
        ]

    def retrieve_sparse_many(
        self, queries: Sequence[dict[str, str]], settings: dict[str, Any] | None = None
    ) -> list[pd.DataFrame]:
        """Retrieve sparse candidates in bounded vectorization batches."""
        settings = settings or {}
        sparse_top_k = int(settings.get("sparse_top_k", self.config.sparse_top_k))
        candidate_cap = int(settings.get("candidate_cap_per_record", self.config.candidate_cap))
        top_k = min(sparse_top_k + 1, len(self.reference))
        matches = self.sparse_backend.retrieve_many(
            [query.get("retrieval_text", "") for query in queries], top_k
        )
        frames: list[pd.DataFrame] = []
        for query, (indices, scores) in zip(queries, matches, strict=True):
            states: dict[int, dict[str, Any]] = {}
            self._add_sparse_results(states, indices, scores)
            frames.append(self._frame_from_states(states, query, candidate_cap))
        return frames

    @staticmethod
    def _add_sparse_results(
        states: dict[int, dict[str, Any]], indices: np.ndarray, scores: np.ndarray
    ) -> None:
        for rank, (idx, score) in enumerate(zip(indices, scores, strict=True), 1):
            state = states.setdefault(int(idx), {})
            state["sparse_score"] = max(float(score), float(state.get("sparse_score", 0.0)))
            state["sparse_rank"] = rank

    def _frame_from_states(
        self, states: dict[int, dict[str, Any]], query: dict[str, str], candidate_cap: int
    ) -> pd.DataFrame:
        rows = []
        for idx, state in states.items():
            exact_count = len(state.get("exact_blocks", ()))
            sparse_score = float(state.get("sparse_score", 0.0))
            dense_score = float(state.get("dense_score", 0.0))
            rows.append(
                {
                    "candidate_index": idx,
                    "exact_count": exact_count,
                    "sparse_score": sparse_score,
                    "dense_score": dense_score,
                    "retrieval_score": max(sparse_score, dense_score) + 0.08 * min(exact_count, 3),
                    "retrieval_fusion_score": retrieval_fusion_score(state, query=query),
                    "dense_view_count": len(state.get("dense_view_scores", {})),
                    "dense_view_scores": state.get("dense_view_scores", {}),
                    "dense_view_ranks": state.get("dense_view_ranks", {}),
                    "retrieval_methods": retrieval_methods(state),
                    "channel_count": int(exact_count > 0)
                    + int(sparse_score > 0)
                    + int(dense_score > 0),
                    "exact_blocks": state.get("exact_blocks", []),
                }
            )
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        return (
            frame.sort_values(
                ["retrieval_fusion_score", "retrieval_score", "candidate_index"],
                ascending=[False, False, True],
            )
            .head(candidate_cap)
            .reset_index(drop=True)
        )

    def retrieve_dense_record_many(
        self,
        vectors: np.ndarray,
        queries: Sequence[dict[str, str]],
        settings: dict[str, Any] | None = None,
    ) -> list[pd.DataFrame]:
        """Search record embeddings for a bounded batch of encoded queries."""
        if self.dense_nn is None or self.reference_embeddings is None:
            raise RuntimeError("dense retrieval artifacts are unavailable")
        settings = settings or {}
        candidate_cap = int(settings.get("candidate_cap_per_record", self.config.candidate_cap))
        dense_limit = min(int(settings.get("dense_top_k", candidate_cap)), len(self.reference))
        if vectors.ndim != 2 or len(vectors) != len(queries):
            raise ValueError("dense query batch shape does not match query count")
        matches = self.dense_nn.search(vectors, dense_limit)
        keys = np.asarray(matches.keys, dtype=np.int32)
        scores = 1.0 - np.asarray(matches.distances, dtype=np.float32)
        if keys.ndim != 2 or scores.shape != keys.shape:
            raise ValueError("dense batch search returned an invalid result shape")
        frames: list[pd.DataFrame] = []
        for query, query_keys, query_scores in zip(queries, keys, scores, strict=True):
            states: dict[int, dict[str, Any]] = {}
            self._add_dense_results(states, query_keys, query_scores)
            frames.append(self._frame_from_states(states, query, candidate_cap))
        return frames

    @staticmethod
    def _add_dense_results(
        states: dict[int, dict[str, Any]], keys: np.ndarray, scores: np.ndarray
    ) -> None:
        for rank, (idx, score) in enumerate(zip(keys, scores, strict=True), 1):
            state = states.setdefault(int(idx), {})
            if float(score) > float(state.get("dense_score", 0.0)):
                state["dense_score"] = float(score)
                state["dense_rank"] = rank

    def _retrieve_dense_many(
        self,
        queries: Sequence[dict[str, str]],
        states: Sequence[dict[int, dict[str, Any]]],
        limit: int,
    ) -> None:
        reference_embeddings = self.reference_embeddings
        dense_nn = self.dense_nn
        if reference_embeddings is None or dense_nn is None:
            raise RuntimeError("dense retrieval artifacts are unavailable")
        retrieve_dense_candidates_many(
            list(queries),
            list(states),
            reference_embeddings=reference_embeddings,
            dense_nn=dense_nn,
            dense_svd=self.dense_svd,
            vectorizer=self.vectorizer,
            dense_views=self.dense_views,
            limit=limit,
            encode_queries=encode_queries,
            include_record_dense=self.config.dense_profile != "field_aware",
        )

    def _retrieve_dense(
        self, query: dict[str, str], states: dict[int, dict[str, Any]], limit: int
    ) -> None:
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
            limit=limit,
            encode_query=encode_query,
            encode_queries=encode_queries,
            include_record_dense=self.config.dense_profile != "field_aware",
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


def _load_dense_artifacts(
    config: EngineSettings, reference: pd.DataFrame
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
    embeddings = np.load(emb_path, mmap_mode="r")
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
    return embeddings, index, dense_svd


def _load_dense_views(
    config: EngineSettings, reference: pd.DataFrame, fingerprint: str
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
    """Canonical fingerprint shared by generation and training manifests."""
    return _artifact_data_fingerprint(frame)


def _load_bundle(config: EngineSettings) -> NayoseEngine:
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
                "(python -m api.cli data generate --profile mac)"
            )
    reference = pd.read_parquet(ref_path)
    engine._reference = reference
    reference_fingerprint = _guard_generation_match(config, reference)

    # Load sparse artifacts (current bundle names; legacy names tolerated).
    bundle_manifest = json.loads(
        (config.model_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    validate_artifact_checksums(config.model_dir)
    runtime_artifacts = bundle_manifest.get("runtime_artifacts", {})
    inverted_metadata = (
        runtime_artifacts.get("sparse_inverted") if isinstance(runtime_artifacts, dict) else None
    )
    if inverted_metadata is not None:
        if not isinstance(inverted_metadata, dict):
            raise RuntimeError("sparse inverted runtime artifact identity is malformed")
        if inverted_metadata.get("data_fingerprint") != reference_fingerprint:
            raise RuntimeError("sparse inverted artifact fingerprint does not match reference")
        cap = inverted_metadata.get("query_feature_cap", DEFAULT_QUERY_FEATURE_CAP)
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
            raise RuntimeError("sparse inverted artifact query feature cap is invalid")
        config = config.with_sparse_query_feature_cap(cap)
        sparse_names: tuple[str, ...] = (SPARSE_INVERTED_ARTIFACT,)
    else:
        sparse_names = ("reference_tfidf.npz", "reference_sparse_matrix.npz")
    sparse_path = next(
        (config.model_dir / name for name in sparse_names if (config.model_dir / name).exists()),
        config.model_dir / sparse_names[0],
    )
    if not sparse_path.is_file():
        raise RuntimeError(f"model bundle is missing sparse artifact {sparse_path}")
    engine._sparse_matrix = load_npz(sparse_path)
    if engine._sparse_matrix.ndim != 2 or engine._sparse_matrix.shape[0] != len(reference):
        raise RuntimeError("sparse artifact dimensions do not match reference records")
    vectorizer_path = next(
        (
            config.model_dir / n
            for n in ("sparse_vectorizer.joblib", "tfidf_vectorizer.joblib")
            if (config.model_dir / n).exists()
        ),
        config.model_dir / "tfidf_vectorizer.joblib",
    )
    engine._vectorizer = joblib.load(vectorizer_path)
    if engine._sparse_matrix.shape[1] != len(engine._vectorizer.vocabulary_):
        raise RuntimeError("sparse artifact dimensions do not match vectorizer vocabulary")

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
    engine._pair_model = load_pair_model(config.model_dir, config.cpu_threads)
    pair_calibrator_path = config.model_dir / "pair_calibrator.joblib"
    engine._pair_calibrator = (
        joblib.load(pair_calibrator_path) if pair_calibrator_path.is_file() else None
    )
    scoring_mode = str(bundle_manifest.get("entity_scoring_mode", "legacy_mean_top3"))
    if scoring_mode not in {"legacy_mean_top3", "logistic_v1"}:
        raise RuntimeError(f"unsupported entity scoring mode: {scoring_mode}")
    if scoring_mode == "logistic_v1":
        entity_paths = {
            "scaler": config.model_dir / "entity_scaler.joblib",
            "model": config.model_dir / "entity_model.joblib",
        }
        missing_entity = [str(path) for path in entity_paths.values() if not path.is_file()]
        if missing_entity:
            raise RuntimeError(
                f"logistic entity scoring requires bundle artifacts: {missing_entity}"
            )
        engine._entity_scaler = joblib.load(entity_paths["scaler"])
        engine._entity_model = joblib.load(entity_paths["model"])
        calibrator_path = config.model_dir / "entity_calibrator.joblib"
        engine._entity_calibrator = (
            joblib.load(calibrator_path) if calibrator_path.is_file() else None
        )
    engine._entity_scoring_mode = scoring_mode

    config = _apply_manifest_thresholds(config)
    engine.config = config
    if engine._index is None:
        raise RuntimeError("candidate index was not initialized")
    engine._index.config = config
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


def _guard_generation_match(config: EngineSettings, reference: pd.DataFrame) -> str:
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


class NayoseEngine:
    """Entity matching inference over a validated model bundle."""

    def __init__(self, config: EngineSettings):
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
        self._entity_scaler: Any | None = None
        self._entity_model: Any | None = None
        self._entity_calibrator: Any | None = None
        self._entity_scoring_mode = "legacy_mean_top3"

    def score_entity_candidates(self, candidates: list[dict[str, Any]]) -> np.ndarray:
        if not candidates:
            return np.empty(0, dtype=np.float64)
        if self._entity_scoring_mode != "logistic_v1":
            return np.asarray(
                [
                    float(candidate.get("mean_top3_probability", candidate.get("probability", 0.0)))
                    for candidate in candidates
                ],
                dtype=np.float64,
            )
        if self._entity_scaler is None or self._entity_model is None:
            raise RuntimeError("logistic entity scoring is configured without its models")
        features = pd.DataFrame(
            [
                {name: float(candidate.get(name, 0.0)) for name in ENTITY_FEATURE_COLS}
                for candidate in candidates
            ]
        )
        raw = np.asarray(
            self._entity_model.predict_proba(self._entity_scaler.transform(features))[:, 1],
            dtype=np.float64,
        )
        if self._entity_calibrator is None:
            return raw
        return np.asarray(
            self._entity_calibrator.predict_proba(logit_clip(raw).reshape(-1, 1))[:, 1],
            dtype=np.float64,
        )

    @classmethod
    def _data_fingerprint(cls, frame: pd.DataFrame) -> str:
        return _data_fingerprint(frame)

    @classmethod
    def load(
        cls,
        model_dir: str | Path = "models/current",
        data_dir: str | Path = "data/current",
        config_snapshot: ConfigSnapshot | None = None,
    ) -> NayoseEngine:
        config = EngineSettings(
            model_dir=Path(model_dir),
            data_dir=Path(data_dir),
            config_snapshot=config_snapshot,
        )
        return _load_bundle(config)

    @classmethod
    def _guard_generation_match(cls, config: Any, reference: pd.DataFrame) -> str:
        return _guard_generation_match(config, reference)

    def _build_block_indexes(self, reference: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
        return _build_block_indexes(reference)

    def close(self) -> None:
        close_query_encoder()


# ─── Convenience ──────────────────────────────────────────────────────────────


def _apply_manifest_embedding_model(config: EngineSettings) -> None:
    """Use the EXACT embedding model the bundle was encoded with (serve parity)."""
    manifest_path = config.model_dir / "run_manifest.json"
    if manifest_path.exists():
        embedded = json.loads(manifest_path.read_text(encoding="utf-8")).get("embedding_model")
        if embedded:
            configure_query_encoder(str(embedded))
            print(f"embedding model from bundle: {embedded}")


def _apply_manifest_thresholds(config: EngineSettings) -> EngineSettings:
    """Use trained thresholds from the bundle while preserving immutable settings."""
    manifest_path = config.model_dir / "run_manifest.json"
    if not manifest_path.exists():
        return config
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    thresholds = manifest.get("thresholds", {})
    bundle_config = manifest.get("config", {})
    policy = config.inference
    if isinstance(bundle_config, dict):
        if "entity_margin_threshold" in bundle_config:
            policy = replace(
                policy, entity_margin_threshold=float(bundle_config["entity_margin_threshold"])
            )
        if "minimum_evidence_families" in bundle_config:
            policy = replace(
                policy,
                minimum_evidence_families=int(bundle_config["minimum_evidence_families"]),
            )
        if "minimum_reference_density" in bundle_config:
            policy = replace(
                policy,
                minimum_reference_density=float(bundle_config["minimum_reference_density"]),
            )
        if "dense_profile" in bundle_config:
            policy = replace(policy, dense_profile=str(bundle_config["dense_profile"]))
    if not config.entity_override:
        if "entity_auto" in thresholds:
            policy = replace(policy, entity_auto_threshold=float(thresholds["entity_auto"]))
        if "entity_review" in thresholds:
            policy = replace(policy, entity_review_threshold=float(thresholds["entity_review"]))
    if "entity_auto" in thresholds:
        print(
            f"thresholds from manifest: entity_auto={thresholds['entity_auto']:.4f} "
            f"entity_review={thresholds.get('entity_review', thresholds.get('entity_auto', 0.0)):.4f}"
        )
    return config.with_inference(policy)


def load_engine(
    model_dir: str = "models/current",
    data_dir: str = "data/current",
    config_snapshot: ConfigSnapshot | None = None,
) -> NayoseEngine:
    return NayoseEngine.load(model_dir, data_dir, config_snapshot=config_snapshot)
