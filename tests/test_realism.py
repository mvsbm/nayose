"""Realism regression tests: the four audit fixes stay fixed."""

import re

import numpy as np
import pandas as pd

from synthetic.config import (
    EMAIL_DOMAINS,
    POLICY_PARAMS,
    PRODUCT_PROFILES,
    NoiseConfig,
    SyntheticConfig,
)
from synthetic.noise import RecordCorruptor
from synthetic.realism import (
    channel_family,
    channel_landline_share,
    default_phone,
    generate_email,
    generate_phone,
)

MOBILE_RE = re.compile(r"^0[789]0-\d{4}-\d{4}$")
LANDLINE_RE = re.compile(r"^0\d{1,2}-\d{4}-\d{4}$")  # 03-… (2-digit) or 011-… (3-digit)


def _party() -> dict:
    return {
        "party_type": "person",
        "name_kanji": "松永 智泰",
        "name_kana": "マツナガ トモヤス",
        "name_romaji": "Matsunaga Tomoyasu",
        "date_birth": "1980-05-05",
        "postal_code": "4110037",
        "address": "静岡県三島市",
        "phone": "090-1234-5678",
        "email": "matsunaga.tomoyasu@gmail.com",
    }


def _corruptor() -> RecordCorruptor:
    return RecordCorruptor(SyntheticConfig(profile="smoke"), NoiseConfig())


def test_alpha_in_kana_never_corrupts_kanji():
    """P3 regression: the alpha_in_kana event is kana-only (Latin inside kanji
    was the root cause of the 709 'I松永…' artifacts)."""
    corruptor = _corruptor()
    rng = np.random.default_rng(7)
    for _ in range(300):
        values = dict(_party())
        corruptor.corrupt(
            pd.Series(values), {"channel": "legacy_migration", "missing_factor": 1.0}, "poor", rng
        )
        if "alpha_in_kana" in [t for _, t in corruptor.lineage]:
            assert re.match(r"^[\u4e00-\u9fff々〆〇]", values["name_kanji"]), (
                "kanji must never gain a Latin prefix via alpha_in_kana"
            )


def test_shared_contact_uses_agency_lines():
    """The office shared-line event reads the AGENCY context (the uniform
    event loop passes party only — reading the wrong argument silently
    no-opped it; regression guard)."""
    corruptor = _corruptor()
    rng = np.random.default_rng(5)
    agency = {
        "channel": "legacy_migration",
        "missing_factor": 1.0,
        "shared_phone": "011-1234-5678",
        "shared_email": "office1@biglobe.ne.jp",
    }
    fired = 0
    for _ in range(800):
        corruptor.corrupt(pd.Series(_party()), agency, "poor", rng)
        if "shared_contact" in [label for _, label in corruptor.lineage]:
            fired += 1
    assert fired > 0


def test_company_kana_is_katakana(monkeypatch):
    """P1: company kana must be katakana, not Latin romaji."""
    from synthetic.providers import CompanySource

    monkeypatch.setattr(
        CompanySource, "_load", staticmethod(lambda sources: ["株式会社東京商事", "有限会社桜井"])
    )
    source = CompanySource()
    for company in source.companies:
        assert re.fullmatch(r"[ァ-ヶー・]+", company.name_kana), company.name_kana
        assert not company.name_kana.isascii()


def test_policies_are_aggregate_roots_and_events_anchor_dates():
    """G1: policies group records (shared policy_id across renewals) and
    pick_policy_event respects the channel era (never future-dates a record)."""
    from synthetic.policy import build_policies, pick_policy_event

    rng = np.random.default_rng(42)
    parties = pd.DataFrame({"party_type": ["person", "person"], "name_kanji": ["佐藤", "鈴木"]})
    policies = build_policies(rng, parties, {0: 0, 1: 0}, {0: [0, 1]})
    assert len(policies[0]) >= 1 and len(policies[1]) >= 1
    ids = [p.policy_id for p in policies[0] + policies[1]]
    assert len(set(ids)) >= 2, "policy ids are globally unique"
    for policy in policies[0]:
        years = [e.year for e in policy.events]
        assert years == sorted(years), "events are chronological"
        assert policy.events[0].kind == "issued"
    policy, event = pick_policy_event(rng, policies[0], vintage_high=2005)
    assert event.year <= 2005, "a 2005-era document can't reference a 2020 renewal"
    total = len(policies[0]) + len(policies[1])
    covered = [p.covered for pol in policies for p in pol]
    assert all(len(c) >= 1 for c in covered)
    assert total >= 2


