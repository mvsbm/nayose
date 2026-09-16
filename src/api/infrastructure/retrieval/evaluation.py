"""Execution stages for same-fingerprint retrieval ablations."""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from api.domain.evidence import VISIBLE_FIELDS
from api.domain.features import compute_pair_features_frame
from api.domain.normalize import normalize_frame, normalize_name, normalize_record
from api.infrastructure.matching.scoring import CandidateAggregator, DecisionPolicy, PairScorer
from api.infrastructure.retrieval.ablation import (
    _LARGE_ENTITY_MIN_RECORDS,
    ABLATION_NAMES,
    AblationReport,
    _field_hybrid_frames,
    _field_mode_frames,
    _metrics,
    _peak_rss_mb,
    _percentile,
    _ranked_rows,
    _score_auto,
    _target_entities,
)
from api.infrastructure.retrieval.bundle_retrieval import BundleRetriever
from api.infrastructure.retrieval.sparse_retrieval import DEFAULT_BATCH_SIZE
from api.infrastructure.runtime.engine import load_engine

_RECORD_DENSE_MODES = {"svd", "dense_only", "full_record"}


@dataclass(slots=True)
class EvaluationContext:
    model_dir: Path
    data_dir: Path
    fingerprint: str
    manifest: dict[str, Any]
    engine: Any
    retriever: BundleRetriever
    reference: pd.DataFrame
    index: Any
    query_items: list[tuple[str, dict[str, Any]]]
    normalized_queries: list[dict[str, str]]
    targets: dict[str, set[str]]
    record_to_entity: dict[str, str]
    scenarios: dict[str, str]
    splits: dict[str, str]
    reference_name_counts: dict[str, int]
    entity_sizes: dict[str, int]


@dataclass(slots=True)
class EvaluationCaches:
    field_frames: dict[str, list[pd.DataFrame]]
    record_vectors: np.ndarray | None
    completed: dict[str, dict[str, Any]]
    normalized_records: dict[int, tuple[Any, ...]] = field(default_factory=dict)
    normalized_columns: tuple[str, ...] = ()
    normalized_frame: pd.DataFrame | None = None
    normalized_positions: dict[int, int] = field(default_factory=dict)


@dataclass(slots=True)
class BatchResult:
    rows: list[dict[str, Any]]
    auto_total: int
    auto_correct: int
    false_auto: int
    latencies: list[float]


def _load_context(model_dir: Path, data_dir: Path, query_limit: int | None) -> EvaluationContext:
    model_dir = model_dir.resolve()
    data_dir = data_dir.resolve()
    manifest = _read_json(model_dir / "run_manifest.json")
    data_manifest = _read_json(data_dir / "generation_manifest.json")
    fingerprint = str(manifest.get("data_fingerprint", ""))
    if not fingerprint or fingerprint != str(data_manifest.get("data_fingerprint", "")):
        raise ValueError("model and data fingerprints must match")
    engine = load_engine(model_dir=str(model_dir), data_dir=str(data_dir))
    retriever = BundleRetriever(engine, manifest, model_dir)
    if engine._index is None or engine._reference is None:
        raise RuntimeError("bundle has no retrieval index or reference records")
    reference = engine._reference
    evaluation = pd.read_parquet(data_dir / "ground_truth" / "evaluation_truth.parquet")
    record_truth = pd.read_parquet(data_dir / "ground_truth" / "record_truth.parquet")
    queries = pd.read_parquet(data_dir / "incoming_queries.parquet")
    mapping = pd.read_parquet(model_dir / "record_to_nayose.parquet")
    record_to_entity = dict(
        zip(mapping["record_id"].astype(str), mapping["nayose_id"].astype(str), strict=True)
    )
    targets = _target_entities(evaluation, record_truth, record_to_entity)
    evaluation_rows = evaluation.to_dict("records")
    scenarios = {
        str(row["query_id"]): str(row.get("scenario", "unknown")) for row in evaluation_rows
    }
    splits = {str(row["query_id"]): str(row.get("split", "unknown")) for row in evaluation_rows}
    if query_limit is not None:
        queries = queries.head(query_limit)
    query_items = [(str(row["query_id"]), row) for row in queries.to_dict("records")]
    normalized_queries = [normalize_record(raw) for _, raw in query_items]
    name_counts = reference["name_kanji"].map(normalize_name).value_counts().to_dict()
    entity_sizes = mapping["nayose_id"].astype(str).value_counts().to_dict()
    return EvaluationContext(
        model_dir=model_dir,
        data_dir=data_dir,
        fingerprint=fingerprint,
        manifest=manifest,
        engine=engine,
        retriever=retriever,
        reference=reference,
        index=engine._index,
        query_items=query_items,
        normalized_queries=normalized_queries,
        targets=targets,
        record_to_entity=record_to_entity,
        scenarios=scenarios,
        splits=splits,
        reference_name_counts={str(key): int(value) for key, value in name_counts.items()},
        entity_sizes={str(key): int(value) for key, value in entity_sizes.items()},
    )


