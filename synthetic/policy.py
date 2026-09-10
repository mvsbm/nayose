"""Policy lifecycle and truth timeline.

G1 — PolicyCatalog: an Aggregate Root per customer. A policy is an event
timeline (issued -> renewals -> claim/cancel); records attach to a (policy,
event) pair, so observations share policy_ids across renewals — the way real
insurance books work (policy № is an agent's first lookup key).

G4 — HistoryBuilder: historical states of a party's truth (renames, moves,
contact changes) with effective years. Stale events sample the state that was
actually true at the record's observed_at — temporal drift is ENFORCED
(validate_temporal), not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import POLICY_PARAMS, PRODUCT_PROFILES
from .realism import default_phone, generate_email

STALE_LABELS = {"stale_rename", "stale_move", "stale_contact", "full_snapshot"}


@dataclass(frozen=True, slots=True)
class PolicyEvent:
    year: int
    kind: str  # issued | renewal | claim_opened | claim_closed | lapse | reinstatement | cancel
    term_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PolicyTerm:
    policy_id: str
    term_id: str
    version: int
    product_type: str
    start_year: int
    end_year: int
    status: str


@dataclass(frozen=True, slots=True)
class PolicyPartyRole:
    policy_id: str
    term_id: str
    party_idx: int
    role: str


@dataclass(frozen=True, slots=True)
class Policy:
    policy_id: str
    holder: int
    status: str
    events: tuple[PolicyEvent, ...]
    covered: tuple[int, ...]  # compatibility view of policy members
    product_type: str = "auto"
    risk_id: str = ""
    risk_kind: str = "vehicle"
    risk_attributes: tuple[tuple[str, str], ...] = ()
    terms: tuple[PolicyTerm, ...] = ()
    roles: tuple[PolicyPartyRole, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoryState:
    effective_year: int
    values: dict[str, str]


def _product_type(rng: np.random.Generator) -> str:
    products = list(POLICY_PARAMS["product_mix"])
    weights = np.asarray([PRODUCT_PROFILES[name]["weight"] for name in products], dtype=float)
    weights /= weights.sum()
    return str(rng.choice(products, p=weights))


def _event_priority(kind: str) -> int:
    return {
        "issued": 0,
        "renewal": 1,
        "claim_opened": 2,
        "claim_closed": 3,
        "lapse": 4,
        "reinstatement": 5,
        "cancel": 6,
    }.get(kind, 99)


def _policy_status(events: tuple[PolicyEvent, ...]) -> str:
    status = "active"
    for event in events:
        if event.kind == "lapse":
            status = "lapsed"
        elif event.kind == "reinstatement":
            status = "active"
        elif event.kind == "cancel":
            status = "cancelled"
    return status


def build_policies(
    rng: np.random.Generator,
    parties: pd.DataFrame,
    relation_of: dict[int, int],
    relation_members: dict[int, list[int]],
) -> list[list[Policy]]:
    """Build clean policy portfolios and attach them to every covered party."""
    params: dict[str, Any] = POLICY_PARAMS
    out: list[list[Policy]] = [[] for _ in range(len(parties))]
    pid = 0
    for idx in range(len(parties)):
        count = max(1, int(rng.poisson(float(params["mean_policies"]) - 1)) + 1)
        for _ in range(count):
            policy_id = f"P{pid:010d}"
            product_type = _product_type(rng)
            profile = PRODUCT_PROFILES[product_type]
            term_years = int(profile["term_years"])
            start = int(rng.integers(*params["start_range"]))
            terms: list[PolicyTerm] = []
            events: list[PolicyEvent] = []
            version = 1
            cursor = start
            term_id = f"{policy_id}-T{version:02d}"
            terms.append(
                PolicyTerm(
                    policy_id, term_id, version, product_type, cursor, cursor + term_years, "active"
                )
            )
            events.append(PolicyEvent(cursor, "issued", term_id=term_id))
            while cursor < 2023 and rng.random() < float(profile["renew_p"]):
                cursor += term_years
                version += 1
                next_term_id = f"{policy_id}-T{version:02d}"
                terms[-1] = PolicyTerm(
                    policy_id,
                    terms[-1].term_id,
                    terms[-1].version,
                    product_type,
                    terms[-1].start_year,
                    cursor,
                    "renewed",
                )
                terms.append(
                    PolicyTerm(
                        policy_id,
                        next_term_id,
                        version,
                        product_type,
                        cursor,
                        cursor + term_years,
                        "active",
                    )
                )
                events.append(PolicyEvent(cursor, "renewal", term_id=next_term_id))
                term_id = next_term_id
            if cursor > start and rng.random() < float(profile["claim_rate"]):
                claim_year = int(rng.integers(start, cursor + 1))
                close_low, close_high = params["claim_close_years"]
                close_year = min(
                    cursor + 1, claim_year + int(rng.integers(close_low, close_high + 1))
                )
                events.extend(
                    [
                        PolicyEvent(claim_year, "claim_opened", term_id=term_id),
                        PolicyEvent(close_year, "claim_closed", term_id=term_id),
                    ]
                )
            status = "active"
            cancellation_year = cursor
            if rng.random() < float(profile["lapse_rate"]):
                events.append(
                    PolicyEvent(
                        cursor,
                        "lapse",
                        term_id=term_id,
                        reason=str(rng.choice(params["lapse_reasons"])),
                    )
                )
                status = "lapsed"
                if rng.random() < float(profile["reinstatement_rate"]):
                    reinstatement_year = cursor + 1
                    events.append(PolicyEvent(reinstatement_year, "reinstatement", term_id=term_id))
                    cancellation_year = reinstatement_year
            if rng.random() < float(profile["cancel_rate"]):
                events.append(
                    PolicyEvent(
                        cancellation_year,
                        "cancel",
                        term_id=term_id,
                        reason=str(rng.choice(params["cancellation_reasons"])),
                    )
                )
                status = "cancelled"
            events = sorted(events, key=lambda event: (event.year, _event_priority(event.kind)))
            event_tuple = tuple(events)
            policy_status = _policy_status(event_tuple) if status == "active" else status
            terms[-1] = PolicyTerm(
                terms[-1].policy_id,
                terms[-1].term_id,
                terms[-1].version,
                terms[-1].product_type,
                terms[-1].start_year,
                terms[-1].end_year,
                policy_status,
            )
            roles = [
                PolicyPartyRole(policy_id, term.term_id, idx, "named_insured") for term in terms
            ]
            covered_members = [idx]
            others = [m for m in relation_members.get(relation_of.get(idx, idx), []) if m != idx]
            for member in others:
                if rng.random() < float(profile["family_rate"]):
                    covered_members.append(member)
                    roles.extend(
                        PolicyPartyRole(
                            policy_id, term.term_id, member, str(profile["family_role"])
                        )
                        for term in terms
                    )
            risk_values = {
                "vehicle_id": f"VH{pid:010d}",
                "garaging_address": str(parties.loc[idx].get("address", "")),
                "driver_truth_ids": ",".join(f"T{member:010d}" for member in covered_members),
                "risk_address": str(parties.loc[idx].get("address", "")),
                "property_type": str(rng.choice(profile["property_types"])),
                "occupancy": str(rng.choice(profile["occupancies"])),
                "household_address": str(parties.loc[idx].get("address", "")),
                "liability_scope": "household",
            }
            risk_attributes = tuple((field, risk_values[field]) for field in profile["risk_fields"])
            policy = Policy(
                policy_id=policy_id,
                holder=idx,
                status=policy_status,
                events=event_tuple,
                covered=tuple(covered_members),
                product_type=product_type,
                risk_id=f"{str(profile['risk_kind'])[0].upper()}{pid:010d}",
                risk_kind=str(profile["risk_kind"]),
                risk_attributes=risk_attributes,
                terms=tuple(terms),
                roles=tuple(roles),
            )
            for member in covered_members:
                out[member].append(policy)
            pid += 1
    return out


def unique_policies(portfolios: list[list[Policy]]) -> list[Policy]:
    """Return one copy of each policy from party portfolio memberships."""
    found: dict[str, Policy] = {}
    for portfolio in portfolios:
        for policy in portfolio:
            found.setdefault(policy.policy_id, policy)
    return list(found.values())


def pick_policy_event(
    rng: np.random.Generator, policies: list[Policy], vintage_high: int
) -> tuple[Policy, PolicyEvent]:
    """Pick a policy and event that fit the source channel's vintage."""
    eligible = [policy for policy in policies if any(e.year <= vintage_high for e in policy.events)]
    if not eligible:
        raise ValueError(f"no policy event is available before vintage {vintage_high}")
    policy = eligible[int(rng.integers(len(eligible)))]
    candidates = [e for e in policy.events if e.year <= vintage_high]
    return policy, candidates[int(rng.integers(len(candidates)))]


