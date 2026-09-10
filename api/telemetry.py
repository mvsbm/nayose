"""Endpoint timing counters used by the operational health surface."""

from __future__ import annotations

from typing import Any


class Stats:
    def __init__(self):
        self.counts: dict[str, list[float]] = {}

    def record(self, name: str, seconds: float) -> None:
        row = self.counts.setdefault(name, [0.0, 0.0])
        row[0] += 1
        row[1] += seconds

    def reset(self) -> None:
        self.counts.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {"endpoint": key, "hits": int(value[0]), "total_ms": round(value[1] * 1000, 1)}
            for key, value in sorted(self.counts.items())
        ]