def _read_json(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _available_modes(context: EvaluationContext) -> dict[str, bool]:
    return {
        name: (
            name in {"sparse_only", "hybrid", "field_hybrid"}
            or (
                name in _RECORD_DENSE_MODES
                and context.index.dense_nn is not None
                and (name != "svd" or context.retriever.svd is not None)
            )
            or name in context.index.dense_views
        )
        for name in ABLATION_NAMES
    }


def _cached_record_vectors(context: EvaluationContext, caches: EvaluationCaches) -> np.ndarray:
    if caches.record_vectors is None:
        caches.record_vectors = context.retriever.encode_many(
            [query["retrieval_text"] for query in context.normalized_queries]
        )
    return caches.record_vectors


def _cached_field_frames(
    context: EvaluationContext,
    caches: EvaluationCaches,
    view: str,
    start: int,
    batch_size: int,
) -> list[pd.DataFrame]:
    frames = caches.field_frames.get(view)
    if frames is None:
        frames = _field_mode_frames(view, context.normalized_queries, context.retriever)
        caches.field_frames[view] = frames
    return frames[start : start + batch_size]


def _retrieve_batch_frames(
    context: EvaluationContext,
    caches: EvaluationCaches,
    name: str,
    start: int,
    batch_queries: list[dict[str, str]],
) -> list[pd.DataFrame]:
    index = context.index
    if name == "sparse_only":
        return index.retrieve_sparse_many(batch_queries)
    if name == "hybrid":
        sparse = index.retrieve_many(
            batch_queries, exact_mode="hybrid", use_dense=False, settings={}
        )
        record = index.retrieve_dense_record_many(
            _cached_record_vectors(context, caches)[start : start + len(batch_queries)],
            batch_queries,
            {"dense_top_k": 20, "candidate_cap_per_record": 20},
        )
        return _field_hybrid_frames(batch_queries, context.retriever, sparse, {"record": record})
    if name in _RECORD_DENSE_MODES:
        return index.retrieve_dense_record_many(
            _cached_record_vectors(context, caches)[start : start + len(batch_queries)],
            batch_queries,
            {"dense_top_k": 20, "candidate_cap_per_record": 20},
        )
    if name in {"name", "address", "alias"}:
        return _cached_field_frames(context, caches, name, start, len(batch_queries))
    if name == "field_hybrid":
        sparse = index.retrieve_many(
            batch_queries, exact_mode="hybrid", use_dense=False, settings={}
        )
        views = {
            view: _cached_field_frames(context, caches, view, start, len(batch_queries))
            for view in ("name", "address", "alias")
            if view in index.dense_views
        }
        return _field_hybrid_frames(batch_queries, context.retriever, sparse, views)
    raise ValueError(f"unsupported ablation mode {name!r}")


def _query_row(
    context: EvaluationContext,
    query_id: str,
    raw: dict[str, Any],
    query: dict[str, str],
    frame: pd.DataFrame,
    scorer: PairScorer,
    aggregator: CandidateAggregator,
    policy: DecisionPolicy,
    normalized: pd.DataFrame,
    raw_candidates: pd.DataFrame,
    features: pd.DataFrame | None = None,
    scores: np.ndarray | None = None,
) -> tuple[dict[str, Any], int, int, int]:
    candidates = _ranked_rows(
        frame, context.reference, context.record_to_entity, raw=raw_candidates
    )
    target = context.targets.get(query_id, set())
    top_entity, auto, action = _score_auto(
        query,
        frame,
        context.engine,
        context.reference,
        scorer=scorer,
        aggregator=aggregator,
        policy=policy,
        normalized=normalized,
        features=features,
        scores=scores,
        raw=raw_candidates,
    )
    is_correct = bool(top_entity and top_entity in target)
    auto_count = int(bool(auto))
    auto_correct = int(bool(auto and is_correct))
    missing_count = sum(
        not query.get(field, "")
        for field in (
            "n_name_loose",
            "n_kana",
            "n_romaji",
            "n_dob",
            "n_postal",
            "n_address",
            "n_phone",
            "n_email",
        )
    )
    name_frequency = context.reference_name_counts.get(query.get("n_name_loose", ""), 0)
    target_size = max((context.entity_sizes.get(entity_id, 0) for entity_id in target), default=0)
    row = {
        "query_id": query_id,
        "scenario": context.scenarios.get(query_id, "unknown"),
        "split": context.splits.get(query_id, "unknown"),
        "channel": str(raw.get("channel", "unknown")),
        "missingness": (
            "0-1_missing"
            if missing_count <= 1
            else "2-3_missing"
            if missing_count <= 3
            else "4+_missing"
        ),
        "name_frequency": "rare" if name_frequency <= 2 else "common",
        "entity_size": (
            "unknown"
            if target_size == 0
            else "large"
            if target_size >= _LARGE_ENTITY_MIN_RECORDS
            else "small"
        ),
        "target_entities": sorted(target),
        "candidate_recall": bool(target & {item["entity_id"] for item in candidates}),
        "action": action,
        "auto_link": bool(auto),
        "auto_correct": bool(auto and is_correct),
        "candidates": candidates,
    }
    return row, auto_count, auto_correct, auto_count - auto_correct


def _normalized_candidate_frames(
    context: EvaluationContext,
    frames: list[pd.DataFrame],
    caches: EvaluationCaches | None = None,
) -> list[pd.DataFrame]:
    candidate_arrays = [
        frame["candidate_index"].to_numpy(dtype=np.int64, copy=False)
        for frame in frames
        if not frame.empty
    ]
    if not candidate_arrays:
        return [pd.DataFrame() for _ in frames]
    unique_candidates = np.unique(np.concatenate(candidate_arrays))
    if caches is None:
        raw = context.reference.iloc[unique_candidates].reindex(
            columns=VISIBLE_FIELDS, fill_value=""
        )
        normalized = normalize_frame(raw.reset_index(drop=True), "record_id")
        positions = pd.Series(
            np.arange(len(unique_candidates), dtype=np.int64), index=unique_candidates
        )
        return [
            normalized.iloc[
                positions.loc[frame["candidate_index"].to_numpy(dtype=np.int64)].to_numpy()
            ].reset_index(drop=True)
            if not frame.empty
            else pd.DataFrame()
            for frame in frames
        ]
    missing = np.asarray(
        [
            candidate
            for candidate in unique_candidates
            if int(candidate) not in caches.normalized_positions
        ],
        dtype=np.int64,
    )
    if len(missing):
        raw = context.reference.iloc[missing].reindex(columns=VISIBLE_FIELDS, fill_value="")
        normalized = normalize_frame(raw.reset_index(drop=True), "record_id")
        start = len(caches.normalized_frame) if caches.normalized_frame is not None else 0
        caches.normalized_frame = (
            normalized
            if caches.normalized_frame is None
            else pd.concat([caches.normalized_frame, normalized], ignore_index=True)
        )
        caches.normalized_columns = tuple(normalized.columns)
        for offset, (candidate, row) in enumerate(
            zip(missing, normalized.itertuples(index=False, name=None), strict=True)
        ):
            caches.normalized_positions[int(candidate)] = start + offset
            caches.normalized_records[int(candidate)] = tuple(row)
    if caches.normalized_frame is None or not caches.normalized_columns:
        raise ValueError("normalized candidate cache has no schema")
    positions = [
        np.asarray(
            [caches.normalized_positions[int(candidate)] for candidate in frame["candidate_index"]],
            dtype=np.int64,
        )
        if not frame.empty
        else np.empty(0, dtype=np.int64)
        for frame in frames
    ]
    combined = caches.normalized_frame.take(np.concatenate(positions)).reset_index(drop=True)
    offsets: np.ndarray = np.cumsum([0, *map(len, positions)], dtype=np.int64)
    return [
        combined.iloc[offsets[position] : offsets[position + 1]].reset_index(drop=True)
        if len(values)
        else pd.DataFrame()
        for position, values in enumerate(positions)
    ]


def _raw_candidate_frames(
    reference: pd.DataFrame, frames: Sequence[pd.DataFrame]
) -> list[pd.DataFrame]:
    """Materialize one batch of candidate records with a single dataframe take."""
    positions = [
        frame["candidate_index"].to_numpy(dtype=np.int64, copy=False)
        if not frame.empty
        else np.empty(0, dtype=np.int64)
        for frame in frames
    ]
    if not any(len(values) for values in positions):
        return [pd.DataFrame() for _ in frames]
    combined = reference.take(np.concatenate(positions)).reset_index(drop=True)
    offsets: np.ndarray = np.cumsum([0, *map(len, positions)], dtype=np.int64)
    return [
        combined.iloc[offsets[position] : offsets[position + 1]].reset_index(drop=True)
        if len(values)
        else pd.DataFrame()
        for position, values in enumerate(positions)
    ]


def _score_batch(
    context: EvaluationContext,
    batch_items: list[tuple[str, dict[str, Any]]],
    batch_queries: list[dict[str, str]],
    frames: list[pd.DataFrame],
    normalized_frames: list[pd.DataFrame],
    raw_candidate_frames: list[pd.DataFrame],
    retrieval_ms: float,
    scorer: PairScorer,
    aggregator: CandidateAggregator,
    policy: DecisionPolicy,
) -> BatchResult:
    feature_frames = [pd.DataFrame() for _ in frames]
    feature_positions = [position for position, frame in enumerate(frames) if not frame.empty]
    if feature_positions:
        left = pd.concat(
            [
                pd.DataFrame([batch_queries[position]] * len(frames[position]))
                for position in feature_positions
            ],
            ignore_index=True,
        )
        right = pd.concat(
            [normalized_frames[position] for position in feature_positions], ignore_index=True
        )
        retrieval = pd.concat(
            [frames[position] for position in feature_positions], ignore_index=True
        )
        combined_features = compute_pair_features_frame(left, right, retrieval)
        feature_offsets: np.ndarray = np.cumsum(
            [0, *[len(frames[position]) for position in feature_positions]], dtype=np.int64
        )
        for offset, position in enumerate(feature_positions):
            feature_frames[position] = combined_features.iloc[
                feature_offsets[offset] : feature_offsets[offset + 1]
            ].reset_index(drop=True)
    else:
        combined_features = pd.DataFrame()
        feature_offsets = np.zeros(1, dtype=np.int64)
    combined_scores = (
        scorer.score(combined_features) if not combined_features.empty else np.empty(0)
    )
    score_offsets: np.ndarray = np.zeros(len(frames) + 1, dtype=np.int64)
    for _offset, position in enumerate(feature_positions):
        score_offsets[position + 1 :] += len(frames[position])
    rows: list[dict[str, Any]] = []
    auto_total = auto_correct = false_auto = 0
    latencies: list[float] = []
    for position, ((query_id, raw), query) in enumerate(
        zip(batch_items, batch_queries, strict=True)
    ):
        started = time.perf_counter()
        row, auto_count, correct_count, false_count = _query_row(
            context,
            query_id,
            raw,
            query,
            frames[position],
            scorer,
            aggregator,
            policy,
            normalized=normalized_frames[position],
            raw_candidates=raw_candidate_frames[position],
            features=feature_frames[position],
            scores=combined_scores[score_offsets[position] : score_offsets[position + 1]],
        )
        rows.append(row)
        auto_total += auto_count
        auto_correct += correct_count
        false_auto += false_count
        latencies.append(retrieval_ms + (time.perf_counter() - started) * 1000)
    return BatchResult(rows, auto_total, auto_correct, false_auto, latencies)


def _mode_report(
    context: EvaluationContext,
    name: str,
    execution_key: str,
    rows: list[dict[str, Any]],
    auto_total: int,
    auto_correct: int,
    false_auto: int,
    latencies: list[float],
    started: float,
    batch_size: int,
) -> dict[str, Any]:
    return {
        "mode": name,
        "available": True,
        "execution_key": execution_key,
        "metrics": _metrics(rows, context.targets),
        "candidate_recall": sum(row["candidate_recall"] for row in rows) / max(len(rows), 1),
        "auto_link_precision": auto_correct / auto_total if auto_total else None,
        "false_auto_rate": false_auto / max(len(rows), 1),
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
            "semantics": "amortized_batch"
            if name in {"sparse_only", "hybrid", "field_hybrid", *_RECORD_DENSE_MODES}
            else "per_query",
            "batch_size": batch_size,
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "peak_rss_mb": round(_peak_rss_mb(), 2),
        "provenance": rows[:20],
    }


def _evaluate_mode(
    context: EvaluationContext,
    caches: EvaluationCaches,
    name: str,
    batch_size: int,
) -> dict[str, Any]:
    execution_key = "record_dense" if name in _RECORD_DENSE_MODES else name
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    auto_total = auto_correct = false_auto = 0
    latencies: list[float] = []
    scorer = PairScorer(context.engine)
    aggregator = CandidateAggregator(context.engine)
    policy = DecisionPolicy(context.engine, object())
    for start in range(0, len(context.query_items), batch_size):
        batch_items = context.query_items[start : start + batch_size]
        batch_queries = context.normalized_queries[start : start + batch_size]
        retrieval_started = time.perf_counter()
        frames = _retrieve_batch_frames(context, caches, name, start, batch_queries)
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000 / max(len(batch_items), 1)
        normalized_frames = _normalized_candidate_frames(context, frames, caches)
        raw_candidate_frames = _raw_candidate_frames(context.reference, frames)
        result = _score_batch(
            context,
            batch_items,
            batch_queries,
            frames,
            normalized_frames,
            raw_candidate_frames,
            retrieval_ms,
            scorer,
            aggregator,
            policy,
        )
        rows.extend(result.rows)
        auto_total += result.auto_total
        auto_correct += result.auto_correct
        false_auto += result.false_auto
        latencies.extend(result.latencies)
    report = _mode_report(
        context,
        name,
        execution_key,
        rows,
        auto_total,
        auto_correct,
        false_auto,
        latencies,
        started,
        batch_size,
    )
    caches.completed[execution_key] = report
    return report


def _requested_modes(modes: Sequence[str] | None) -> set[str]:
    requested = set(modes or ABLATION_NAMES)
    unknown = requested - set(ABLATION_NAMES)
    if unknown:
        raise ValueError(f"unknown ablation modes: {sorted(unknown)}")
    return requested


def run_evaluation(
    model_dir: Path,
    data_dir: Path,
    *,
    query_limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    modes: Sequence[str] | None = None,
) -> AblationReport:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    context = _load_context(model_dir, data_dir, query_limit)
    caches = EvaluationCaches({}, None, {})
    available = _available_modes(context)
    requested = _requested_modes(modes)
    reports: list[dict[str, Any]] = []
    try:
        for name in ABLATION_NAMES:
            if name not in requested:
                continue
            if not available[name]:
                reports.append({"mode": name, "available": False, "reason": "artifact unavailable"})
                continue
            execution_key = "record_dense" if name in _RECORD_DENSE_MODES else name
            previous = caches.completed.get(execution_key)
            if previous is not None:
                alias = dict(previous)
                alias["mode"] = name
                alias["alias_of"] = previous["mode"]
                reports.append(alias)
                continue
            reports.append(_evaluate_mode(context, caches, name, batch_size))
    finally:
        context.retriever.close()
        context.engine.close()
    return AblationReport(
        context.fingerprint, str(context.model_dir), str(context.data_dir), reports
    )
