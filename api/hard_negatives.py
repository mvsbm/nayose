"""Deterministic hard-negative selection for pair-model training."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

_CATEGORY_NAMES = (
    "same_name",
    "similar_address",
    "missing_contact",
    "noisy_channel",
    "common_name",
)


def select_hard_negatives(
    candidate_pairs: pd.DataFrame,
    reference: pd.DataFrame,
    negative_rows: np.ndarray,
    limit: int,
    *,
    frequency_maps: Mapping[str, Mapping[str, int]] | None = None,
) -> np.ndarray:
    """Select high-scoring negatives while reserving space for hard categories."""
    if limit < 0:
        raise ValueError("hard-negative limit must not be negative")
    if limit == 0 or len(negative_rows) == 0:
        return np.empty(0, dtype=np.int64)
    if limit >= len(negative_rows):
        return np.asarray(negative_rows, dtype=np.int64)
    required = {"left_index", "right_index", "retrieval_score"}
    missing = required - set(candidate_pairs.columns)
    if missing:
        raise ValueError(f"candidate pairs are missing {sorted(missing)}")
    left_indices = candidate_pairs.loc[negative_rows, "left_index"].to_numpy(dtype=int)
    right_indices = candidate_pairs.loc[negative_rows, "right_index"].to_numpy(dtype=int)
    columns = ["n_name_loose", "n_address", "n_postal", "n_phone", "n_email", "channel"]
    available_columns = [column for column in columns if column in reference]
    left = reference.iloc[left_indices][available_columns]
    right = reference.iloc[right_indices][available_columns]
    categories = _category_masks(
        left.reset_index(drop=True), right.reset_index(drop=True), frequency_maps
    )
    scores = candidate_pairs.loc[negative_rows, "retrieval_score"].to_numpy(dtype=np.float32)
    selected: list[int] = []
    remaining: np.ndarray = np.ones(len(negative_rows), dtype=bool)
    active = [name for name in _CATEGORY_NAMES if categories[name].any()]
    quota = max(1, limit // max(len(active), 1))
    for name in active:
        eligible = np.flatnonzero(categories[name] & remaining)
        for position in eligible[np.argsort(-scores[eligible], kind="stable")[:quota]]:
            selected.append(int(negative_rows[position]))
            remaining[position] = False
            if len(selected) == limit:
                return np.asarray(selected, dtype=np.int64)
    remaining_positions = np.flatnonzero(remaining)
    order = np.argsort(-scores[remaining_positions], kind="stable")
    selected.extend(
        int(negative_rows[position])
        for position in remaining_positions[order[: limit - len(selected)]]
    )
    return np.asarray(selected, dtype=np.int64)


def _category_masks(
    left: pd.DataFrame,
    right: pd.DataFrame,
    frequency_maps: Mapping[str, Mapping[str, int]] | None,
) -> dict[str, np.ndarray]:
    left_name = _column(left, "n_name_loose")
    right_name = _column(right, "n_name_loose")
    left_address = _column(left, "n_address")
    right_address = _column(right, "n_address")
    left_postal = _column(left, "n_postal")
    right_postal = _column(right, "n_postal")
    left_phone = _column(left, "n_phone")
    right_phone = _column(right, "n_phone")
    left_email = _column(left, "n_email")
    right_email = _column(right, "n_email")
    channels = pd.concat([_column(left, "channel"), _column(right, "channel")], axis=1).astype(str)
    noisy_channel = channels.isin({"paper_ocr", "legacy_migration"}).any(axis=1).to_numpy()
    frequency = (frequency_maps or {}).get("n_name_loose", {})
    common_name = left_name.map(frequency).fillna(0).to_numpy() >= 20
    return {
        "same_name": (left_name.ne("") & left_name.eq(right_name)).to_numpy(),
        "similar_address": (
            (left_address.ne("") & left_address.eq(right_address))
            | (left_postal.ne("") & left_postal.eq(right_postal))
        ).to_numpy(),
        "missing_contact": (
            left_phone.eq("") | right_phone.eq("") | left_email.eq("") | right_email.eq("")
        ).to_numpy(),
        "noisy_channel": noisy_channel,
        "common_name": common_name,
    }


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame:
        return frame[name].fillna("").astype(str)
    return pd.Series("", index=frame.index, dtype="string")
