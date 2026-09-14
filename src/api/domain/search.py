"""Domain filters shared by entity search adapters and use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
