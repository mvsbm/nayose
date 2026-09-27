"""Runtime and process-measurement command family."""

from __future__ import annotations

import sys

from api.commands._dispatch import route

_USAGE = """Usage: nayose benchmark <command> [options]

Commands:
  runtime     Measure cold load and warm recommendation latency
  measure     Measure a complete descendant process tree
  external    Evaluate a local dirty-record benchmark
  resolution  Evaluate the production resolver against Nayose truth
"""

_ROUTES = {
    "runtime": "api.commands.runtime",
    "measure": "api.commands.measure",
    "external": "api.commands.benchmark_external",
    "resolution": "api.commands.benchmark_resolution",
}


def main() -> int:
    return route(sys.argv[1:], _ROUTES, _USAGE)


if __name__ == "__main__":
    raise SystemExit(main())
