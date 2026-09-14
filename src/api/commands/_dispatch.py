"""Small adapter for dispatching command implementations without subprocesses."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence


def invoke(module_name: str, argv: Sequence[str]) -> int:
    module = importlib.import_module(module_name)
    command = getattr(module, "main", None)
    if not callable(command):
        raise RuntimeError(f"command module has no main(): {module_name}")
    previous = sys.argv
    sys.argv = [previous[0], *argv]
    try:
        result = command()
    finally:
        sys.argv = previous
    return int(result or 0)


def route(argv: Sequence[str], routes: dict[str, str], usage: str) -> int:
    values = list(argv)
    if not values or values[0] in {"-h", "--help"}:
        print(usage)
        return 0
    module_name = routes.get(values[0])
    if module_name is None:
        raise SystemExit(f"unknown command {values[0]!r}; choose one of {', '.join(routes)}")
    return invoke(module_name, values[1:])