def policy_status_at(policy: Policy, year: int) -> str:
    """Resolve the policy state visible at a historical observation year."""
    status = "not_started"
    for event in policy.events:
        if event.year > year:
            break
        if event.kind == "issued":
            status = "active"
        elif event.kind == "lapse":
            status = "lapsed"
        elif event.kind == "reinstatement":
            status = "active"
        elif event.kind == "cancel":
            status = "cancelled"
    return status


def policy_role(policy: Policy, party_idx: int, term_id: str) -> str:
    """Find the party's role for a policy term, failing closed to holder."""
    for role in policy.roles:
        if role.party_idx == party_idx and role.term_id == term_id:
            return role.role
    return "named_insured" if party_idx == policy.holder else "covered_party"


def build_history(
    rng: np.random.Generator,
    party: pd.Series,
    homophones: dict[str, tuple[str, ...]] | None,
    address: Any,
    buildings: Any,
) -> list[HistoryState]:
    """Historical states of a party's truth over time."""
    if rng.random() > float(POLICY_PARAMS["history_rate"]):
        return []
    dob = str(party.get("date_birth", ""))
    birth_year = int(dob[:4]) if len(dob) >= 4 and dob[:4].isdigit() else 1950
    low = min(max(1985, birth_year + 18), 2022)
    years = sorted(set(int(rng.integers(low, 2023)) for _ in range(int(rng.integers(1, 3)))))
    states: list[HistoryState] = []
    for year in years:
        kind = str(rng.choice(("rename", "move", "contact")))
        values: dict[str, str] = {}
        if kind == "rename":
            key = str(party["name_kana"]).replace(" ", "")
            current = str(party["name_kanji"]).replace(" ", "")
            pool = [v for v in (homophones or {}).get(key, ()) if v != current]
            if pool:
                values["name_kanji"] = str(rng.choice(pool))
        elif kind == "move":
            postal, addr = address.format(
                address.sample(rng, str(party.get("prefecture")) or None), rng, buildings
            )
            values["postal_code"], values["address"] = postal, addr
        else:
            values["phone"] = default_phone(rng)
            values["email"] = generate_email(
                str(party["name_kanji"]), str(party["name_romaji"]), rng
            )
        if values:
            states.append(HistoryState(effective_year=year, values=values))
    return states


