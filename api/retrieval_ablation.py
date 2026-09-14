"""Same-fingerprint retrieval ablations with identity and provenance metrics."""

from __future__ import annotations

import json
import resource
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from api.dense_views import DenseView, dense_view_queries
from api.evidence import visible_record
from api.features import compute_pair_features
from api.matching_components import CandidateAggregator, DecisionPolicy, PairScorer
from api.normalize import normalize_record
from api.retrieval import BundleRetriever
from api.retrieval_fusion import retrieval_fusion_score, retrieval_methods
from api.sparse_retrieval import DEFAULT_BATCH_SIZE

_LARGE_ENTITY_MIN_RECORDS = 5

ABLATION_NAMES = (
    "svd",
    "sparse_only",
    "dense_only",
    "full_record",
    "name",
    "address",
    "alias",
    "field_hybrid",
    "hybrid",
)


@dataclass(frozen=True, slots=True)
class AblationSpec:
    name: str
    available: bool
    unavailable_reason: str = ""


@dataclass(frozen=True, slots=True)
class AblationReport:
    fingerprint: str
    model_dir: str
    data_dir: str
    rows: list[dict[str, Any]]


def _peak_rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    bytes_used = value if sys.platform == "darwin" else value * 1024
    return bytes_used / (1024 * 1024)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return sorted(values)[min(len(values) - 1, max(0, int(len(values) * fraction) - 1))]


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _target_entities(
    evaluation: pd.DataFrame, record_truth: pd.DataFrame, record_to_entity: dict[str, str]
) -> dict[str, set[str]]:
    truth_records = record_truth.groupby("truth_id")["record_id"].apply(list).to_dict()
    targets: dict[str, set[str]] = {}
    for row in evaluation.to_dict("records"):
        truth_id = str(row.get("target_truth_id", ""))
        targets[str(row["query_id"])] = {
            record_to_entity[record_id]
            for record_id in truth_records.get(truth_id, [])
            if record_id in record_to_entity
        }
    return targets


