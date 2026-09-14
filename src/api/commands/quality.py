"""Repository, data-quality, and CI-metric command family."""

from __future__ import annotations

import sys

from api.commands._dispatch import route

_USAGE = """Usage: nayose audit <command> [options]

Commands:
  repo        Run the authoritative repository gate
  structure   Check source-size and module boundaries
  decisions   Score persisted review decisions against truth
  retrieval   Evaluate retrieval ablations
  resource    Validate process/resource budgets
  compiler    Validate TypeScript compiler budgets
  tsc         Convert TypeScript diagnostics into CI JSON
"""

_ROUTES = {
    "repo": "api.commands.repo_audit",
    "structure": "api.commands.structure",
    "decisions": "api.commands.decisions",
    "retrieval": "api.commands.retrieval",
    "resource": "api.commands.resource_budget",
    "compiler": "api.commands.compiler_budget",
    "tsc": "api.commands.tsc_metrics",
}


def main() -> int:
    return route(sys.argv[1:], _ROUTES, _USAGE)


if __name__ == "__main__":
    raise SystemExit(main())
