"""Pair features + calibrator helper — shared by training-adjacent engine code
and the API matching worker. Extracted so new serving code does not depend on
the legacy inference module (single source of truth; engine re-exports)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from rapidfuzz.fuzz import ratio


def _fuzzy(a: str, b: str) -> float:
    return ratio(a, b) / 100.0 if a and b else 0.0


def has_safe_identity_anchor(features: Mapping[str, float]) -> bool:
    """Return whether evidence has exact name plus DOB or email agreement."""
    name_exact = bool(features.get("name_strict_exact", 0) or features.get("name_loose_exact", 0))
    stable_exact = bool(features.get("dob_exact", 0) or features.get("email_exact", 0))
    return name_exact and stable_exact


PAIR_FEATURE_COLS = [
    "type_exact",
    "name_strict_exact",
    "name_loose_exact",
    "kana_exact",
    "romaji_exact",
    "dob_exact",
    "postal_exact",
    "address_exact",
    "phone_exact",
    "phone_suffix_exact",
    "email_exact",
    "name_similarity",
    "kana_similarity",
    "romaji_similarity",
    "address_similarity",
    "email_local_similarity",
    "email_domain_exact",
    "name_frequency_weight",
    "kana_frequency_weight",
    "phone_frequency_weight",
    "email_frequency_weight",
    "agreement_count",
    "evidence_family_count",
    "both_populated_count",
    "missing_fraction",
    "type_conflict",
    "dob_conflict",
    "postal_conflict",
    "name_dob_evidence",
    "name_address_evidence",
    "sparse_score",
    "dense_score",
    "exact_count",
    "channel_count",
    "retrieval_score",
    "reranker_score",
]

ENTITY_FEATURE_COLS = [
    "max_pair_probability",
    "second_pair_probability",
    "mean_top3_probability",
    "record_score_margin",
    "support_record_count",
    "support_agency_count",
    "candidate_entity_size",
    "max_name_similarity",
    "max_kana_similarity",
    "max_romaji_similarity",
    "max_address_similarity",
    "any_name_exact",
    "any_dob_exact",
    "any_phone_exact",
    "any_email_exact",
    "any_postal_exact",
    "any_identity_anchor",
    "max_evidence_family_count",
    "max_retrieval_score",
    "max_channel_count",
    "hard_conflict_count",
    "review_or_auto_record_count",
]


def compute_pair_features(
    query: dict[str, str], candidates: pd.DataFrame, retrieval: pd.DataFrame
) -> pd.DataFrame:
    """Compute all pair features between a query and each candidate."""
    if candidates.empty:
        return pd.DataFrame(columns=PAIR_FEATURE_COLS)
    left = pd.DataFrame([query] * len(candidates)).reset_index(drop=True)
    if "n_party_type" not in left:
        left["n_party_type"] = left["party_type"]
    if "n_party_type" not in candidates:
        candidates = candidates.copy()
        candidates["n_party_type"] = candidates["party_type"]
    return compute_pair_features_frame(left, candidates, retrieval)


def compute_pair_features_frame(
    left: pd.DataFrame,
    right: pd.DataFrame,
    retrieval: pd.DataFrame | None = None,
    frequency_maps: Mapping[str, Mapping[str, int]] | None = None,
) -> pd.DataFrame:
    """Compute pair features for aligned normalized record frames."""
    left, right = left.reset_index(drop=True), right.reset_index(drop=True)
    if len(left) != len(right):
        raise ValueError("left and right frames must have equal lengths")
    out = pd.DataFrame(index=np.arange(len(left)))

    exact_specs = {
        "type_exact": "n_party_type",
        "name_strict_exact": "n_name_strict",
        "name_loose_exact": "n_name_loose",
        "kana_exact": "n_kana",
        "romaji_exact": "n_romaji",
        "dob_exact": "n_dob",
        "postal_exact": "n_postal",
        "address_exact": "n_address",
        "phone_exact": "n_phone",
        "phone_suffix_exact": "n_phone_suffix8",
        "email_exact": "n_email",
    }
    for feature, column in exact_specs.items():
        out[feature] = (
            left[column].ne("") & right[column].ne("") & left[column].eq(right[column])
        ).astype(np.float32)

    def fuzzy_array(left_values: pd.Series, right_values: pd.Series) -> np.ndarray:
        return np.fromiter(
            (_fuzzy(a, b) for a, b in zip(left_values, right_values, strict=True)),
            dtype=np.float32,
            count=len(left_values),
        )

    out["name_similarity"] = np.maximum(
        fuzzy_array(left.n_name_loose, right.n_name_loose),
        fuzzy_array(left.n_name_signature, right.n_name_signature),
    )
    out["kana_similarity"] = fuzzy_array(left.n_kana, right.n_kana)
    out["romaji_similarity"] = fuzzy_array(left.n_romaji, right.n_romaji)
    out["address_similarity"] = fuzzy_array(left.n_address, right.n_address)
    out["email_local_similarity"] = fuzzy_array(left.n_email_local, right.n_email_local)
    out["email_domain_exact"] = (
        left.n_email_domain.ne("")
        & right.n_email_domain.ne("")
        & left.n_email_domain.eq(right.n_email_domain)
    ).astype(np.float32)

    frequency_maps = frequency_maps or {}
    for feature, column in (
        ("name_frequency_weight", "n_name_loose"),
        ("kana_frequency_weight", "n_kana"),
        ("phone_frequency_weight", "n_phone"),
        ("email_frequency_weight", "n_email"),
    ):
        frequencies = (
            left[column].map(frequency_maps.get(column, {}).get).fillna(0).to_numpy(np.float32)
        )
        out[feature] = out[column.removeprefix("n_") + "_exact"] / np.log2(frequencies + 2.0)

    agreement_columns = [
        "name_loose_exact",
        "kana_exact",
        "romaji_exact",
        "dob_exact",
        "postal_exact",
        "address_exact",
        "phone_exact",
        "email_exact",
    ]
    out["agreement_count"] = out[agreement_columns].sum(axis=1)
    evidence = pd.DataFrame(
        {
            "name": np.maximum.reduce(
                [
                    out.name_similarity.to_numpy(),
                    out.kana_similarity.to_numpy(),
                    out.romaji_similarity.to_numpy(),
                ]
            )
            >= 0.82,
            "birth": out.dob_exact.eq(1),
            "address": np.maximum(out.address_similarity, out.postal_exact) >= 0.82,
            "phone": np.maximum(out.phone_exact, out.phone_suffix_exact) >= 1,
            "email": np.maximum(out.email_exact, out.email_local_similarity) >= 0.90,
        }
    )
    out["evidence_family_count"] = evidence.sum(axis=1)

    identity_columns = [
        "n_name_loose",
        "n_kana",
        "n_romaji",
        "n_dob",
        "n_postal",
        "n_address",
        "n_phone",
        "n_email",
    ]
    both_populated = sum(
        (left[column].ne("") & right[column].ne("")).astype(int) for column in identity_columns
    )
    out["both_populated_count"] = both_populated
    out["missing_fraction"] = 1.0 - both_populated / len(identity_columns)
    out["type_conflict"] = (
        left.n_party_type.ne("")
        & right.n_party_type.ne("")
        & left.n_party_type.ne(right.n_party_type)
    ).astype(np.float32)
    out["dob_conflict"] = (
        left.n_dob.ne("") & right.n_dob.ne("") & left.n_dob.ne(right.n_dob)
    ).astype(np.float32)
    out["postal_conflict"] = (
        left.n_postal.ne("") & right.n_postal.ne("") & left.n_postal.ne(right.n_postal)
    ).astype(np.float32)
    best_name = np.maximum.reduce(
        [
            out.name_similarity.to_numpy(),
            out.kana_similarity.to_numpy(),
            out.romaji_similarity.to_numpy(),
        ]
    )
    out["name_dob_evidence"] = best_name * out.dob_exact
    out["name_address_evidence"] = best_name * np.maximum(out.address_similarity, out.postal_exact)
    for column in ("sparse_score", "dense_score", "exact_count", "reranker_score"):
        out[column] = (
            retrieval[column].to_numpy(np.float32)
            if retrieval is not None and column in retrieval
            else 0.0
        )
    if retrieval is not None and "channel_count" in retrieval:
        out["channel_count"] = retrieval.channel_count.to_numpy(np.float32)
    else:
        out["channel_count"] = (
            out.exact_count.gt(0).astype(np.float32)
            + out.sparse_score.gt(0).astype(np.float32)
            + out.dense_score.gt(0).astype(np.float32)
        )
    if retrieval is not None and "retrieval_score" in retrieval:
        out["retrieval_score"] = retrieval.retrieval_score.to_numpy(np.float32)
    else:
        out["retrieval_score"] = np.maximum(out.sparse_score, out.dense_score) + (
            0.08 * out.exact_count.clip(upper=3)
        )
    return out[PAIR_FEATURE_COLS]


def logit_clip(probability: np.ndarray, precision: float = 1e-6) -> np.ndarray:
    p = np.clip(np.asarray(probability, dtype=np.float64), precision, 1.0 - precision)
    return np.log(p / (1.0 - p))
