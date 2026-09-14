from __future__ import annotations

import sys
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from api.infrastructure.process.work_ledger import WorkflowLedger
from api.training.contracts import StageRecord


class ResourceModule(Protocol):
    RUSAGE_SELF: int

    def getrusage(self, resource: int) -> object: ...


resource: ResourceModule | None
try:
    import resource as resource
except ImportError:
    resource = None


T = TypeVar("T")


@dataclass(slots=True)
class StageReporter:
    ledger: WorkflowLedger
    records: list[StageRecord] = field(default_factory=list)
    sample_resources: bool = True

    def run(self, name: str, operation: Callable[[], T]) -> T:
        sampled = (
            self.sample_resources and name != "COMPUTE STRUCTURED FEATURES FOR EVERY CANDIDATE EDGE"
        )
        before = _rss_mib() if sampled else 0.0
        started = time.perf_counter()
        print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
        ledger_span = self.ledger.span(name, "pipeline") if sampled else nullcontext()
        try:
            with ledger_span:
                result = operation()
        except Exception as exc:
            elapsed = time.perf_counter() - started
            self.records.append(
                StageRecord(name, elapsed, before, _rss_mib() if sampled else before, 0.0, sampled)
            )
            self.ledger.metadata(f"failed_stage.{name}", type(exc).__name__)
            raise
        elapsed = time.perf_counter() - started
        after = _rss_mib() if sampled else before
        self.records.append(StageRecord(name, elapsed, before, after, after - before, sampled))
        print(f"completed in {elapsed:,.2f}s; RSS={after:,.1f} MiB")
        return result


def _rss_mib() -> float:
    if resource is None:
        return float("nan")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    value = getattr(usage, "ru_maxrss", 0)
    if sys.platform != "darwin":
        value *= 1024
    return float(value) / 2**20
