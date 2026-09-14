"""Enforcement gate: the frontend's own node:test suite must pass.
TypeScript compilation is performed once by the authoritative audit build.
Skipped on machines without node/npm; fail otherwise (hermetic, no network)."""

import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
NODE = shutil.which("node")
NPM = shutil.which("npm")

pytestmark = pytest.mark.skipif(
    NODE is None or NPM is None,
    reason="node/npm not available; cannot run the frontend test suite",
)


def test_frontend_suite_passes():
    assert NODE is not None and NPM is not None
    proc = subprocess.run(
        [NPM, "test"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"frontend suite failed:\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}"
    )
    assert "fail 0" in proc.stdout
