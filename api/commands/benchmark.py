"""Runtime and process-measurement command family."""

from __future__ import annotations

import sys

from api.commands._dispatch import route

_USAGE = """Usage: nayose benchmark <command> [options]

Commands:
  runtime     Measure cold load and warm recommendation latency
  measure     Measure a complete descendant process tree
"""

_ROUTES = {
    "runtime": "api.commands.runtime",
    "measure": "api.commands.measure",
}


def main() -> int:
    return route(sys.argv[1:], _ROUTES, _USAGE)


if __name__ == "__main__":
    raise SystemExit(main())
