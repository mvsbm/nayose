"""Low-overhead workflow spans and process resource accounting."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol, TypeAlias

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows
    resource = None  # type: ignore[assignment]

JsonScalar: TypeAlias = str | int | float | bool | None
Attributes: TypeAlias = dict[str, JsonScalar]
MetricEvent: TypeAlias = dict[str, JsonScalar | Attributes]


class MetricSink(Protocol):
    """Destination for optional workflow events."""

    def emit(self, event: MetricEvent) -> None:
        """Publish one non-sensitive workflow event."""


class NullMetricSink:
    """No-op sink for callers that only need the workflow API."""

    def emit(self, event: MetricEvent) -> None:
        del event


class InMemoryMetricSink:
    """Bounded sink useful for tests and embedding in another exporter."""

    def __init__(self, max_events: int = 4_096) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: list[MetricEvent] = []
        self._max_events = max_events
        self._lock = Lock()

    def emit(self, event: MetricEvent) -> None:
        with self._lock:
            if len(self._events) < self._max_events:
                self._events.append(dict(event))

    def snapshot(self) -> list[MetricEvent]:
        with self._lock:
            return [dict(event) for event in self._events]


class JsonlMetricSink:
    """Append workflow events as JSONL without coupling callers to files."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def emit(self, event: MetricEvent) -> None:
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    user_cpu_seconds: float
    system_cpu_seconds: float
    rss_bytes: int


def current_resource_usage() -> ResourceUsage:
    """Return portable process CPU time and resident-memory measurements."""
    if resource is None:
        return ResourceUsage(0.0, 0.0, 0)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss_bytes = int(usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024)
    return ResourceUsage(
        user_cpu_seconds=float(usage.ru_utime),
        system_cpu_seconds=float(usage.ru_stime),
        rss_bytes=rss_bytes,
    )


@dataclass(slots=True)
class SpanHandle:
    name: str
    boundary: str
    attributes: Attributes

    def set_attribute(self, name: str, value: JsonScalar) -> None:
        self.attributes[name] = value


class WorkflowLedger:
    """Collect bounded workflow spans and counters without retaining payloads."""

    def __init__(
        self,
        workflow: str,
        *,
        workflow_id: str | None = None,
        metadata: Mapping[str, JsonScalar] | None = None,
        enabled: bool = True,
        sink: MetricSink | None = None,
    ) -> None:
        if not workflow.strip():
            raise ValueError("workflow must not be empty")
        self.workflow = workflow.strip()
        self.workflow_id = workflow_id or f"{self.workflow}-{os.getpid()}-{time.time_ns()}"
        self.enabled = enabled
        self._started_at = datetime.now(UTC).isoformat()
        self._started = time.perf_counter()
        self._start_usage = current_resource_usage()
        self._peak_rss_bytes = self._start_usage.rss_bytes
        self._spans: list[MetricEvent] = []
        self._counters: dict[str, int | float] = {}
        self._metadata: Attributes = dict(metadata or {})
        self._sink = sink or NullMetricSink()
        self._sink_errors = 0
        self._lock = Lock()

    @contextmanager
    def span(
        self,
        name: str,
        boundary: str,
        attributes: Mapping[str, JsonScalar] | None = None,
    ) -> Iterator[SpanHandle]:
        """Measure one architectural operation and re-raise its failures."""
        handle = SpanHandle(name, boundary, dict(attributes or {}))
        if not self.enabled:
            yield handle
            return
        started = time.perf_counter()
        before = current_resource_usage()
        self._observe(before)
        status = "ok"
        try:
            yield handle
        except BaseException as exc:
            status = "error"
            handle.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            after = current_resource_usage()
            self._observe(after)
            event: MetricEvent = {
                "name": handle.name,
                "boundary": handle.boundary,
                "status": status,
                "wall_ms": round((time.perf_counter() - started) * 1000, 3),
                "cpu_user_ms": round(
                    max(0.0, after.user_cpu_seconds - before.user_cpu_seconds) * 1000,
                    3,
                ),
                "cpu_system_ms": round(
                    max(0.0, after.system_cpu_seconds - before.system_cpu_seconds) * 1000,
                    3,
                ),
                "rss_peak_bytes": after.rss_bytes,
                "attributes": dict(handle.attributes),
            }
            with self._lock:
                self._spans.append(event)
            self._emit({"kind": "span", **event})

    def counter(self, name: str, amount: int | float = 1) -> None:
        """Increment a numeric work-unit counter."""
        if not name.strip():
            raise ValueError("counter name must not be empty")
        with self._lock:
            total = self._counters.get(name, 0) + amount
            self._counters[name] = total
        self._emit({"kind": "counter", "name": name, "amount": amount, "total": total})

    def metadata(self, name: str, value: JsonScalar) -> None:
        """Attach non-sensitive workflow metadata to the final summary."""
        with self._lock:
            self._metadata[name] = value
        self._emit({"kind": "metadata", "name": name, "value": value})

    def summary(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot of the workflow measurements."""
        final_usage = current_resource_usage()
        self._observe(final_usage)
        with self._lock:
            metadata = dict(self._metadata)
            counters = dict(self._counters)
            spans = list(self._spans)
            peak_rss_bytes = self._peak_rss_bytes
            sink_errors = self._sink_errors
        return {
            "schema_version": 1,
            "workflow": self.workflow,
            "workflow_id": self.workflow_id,
            "started_at_utc": self._started_at,
            "duration_ms": round((time.perf_counter() - self._started) * 1000, 3),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "pid": os.getpid(),
            },
            "metadata": metadata,
            "counters": counters,
            "resource": {
                "user_cpu_ms": round(
                    max(0.0, final_usage.user_cpu_seconds - self._start_usage.user_cpu_seconds)
                    * 1000,
                    3,
                ),
                "system_cpu_ms": round(
                    max(0.0, final_usage.system_cpu_seconds - self._start_usage.system_cpu_seconds)
                    * 1000,
                    3,
                ),
                "peak_rss_bytes": peak_rss_bytes,
            },
            "spans": spans,
            "sink_errors": sink_errors,
        }

    def write(self, path: Path) -> dict[str, object]:
        """Atomically write the summary and return it."""
        summary = self.summary()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return summary

    def _emit(self, event: MetricEvent) -> None:
        if not self.enabled:
            return
        enriched = {"workflow": self.workflow, "workflow_id": self.workflow_id, **event}
        try:
            self._sink.emit(enriched)
        except Exception:
            with self._lock:
                self._sink_errors += 1

    def _observe(self, usage: ResourceUsage) -> None:
        with self._lock:
            self._peak_rss_bytes = max(self._peak_rss_bytes, usage.rss_bytes)
