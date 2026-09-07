"""Generation orchestration: parties -> observations -> queries.

Pure-ish functions taking an injected ``rng``; deterministic per seed.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .config import NoiseConfig, SyntheticConfig
from .noise import QueryCorruptor, RecordCorruptor
from .policy import HistoryState, Policy, pick_policy_event, snapshot_at
from .providers import AddressSource, BuildingComposer, CompanySource, PersonaSource
from .realism import (  # DIP: realistic contacts through one interface
    channel_family,
    channel_landline_share,
    default_phone,
    generate_email,
    generate_phone,
)


def _eta_line(name: str, done: int, total: int, t0: float) -> str:
    elapsed = time.perf_counter() - t0
    rate = done / max(elapsed, 1e-9)
    remaining = int((total - done) / max(rate, 1e-9))
    pct = 100.0 * done / max(total, 1)
    return (
        f"  [{name}] {done:,}/{total:,} ({pct:.0f}%) eta {remaining // 60:02d}:{remaining % 60:02d}"
    )


def _progress_interval(total: int) -> int:
    return max(1, total // 20)


def _dob(person_age: int, rng: np.random.Generator, cfg: SyntheticConfig) -> str:
    year = cfg.snapshot_year - person_age
    return f"{year}-{int(rng.integers(1, 13)):02d}-{int(rng.integers(1, 29)):02d}"


def generate_parties(
    cfg: SyntheticConfig,
    rng: np.random.Generator,
    persona: PersonaSource,
    companies: CompanySource,
    address: AddressSource,
    buildings: BuildingComposer,
) -> pd.DataFrame:
    """Clean party truth: one row per customer entity."""
    rows = []
    t0 = time.perf_counter()
    step = _progress_interval(cfg.n_parties)
    for i in range(cfg.n_parties):
        is_person = rng.random() < cfg.person_share
        if is_person:
            p = persona.sample(rng, 1)[0]
            postal, full_addr = address.format(address.sample(rng, p.prefecture), rng, buildings)
            kanji, kana, romaji = p.name_kanji, p.name_kana, p.name_romaji
            dob = _dob(p.age, rng, cfg)
            email = generate_email(kanji, romaji, rng)
            party_type = "person"
            if rng.random() < cfg.twin_rate:
                # Hard-case twin: two customers with the SAME name/kana/romaji but
                # different identity fields (dob, address, contacts). Exact-name
                # anchors become ambiguous; resolution must use other fields.
                dna = int(rng.integers(1, 10))
                t_dob = _dob(max(p.age - dna, 18), rng, cfg)
                t_postal, t_addr = address.format(address.sample(rng, p.prefecture), rng, buildings)
                rows.append(
                    {
                        "party_type": "person",
                        "name_kanji": kanji,
                        "name_kana": kana,
                        "name_romaji": romaji,
                        "date_birth": t_dob,
                        "postal_code": t_postal,
                        "address": t_addr,
                        "phone": default_phone(rng),
                        "email": generate_email(kanji, romaji, rng),
                    }
                )
        else:
            c = companies.sample(rng)
            postal, full_addr = address.format(address.sample(rng, None), rng, buildings)
            kanji, kana, romaji = c.name_kanji, c.name_kana, c.name_romaji
            dob = ""
            email = generate_email(kanji, romaji, rng)
            party_type = "company"
        rows.append(
            {
                "party_type": party_type,
                "name_kanji": kanji,
                "name_kana": kana,
                "name_romaji": romaji,
                "date_birth": dob,
                "postal_code": postal,
                "address": full_addr,
                "phone": default_phone(rng),
                "email": email,
            }
        )
        if (i + 1) % step == 0:
            print(_eta_line("parties", i + 1, cfg.n_parties, t0))
    frame = pd.DataFrame(rows)
    dup = frame.name_kanji.value_counts()
    frame["name_dup_count"] = frame.name_kanji.map(dup).to_numpy()
    print(
        f"  [parties] {len(frame):,} rows, {(frame.name_dup_count > 1).sum():,} rows share a "
        f"name with another entity (twin_rate {cfg.twin_rate:.0%}), "
        f"unique names {frame.name_kanji.nunique():,}"
    )
    return frame


def generate_agencies(cfg: SyntheticConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Agent book-of-business context (G3). The per-office severity knobs are
    CONSUMED by the corruptor (paper_ocr/legacy_format events), and each office
    carries a small personal typo profile (their habitual char confusions) used
    by the OCR family — agents degrade their own customers' data.

    Office RNGs are derived from the global seed (stable across processes;
    Python's hash() is randomized, so it is never used for seeds)."""
    channels = ["web", "agent_pc", "paper_ocr", "legacy_migration"]
    confusion_pool = [
        ("人", "入"),
        ("大", "太"),
        ("土", "士"),
        ("未", "末"),
        ("日", "目"),
        ("田", "由"),
        ("名", "各"),
        ("島", "鳥"),
        ("シ", "ツ"),
        ("ソ", "ン"),
    ]
    rows = []
    for i in range(cfg.n_agencies):
        channel = str(rng.choice(channels))
        office_rng = np.random.default_rng(cfg.seed + i * 7919)
        profile = tuple(
            confusion_pool[int(office_rng.integers(len(confusion_pool)))]
            for _ in range(int(office_rng.integers(1, 3)))
        )
        rows.append(
            {
                "agency_id": f"A{i:03d}",
                "channel": channel,
                "missing_factor": float(np.exp(office_rng.normal(0.0, 0.35))),
                "ocr_factor": float(office_rng.uniform(0.15, 0.50)),
                "format_factor": float(np.exp(office_rng.normal(0.0, 0.25))),
                "typo_profile": profile,
                # the office's shared contact lines carry the CHANNEL's era style
                "shared_phone": generate_phone(
                    office_rng, landline=office_rng.random() < channel_landline_share(channel)
                ),
                "shared_email": f"office{i:03d}@{generate_office_domain(channel, office_rng)}",
            }
        )
    return pd.DataFrame(rows)


