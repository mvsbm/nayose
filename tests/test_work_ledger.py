from __future__ import annotations

import json
import time
from typing import Any, cast

import pytest

from api.infrastructure.process.work_ledger import (
    InMemoryMetricSink,
    JsonlMetricSink,
    WorkflowLedger,
)


def test_workflow_ledger_records_spans_counters_and_resources(tmp_path):
    ledger = WorkflowLedger("unit", workflow_id="unit-1", metadata={"profile": "smoke"})
    with ledger.span("load", "file", {"rows": 3}):
        time.sleep(0.001)
    ledger.counter("rows", 3)

    summary = cast(dict[str, Any], ledger.write(tmp_path / "workflow_metrics.json"))

    assert summary["workflow_id"] == "unit-1"
    assert summary["counters"] == {"rows": 3}
    assert summary["spans"][0]["status"] == "ok"
    assert summary["spans"][0]["attributes"] == {"rows": 3}
    assert summary["resource"]["peak_rss_bytes"] >= 0
    assert json.loads((tmp_path / "workflow_metrics.json").read_text()) == summary


def test_metric_sinks_capture_events_without_changing_workflow_summary(tmp_path):
    memory = InMemoryMetricSink(max_events=2)
    jsonl = JsonlMetricSink(tmp_path / "events.jsonl")
    ledger = WorkflowLedger("unit", workflow_id="unit-3", sink=memory)
    ledger.counter("rows", 2)
    ledger.metadata("profile", "smoke")
    with ledger.span("save", "file"):
        pass

    assert len(memory.snapshot()) == 2
    jsonl.emit({"kind": "test", "value": 1})
    assert json.loads((tmp_path / "events.jsonl").read_text()) == {"kind": "test", "value": 1}


def test_workflow_ledger_fails_open_when_sink_is_unavailable():
    class FailingSink:
        def emit(self, event):
            raise OSError("sink unavailable")

    ledger = WorkflowLedger("unit", workflow_id="unit-4", sink=FailingSink())
    ledger.counter("rows")

    assert ledger.summary()["sink_errors"] == 1


def test_workflow_ledger_marks_failed_spans(tmp_path):
    ledger = WorkflowLedger("unit", workflow_id="unit-2")
    with pytest.raises(RuntimeError, match="boom"), ledger.span("compile", "process"):
        raise RuntimeError("boom")

    summary = cast(dict[str, Any], ledger.write(tmp_path / "workflow_metrics.json"))

    span = summary["spans"][0]
    assert span["status"] == "error"
    assert span["attributes"]["error.type"] == "RuntimeError"
