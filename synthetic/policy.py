"""Policy lifecycle + truth timeline (SRP: one domain module).

G1 — PolicyCatalog: an Aggregate Root per customer. A policy is an event
timeline (issued -> renewals -> claim/cancel); records attach to a (policy,
event) pair, so observations share policy_ids across renewals — the way real
insurance books work (policy № is an agent's first lookup key).

G4 — HistoryBuilder: Memento states of a party's truth (renames, moves,
contact changes) with effective years. Stale events sample the state that was
actually true at the record's observed_at — temporal drift is ENFORCED
(validate_temporal), not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import POLICY_PARAMS
from .realism import default_phone, generate_email

STALE_LABELS = {"stale_rename", "stale_move", "stale_contact", "full_snapshot"}


@dataclass(frozen=True, slots=True)
class PolicyEvent:
    year: int
    kind: str  # issued | renewal | claim | cancel


@dataclass(frozen=True, slots=True)
class Policy:
    policy_id: str
    holder: int
    status: str
    events: tuple[PolicyEvent, ...]
    covered: tuple[int, ...]  # party indices sharing this policy (family plan)


@dataclass(frozen=True, slots=True)
class HistoryState:
    effective_year: int
    values: dict[str, str]


def build_policies(
    rng: np.random.Generator,
    parties: pd.DataFrame,
    relation_of: dict[int, int],
    relation_members: dict[int, list[int]],
) -> list[list[Policy]]:
    """One Aggregate Root (policy + event timeline) per customer."""
    params: dict[str, Any] = POLICY_PARAMS
    out: list[list[Policy]] = [[] for _ in range(len(parties))]
    pid = 0
    for idx in range(len(parties)):
        count = max(1, int(rng.poisson(float(params["mean_policies"]) - 1)) + 1)
        for _ in range(count):
            start = int(rng.integers(*params["start_range"]))
            events = [PolicyEvent(start, "issued")]
            cursor = start
            while cursor < 2023 and rng.random() < float(params["renew_p"]):
                cursor += int(rng.integers(1, 3))
                events.append(PolicyEvent(cursor, "renewal"))
            if len(events) > 1 and rng.random() < float(params["claim_rate"]):
                events.append(PolicyEvent(cursor, "claim"))
            status = "active"
            if rng.random() < float(params["cancel_rate"]):
                events.append(PolicyEvent(cursor, "cancel"))
                status = "cancelled"
            covered: tuple[int, ...] = (idx,)
            others = [m for m in relation_members.get(relation_of.get(idx, idx), []) if m != idx]
            if others and rng.random() < float(params["coverage_rate"]):
                covered = (idx, int(rng.choice(others)))
            out[idx].append(
                Policy(
                    policy_id=f"P{pid:010d}",
                    holder=idx,
                    status=status,
                    events=tuple(events),
                    covered=covered,
                )
            )
            pid += 1
    return out


def pick_policy_event(
    rng: np.random.Generator, policies: list[Policy], vintage_high: int
) -> tuple[Policy, PolicyEvent]:
    """Pick a (policy, event) whose year fits the record's channel era."""
    policy = policies[int(rng.integers(len(policies)))]
    candidates = [e for e in policy.events if e.year <= vintage_high] or policy.events
    return policy, candidates[int(rng.integers(len(candidates)))]


def build_history(
    rng: np.random.Generator,
    party: pd.Series,
    homophones: dict[str, tuple[str, ...]] | None,
    address: Any,
    buildings: Any,
) -> list[HistoryState]:
    """Memento states of a party's truth over time (renames/moves/contacts)."""
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
    """Latest state effective at ``year`` (Memento fold)."""
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