def generate_office_domain(channel: str, rng: np.random.Generator) -> str:
    from .config import EMAIL_DOMAINS

    return str(rng.choice(EMAIL_DOMAINS[channel_family(channel)]))


def build_observations(
    cfg: SyntheticConfig,
    noise_cfg: NoiseConfig,
    parties: pd.DataFrame,
    agencies: pd.DataFrame,
    rng: np.random.Generator,
    homophones: dict[str, tuple[str, ...]] | None = None,
    policies: list[list[Policy]] | None = None,
    history: dict[int, list[HistoryState]] | None = None,
) -> tuple[pd.DataFrame, pd.Series, dict[str, list[tuple[str, str]]]]:
    """Dirty multi-record observations per party (the reference universe).

    Each record attaches to a (policy, policy-event) pair (G1): records from
    the same policy share a policy_id across renewals, and observed_at is
    anchored to the event's year. A per-record truth snapshot (G4) is injected
    into the corruptor so stale events reflect the state true AT the record's
    date — never a future truth.

    Returns (observations, party_of[record_id -> party idx], record lineage).
    """
    if policies is None:
        policies = [[] for _ in range(len(parties))]  # bare-mode: unique per record
    if history is None:
        history = {}
    corruptor = RecordCorruptor(cfg, noise_cfg, homophones)
    agency_rows = agencies.to_dict("records")
    rows, party_of, lineage_by_item = [], {}, {}
    record_idx = 0
    t0 = time.perf_counter()
    expected = int(len(parties) * max(1.0, cfg.mean_records))
    rec_interval = max(1, expected // 20)
    for party_idx, party in parties.iterrows():
        n_records = max(1, int(rng.poisson(cfg.mean_records - 1)) + 1)
        for _ in range(n_records):
            quality_roll = rng.random()
            quality = (
                "catastrophic"
                if quality_roll < cfg.catastrophic
                else "poor"
                if quality_roll < cfg.poor
                else "average"
            )
            agency = agency_rows[int(rng.integers(len(agency_rows)))]
            channel = agency["channel"]
            low, high = noise_cfg.vintage[channel]
            # G1: attach the record to a (policy, event) that fits the channel era
            party_policies = policies[party_idx]
            if party_policies:
                policy, event = pick_policy_event(rng, party_policies, high)
                policy_id = policy.policy_id
                upper = max(low + 1, min(high, event.year) + 1)
                obs_year = int(rng.integers(low, upper))
            else:
                policy_id = f"P{record_idx // 1:010d}"
                obs_year = int(rng.integers(low, high + 1))
            snapshot = snapshot_at(history.get(party_idx, []), obs_year)
            observed_at = (
                f"{obs_year}-{int(rng.integers(1, 13)):02d}-{int(rng.integers(1, 29)):02d}"
            )
            result = corruptor.corrupt(party, agency, quality, rng, snapshot=snapshot)
            record_id = f"R{record_idx:010d}"
            rows.append(
                {
                    "record_id": record_id,
                    "policy_id": policy_id,
                    "agency_id": agency["agency_id"],
                    "channel": channel,
                    "observed_at": observed_at,
                    "record_quality": quality,
                    **result.values,
                }
            )
            party_of[record_id] = party_idx
            lineage_by_item[record_id] = result.lineage
            record_idx += 1
            if record_idx % rec_interval == 0:
                print(_eta_line("observations", record_idx, expected, t0))
    return pd.DataFrame(rows), pd.Series(party_of, dtype="int64"), lineage_by_item


def generate_queries(
    cfg: SyntheticConfig,
    noise_cfg: NoiseConfig,
    parties: pd.DataFrame,
    relation_of: dict[int, int],
    party_truth: pd.DataFrame,
    record_ids_by_truth: dict[str, list[str]],
    persona: PersonaSource,
    companies: CompanySource,
    address: AddressSource,
    buildings: BuildingComposer,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, list[dict], dict[str, list[tuple[str, str]]]]:
    """Benchmark queries + evaluation metadata + query lineage."""
    n_queries = int(len(parties) * cfg.query_share)
    scenarios = list(cfg.query_mix.keys())
    probs = list(cfg.query_mix.values())
    corruptor = QueryCorruptor(noise_cfg)
    rows, meta, lineage = [], [], {}
    relation_members: dict[int, list[int]] = {}
    for party_idx, rel in relation_of.items():
        relation_members.setdefault(rel, []).append(party_idx)
    multi_members = [m for m in relation_members.values() if len(m) >= 2]
    party_truth_idx = party_truth.set_index("truth_id")
    n = len(parties)
    t0 = time.perf_counter()
    q_interval = max(1, n_queries // 20)

    for i in range(n_queries):
        query_id = f"Q{i:010d}"
        scenario = str(rng.choice(scenarios, p=probs))
        if scenario == "related_only" and not multi_members:
            scenario = "existing_stable"

        if scenario == "new_customer":
            p = persona.sample(rng, 1)[0]
            postal, addr = address.format(address.sample(rng, p.prefecture), rng, buildings)
            values = {
                "party_type": "person",
                "name_kanji": p.name_kanji,
                "name_kana": p.name_kana,
                "name_romaji": p.name_romaji,
                "date_birth": _dob(p.age, rng, cfg),
                "postal_code": postal,
                "address": addr,
                "phone": default_phone(rng),
                "email": generate_email(p.name_kanji, p.name_romaji, rng),
            }
            party_idx = int(rng.integers(n))  # anchor truth id (not in reference)
            target = f"T{party_idx:010d}"
            owner = target
            related_truths: list[str] = []
            eligible: list[str] = []
            expected_existing = False
            action = "NEW_CUSTOMER"
            rel = relation_of.get(party_idx, party_idx)
            hard_case = ""
            lineage[query_id] = [(f, "new_customer") for f in values if f != "party_type"]
        else:
            if scenario == "related_only":
                members = list(multi_members[int(rng.integers(len(multi_members)))])
                party_idx = int(members[int(rng.integers(len(members)))])
                related_idx = int(members[int(rng.integers(len(members)))])
                while related_idx == party_idx and len(members) > 1:
                    related_idx = int(members[int(rng.integers(len(members)))])
            else:
                party_idx = int(rng.integers(n))
                related_idx = party_idx
            party = parties.loc[party_idx]
            related = parties.loc[related_idx] if scenario == "related_only" else None
            result = corruptor.corrupt(scenario, party, rng, related_party=related)
            values = result.values
            target = f"T{party_idx:010d}"
            owner = f"T{related_idx:010d}" if scenario == "related_only" else target
            rel = relation_of.get(party_idx, party_idx)
            t = party_truth_idx.loc[target]
            related_truths = (
                [f"T{m:010d}" for m in relation_members.get(rel, []) if m != party_idx]
                if scenario == "related_only"
                else []
            )
            eligible = [
                rid
                for tid in ([target] + related_truths)
                for rid in record_ids_by_truth.get(tid, [])
            ]
            expected_existing = True
            action = {
                "existing_stable": "MATCH_OR_REVIEW",
                "existing_changed": "MATCH_OR_REVIEW",
                "existing_damaged": "MATCH_OR_REVIEW",
                "related_only": "MATCH_OR_REVIEW",
                "insufficient": "REVIEW_OR_MINIMAL",
            }.get(scenario, "NEW_CUSTOMER")
            hard_case = t["hard_case"]
            lineage[query_id] = result.lineage

        rows.append(
            {
                "query_id": query_id,
                "agency_id": f"A{int(rng.integers(cfg.n_agencies)):03d}",
                "channel": str(rng.choice(["web", "agent_pc", "paper_ocr", "legacy_migration"])),
                "observed_at": cfg.query_date,
                **values,
            }
        )
        meta.append(
            {
                "query_id": query_id,
                "scenario": scenario,
                "target_truth_id": target,
                "expected_existing_customer": expected_existing,
                "expected_action": action,
                "eligible_records": eligible,
                "related_truths": related_truths,
                "relation_id": f"H{rel:08d}",
                "hard_case": hard_case,
                "value_owner_truth_id": owner,
            }
        )
        if (i + 1) % q_interval == 0:
            print(_eta_line("queries", i + 1, n_queries, t0))
    return pd.DataFrame(rows), meta, lineage
