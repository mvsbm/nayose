"""Bounded candidate retrieval used during model training."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

import numpy as np
import pandas as pd

ArrayParts: TypeAlias = tuple[
    list[np.ndarray], list[np.ndarray], list[np.ndarray], list[np.ndarray]
]


class CandidateIndex(Protocol):
    reference: pd.DataFrame
    block_indexes: dict[str, dict[str, np.ndarray]]
    reference_embeddings: np.ndarray | None

    def sparse_candidates(
        self, query_texts: list[str], k: int
    ) -> tuple[np.ndarray, np.ndarray]: ...

    def dense_reference_candidates(
        self, start: int, stop: int, k: int
    ) -> tuple[np.ndarray, np.ndarray]: ...


@dataclass(frozen=True, slots=True)
class CandidateGraphConfig:
    exact_mode: str = "hybrid"
    exact_block_cap: int = 80
    sparse_top_k: int = 24
    dense_top_k: int = 16
    sparse_batch_size: int = 512
    candidate_cap_per_record: int = 80


def _empty_pair_values() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.float32),
        np.empty(0, dtype=np.int64),
    )


def _collapse(
    lo_parts: list[np.ndarray],
    hi_parts: list[np.ndarray],
    score_parts: list[np.ndarray],
    rank_parts: list[np.ndarray],
    row_count: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not lo_parts:
        return _empty_pair_values()
    left = np.concatenate(lo_parts)
    right = np.concatenate(hi_parts)
    scores = np.concatenate(score_parts)
    ranks = np.concatenate(rank_parts)
    pair_ids = left * row_count + right
    order = np.argsort(pair_ids, kind="stable")
    pair_ids, scores, ranks = pair_ids[order], scores[order], ranks[order]
    unique_ids, starts = np.unique(pair_ids, return_index=True)
    counts: np.ndarray = np.diff(np.append(starts, len(pair_ids)))
    best_scores = np.maximum.reduceat(scores, starts)
    groups: np.ndarray = np.repeat(np.arange(len(starts)), counts)
    kept = best_scores[groups] == scores
    max_rank = np.iinfo(np.int64).max
    best_ranks = np.minimum.reduceat(np.where(kept, ranks, max_rank), starts)
    return unique_ids, best_scores, best_ranks


def _exact_pairs(
    index: CandidateIndex, config: CandidateGraphConfig
) -> tuple[np.ndarray, np.ndarray, int, int]:
    row_count = len(index.reference)
    pair_parts: list[np.ndarray] = []
    mask_parts: list[np.ndarray] = []
    used = 0
    skipped = 0
    for block_position, (block_name, groups) in enumerate(index.block_indexes.items()):
        used_block = 0
        skipped_block = 0
        block_mask = np.uint16(1 << block_position)
        for members in groups.values():
            size = len(members)
            if size < 2:
                continue
            if size > config.exact_block_cap:
                skipped_block += 1
                continue
            used_block += 1
            if config.exact_mode == "fuzzy_only":
                continue
            left, right = np.triu_indices(size, 1)
            pair_parts.append(
                members[left].astype(np.int64) * row_count + members[right].astype(np.int64)
            )
            mask_parts.append(np.full(left.size, block_mask, dtype=np.uint16))
        used += used_block
        skipped += skipped_block
        print(
            f"  {block_name:<20} multi-record blocks used={used_block:,}, "
            f"skipped-over-cap={skipped_block:,}"
        )
    if not pair_parts:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.uint16), used, skipped
    pair_ids = np.concatenate(pair_parts)
    masks = np.concatenate(mask_parts)
    order = np.argsort(pair_ids, kind="stable")
    pair_ids, masks = pair_ids[order], masks[order]
    unique_ids, starts = np.unique(pair_ids, return_index=True)
    return unique_ids, np.bitwise_or.reduceat(masks, starts), used, skipped


def _collect_sparse(
    index: CandidateIndex, config: CandidateGraphConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    row_count = len(index.reference)
    if config.exact_mode == "exact_only":
        return _empty_pair_values()
    left_parts: list[np.ndarray] = []
    right_parts: list[np.ndarray] = []
    score_parts: list[np.ndarray] = []
    rank_parts: list[np.ndarray] = []
    texts = index.reference.retrieval_text.tolist()
    top_k = min(config.sparse_top_k + 1, row_count)
    for start in range(0, row_count, config.sparse_batch_size):
        stop = min(start + config.sparse_batch_size, row_count)
        ids, scores = index.sparse_candidates(texts[start:stop], top_k)
        source: np.ndarray = np.arange(start, stop, dtype=np.int64)[:, None]
        valid = (ids >= 0) & (ids != source)
        left = np.minimum(np.broadcast_to(source, ids.shape), ids.astype(np.int64))
        right = np.maximum(np.broadcast_to(source, ids.shape), ids.astype(np.int64))
        ranks = np.cumsum(valid, axis=1).astype(np.int64)
        left_parts.append(left[valid])
        right_parts.append(right[valid])
        score_parts.append(scores[valid])
        rank_parts.append(ranks[valid])
        if start == 0 or stop == row_count:
            print(f"  sparse progress {stop:>9,}/{row_count:,}")
    return _collapse(left_parts, right_parts, score_parts, rank_parts, row_count)


def _collect_dense(
    index: CandidateIndex, config: CandidateGraphConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    row_count = len(index.reference)
    if config.exact_mode == "exact_only" or index.reference_embeddings is None:
        return _empty_pair_values()
    left_parts: list[np.ndarray] = []
    right_parts: list[np.ndarray] = []
    score_parts: list[np.ndarray] = []
    rank_parts: list[np.ndarray] = []
    top_k = min(config.dense_top_k + 1, row_count)
    for start in range(0, row_count, config.sparse_batch_size):
        stop = min(start + config.sparse_batch_size, row_count)
        ids, scores = index.dense_reference_candidates(start, stop, top_k)
        if ids.shape[1] == 0:
            continue
        source: np.ndarray = np.arange(start, stop, dtype=np.int64)[:, None]
        valid = (ids >= 0) & (ids != source)
        left = np.minimum(np.broadcast_to(source, ids.shape), ids.astype(np.int64))
        right = np.maximum(np.broadcast_to(source, ids.shape), ids.astype(np.int64))
        ranks = np.cumsum(valid, axis=1).astype(np.int64)
        left_parts.append(left[valid])
        right_parts.append(right[valid])
        score_parts.append(scores[valid])
        rank_parts.append(ranks[valid])
    return _collapse(left_parts, right_parts, score_parts, rank_parts, row_count)


def _lookup(
    source_ids: np.ndarray,
    source_values: np.ndarray,
    target_ids: np.ndarray,
    default: float | int = 0,
) -> np.ndarray:
    if len(source_ids) == 0:
        return np.full(len(target_ids), default, dtype=source_values.dtype)
    positions = np.searchsorted(source_ids, target_ids)
    safe_positions = positions.clip(max=len(source_ids) - 1)
    present = (positions < len(source_ids)) & (source_ids[safe_positions] == target_ids)
    result: np.ndarray = np.full(len(target_ids), default, dtype=source_values.dtype)
    result[present] = source_values[positions[present]]
    return result


def _empty_graph() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "left_index",
            "right_index",
            "left_record_id",
            "right_record_id",
            "exact_blocks",
            "exact_count",
            "sparse_score",
            "dense_score",
            "sparse_rank",
            "dense_rank",
            "retrieval_score",
            "channel_count",
        ]
    )


def build_candidate_graph(index: CandidateIndex, config: CandidateGraphConfig) -> pd.DataFrame:
    """Build a bounded union of exact, sparse, and dense candidate edges."""
    row_count = len(index.reference)
    if row_count < 1:
        return _empty_graph()
    print("1/3 Exact block pairs")
    exact_ids, exact_masks, _, _ = _exact_pairs(index, config)
    print(f"  Unique pairs after exact blocks: {len(exact_ids):,}")
    print("2/3 Character sparse nearest neighbours")
    sparse_ids, sparse_scores, sparse_ranks = _collect_sparse(index, config)
    print("3/3 Optional dense nearest neighbours")
    dense_ids, dense_scores, dense_ranks = _collect_dense(index, config)
    if len(exact_ids) + len(sparse_ids) + len(dense_ids) == 0:
        return _empty_graph()

    all_ids = np.unique(np.concatenate((exact_ids, sparse_ids, dense_ids)))
    exact_masks_for_all = _lookup(exact_ids, exact_masks, all_ids, default=0)
    sparse_scores_for_all = _lookup(sparse_ids, sparse_scores, all_ids)
    sparse_ranks_for_all = _lookup(sparse_ids, sparse_ranks, all_ids)
    dense_scores_for_all = _lookup(dense_ids, dense_scores, all_ids)
    dense_ranks_for_all = _lookup(dense_ids, dense_ranks, all_ids)
    block_names = tuple(index.block_indexes)
    mask_names = {
        mask: ",".join(
            sorted(name for position, name in enumerate(block_names) if mask & (1 << position))
        )
        for mask in range(1 << len(block_names))
    }
    exact_blocks = np.asarray([mask_names[int(mask)] for mask in exact_masks_for_all], dtype=object)
    exact_counts: np.ndarray = np.asarray(
        [int(mask).bit_count() for mask in exact_masks_for_all], dtype=np.int16
    )
    left = all_ids // row_count
    right = all_ids % row_count
    record_ids = index.reference["record_id"].astype(str).to_numpy()
    frame = pd.DataFrame(
        {
            "left_index": left,
            "right_index": right,
            "left_record_id": record_ids[left],
            "right_record_id": record_ids[right],
            "exact_blocks": exact_blocks,
            "exact_count": exact_counts,
            "sparse_score": sparse_scores_for_all,
            "dense_score": dense_scores_for_all,
            "sparse_rank": sparse_ranks_for_all,
            "dense_rank": dense_ranks_for_all,
        }
    )
    frame["retrieval_score"] = frame[["sparse_score", "dense_score"]].max(axis=1) + (
        0.08 * frame.exact_count.clip(upper=3)
    )
    frame["channel_count"] = (
        frame.exact_count.gt(0).astype(int)
        + frame.sparse_score.gt(0).astype(int)
        + frame.dense_score.gt(0).astype(int)
    )
    exact = frame.loc[frame.exact_count.gt(0)]
    fuzzy = frame.loc[frame.exact_count.eq(0)].sort_values("retrieval_score", ascending=False)
    from_left = fuzzy.groupby("left_index", sort=False).head(config.candidate_cap_per_record)
    from_right = fuzzy.groupby("right_index", sort=False).head(config.candidate_cap_per_record)
    return (
        pd.concat([exact, from_left, from_right], ignore_index=True)
        .drop_duplicates(["left_index", "right_index"])
        .sort_values(["retrieval_score", "channel_count"], ascending=False)
        .reset_index(drop=True)
    )
