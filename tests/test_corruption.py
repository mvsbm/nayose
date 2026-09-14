"""Unit tests for the dirty-data engine (no external data)."""

from typing import Any, cast

import numpy as np
import pandas as pd
import pytest

from data_generation.config import EVENT_NAMES, NoiseConfig, SyntheticConfig, noise_for_config
from data_generation.corruption import CUSTOMER_FIELDS, QueryCorruptor, RecordCorruptor

PARTY = pd.Series(
    {
        "party_type": "person",
        "name_kanji": "佐藤 太郎",
        "name_kana": "サトウ タロウ",
        "name_romaji": "Sato Taro",
        "date_birth": "1990-04-01",
        "postal_code": "1000001",
        "address": "東京都千代田区千代田1丁目2番3号",
        "phone": "090-1234-5678",
        "email": "sato.taro@example.com",
        "old_name_kanji": "鈴木 次郎",
        "old_name_kana": "スズキ ジロウ",
        "old_name_romaji": "Suzuki Jiro",
        "old_postal_code": "1500001",
        "old_address": "東京都渋谷区神宮前1丁目1番1号",
        "old_phone": "080-9999-8888",
        "old_email": "suzuki.jiro@example.com",
    }
)
AGENCY = {"missing_factor": 1.0, "ocr_factor": 0.25, "format_factor": 1.0}


@pytest.fixture
def cfg():
    return SyntheticConfig(profile="preflight", difficulty="realistic")


def _corrupt(cfg, seed=0, quality="average"):
    rng = np.random.default_rng(seed)
    result = RecordCorruptor(cfg, NoiseConfig()).corrupt(PARTY, AGENCY, quality, rng)
    return result.values, result.lineage


def test_party_type_never_blank(cfg):
    for seed in range(30):
        values, _ = _corrupt(cfg, seed)
        assert values["party_type"] in {"person", "company"}


def test_missing_values_are_labeled(cfg):
    for seed in range(30):
        values, lineage = _corrupt(cfg, seed)
        labels = dict(lineage)
        for field in CUSTOMER_FIELDS:
            if values[field] == "":
                assert labels.get(field) == "missing", (field, values[field], labels.get(field))
            else:
                assert field not in labels or labels[field] != "missing"


def test_stale_rate_and_event_overrides_are_consumed():
    event_rates = {name: 0.0 for name in EVENT_NAMES}
    event_rates["rename_stale"] = 1.0
    cfg = SyntheticConfig(
        profile="preflight",
        noise_event_rates=event_rates,
        noise_band_scale={"average": 3.0},
        noise_stale_rate=0.0,
    )
    no_stale = RecordCorruptor(cfg, noise_for_config(cfg)).corrupt(
        PARTY, AGENCY, "average", np.random.default_rng(0)
    )
    assert no_stale.values["name_kanji"] == PARTY["name_kanji"]
    baseline_cfg = SyntheticConfig(
        profile="preflight",
        noise_event_rates=event_rates,
        noise_band_scale={"average": 3.0},
    )
    stale = RecordCorruptor(baseline_cfg, noise_for_config(baseline_cfg)).corrupt(
        PARTY, AGENCY, "average", np.random.default_rng(0)
    )
    assert stale.values["name_kanji"] == PARTY["old_name_kanji"]


def test_stale_rename_is_coherent(cfg):
    corruptor = RecordCorruptor(cfg, NoiseConfig())
    found = False
    for seed in range(200):
        rng = np.random.default_rng(seed)
        result = corruptor.corrupt(PARTY, AGENCY, "poor", rng)
        values, lineage = result.values, result.lineage
        if any(label == "stale_rename" for _, label in lineage):
            found = True
            assert values["name_kanji"] == "鈴木 次郎"
            assert values["name_kana"] == "スズキ ジロウ"
        if any(label == "stale_move" for _, label in lineage):
            assert values["address"] == "東京都渋谷区神宮前1丁目1番1号"
            assert values["postal_code"] == "1500001"
    assert found


