"""Canonical Nayose command-line facade."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from api.commands._dispatch import invoke
from api.infrastructure.runtime.native_runtime import reexec_with_aligned_macos_runtime

_USAGE = """Usage: nayose <family> <command> [options]

Families:
  data        Generate immutable data products
  model       Train and materialize model bundles
  release     Validate or publish a release
  audit       Run repository, data, and CI quality checks
  benchmark   Measure runtime and process resources

Examples:
  nayose data generate --profile smoke
  nayose model train --data-dir data/current --models-dir models --seed 42
  nayose audit repo
  nayose benchmark runtime --iterations 20
"""

_FAMILY_USAGE = {
    "data": "Usage: nayose data generate [options]",
    "model": "Usage: nayose model {train,materialize} [options]",
    "release": "Usage: nayose release {status,validate,promote} [options]",
    "audit": "Use: nayose audit --help",
    "benchmark": "Use: nayose benchmark --help",
}

_ROUTES = {
    ("data", "generate"): ("api.commands.data", False),
    ("model", "train"): ("api.commands.lifecycle", False),
    ("model", "materialize"): ("api.commands.materialize", False),
    ("release", "status"): ("api.commands.release", True),
    ("release", "validate"): ("api.commands.release", True),
    ("release", "promote"): ("api.commands.release", True),
    ("audit",): ("api.commands.quality", True),
    ("benchmark",): ("api.commands.benchmark", True),
}


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if argv is None and values[:2] in (["model", "train"], ["model", "materialize"]):
        reexec_with_aligned_macos_runtime(values)
    if not values or values[0] in {"-h", "--help"}:
        print(_USAGE)
        return 0
    if len(values) < 2:
        family_usage = _FAMILY_USAGE.get(values[0])
        if family_usage:
            print(family_usage)
            return 0
        raise SystemExit(_USAGE)
    family, command = values[:2]
    if command in {"-h", "--help"}:
        print(_FAMILY_USAGE.get(family, _USAGE))
        return 0
    route = _ROUTES.get((family, command)) or _ROUTES.get((family,))
    if route is None:
        raise SystemExit(f"unknown command: {family} {command}\n\n{_USAGE}")
    module_name, nested = route
    remainder = values[2:]
    if nested:
        remainder = [command, *remainder]
    return invoke(module_name, remainder)


if __name__ == "__main__":
    raise SystemExit(main())
