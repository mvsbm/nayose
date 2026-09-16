"""Conversion of retrieval channel arrays into serving candidate frames."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from api.infrastructure.retrieval.fusion import retrieval_fusion_score, retrieval_methods


def retrieve_index_without_dense(
    index: Any,
    queries: Sequence[dict[str, Any]],
    exact_mode: str,
    settings: dict[str, Any],
) -> list[pd.DataFrame]:
    matches = (
        index.sparse_backend.retrieve_many(
            [query.get("retrieval_text", "") for query in queries],
            min(
                int(settings.get("sparse_top_k", index.config.sparse_top_k)) + 1,
                len(index.reference),
            ),
        )
        if exact_mode != "exact_only"
        else None
    )
    return build_candidate_frames(
        queries,
        index.block_indexes,
        matches,
        None,
        exact_mode=exact_mode,
        exact_cap=int(settings.get("exact_block_cap", index.config.exact_block_cap)),
        candidate_cap=int(settings.get("candidate_cap_per_record", index.config.candidate_cap)),
    )


def build_candidate_frames(
    queries: Sequence[dict[str, Any]],
    block_indexes: dict[str, dict[str, np.ndarray]],
    sparse_matches: Sequence[tuple[np.ndarray, np.ndarray]] | None,
    dense_matches: Sequence[tuple[np.ndarray, np.ndarray]] | None,
    *,
    exact_mode: str,
    exact_cap: int,
    candidate_cap: int,
) -> list[pd.DataFrame]:
    """Merge retrieval results while preserving the single-query frame contract."""
    frames: list[pd.DataFrame] = []
    for position, query in enumerate(queries):
        states: dict[int, dict[str, Any]] = {}
        if exact_mode != "fuzzy_only":
            for block_name, groups in block_indexes.items():
                key = _block_key(query, block_name)
                if key and key in groups and len(groups[key]) <= exact_cap:
                    for candidate in groups[key]:
                        state = states.setdefault(int(candidate), {})
                        state.setdefault("exact_blocks", []).append(block_name)
        if sparse_matches is not None:
            _add_channel(states, sparse_matches[position], "sparse")
        if dense_matches is not None:
            _add_channel(states, dense_matches[position], "dense")
        frames.append(_frame_from_states(states, query, candidate_cap))
    return frames


def _add_channel(
    states: dict[int, dict[str, Any]],
    match: tuple[np.ndarray, np.ndarray],
    channel: str,
) -> None:
    indices, scores = match
    score_key = f"{channel}_score"
    rank_key = f"{channel}_rank"
    for rank, (candidate, score) in enumerate(zip(indices, scores, strict=True), 1):
        state = states.setdefault(int(candidate), {})
        previous = float(state.get(score_key, 0.0))
        state[score_key] = max(float(score), previous)
        if channel == "sparse" or float(score) > previous:
            state[rank_key] = rank


def _frame_from_states(
    states: dict[int, dict[str, Any]], query: dict[str, Any], candidate_cap: int
) -> pd.DataFrame:
    rows = []
    for candidate, state in states.items():
        exact_blocks = state.get("exact_blocks", [])
        exact_count = len(exact_blocks)
        sparse_score = float(state.get("sparse_score", 0.0))
        dense_score = float(state.get("dense_score", 0.0))
        rows.append(
            {
                "candidate_index": candidate,
                "exact_count": exact_count,
                "sparse_score": sparse_score,
                "dense_score": dense_score,
                "retrieval_score": max(sparse_score, dense_score) + 0.08 * min(exact_count, 3),
                "retrieval_fusion_score": retrieval_fusion_score(state, query=query),
                "dense_view_count": len(state.get("dense_view_scores", {})),
                "dense_view_scores": state.get("dense_view_scores", {}),
                "dense_view_ranks": state.get("dense_view_ranks", {}),
                "retrieval_methods": retrieval_methods(state),
                "channel_count": int(bool(exact_count))
                + int(sparse_score > 0)
                + int(dense_score > 0),
                "exact_blocks": exact_blocks,
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


def _block_key(query: dict[str, Any], block_name: str) -> str:
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
    fields = specs.get(block_name, ())
    values = [str(query.get(field, "")) for field in fields]
    return "¦".join(values) if all(values) else ""
