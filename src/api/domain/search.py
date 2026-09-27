"""Domain filters shared by entity search adapters and use cases."""

from __future__ import annotations

import re
import unicodedata as ud
from dataclasses import dataclass
from typing import Any

from api.domain.normalize import normalize_email, normalize_phone, normalize_postal


def structured_query_fields(query: str) -> tuple[tuple[str, str], ...]:
    """Return high-confidence structured fields for a single-value query."""
    raw = ud.normalize("NFKC", query.strip())
    digits = re.sub(r"\D+", "", raw)
    if "@" in raw:
        return (("email", normalize_email(raw)),)
    if len(digits) == 7:
        return (("postal_code", normalize_postal(raw)),)
    if len(digits) in {10, 11} or (digits.startswith("81") and len(digits) == 12):
        return (("phone", normalize_phone(raw)),)
    return ()


@dataclass(frozen=True)
class SearchFilters:
    party_type: str | None = None
    min_record_count: int | None = None

    def matches(self, hit: dict[str, Any]) -> bool:
        party_matches = not self.party_type or (
            str(hit.get("party_type") or "").casefold() == self.party_type.casefold()
        )
        count_matches = self.min_record_count is None or (
            int(hit.get("record_count") or 0) >= self.min_record_count
        )
        return party_matches and count_matches

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "party_type": self.party_type,
                "min_record_count": self.min_record_count,
            }.items()
            if value is not None
        }
