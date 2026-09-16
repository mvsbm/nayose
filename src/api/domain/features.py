"""Pair features + calibrator helper — shared by training-adjacent engine code
and the API matching worker. This module is the single source of truth for
pair and entity feature definitions (the engine re-exports them)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from rapidfuzz.fuzz import ratio
from rapidfuzz.process import cpdist


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
    size = len(left)
    if not size:
        return pd.DataFrame(columns=PAIR_FEATURE_COLS)

    left_values = {
        column: left[column].to_numpy(dtype=object, copy=False)
        for column in (
            "n_party_type",
            "n_name_strict",
            "n_name_loose",
            "n_name_signature",
            "n_kana",
            "n_romaji",
            "n_dob",
            "n_postal",
            "n_address",
            "n_phone",
            "n_phone_suffix8",
            "n_email",
            "n_email_local",
            "n_email_domain",
        )
    }
    right_values = {
        column: right[column].to_numpy(dtype=object, copy=False) for column in left_values
    }
    output: dict[str, np.ndarray] = {}

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
        populated = (left_values[column] != "") & (right_values[column] != "")
        output[feature] = (populated & (left_values[column] == right_values[column])).astype(
            np.float32
        )

    def fuzzy_array(column: str) -> np.ndarray:
        values = np.asarray(
            cpdist(
                left_values[column],
                right_values[column],
                scorer=ratio,
                score_multiplier=0.01,
                dtype=np.float32,
                workers=1,
            ),
            dtype=np.float32,
        )
        populated = (left_values[column] != "") & (right_values[column] != "")
        return np.where(populated, values, 0.0).astype(np.float32, copy=False)

    output["name_similarity"] = np.maximum(
        fuzzy_array("n_name_loose"), fuzzy_array("n_name_signature")
    )
    output["kana_similarity"] = fuzzy_array("n_kana")
    output["romaji_similarity"] = fuzzy_array("n_romaji")
    output["address_similarity"] = fuzzy_array("n_address")
    output["email_local_similarity"] = fuzzy_array("n_email_local")
    domain_populated = (left_values["n_email_domain"] != "") & (
        right_values["n_email_domain"] != ""
    )
    output["email_domain_exact"] = (
        domain_populated & (left_values["n_email_domain"] == right_values["n_email_domain"])
    ).astype(np.float32)

    frequency_maps = frequency_maps or {}
    for feature, column in (
        ("name_frequency_weight", "n_name_loose"),
        ("kana_frequency_weight", "n_kana"),
        ("phone_frequency_weight", "n_phone"),
        ("email_frequency_weight", "n_email"),
    ):
        frequencies: np.ndarray = np.fromiter(
            (frequency_maps.get(column, {}).get(value, 0) for value in left_values[column]),
            dtype=np.float32,
            count=size,
        )
        exact = output[column.removeprefix("n_") + "_exact"]
        output[feature] = exact / np.log2(frequencies + 2.0)

    agreement_columns = (
        "name_loose_exact",
        "kana_exact",
        "romaji_exact",
        "dob_exact",
        "postal_exact",
        "address_exact",
        "phone_exact",
        "email_exact",
    )
    output["agreement_count"] = np.sum(
        np.stack([output[column] for column in agreement_columns]), axis=0, dtype=np.float32
    )
    best_name = np.maximum.reduce(
        [output["name_similarity"], output["kana_similarity"], output["romaji_similarity"]]
    )
    evidence_count = (
        (best_name >= 0.82).astype(np.float32)
        + output["dob_exact"]
        + (np.maximum(output["address_similarity"], output["postal_exact"]) >= 0.82)
        + (np.maximum(output["phone_exact"], output["phone_suffix_exact"]) >= 1)
        + (np.maximum(output["email_exact"], output["email_local_similarity"]) >= 0.90)
    )
    output["evidence_family_count"] = evidence_count.astype(np.float32, copy=False)

    identity_columns = (
        "n_name_loose",
        "n_kana",
        "n_romaji",
        "n_dob",
        "n_postal",
        "n_address",
        "n_phone",
        "n_email",
    )
    both_populated: np.ndarray = np.zeros(size, dtype=np.float32)
    for column in identity_columns:
        both_populated += ((left_values[column] != "") & (right_values[column] != "")).astype(
            np.float32
        )
    output["both_populated_count"] = both_populated
    output["missing_fraction"] = 1.0 - both_populated / len(identity_columns)
    for feature, column in (
        ("type_conflict", "n_party_type"),
        ("dob_conflict", "n_dob"),
        ("postal_conflict", "n_postal"),
    ):
        populated = (left_values[column] != "") & (right_values[column] != "")
        output[feature] = (populated & (left_values[column] != right_values[column])).astype(
            np.float32
        )
    output["name_dob_evidence"] = best_name * output["dob_exact"]
    output["name_address_evidence"] = best_name * np.maximum(
        output["address_similarity"], output["postal_exact"]
    )
    for column in ("sparse_score", "dense_score", "exact_count", "reranker_score"):
        output[column] = (
            retrieval[column].to_numpy(np.float32)
            if retrieval is not None and column in retrieval
            else np.zeros(size, dtype=np.float32)
        )
    if retrieval is not None and "channel_count" in retrieval:
        output["channel_count"] = retrieval["channel_count"].to_numpy(np.float32)
    else:
        output["channel_count"] = (
            (output["exact_count"] > 0).astype(np.float32)
            + (output["sparse_score"] > 0).astype(np.float32)
            + (output["dense_score"] > 0).astype(np.float32)
        )
    if retrieval is not None and "retrieval_score" in retrieval:
        output["retrieval_score"] = retrieval["retrieval_score"].to_numpy(np.float32)
    else:
        output["retrieval_score"] = np.maximum(
            output["sparse_score"], output["dense_score"]
        ) + 0.08 * np.minimum(output["exact_count"], 3)
    return pd.DataFrame(output, columns=PAIR_FEATURE_COLS)


def logit_clip(probability: np.ndarray, precision: float = 1e-6) -> np.ndarray:
    p = np.clip(np.asarray(probability, dtype=np.float64), precision, 1.0 - precision)
    return np.log(p / (1.0 - p))
