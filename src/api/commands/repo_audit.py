"""One-command repository status (the "did I break anything?" gate).

Runs every enforcement layer and prints a PASS/FAIL table; nonzero exit on
failure. Fastest checks first (lint -> types -> JS -> pytest -> optional E2E).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from nayose.config import load_config, repository_root

REPO = repository_root()
PY = [sys.executable, "-m"]
NPM = shutil.which("npm")
NODE = shutil.which("node")


def run(
    name: str,
    cmd: list[str],
    cwd: Path,
    timeout: int = 900,
    env=None,
    output_path: Path | None = None,
) -> bool:
    print(f"  {name:<32} ", end="", flush=True)
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("FAIL")
        print(f"{type(exc).__name__}: {exc}")
        return False
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    ok = proc.returncode == 0
    print("PASS" if ok else "FAIL")
    if not ok:
        print(proc.stdout[-1800:])
        print(proc.stderr[-1800:])
    return ok


def main() -> int:
    print("Auditing repository (fastest first)...")
    results: dict[str, bool] = {}
    config = load_config()
    metrics_dir = config.tooling.metrics_dir

    # 1-3: Python lint, formatting, and types
    results["ruff"] = run("ruff (lint)", [*PY, "ruff", "check", "."], REPO)
    structure_env = os.environ.copy()
    if metrics_dir is not None:
        structure_env["NAYOSE_ARCHITECTURE_LEDGER"] = str(metrics_dir / "architecture.json")
    results["structure"] = run(
        "source structure", [*PY, "api.commands.structure"], REPO, env=structure_env
    )
    results["ruff format"] = run("ruff (format)", [*PY, "ruff", "format", "--check", "."], REPO)
    results["mypy"] = run(
        "mypy (types)", [*PY, "mypy", "src/api", "src/data_generation", "tests"], REPO
    )

    # 4: frontend types (tsc) + compile
    if NPM and NODE:
        results["tsc"] = run(
            "tsc (frontend)",
            [NPM, "--prefix", "frontend", "run", "build"],
            REPO,
            300,
            output_path=metrics_dir / "tsc.txt" if metrics_dir else None,
        )
        results["npm test"] = run(
            "npm test (js)",
            [NODE, "--test", "test/core.test.mjs", "test/view.test.mjs"],
            REPO / "frontend",
            300,
        )
    else:
        results["tsc"] = results["npm test"] = False
        print("  npm/node not found - frontend gates cannot be verified")

    # 5: Python tests (includes frontend-gate, schema-contract, API functional)
    results["pytest"] = run("pytest (python + gates)", [*PY, "pytest", "tests/", "-q"], REPO)

    if config.tooling.e2e:
        if not (NPM and NODE):
            results["e2e"] = False
        else:
            results["e2e"] = run(
                "playwright e2e",
                [NPM, "--prefix", "frontend", "run", "test:e2e"],
                REPO,
                timeout=1800,
            )

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
