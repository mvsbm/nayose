"""Unit tests for truth-table builders (self-contained)."""

import numpy as np
import pandas as pd

from data_generation.ground_truth import (
    build_households,
    build_party_truth,
    build_query_tables,
    build_relation_truth,
    party_split,
    stable_bucket,
)


def test_stable_bucket_deterministic_and_split_shares():
    assert stable_bucket("abc") == stable_bucket("abc")
    buckets = [party_split(i) for i in range(2000)]
    assert abs(buckets.count("train") / 2000 - 0.6) < 0.05
    assert abs(buckets.count("calibration") / 2000 - 0.2) < 0.05
    assert abs(buckets.count("test") / 2000 - 0.2) < 0.05


def test_households_partition_all_indices():
    rng = np.random.default_rng(1)
    relation_of = build_households(1000, rng)
    assert sorted(relation_of) == list(range(1000))


def test_party_truth_and_relation_truth():
    parties = pd.DataFrame(
        {
            "party_type": ["person", "company"],
            "name_kanji": ["佐藤 太郎", "株式会社エディオン"],
            "name_kana": ["サトウ タロウ", "KABUSHIKIGAISHAEDION"],
            "name_romaji": ["Sato Taro", "Kabushikigaishaedion"],
            "date_birth": ["1990-04-01", ""],
            "postal_code": ["1000001", "1040001"],
            "address": ["東京都千代田区千代田", "東京都中央区銀座"],
            "phone": ["090-1", "03-1"],
            "email": ["a@b.c", "d@e.f"],
        }
    )
    name_dup = parties.name_kanji.value_counts()
    pt = build_party_truth(parties, build_households(2, np.random.default_rng(0)), name_dup)
    assert list(pt.columns) == [
        "truth_id",
        "relation_id",
        "party_type",
        "name_kanji",
        "name_kana",
        "name_romaji",
        "date_birth",
        "postal_code",
        "address",
        "phone",
        "email",
        "old_name_kanji",
        "old_name_kana",
        "old_postal_code",
        "old_address",
        "old_phone",
        "old_email",
        "hard_case",
        "split",
    ]
    rt = build_relation_truth(pt)
    assert list(rt.columns) == ["truth_id", "relation_id", "party_type", "hard_case"]


def test_query_tables_lineage_counts():
    parties = pd.DataFrame(
        {
            "party_type": ["person"],
            "name_kanji": ["佐藤 太郎"],
            "name_kana": ["サトウ タロウ"],
            "name_romaji": ["Sato Taro"],
            "date_birth": ["1990-04-01"],
            "postal_code": ["1000001"],
            "address": ["東京都千代田区千代田"],
            "phone": ["090-1"],
            "email": ["a@b.c"],
        }
    )
    queries = pd.DataFrame(
        [
            {
                "query_id": "Q0000000000",
                "party_type": "person",
                "name_kanji": "佐藤 太郎",
                "name_kana": "サトウ タロウ",
                "name_romaji": "Sato Taro",
                "date_birth": "1990-04-01",
                "postal_code": "",
                "address": "東京都千代田区千代田",
                "phone": "090-1",
                "email": "a@b.c",
            }
        ]
    )
    meta = [
        {
            "scenario": "insufficient",
            "target_truth_id": "T0000000000",
            "expected_existing_customer": True,
            "expected_action": "REVIEW_OR_MINIMAL",
            "eligible_records": [],
            "related_truths": [],
            "relation_id": "H00000000",
            "hard_case": "",
            "value_owner_truth_id": "T0000000000",
        }
    ]
    lineage = {"Q0000000000": [("postal_code", "missing")]}
    qt, et, fl = build_query_tables(
        queries, meta, parties, pd.Series(dtype="int64"), {"T0000000000": []}, lineage
    )
    assert qt.loc[0, "postal_code"] == ""
    assert et.loc[0, "scenario"] == "insufficient"
    assert len(fl) == 8
    assert fl[fl.field == "postal_code"].iloc[0].transformation == "missing"