def _ranked_rows(
    frame: pd.DataFrame, reference: pd.DataFrame, record_to_entity: dict[str, str]
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    seen_entities: set[str] = set()
    for rank, candidate in enumerate(frame.head(20).to_dict("records"), 1):
        index = int(candidate["candidate_index"])
        record = reference.iloc[index]
        record_id = str(record["record_id"])
        entity_id = record_to_entity.get(record_id, "")
        if not entity_id or entity_id in seen_entities:
            continue
        seen_entities.add(entity_id)
        rows.append(
            {
                "rank": rank,
                "record_id": record_id,
                "entity_id": entity_id,
                "score": float(
                    candidate.get(
                        "retrieval_fusion_score",
                        candidate.get("dense_score", candidate.get("retrieval_score", 0.0)),
                    )
                ),
                "methods": [str(item) for item in candidate.get("retrieval_methods", [])],
            }
        )
    return rows


def _metrics(
    rows: list[dict[str, Any]],
    targets: dict[str, set[str]],
) -> dict[str, Any]:
    by_query = {str(row["query_id"]): row for row in rows}
    grouped: dict[str, list[dict[str, Any]]] = {"overall": rows}
    for row in rows:
        grouped.setdefault(str(row.get("scenario", "unknown")), []).append(row)

    def one(items: list[dict[str, Any]]) -> dict[str, Any]:
        query_ids = list(dict.fromkeys(str(item["query_id"]) for item in items))
        existing = [query_id for query_id in query_ids if targets.get(query_id)]
        ranks = {
            query_id: min(
                (
                    candidate["rank"]
                    for candidate in by_query.get(query_id, {}).get("candidates", [])
                    if candidate["entity_id"] in targets[query_id]
                ),
                default=0,
            )
            for query_id in existing
        }
        correct = [rank for rank in ranks.values() if rank]
        auto_rows = [item for item in items if item.get("auto_link")]
        auto_correct = sum(bool(item.get("auto_correct")) for item in auto_rows)
        return {
            "queries": len(query_ids),
            "existing_queries": len(existing),
            "candidate_recall": sum(bool(item.get("candidate_recall")) for item in items)
            / max(len(items), 1),
            "recall_at_1": sum(rank == 1 for rank in correct) / max(len(existing), 1),
            "recall_at_5": sum(1 <= rank <= 5 for rank in correct) / max(len(existing), 1),
            "recall_at_20": sum(1 <= rank <= 20 for rank in correct) / max(len(existing), 1),
            "entity_accuracy": sum(rank == 1 for rank in correct) / max(len(existing), 1),
            "auto_link_precision": auto_correct / len(auto_rows) if auto_rows else None,
            "false_auto_rate": (len(auto_rows) - auto_correct) / max(len(items), 1),
            "no_match_rate": sum(item.get("action") == "NO_MATCH" for item in items)
            / max(len(items), 1),
            "review_rate": sum(item.get("action") == "REVIEW" for item in items)
            / max(len(items), 1),
        }

    metrics = {name: one(items) for name, items in grouped.items()}
    dimensions = ("channel", "missingness", "name_frequency", "entity_size", "split")
    metrics["slices"] = {
        dimension: {
            str(value): one([row for row in rows if row.get(dimension) == value])
            for value in sorted({str(row.get(dimension, "unknown")) for row in rows})
        }
        for dimension in dimensions
    }
    return metrics


def _field_mode_frames(
    view: str,
    queries: list[dict[str, str]],
    retriever: BundleRetriever,
) -> list[pd.DataFrame]:
    """Encode one field view per batch, preserving alias-to-record row mapping."""
    artifacts = retriever.index.dense_views
    if view not in artifacts:
        raise RuntimeError(f"dense view {view} is unavailable")
    requests: list[tuple[int, int]] = []
    texts: list[str] = []
    for query_index, query in enumerate(queries):
        for text in dense_view_queries(query, view):
            requests.append((query_index, len(texts)))
            texts.append(text)
    frames = [pd.DataFrame() for _ in queries]
    if not texts:
        return frames
    vectors = retriever.encode_many(texts)
    artifact = artifacts[view]
    positions: np.ndarray = np.asarray([position for _, position in requests], dtype=np.int32)
    matches = artifact.index.search(vectors[positions], 20)
    keys = np.asarray(matches.keys, dtype=np.int32)
    scores = 1.0 - np.asarray(matches.distances, dtype=np.float32)
    if keys.ndim == 1:
        keys, scores = keys.reshape(1, -1), scores.reshape(1, -1)
    states: list[dict[int, dict[str, Any]]] = [{} for _ in queries]
    for request_index, (query_index, _) in enumerate(requests):
        for rank, (key, score) in enumerate(
            zip(keys[request_index], scores[request_index], strict=True), 1
        ):
            candidate_index = (
                int(artifact.row_indices[int(key)])
                if artifact.row_indices is not None
                else int(key)
            )
            state = states[query_index].setdefault(
                candidate_index, {"dense_score": 0.0, "dense_rank": rank}
            )
            if float(score) > float(state["dense_score"]):
                state["dense_score"] = float(score)
                state["dense_rank"] = rank
    for query_index, values in enumerate(states):
        frame = pd.DataFrame(
            [
                {
                    "candidate_index": candidate_index,
                    **state,
                    "dense_view_scores": {view: float(state["dense_score"])},
                    "dense_view_ranks": {view: int(state["dense_rank"])},
                    "retrieval_methods": [f"dense:{view}"],
                }
                for candidate_index, state in values.items()
            ]
        )
        if frame.empty:
            frames[query_index] = frame
            continue
        frames[query_index] = (
            frame.sort_values(["dense_score", "candidate_index"], ascending=[False, True])
            .head(20)
            .reset_index(drop=True)
        )
    return frames


def _field_hybrid_frames(
    queries: list[dict[str, str]],
    retriever: BundleRetriever,
    sparse_frames: list[pd.DataFrame],
    view_frames: dict[str, list[pd.DataFrame]] | None = None,
) -> list[pd.DataFrame]:
    """Combine exact/sparse evidence with field-view dense candidates."""
    active_view_frames = view_frames or {
        view: _field_mode_frames(view, queries, retriever)
        for view in ("name", "address", "alias")
        if view in retriever.index.dense_views
    }
    combined: list[pd.DataFrame] = []
    for query_index, sparse in enumerate(sparse_frames):
        parts = [sparse] if not sparse.empty else []
        parts.extend(
            frames[query_index]
            for frames in active_view_frames.values()
            if not frames[query_index].empty
        )
        states: dict[int, dict[str, Any]] = {}
        for part in parts:
            for row in part.to_dict("records"):
                candidate_index = int(row["candidate_index"])
                state = states.setdefault(candidate_index, {})
                existing_blocks = state.get("exact_blocks", set())
                row_blocks = row.get("exact_blocks", [])
                state["exact_blocks"] = set(existing_blocks) | (
                    set(row_blocks) if isinstance(row_blocks, list) else set()
                )
                for field in ("sparse_score", "dense_score"):
                    state[field] = max(float(state.get(field, 0.0)), float(row.get(field, 0.0)))
                for field in ("sparse_rank", "dense_rank"):
                    rank = int(row.get(field, 0))
                    if rank > 0:
                        state[field] = min(int(state.get(field, rank)), rank)
                for field in ("dense_view_scores", "dense_view_ranks"):
                    values = row.get(field, {})
                    if isinstance(values, dict):
                        state.setdefault(field, {}).update(values)
        rows = []
        for candidate_index, state in states.items():
            state["exact_count"] = len(state.get("exact_blocks", set()))
            state["retrieval_score"] = max(
                float(state.get("sparse_score", 0.0)), float(state.get("dense_score", 0.0))
            ) + 0.08 * min(int(state["exact_count"]), 3)
            state["retrieval_fusion_score"] = retrieval_fusion_score(
                state, query=queries[query_index]
            )
            state["retrieval_methods"] = retrieval_methods(state)
            state["channel_count"] = (
                int(state.get("exact_count", 0) > 0)
                + int(state.get("sparse_score", 0.0) > 0)
                + int(state.get("dense_score", 0.0) > 0)
            )
            rows.append({"candidate_index": candidate_index, **state})
        combined.append(
            pd.DataFrame(rows)
            .sort_values(
                ["retrieval_fusion_score", "retrieval_score", "candidate_index"],
                ascending=[False, False, True],
            )
            .head(20)
            .reset_index(drop=True)
            if rows
            else pd.DataFrame()
        )
    return combined


def _mode_frame(
    name: str,
    query: dict[str, str],
    engine: Any,
    retriever: BundleRetriever,
) -> pd.DataFrame:
    index = engine._index
    if index is None:
        raise RuntimeError("bundle has no retrieval index")
    if name == "sparse_only":
        return index.retrieve(query, exact_mode="fuzzy_only", use_dense=False, settings={})
    if name == "hybrid":
        return index.retrieve(query, exact_mode="hybrid", use_dense=True, settings={})
    if name == "svd" and retriever.svd is None:
        raise RuntimeError("SVD artifact is unavailable")
    view = "record" if name in {"svd", "dense_only", "full_record"} else name
    if view != "record" and view not in index.dense_views:
        raise RuntimeError(f"dense view {view} is unavailable")
    if view == "record":
        vector = retriever.encode(query["retrieval_text"])[0]
        matches = index.dense_nn.search(vector.reshape(1, -1), 20)
        keys = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
        scores = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
        return pd.DataFrame(
            {
                "candidate_index": keys,
                "dense_score": scores,
                "dense_rank": np.arange(1, len(keys) + 1),
                "retrieval_methods": [[name] for _ in keys],
            }
        )
    artifact: DenseView = index.dense_views[view]
    texts = dense_view_queries(query, view)
    if not texts:
        return pd.DataFrame()
    states: dict[int, dict[str, Any]] = {}
    for text in texts:
        vector = retriever.encode(text)[0]
        matches = artifact.index.search(vector.reshape(1, -1), 20)
        keys = np.asarray(matches.keys, dtype=np.int32).reshape(-1)
        scores = 1.0 - np.asarray(matches.distances, dtype=np.float32).reshape(-1)
        for rank, (key, score) in enumerate(zip(keys, scores, strict=True), 1):
            candidate_index = (
                int(artifact.row_indices[int(key)])
                if artifact.row_indices is not None
                else int(key)
            )
            state = states.setdefault(candidate_index, {"dense_score": 0.0, "dense_rank": rank})
            if float(score) > float(state["dense_score"]):
                state["dense_score"] = float(score)
                state["dense_rank"] = rank
    return (
        pd.DataFrame(
            [
                {"candidate_index": key, **value, "retrieval_methods": [[name]]}
                for key, value in states.items()
            ]
        )
        .sort_values(["dense_score", "candidate_index"], ascending=[False, True])
        .head(20)
        .reset_index(drop=True)
    )


def _score_auto(
    query: dict[str, str],
    frame: pd.DataFrame,
    engine: Any,
    reference: pd.DataFrame,
    *,
    scorer: PairScorer | None = None,
    aggregator: CandidateAggregator | None = None,
    policy: DecisionPolicy | None = None,
) -> tuple[str | None, bool | None, str]:
    if frame.empty:
        return None, None, "NO_MATCH"
    raw = reference.iloc[frame["candidate_index"].astype(int).tolist()].reset_index(drop=True)
    normalized = pd.DataFrame(
        [normalize_record(visible_record(row)) for row in raw.to_dict("records")]
    )
    features = compute_pair_features(query, normalized, frame.reset_index(drop=True))
    active_scorer = scorer or PairScorer(engine)
    active_aggregator = aggregator or CandidateAggregator(engine)
    candidates = active_aggregator.build(query, raw, features, active_scorer.score(features), frame)
    if not candidates:
        return None, None, "NO_MATCH"
    active_policy = policy or DecisionPolicy(engine, object())
    result = active_policy.decide(candidates, len(frame))
    return (
        str(result["top_nayose_id"]),
        result["action"] == "AUTO_LINK",
        str(result["action"]),
    )


def evaluate(
    model_dir: Path,
    data_dir: Path,
    *,
    query_limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    modes: Sequence[str] | None = None,
) -> AblationReport:
    from api.retrieval_evaluation import run_evaluation

    return run_evaluation(
        model_dir,
        data_dir,
        query_limit=query_limit,
        batch_size=batch_size,
        modes=modes,
    )