def test_catastrophic_misses_more_than_average(cfg):
    miss = {}
    for quality in ("average", "catastrophic"):
        counts = []
        for seed in range(40):
            values, _ = _corrupt(cfg, seed, quality)
            counts.append(sum(values[f] == "" for f in CUSTOMER_FIELDS))
        miss[quality] = np.mean(counts)
    assert miss["catastrophic"] > miss["average"] * 2


def test_insufficient_query_is_sparse(cfg):
    rng = np.random.default_rng(3)
    result = QueryCorruptor(NoiseConfig()).corrupt("insufficient", PARTY, rng)
    assert sum(result.values[f] == "" for f in CUSTOMER_FIELDS) >= 5


def test_existing_stable_query_is_clean(cfg):
    rng = np.random.default_rng(3)
    result = QueryCorruptor(NoiseConfig()).corrupt("existing_stable", PARTY, rng)
    assert all(result.values[f] == PARTY[f] for f in CUSTOMER_FIELDS)


def test_existing_changed_query_has_multiple_noncanonical_fields():
    changed = []
    for seed in range(80):
        result = QueryCorruptor(NoiseConfig()).corrupt(
            "existing_changed", PARTY, np.random.default_rng(seed)
        )
        changed.append(sum(result.values[field] != PARTY[field] for field in CUSTOMER_FIELDS))
    assert min(changed) >= 3
    assert sum(value == 0 for value in changed) == 0


def test_challenge_query_profile_has_coherent_high_damage():
    noise = noise_for_config(SyntheticConfig(profile="preflight", difficulty="challenge"))
    changed = []
    damaged = []
    for seed in range(80):
        changed_result = QueryCorruptor(noise).corrupt(
            "existing_changed", PARTY, np.random.default_rng(seed)
        )
        damaged_result = QueryCorruptor(noise).corrupt(
            "existing_damaged", PARTY, np.random.default_rng(seed)
        )
        changed.append(
            sum(changed_result.values[field] != PARTY[field] for field in CUSTOMER_FIELDS)
        )
        damaged.append(
            sum(damaged_result.values[field] != PARTY[field] for field in CUSTOMER_FIELDS)
        )
    assert min(changed) >= 6
    assert min(damaged) >= 8
    stale_labels = [
        label
        for seed in range(200)
        for _, label in QueryCorruptor(noise)
        .corrupt("existing_changed", PARTY, np.random.default_rng(seed))
        .lineage
    ]
    assert "stale_snapshot" in stale_labels


def test_existing_damaged_query_is_a_real_hard_case():
    damaged = []
    for seed in range(80):
        result = QueryCorruptor(NoiseConfig()).corrupt(
            "existing_damaged", PARTY, np.random.default_rng(seed)
        )
        damaged.append(sum(result.values[field] != PARTY[field] for field in CUSTOMER_FIELDS))
    assert min(damaged) >= 5
    assert sum(value >= 6 for value in damaged) >= 40


def test_queries_keep_agency_and_channel_from_the_same_source():
    from data_generation.generator import generate_queries

    parties = pd.DataFrame([PARTY.to_dict()] * 4)
    party_truth = pd.DataFrame(
        {"truth_id": [f"T{i:010d}" for i in range(4)], "hard_case": [""] * 4}
    )
    agencies = pd.DataFrame(
        {
            "agency_id": ["A001", "A002"],
            "channel": ["web", "legacy_migration"],
        }
    )
    query_mix = {
        "existing_stable": 1.0,
        "existing_changed": 0.0,
        "existing_damaged": 0.0,
        "new_customer": 0.0,
        "related_only": 0.0,
        "insufficient": 0.0,
    }
    cfg = SyntheticConfig(
        profile="preflight",
        n_parties_override=4,
        query_share=1.0,
        query_mix=query_mix,
    )
    queries, _, _ = generate_queries(
        cfg,
        NoiseConfig(),
        parties,
        {i: i for i in range(4)},
        party_truth,
        {f"T{i:010d}": [f"R{i:010d}"] for i in range(4)},
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        np.random.default_rng(19),
        agencies=agencies,
    )
    joined = queries.merge(agencies, on="agency_id", suffixes=("_query", "_agency"))
    assert len(joined) == len(queries)
    assert (joined["channel_query"] == joined["channel_agency"]).all()
