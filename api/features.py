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


def compute_pair_features(
    query: dict[str, str], candidates: pd.DataFrame, retrieval: pd.DataFrame
) -> pd.DataFrame:
    """Compute all pair features between a query and each candidate."""
    rows: list[dict[str, float]] = []
    for i, (_, cand) in enumerate(candidates.iterrows()):
        q, c = query, cand

        type_exact = float(
            bool(q["party_type"] and c["party_type"] and q["party_type"] == c["party_type"])
        )
        name_strict_exact = float(
            bool(
                q.get("n_name_strict", "")
                and c.get("n_name_strict", "")
                and q["n_name_strict"] == c["n_name_strict"]
            )
        )
        name_loose_exact = float(
            bool(q["n_name_loose"] and c["n_name_loose"] and q["n_name_loose"] == c["n_name_loose"])
        )
        kana_exact = float(bool(q["n_kana"] and c["n_kana"] and q["n_kana"] == c["n_kana"]))
        romaji_exact = float(
            bool(q["n_romaji"] and c["n_romaji"] and q["n_romaji"] == c["n_romaji"])
        )
        dob_exact = float(bool(q["n_dob"] and c["n_dob"] and q["n_dob"] == c["n_dob"]))
        postal_exact = float(
            bool(q["n_postal"] and c["n_postal"] and q["n_postal"] == c["n_postal"])
        )
        address_exact = float(
            bool(q["n_address"] and c["n_address"] and q["n_address"] == c["n_address"])
        )
        phone_exact = float(bool(q["n_phone"] and c["n_phone"] and q["n_phone"] == c["n_phone"]))
        phone_suffix_exact = float(
            bool(
                q["n_phone_suffix8"]
                and c["n_phone_suffix8"]
                and q["n_phone_suffix8"] == c["n_phone_suffix8"]
            )
        )
        email_exact = float(bool(q["n_email"] and c["n_email"] and q["n_email"] == c["n_email"]))

        name_sim = max(
            _fuzzy(q["n_name_loose"], c["n_name_loose"]),
            _fuzzy(q["n_name_signature"], c["n_name_signature"]),
        )
        kana_sim = _fuzzy(q["n_kana"], c["n_kana"])
        romaji_sim = _fuzzy(q["n_romaji"], c["n_romaji"])
        addr_sim = _fuzzy(q["n_address"], c["n_address"])
        email_local_sim = _fuzzy(q["n_email_local"], c["n_email_local"])
        email_domain_exact = float(
            bool(
                q["n_email_domain"]
                and c["n_email_domain"]
                and q["n_email_domain"] == c["n_email_domain"]
            )
        )

        name_evidence = max(name_sim, kana_sim, romaji_sim) >= 0.82
        dob_evidence = dob_exact >= 1
        address_evidence = max(addr_sim, postal_exact) >= 0.82
        phone_evidence = max(phone_exact, phone_suffix_exact) >= 1
        email_evidence = max(email_exact, email_local_sim) >= 0.90
        evidence_family_count = sum(
            [
                name_evidence,
                dob_evidence,
                address_evidence,
                phone_evidence,
                email_evidence,
            ]
        )

        agreement_count = sum(
            [
                name_loose_exact,
                kana_exact,
                romaji_exact,
                dob_exact,
                postal_exact,
                address_exact,
                phone_exact,
                email_exact,
            ]
        )
        identity_fields = [
            "n_name_loose",
            "n_kana",
            "n_romaji",
            "n_dob",
            "n_postal",
            "n_address",
            "n_phone",
            "n_email",
        ]
        both_populated = sum(1 for f in identity_fields if q.get(f, "") and c.get(f, ""))
        missing_fraction = 1.0 - both_populated / len(identity_fields)

        type_conflict = float(
            bool(q["party_type"] and c["party_type"] and q["party_type"] != c["party_type"])
        )
        dob_conflict = float(bool(q["n_dob"] and c["n_dob"] and q["n_dob"] != c["n_dob"]))
        postal_conflict = float(
            bool(q["n_postal"] and c["n_postal"] and q["n_postal"] != c["n_postal"])
        )

        best_name = max(name_sim, kana_sim, romaji_sim)
        name_dob_evidence = best_name * dob_exact
        name_address_evidence = best_name * max(addr_sim, postal_exact)

        ret = retrieval.iloc[i] if i < len(retrieval) else {}
        sparse = float(ret.get("sparse_score", 0))
        dense = float(ret.get("dense_score", 0))
        exact_n = int(ret.get("exact_count", 0))

        rows.append(
            {
                "type_exact": type_exact,
                "name_strict_exact": name_strict_exact,
                "name_loose_exact": name_loose_exact,
                "kana_exact": kana_exact,
                "romaji_exact": romaji_exact,
                "dob_exact": dob_exact,
                "postal_exact": postal_exact,
                "address_exact": address_exact,
                "phone_exact": phone_exact,
                "phone_suffix_exact": phone_suffix_exact,
                "email_exact": email_exact,
                "name_similarity": name_sim,
                "kana_similarity": kana_sim,
                "romaji_similarity": romaji_sim,
                "address_similarity": addr_sim,
                "email_local_similarity": email_local_sim,
                "email_domain_exact": email_domain_exact,
                "name_frequency_weight": 0.0,
                "kana_frequency_weight": 0.0,
                "phone_frequency_weight": 0.0,
                "email_frequency_weight": 0.0,
                "agreement_count": agreement_count,
                "evidence_family_count": evidence_family_count,
                "both_populated_count": both_populated,
                "missing_fraction": missing_fraction,
                "type_conflict": type_conflict,
                "dob_conflict": dob_conflict,
                "postal_conflict": postal_conflict,
                "name_dob_evidence": name_dob_evidence,
                "name_address_evidence": name_address_evidence,
                "sparse_score": sparse,
                "dense_score": dense,
                "exact_count": exact_n,
                "channel_count": int(exact_n > 0) + int(sparse > 0) + int(dense > 0),
                "retrieval_score": max(sparse, dense) + 0.08 * min(exact_n, 3),
                "reranker_score": 0.0,
            }
        )
    return pd.DataFrame(rows)


def logit_clip(probability: np.ndarray, precision: float = 1e-6) -> np.ndarray:
    p = np.clip(np.asarray(probability, dtype=np.float64), precision, 1.0 - precision)
    return np.log(p / (1.0 - p))
