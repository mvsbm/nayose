"""Generation orchestration: parties -> observations -> queries.

Pure-ish functions taking an injected ``rng``; deterministic per seed.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .config import NoiseConfig, SyntheticConfig
from .jp import ascii_romaji, ocr_mutate
from .noise import QueryCorruptor, RecordCorruptor
from .providers import AddressSource, BuildingComposer, CompanySource, PersonaSource


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


def generate_mobile(rng: np.random.Generator) -> str:
    prefix = str(rng.choice(["090", "080", "070"]))
    return f"{prefix}-{int(rng.integers(1000, 9999)):04d}-{int(rng.integers(1000, 9999)):04d}"


def generate_email(kanji: str, romaji: str, rng: np.random.Generator) -> str:
    romaji_parts = romaji.split()
    if len(kanji.split()) == 2 and len(romaji_parts) == 2:
        return f"{ascii_romaji(romaji_parts[0])}.{ascii_romaji(romaji_parts[1])}@example.com"
    return f"user{int(rng.integers(100000, 999999))}@example.com"


def _dob(person_age: int, rng: np.random.Generator, cfg: SyntheticConfig) -> str:
    year = cfg.snapshot_year - person_age
    return f"{year}-{int(rng.integers(1, 13)):02d}-{int(rng.integers(1, 29)):02d}"


def _sample_old_fields(
    party_values: dict,
    rng: np.random.Generator,
    address: AddressSource,
    buildings: BuildingComposer,
    is_person: bool,
) -> dict[str, str]:
    """Historical values (renames, relocations, contact changes) for stale
    simulations, sampled with the config's stale probability."""
    old = {
        "old_name_kanji": "",
        "old_name_kana": "",
        "old_name_romaji": "",
        "old_postal_code": "",
        "old_address": "",
        "old_phone": "",
        "old_email": "",
    }
    if is_person and rng.random() < 0.30:
        old["old_name_kanji"], _ = ocr_mutate(party_values["name_kanji"], 0.3, rng)
        old["old_name_kana"], _ = ocr_mutate(party_values["name_kana"], 0.3, rng)
        old["old_name_romaji"], _ = ocr_mutate(party_values["name_romaji"], 0.3, rng)
    if rng.random() < 0.30:
        postal, addr = address.format(
            address.sample(rng, party_values.get("prefecture")), rng, buildings
        )
        old["old_postal_code"], old["old_address"] = postal, addr
    if rng.random() < 0.15:
        old["old_phone"] = generate_mobile(rng)
        old["old_email"] = generate_email(
            party_values["name_kanji"], party_values["name_romaji"], rng
        )
    return old


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
            old = _sample_old_fields(
                {
                    "name_kanji": kanji,
                    "name_kana": kana,
                    "name_romaji": romaji,
                    "prefecture": p.prefecture,
                },
                rng,
                address,
                buildings,
                is_person=True,
            )
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
                        "phone": generate_mobile(rng),
                        "email": f"user{int(rng.integers(100000, 999999))}@example.com",
                        "old_name_kanji": "",
                        "old_name_kana": "",
                        "old_name_romaji": "",
                        "old_postal_code": "",
                        "old_address": "",
                        "old_phone": "",
                        "old_email": "",
                    }
                )
        else:
            c = companies.sample(rng)
            postal, full_addr = address.format(address.sample(rng, None), rng, buildings)
            kanji, kana, romaji = c.name_kanji, c.name_kana, c.name_romaji
            dob = ""
            email = generate_email(kanji, romaji, rng)
            party_type = "company"
            old = _sample_old_fields(
                {"name_kanji": kanji, "name_kana": kana, "name_romaji": romaji, "prefecture": None},
                rng,
                address,
                buildings,
                is_person=False,
            )
        rows.append(
            {
                "party_type": party_type,
                "name_kanji": kanji,
                "name_kana": kana,
                "name_romaji": romaji,
                "date_birth": dob,
                "postal_code": postal,
                "address": full_addr,
                "phone": generate_mobile(rng),
                "email": email,
                **old,
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
    channels = ["web", "agent_pc", "paper_ocr", "legacy_migration"]
    rows = []
    for i in range(cfg.n_agencies):
        rows.append(
            {
                "agency_id": f"A{i:03d}",
                "channel": str(rng.choice(channels)),
                "missing_factor": float(np.exp(rng.normal(0.0, 0.35))),
                "ocr_factor": float(rng.uniform(0.15, 0.35)),
                "format_factor": float(np.exp(rng.normal(0.10, 0.20))),
                "shared_phone": generate_mobile(rng),
                "shared_email": f"agency{i:03d}@agency.invalid",
            }
        )
    return pd.DataFrame(rows)


def build_observations(
    cfg: SyntheticConfig,
    noise_cfg: NoiseConfig,
    parties: pd.DataFrame,
    agencies: pd.DataFrame,
    rng: np.random.Generator,
    homophones: dict[str, tuple[str, ...]] | None = None,
) -> tuple[pd.DataFrame, pd.Series, dict[str, list[tuple[str, str]]]]:
    """Dirty multi-record observations per party (the reference universe).

    Returns (observations, party_of[record_id -> party idx], record lineage).
    """
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
            observed_at = (
                f"{int(rng.integers(low, high + 1))}-"
                f"{int(rng.integers(1, 13)):02d}-{int(rng.integers(1, 29)):02d}"
            )
            result = corruptor.corrupt(party, agency, quality, rng)
            record_id = f"R{record_idx:010d}"
            rows.append(
                {
                    "record_id": record_id,
                    "policy_id": f"P{record_idx:010d}",
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
                "phone": generate_mobile(rng),
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