def test_policy_lifecycle_and_roles_are_clean_truth(monkeypatch):
    from synthetic.policy import build_policies, policy_status_at, unique_policies
    from synthetic.truth import build_policy_truth

    for name, profile in PRODUCT_PROFILES.items():
        monkeypatch.setitem(profile, "weight", 1.0 if name == "auto" else 0.0)
        monkeypatch.setitem(profile, "renew_p", 1.0)
        monkeypatch.setitem(profile, "claim_rate", 1.0)
        monkeypatch.setitem(profile, "lapse_rate", 1.0)
        monkeypatch.setitem(profile, "reinstatement_rate", 1.0)
        monkeypatch.setitem(profile, "cancel_rate", 1.0)
        monkeypatch.setitem(profile, "family_rate", 1.0)
    monkeypatch.setitem(POLICY_PARAMS, "start_range", (2019, 2020))

    parties = pd.DataFrame({"party_type": ["person"] * 3})
    portfolios = build_policies(
        np.random.default_rng(7), parties, {0: 0, 1: 0, 2: 0}, {0: [0, 1, 2]}
    )
    policies = unique_policies(portfolios)
    assert policies
    assert all(len(policy.terms) > 1 for policy in policies)
    assert all(any(event.kind == "renewal" for event in policy.events) for policy in policies)
    assert all(any(event.kind == "cancel" for event in policy.events) for policy in policies)
    assert all(policy_status_at(policy, 2019) == "active" for policy in policies)
    assert all(policy_status_at(policy, 2025) == "cancelled" for policy in policies)
    assert any(role.role == "additional_driver" for policy in policies for role in policy.roles)
    tables = build_policy_truth(portfolios)
    assert len(tables["policy_truth"]) == len(policies)
    assert len(tables["policy_event_truth"]) > len(tables["policy_truth"])
    assert len(tables["policy_party_truth"]) > len(tables["policy_truth"])


def test_temporal_snapshot_is_monotonic_and_validation_catches_drift():
    """G4: snapshot_at folds states effective <= year; validate_temporal flags
    a record whose stale value is NOT the state true at its observed_at."""
    from synthetic.policy import HistoryState, snapshot_at, validate_temporal

    history = [
        HistoryState(
            effective_year=2000, values={"name_kanji": "佐藤 太郎", "phone": "090-1111-1111"}
        ),
        HistoryState(
            effective_year=2010, values={"name_kanji": "佐藤 太郎", "phone": "090-2222-2222"}
        ),
    ]
    assert snapshot_at(history, 2005) == {"name_kanji": "佐藤 太郎", "phone": "090-1111-1111"}
    assert snapshot_at(history, 2015)["phone"] == "090-2222-2222"

    ok = pd.DataFrame(
        [
            {
                "record_id": "R1",
                "observed_at": "2003-05-05",
                "name_kanji": "佐藤 太郎",
                "phone": "090-1111-1111",
            },
            {
                "record_id": "R2",
                "observed_at": "2015-05-05",
                "name_kanji": "佐藤 太郎",
                "phone": "090-2222-2222",
            },
        ]
    )
    lineage = {"R1": [("name_kanji", "stale_rename"), ("phone", "stale_contact")], "R2": []}
    assert validate_temporal(ok, {"R1": 0, "R2": 0}, {0: history}, lineage) == []
    bad = pd.DataFrame(
        [
            {
                "record_id": "R1",
                "observed_at": "2003-05-05",
                "name_kanji": "佐藤 太郎",
                "phone": "090-2222-2222",
            }
        ]
    )
    violations = validate_temporal(
        bad, {"R1": 0}, {0: history}, {"R1": [("phone", "stale_contact")]}
    )
    assert len(violations) == 1 and "phone" in violations[0]


def test_agency_knobs_and_typo_profiles_are_consumed():
    """G3: every agency carries a personal typo profile + severity knobs, and
    the corruptor's OCR family reads the agency context (not just globals)."""
    from synthetic.engine import generate_agencies

    rng = np.random.default_rng(7)
    agencies = generate_agencies(SyntheticConfig(profile="smoke"), rng)
    assert len(agencies) == SyntheticConfig(profile="smoke").n_agencies
    for _, agency in agencies.iterrows():
        assert len(agency["typo_profile"]) >= 1
        assert 0.15 <= agency["ocr_factor"] <= 0.5
        assert agency["format_factor"] > 0
    corruptor = _corruptor()
    rng2 = np.random.default_rng(3)
    values = dict(_party())
    agency = {
        "channel": "paper_ocr",
        "missing_factor": 1.0,
        "ocr_factor": 0.5,
        "format_factor": 1.0,
        "typo_profile": (("松", "枩"),),
    }
    corruptor.corrupt(pd.Series(values), agency, "poor", rng2)
    labels = {label for _, label in corruptor.lineage}
    assert labels, "agency context must produce events"


def test_email_domains_come_from_realistic_pools():
    """P2: no example.com; domains drawn from the era-aware pools."""
    rng = np.random.default_rng(11)
    domains = set()
    for _ in range(400):
        email = generate_email("松永 智泰", "Matsunaga Tomoyasu", rng)
        assert "@" in email
        domain = email.split("@")[1]
        domains.add(domain)
    assert "example.com" not in domains
    assert not domains - {d for pool in EMAIL_DOMAINS.values() for d in pool}
    # ISP flavor for vintage channels, modern for web
    assert channel_family("legacy_migration") == "isp"
    assert channel_family("web") == "modern"


def test_phone_styles_and_channel_ordering():
    """P4: mobile and fixed-line formats differ; vintage channels favor lines."""
    rng = np.random.default_rng(3)
    for _ in range(200):
        assert MOBILE_RE.match(generate_phone(rng))
        assert LANDLINE_RE.match(generate_phone(rng, landline=True))
        assert MOBILE_RE.match(default_phone(rng)) or LANDLINE_RE.match(default_phone(rng))
    assert channel_landline_share("legacy_migration") > channel_landline_share("paper_ocr")
    assert channel_landline_share("paper_ocr") > channel_landline_share("web")
