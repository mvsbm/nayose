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

import jaconv
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
    """Applies source-driven, truth-derivable corruption to one reference record.

    ``homophones`` maps a kana reading to the distinct kanji spellings observed
    in the real name corpus (同音異字), enabling kana-driven kanji divergence.
    """

    def __init__(self, cfg: SyntheticConfig, noise: NoiseConfig,
                 homophones: dict[str, tuple[str, ...]] | None = None):
        self.cfg = cfg
        self.noise = noise
        self.homophones = homophones

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

    def _e_full_snapshot(self, values, party, rng) -> None:
        """Record is a coherent PRE-MOVE / PRE-RE-CONTACT snapshot of the truth:
        name, address(+postal) and phone(+email) all show the entity's OLD values
        (captured before the canonical update). Internally consistent; the old
        values come from the truth itself (party.old_*)."""
        old_name = party.get("old_name_kanji", "")
        old_addr = party.get("old_address", "")
        old_phone = party.get("old_phone", "")
        if old_name and old_name != values["name_kanji"]:
            values["name_kanji"] = old_name
            values["name_kana"] = str(party.get("old_name_kana", "")) or values["name_kana"]
            values["name_romaji"] = str(party.get("old_name_romaji", "")) or values["name_romaji"]
        if old_addr and old_addr != values["address"]:
            values["address"] = old_addr
            values["postal_code"] = str(party.get("old_postal_code", "")) or values["postal_code"]
        if old_phone and old_phone != values["phone"]:
            values["phone"] = old_phone
            values["email"] = str(party.get("old_email", "")) or values["email"]
        fields = (
            "name_kanji",
            "name_kana",
            "name_romaji",
            "postal_code",
            "address",
            "phone",
            "email",
        )
        for f in fields:
            self.protected.add(f)
            self.lineage.append((f, "full_snapshot"))

    def _e_contact_omission(self, values, party, rng) -> None:
        """A data-entry omission: the whole contact block (phone + email) is
        absent together (no partial contact info on a scanned card)."""
        values["phone"] = ""
        values["email"] = ""
        self.lineage.append(("phone", "missing"))
        self.lineage.append(("email", "missing"))
        self.protected.add("phone")
        self.protected.add("email")

    def _e_homophone_kanji(self, values, party, rng) -> None:
        """Reading (kana) stays; kanji diverges to a REAL homophone spelling
        (佐藤 -> 左藤, same サトウ reading). Truth keeps the canonical kanji;
        the observed record carries the typist's spelling."""
        if self.homophones is None:
            return
        key = values["name_kana"].replace(" ", "")
        pool = self.homophones.get(key)
        if not pool:
            return
        current = values["name_kanji"].replace(" ", "")
        candidates = [k for k in pool if k != current]
        if not candidates:
            return
        chosen = str(rng.choice(candidates))
        values["name_kanji"] = chosen
        self.lineage.append(("name_kanji", "homophone_kanji"))
        self.protected.add("name_kanji")

    def _e_kana_form(self, values, party, rng) -> None:
        """Same reading, different kana script: hiragana or half-width katakana."""
        if not values["name_kana"]:
            return
        if rng.random() < 0.5:
            values["name_kana"] = jaconv.kata2hira(values["name_kana"])
        else:
            values["name_kana"] = mixed_width(values["name_kana"])
        self.lineage.append(("name_kana", "kana_form"))
        self.protected.add("name_kana")

    def _e_digit_transpose(self, values, party, rng) -> None:
        """Classic typist typo: two adjacent digits swapped in the phone number."""
        chars = list(values["phone"])
        digit_idx = [i for i, ch in enumerate(chars) if ch.isdigit()]
        if len(digit_idx) < 2:
            return
        pos = int(rng.integers(len(digit_idx) - 1))
        a, b = digit_idx[pos], digit_idx[pos + 1]
        chars[a], chars[b] = chars[b], chars[a]
        values["phone"] = "".join(chars)
        self.lineage.append(("phone", "digit_transpose"))
        self.protected.add("phone")

    def _e_email_typo(self, values, party, rng) -> None:
        """Local-part typo in email (stays the same person; domain often intact)."""
        if "@" not in values["email"]:
            return
        local, _, domain = values["email"].partition("@")
        if len(local) < 3:
            return
        target = int(rng.integers(len(local)))
        local = local[:target] + "x" + local[target + 1:]
        values["email"] = f"{local}@{domain}"
        self.lineage.append(("email", "email_typo"))
        self.protected.add("email")

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
        "full_snapshot": _e_full_snapshot,
        "contact_omission": _e_contact_omission,
        "homophone_kanji": _e_homophone_kanji,
        "kana_form": _e_kana_form,
        "digit_transpose": _e_digit_transpose,
        "email_typo": _e_email_typo,
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
        channel = agency.get("channel", "web")

        # (A) SYSTEMATIC / vintage availability: some fields are absent for a
        # whole channel (legacy never had email), not randomly missing.
        availability = noise.field_availability.get(channel, {})
        for field, keep in availability.items():
            if values[field] and keep < 1.0 and rng.random() > keep:
                values[field] = ""
                self.lineage.append((field, "missing"))

        # (B) doc_damage first so a stale value that survives damage is never
        # blanked afterwards.
        if rng.random() < min(1.0, noise.event_rates["doc_damage"] * scale):
            self._e_doc_damage(values, party, rng)

        # (C) SOURCE-DOCUMENT BUNDLE: the channel's characteristic disorders
        # fire as one coherent group (typed-from-this-form errors).
        bundle = noise.channel_events.get(channel, ())
        if bundle and rng.random() < min(1.0, 0.6 * scale):
            for name in bundle:
                p = noise.event_rates.get(name, 0.05) * scale
                if rng.random() < min(1.0, p):
                    self.EVENT_HANDLERS[name](self, values, party, rng)

        # (D) GLOBAL events (stale/history events apply regardless of channel).
        event_order = [k for k in noise.event_rates if k not in ("doc_damage", *bundle)]
        for name in event_order:
            p = noise.event_rates[name] * scale
            if rng.random() < min(1.0, p):
                self.EVENT_HANDLERS[name](self, values, party, rng)

        # (E) Fields a system can NEVER hold stay absent (legacy email = "").
        for field, keep in availability.items():
            if keep == 0.0:
                values[field] = ""
                self.lineage.append((field, "missing"))

        # (F) residual band-driven omission only (accidental per-record blank);
        # fields already purposefully corrupted by an event stay coherent.
        touched = {f for f, _ in self.lineage}
        for field in CUSTOMER_FIELDS:
            if not values[field] or field in touched:
                continue
            if rng.random() < min(0.9, noise.missing_rates[quality] * agency["missing_factor"]):
                values[field] = ""
                self.lineage.append((field, "missing"))

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
