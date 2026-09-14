from __future__ import annotations

import pytest

from api.commands.compiler_budget import violations as compiler_violations
from api.commands.tsc_metrics import parse_diagnostics

TSC_OUTPUT = """
Files:                          97
Types:                       11647
Instantiations:               8801
Memory used:               110796K
Total time:                  0.37s
"""


def test_parse_typescript_extended_diagnostics_uses_stable_units():
    metrics = parse_diagnostics(TSC_OUTPUT)

    assert metrics["Files"] == 97
    assert metrics["Memory used"] == 110796 * 1024
    assert metrics["Total time"] == 370.0


def test_parse_typescript_extended_diagnostics_requires_core_fields():
    with pytest.raises(ValueError, match="missing fields"):
        parse_diagnostics("Files: 1\n")


def test_compiler_budget_reports_growth():
    metrics = {
        "metrics": {
            "Files": 200,
            "Types": 40_000,
            "Instantiations": 40_000,
            "Memory used": 600 * 1024**2,
            "Total time": 12_000,
        }
    }

    errors = compiler_violations(
        metrics,
        {
            "max_files": 140,
            "max_types": 30_000,
            "max_instantiations": 30_000,
            "max_memory_mb": 512,
            "max_total_ms": 10_000,
        },
    )

    assert len(errors) == 5
