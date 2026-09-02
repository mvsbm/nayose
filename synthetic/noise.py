"""Dirty-data engine: transformation catalog + correlated corruption events.

Design notes
------------
- Noise is *event-based*, not per-field roulette: an event is a coherent
  bundle of field changes (e.g. ``move_stale`` replaces address AND postal
  with the old values together — realistic for a relocation that was never
  updated). This produces correlation, which per-field roulette cannot.
- Every value change returns ``(field, transformation_label)`` so the truth
  builder can emit a full field-lineage audit trail.
- Rates and severities are data (NoiseConfig), never inline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import NoiseConfig, SyntheticConfig
from .jp import date_to_era, mixed_width, ocr_mutate

CUSTOMER_FIELDS = (
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "postal_code",
    "address",
    "phone",
    "email",
)
NAME_OLD = {"name_kanji": "old_name_kanji", "name_kana": "old_name_kana", "name_romaji": ""}
MOVE_OLD = {"postal_code": "old_postal_code", "address": "old_address"}
CONTACT_OLD = {"phone": "old_phone", "email": "old_email"}


def _old(party: pd.Series, party_field: str) -> str:
    return "" if not party_field else str(party.get(party_field, ""))


def phone_variant(value: str, rng: np.random.Generator) -> str:
    digits = value.replace("-", "")
    kind = rng.choice(["plain", "plus81", "spaced"], p=[0.5, 0.25, 0.25])
    if kind == "plain":
        return digits
    if kind == "plus81":
        return "+81" + (digits[1:] if digits.startswith("0") else digits)
    return f"{digits[:3]} {digits[3:7]} {digits[7:]}"


def postal_variant(value: str, rng: np.random.Generator) -> str:
    if "-" in value:
        return value.replace("-", "")
    return f"{value[:3]}-{value[3:]}"


@dataclass(slots=True)
class CorruptionResult:
    values: dict[str, str]
    lineage: list[tuple[str, str]]  # (field, transformation_label)


class RecordCorruptor:
    """Applies correlated corruption events + single-field backfill to one
    reference record derived from a party."""

    def __init__(self, cfg: SyntheticConfig, noise: NoiseConfig):
        self.cfg = cfg
        self.noise = noise

    def _baseline(self, party: pd.Series) -> dict[str, str]:
        return {
            "party_type": str(party["party_type"]),
            "name_kanji": str(party["name_kanji"]),
            "name_kana": str(party["name_kana"]),
            "name_romaji": str(party["name_romaji"]),
            "date_birth": str(party["date_birth"]),
            "postal_code": str(party["postal_code"]),
            "address": str(party["address"]),
            "phone": str(party["phone"]),
            "email": str(party["email"]),
        }

    # -- event implementations: each mutates values in place, appends labels --
    def _e_rename_stale(self, values, party, rng) -> None:
        old = party.get("old_name_kanji", "")
        if old and old != values["name_kanji"]:
            values["name_kanji"] = str(party["old_name_kanji"])
            values["name_kana"] = str(party.get("old_name_kana", "")) or values["name_kana"]
            values["name_romaji"] = str(party.get("old_name_romaji", "")) or values["name_romaji"]
            for f in ("name_kanji", "name_kana", "name_romaji"):
                self.lineage.append((f, "stale_rename"))
                self.protected.add(f)
        else:
            val = ocr_mutate(values["name_kanji"], 0.4, rng)
            values["name_kanji"], label = val
            if label != "unchanged":
                self.lineage.append(("name_kanji", label))

    def _e_move_stale(self, values, party, rng) -> None:
        old_addr = party.get("old_address", "")
        if old_addr and old_addr != values["address"]:
            values["address"] = str(party["old_address"])
            values["postal_code"] = str(party.get("old_postal_code", "")) or values["postal_code"]
            self.lineage.append(("address", "stale_move"))
            self.lineage.append(("postal_code", "stale_move"))
            self.protected.add("address")
            self.protected.add("postal_code")

    def _e_contact_change(self, values, party, rng) -> None:
        old_phone = party.get("old_phone", "")
        if old_phone and old_phone != values["phone"]:
            values["phone"] = old_phone
            values["email"] = str(party.get("old_email", "")) or values["email"]
            self.lineage.append(("phone", "stale_contact"))
            self.lineage.append(("email", "stale_contact"))
            self.protected.add("phone")
            self.protected.add("email")

    def _e_paper_ocr(self, values, party, rng) -> None:
        for field in ("name_kanji", "name_kana", "address"):
            if field in self.protected:
                continue
            val = ocr_mutate(values[field], 0.5, rng)
            values[field], label = val
            if label != "unchanged":
                self.lineage.append((field, label))

    def _e_legacy_format(self, values, party, rng) -> None:
        dob = values["date_birth"]
        if dob:
            y, m, d = dob.split("-")
            values["date_birth"] = date_to_era(int(y), int(m), int(d))
            self.lineage.append(("date_birth", "format_date_era"))
        if values["name_kana"] and "name_kana" not in self.protected:
            values["name_kana"] = mixed_width(values["name_kana"])
            self.lineage.append(("name_kana", "width_flip"))
        if values["phone"] and "phone" not in self.protected:
            values["phone"] = phone_variant(values["phone"], rng)
            self.lineage.append(("phone", "format_phone"))

    def _e_doc_damage(self, values, party, rng) -> None:
        cluster = rng.choice(["name", "contact", "location"])
        fields = {
            "name": ("name_kanji", "name_kana", "name_romaji"),
            "contact": ("phone", "email"),
            "location": ("postal_code", "address"),
        }[cluster]
        for field in fields:
            if field not in self.protected:
                values[field] = ""
                self.lineage.append((field, "missing"))

    def _e_wrong_party_type(self, values, party, rng) -> None:
        values["party_type"] = "company" if values["party_type"] == "person" else "person"

    def _e_alpha_in_kana(self, values, party, rng) -> None:
        field = rng.choice(["name_kanji", "name_kana"])
        if values[field]:
            pos = int(rng.integers(len(values[field])))
            values[field] = (
                values[field][:pos] + chr(int(rng.integers(65, 91))) + values[field][pos:]
            )
            self.lineage.append((field, "alpha_in_kana"))

    def _e_combined_kana(self, values, party, rng) -> None:
        if " " in values["name_kana"]:
            values["name_kana"] = values["name_kana"].replace(" ", "")
            self.lineage.append(("name_kana", "combined_kana"))

    def _e_kana_in_romaji(self, values, party, rng) -> None:
        values["name_romaji"] = values["name_kana"]
        self.lineage.append(("name_romaji", "kana_in_romaji"))

    EVENT_HANDLERS = {
        "rename_stale": _e_rename_stale,
        "move_stale": _e_move_stale,
        "contact_change": _e_contact_change,
        "paper_ocr": _e_paper_ocr,
        "legacy_format": _e_legacy_format,
        "doc_damage": _e_doc_damage,
        "wrong_party_type": _e_wrong_party_type,
        "alpha_in_kana": _e_alpha_in_kana,
        "combined_kana": _e_combined_kana,
        "kana_in_romaji": _e_kana_in_romaji,
    }

    def corrupt(
        self, party: pd.Series, agency: dict, quality: str, rng: np.random.Generator
    ) -> CorruptionResult:
        values = self._baseline(party)
        self.lineage: list[tuple[str, str]] = []
        self.protected: set[str] = set()  # fields already carrying event values
        noise, scale = self.noise, self.noise.band_scale[quality]

        # event bursts (scale with severity; doc_damage first so a stale value
        # that survives damage is never blanked afterwards)
        event_order = ["doc_damage"] + [k for k in noise.event_rates if k != "doc_damage"]
        for name in event_order:
            base_rate = noise.event_rates[name]
            p = base_rate * scale
            if rng.random() < min(1.0, p):
                self.EVENT_HANDLERS[name](self, values, party, rng)

        # single-field backfill: missing / width / ocr / format (fields already
        # purposefully corrupted by an event are left coherent)
        touched = {f for f, _ in self.lineage}
        for field in CUSTOMER_FIELDS:
            value = values[field]
            if not value or field in touched:
                continue
            if rng.random() < min(0.9, noise.missing_rates[quality] * agency["missing_factor"]):
                values[field] = ""
                self.lineage.append((field, "missing"))
            elif field == "phone" and rng.random() < min(0.9, 0.05 * agency["format_factor"]):
                values[field] = phone_variant(value, rng)
                self.lineage.append((field, "format_phone"))
            elif field == "postal_code" and rng.random() < min(0.9, 0.05 * agency["format_factor"]):
                values[field] = postal_variant(value, rng)
                self.lineage.append((field, "format_postal"))
            elif field == "name_kana" and rng.random() < min(0.9, 0.03 * agency["format_factor"]):
                values[field] = mixed_width(value)
                self.lineage.append((field, "width_flip"))
            elif rng.random() < min(0.9, 0.05 * agency["ocr_factor"]):
                values[field], label = ocr_mutate(value, 0.3, rng)
                if label != "unchanged":
                    self.lineage.append((field, label))

        return CorruptionResult(values=values, lineage=self.lineage)


class QueryCorruptor:
    """Scenario-driven corruption for benchmark queries (same lineage path)."""

    def __init__(self, noise: NoiseConfig):
        self.noise = noise

    def corrupt(
        self,
        scenario: str,
        party: pd.Series,
        rng: np.random.Generator,
        related_party: pd.Series | None = None,
    ) -> CorruptionResult:
        base = {
            "party_type": str(party["party_type"]),
            "name_kanji": str(party["name_kanji"]),
            "name_kana": str(party["name_kana"]),
            "name_romaji": str(party["name_romaji"]),
            "date_birth": str(party["date_birth"]),
            "postal_code": str(party["postal_code"]),
            "address": str(party["address"]),
            "phone": str(party["phone"]),
            "email": str(party["email"]),
        }
        lineage: list[tuple[str, str]] = []

        if scenario == "existing_stable":
            pass
        elif scenario == "existing_changed":
            for field in CUSTOMER_FIELDS:
                if base[field] and rng.random() < 0.30:
                    base[field], label = ocr_mutate(base[field], 0.2, rng)
                    if label != "unchanged":
                        lineage.append((field, f"changed_{label}"))
        elif scenario == "existing_damaged":
            for field in CUSTOMER_FIELDS:
                if base[field] and rng.random() < 0.5:
                    base[field], label = ocr_mutate(base[field], 0.5, rng)
                    if label != "unchanged":
                        lineage.append((field, label))
                    elif rng.random() < 0.4:
                        base[field] = ""
                        lineage.append((field, "missing"))
        elif scenario == "new_customer":
            for field in CUSTOMER_FIELDS:
                if base[field] and rng.random() < 0.6:
                    base[field], label = ocr_mutate(base[field], 0.35, rng)
                    if label != "unchanged":
                        lineage.append((field, label))
            base["postal_code"] = (
                postal_variant(base["postal_code"], rng) if base["postal_code"] else ""
            )
        elif scenario == "related_only" and related_party is not None:
            for field in CUSTOMER_FIELDS[1:]:
                base[field] = str(related_party.get(field, ""))
                lineage.append((field, "related"))
        elif scenario == "insufficient":
            for field in CUSTOMER_FIELDS:
                if rng.random() < 0.7:
                    base[field] = ""
                    lineage.append((field, "missing"))

        return CorruptionResult(values=base, lineage=lineage)