def history_by_party(
    rng: np.random.Generator,
    parties: pd.DataFrame,
    homophones: dict[str, tuple[str, ...]] | None,
    address: Any,
    buildings: Any,
) -> dict[int, list[HistoryState]]:
    return {
        idx: build_history(rng, party, homophones, address, buildings)
        for idx, party in parties.iterrows()
    }


def snapshot_at(history: list[HistoryState], year: int) -> dict[str, str]:
    """Latest state effective at ``year``."""
    merged: dict[str, str] = {}
    for state in history:
        if state.effective_year <= year:
            merged.update(state.values)
    return merged


def validate_temporal(
    observations: pd.DataFrame,
    party_of: dict,
    history: dict[int, list[HistoryState]],
    record_lineage: dict[str, list[tuple[str, str]]],
) -> list[str]:
    """Data-contract guard: a record observed at year t must never carry a
    truth value newer than t (stale events must exactly reflect the state that
    was true then). Returns violations (empty == invariant holds)."""
    violations: list[str] = []
    for _, rec in observations.iterrows():
        obs_year = int(str(rec["observed_at"])[:4])
        snapshot = snapshot_at(history.get(party_of.get(rec["record_id"], -1), []), obs_year)
        if not snapshot:
            continue
        last_label: dict[str, str] = {}
        for field, label in record_lineage.get(rec["record_id"], []):
            last_label[field] = label
        for field, label in last_label.items():
            if label in STALE_LABELS and snapshot.get(field):
                observed = str(rec.get(field, ""))
                if observed and observed != snapshot[field]:
                    violations.append(
                        f"{rec['record_id']} @ {obs_year}: {field} observed {observed!r} "
                        f"!= snapshot {snapshot[field]!r}"
                    )
    return violations
