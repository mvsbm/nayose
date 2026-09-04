#!/usr/bin/env python3
"""One-command repository status (the "did I break anything?" gate).

Runs every enforcement layer and prints a PASS/FAIL table; nonzero exit on
failure. Fastest checks first (lint -> types -> JS -> pytest -> optional E2E).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = [sys.executable, "-m"]
NPM = shutil.which("npm")
NODE = shutil.which("node")


def run(name: str, cmd: list[str], cwd: Path, timeout: int = 900, env=None) -> bool:
    print(f"  {name:<32} ", end="", flush=True)
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    ok = proc.returncode == 0
    print("PASS" if ok else "FAIL")
    if not ok:
        print(proc.stdout[-1800:])
        print(proc.stderr[-1800:])
    return ok


def main() -> int:
    print("Auditing repository (fastest first)...")
    results: dict[str, bool] = {}

    # 1-2: python lint/types
    results["ruff"] = run("ruff (lint)", [*PY, "ruff", "check", "."], REPO)
    results["mypy"] = run("mypy (types)", [*PY, "mypy", "synthetic", "tests"], REPO)

    # 3: frontend types (tsc) + compile
    if NPM:
        results["tsc"] = run(
            "tsc (frontend)", [NPM, "--prefix", "frontend", "run", "typecheck"], REPO, 300
        )
        results["npm test"] = run(
            "npm test (js)",
            [NODE, "--test", "test/core.test.mjs", "test/view.test.mjs"],
            REPO / "frontend",
            300,
        )
    else:
        results["tsc"] = results["npm test"] = True
        print("  npm/node not found - frontend gates skipped")

    # 4: python tests (includes frontend-gate, schema-contract, API functional)
    results["pytest"] = run("pytest (python + gates)", [*PY, "pytest", "tests/", "-q"], REPO)

    print("\nSummary:")
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        return 1
    print("\nAll gates green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
