"""Parse generator stdout ETA lines into deterministic admin job updates.

Unparseable lines yield None; the UI then uses an indeterminate state instead
of presenting fabricated progress.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# matches engine._eta_line:  [parties] 1,234/150,000 (1%) eta 12:34  (or H:MM:SS)
_ETA_RE = re.compile(
    r"\[\s*(\w+)\s*\]\s*([\d,]+)/([\d,]+)\s*\((\d+)%\)\s*eta\s+(?:(\d+):)?(\d+):(\d+)"
)


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    phase: str
    done: int
    total: int
    pct: float
    eta_seconds: int


def parse(line: str) -> ProgressUpdate | None:
    match = _ETA_RE.search(line)
    if match is None:
        return None
    phase, done, total, pct, hours, minutes, seconds = match.groups()
    return ProgressUpdate(
        phase=phase,
        done=int(done.replace(",", "")),
        total=int(total.replace(",", "")),
        pct=min(100.0, max(0.0, float(pct))),
        eta_seconds=int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds),
    )
