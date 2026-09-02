"""Ground-truth builders: the six hidden tables nayose.ipynb consumes.

Schemas are the authoritative v2 contract (record_truth / evaluation_truth /
query_truth / party_truth / relation_truth / field_lineage) plus the visible
reference/query snapshots. A single lineage path records every field value's
provenance (canonical owner + transformation).
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd

from .noise import CUSTOMER_FIELDS

FIELDS = ["party_type", *CUSTOMER_FIELDS]
VISIBLE_FIELDS = list(CUSTOMER_FIELDS)


def stable_bucket(value: str, modulo: int = 10) -> int:
    """Deterministic hash bucket for reproducible train/calibration/test splits."""
    return (
        int.from_bytes(hashlib.blake2b(value.encode(), digest_size=4).digest(), "little") % modulo
    )


def party_split(party_idx: int) -> str:
    bucket = stable_bucket(str(party_idx))
    return "train" if bucket < 6 else "calibration" if bucket < 8 else "test"


def build_households(n_parties: int, rng) -> dict[int, int]:
    """Deterministic relation-id (household) partition of party indices."""
    relation_of: dict[int, int] = {}
    counter = 0
    i = 0
    while i < n_parties:
        size = rng.choice([1, 2, 3], p=[0.88, 0.09, 0.03])
        size = min(size, n_parties - i)
        for j in range(size):
            relation_of[i + j] = counter
        counter += 1
        i += size
    return relation_of


def build_party_truth(
    parties: pd.DataFrame, relation_of: dict[int, int], name_dup_counts: pd.Series
) -> pd.DataFrame:
    rows = []
    for party_idx, party in parties.iterrows():
        duplicates = name_dup_counts.get(party["name_kanji"], 0) > 1
        household = relation_of.get(party_idx, party_idx)
        rows.append(
            {
                "truth_id": f"T{party_idx:010d}",
                "relation_id": f"H{household:08d}",
                "party_type": party["party_type"],
                "name_kanji": party["name_kanji"],
                "name_kana": party["name_kana"],
                "name_romaji": party["name_romaji"],
                "date_birth": party["date_birth"],
                "postal_code": party["postal_code"],
                "address": party["address"],
                "phone": party["phone"],
                "email": party["email"],
                "old_name_kanji": party.get("old_name_kanji", ""),
                "old_name_kana": party.get("old_name_kana", ""),
                "old_postal_code": party.get("old_postal_code", ""),
                "old_address": party.get("old_address", ""),
                "old_phone": party.get("old_phone", ""),
                "old_email": party.get("old_email", ""),
                "hard_case": "twin"
                if duplicates
                else ("household" if relation_of.get(party_idx, party_idx) != party_idx else ""),
                "split": party_split(party_idx),
            }
        )
    return pd.DataFrame(rows)


def build_relation_truth(party_truth: pd.DataFrame) -> pd.DataFrame:
    return party_truth[["truth_id", "relation_id", "party_type", "hard_case"]].copy()


def build_record_truth(
    observations: pd.DataFrame, party_of: pd.Series, party_truth: pd.DataFrame
) -> pd.DataFrame:
    truth = party_truth.set_index("truth_id")
    quality_by_id = observations.set_index("record_id")["record_quality"]
    rows = []
    for record_id, party_idx in party_of.items():
        t = truth.loc[f"T{party_idx:010d}"]
        rows.append(
            {
                "record_id": record_id,
                "truth_id": f"T{party_idx:010d}",
                "relation_id": t["relation_id"],
                "split": t["split"],
                "hard_case": t["hard_case"],
                "record_quality": quality_by_id.loc[record_id],
            }
        )
    return pd.DataFrame(rows)


def build_field_lineage(
    observations: pd.DataFrame,
    party_of: pd.Series,
    lineage_by_item: dict[str, list[tuple[str, str]]],
    parties: pd.DataFrame,
) -> pd.DataFrame:
    obs_idx = observations.set_index("record_id")
    rows = []
    for record_id, party_idx in party_of.items():
        owner = f"T{party_idx:010d}"
        party = parties.loc[party_idx]
        rec = obs_idx.loc[record_id]
        labels = dict(lineage_by_item.get(record_id, []))
        for field in CUSTOMER_FIELDS:
            rows.append(
                {
                    "item_id": record_id,
                    "field": field,
                    "canonical_value": str(party.get(field, "")),
                    "observed_value": str(rec[field]),
                    "value_owner_truth_id": owner,
                    "transformation": labels.get(field, "current"),
                }
            )
    return pd.DataFrame(rows)


def build_query_tables(
    queries: pd.DataFrame,
    query_meta: list[dict],
    parties: pd.DataFrame,
    party_of: pd.Series,
    record_ids_by_truth: dict[str, list[str]],
    lineage: dict[str, list[tuple[str, str]]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (query_truth, evaluation_truth, query_field_lineage)."""
    qt_rows, et_rows, fl_rows = [], [], []
    for i, meta in enumerate(query_meta):
        qid = queries.loc[i, "query_id"]
        target = meta["target_truth_id"]
        scen = meta["scenario"]
        eligible = meta["eligible_records"]
        related = meta["related_truths"]
        qt = queries.loc[i, VISIBLE_FIELDS].to_dict()
        qt_rows.append({"query_id": qid, "truth_id": target, **qt})
        et_rows.append(
            {
                "query_id": qid,
                "target_truth_id": target,
                "split": party_split(int(target[1:])),
                "scenario": scen,
                "expected_existing_customer": meta["expected_existing_customer"],
                "expected_action": meta["expected_action"],
                "eligible_reference_record_ids": json.dumps(eligible, ensure_ascii=False),
                "related_reference_truth_ids": json.dumps(related, ensure_ascii=False),
                "relation_id": meta["relation_id"],
                "hard_case": meta["hard_case"],
            }
        )
        labels = dict(lineage.get(qid, []))
        for field in CUSTOMER_FIELDS:
            owner = meta["value_owner_truth_id"]
            party = parties.loc[int(owner[1:])]
            fl_rows.append(
                {
                    "item_id": qid,
                    "field": field,
                    "canonical_value": str(party.get(field, "")),
                    "observed_value": str(queries.loc[i, field]),
                    "value_owner_truth_id": owner,
                    "transformation": labels.get(field, "current"),
                }
            )
    return pd.DataFrame(qt_rows), pd.DataFrame(et_rows), pd.DataFrame(fl_rows)
