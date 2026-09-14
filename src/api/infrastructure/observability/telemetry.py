"""Endpoint timing counters used by the operational health surface."""

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from typing import Any


class Stats:
    """Bounded, process-local endpoint telemetry.

    ``total_ms`` remains part of the response for compatibility. Samples are
    capped so a long-running process cannot retain unbounded request history.
    """

    _SAMPLE_LIMIT = 1_024

    def __init__(self):
        self.counts: dict[str, list[float]] = {}
        self.errors: dict[str, int] = defaultdict(int)
        self.samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._SAMPLE_LIMIT)
        )
        self._lock = Lock()

    def record(self, name: str, seconds: float, *, error: bool = False) -> None:
        with self._lock:
            row = self.counts.setdefault(name, [0.0, 0.0])
            row[0] += 1
            row[1] += seconds
            self.samples[name].append(seconds)
            if error:
                self.errors[name] += 1

    def reset(self) -> None:
        with self._lock:
            self.counts.clear()
            self.errors.clear()
            self.samples.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for key in sorted(self.counts):
                hits, total = self.counts[key]
                samples = sorted(self.samples[key])
                rows.append(
                    {
                        "endpoint": key,
                        "hits": int(hits),
                        "errors": self.errors.get(key, 0),
                        "total_ms": round(total * 1000, 1),
                        "p50_ms": _percentile_ms(samples, 0.50),
                        "p95_ms": _percentile_ms(samples, 0.95),
                    }
                )
            return rows


def _percentile_ms(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    index = min(len(samples) - 1, int((len(samples) - 1) * percentile))
    return round(samples[index] * 1000, 1)
