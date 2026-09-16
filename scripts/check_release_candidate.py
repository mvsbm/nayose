"""Fail closed when the checked-out release candidate is not reproducible."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="skip the clean-worktree check for local diagnostic runs",
    )
    args = parser.parse_args()
    checks = [
        _clean_worktree(args.allow_dirty),
        _run((sys.executable, "scripts/generate_architecture_diagrams.py", "--check")),
        _contains(Path("Dockerfile"), "--extra neural"),
        _contains(Path("docker-compose.yml"), ":ro"),
    ]
    if all(checks):
        print("release candidate checks passed")
        return 0
    print("release candidate checks failed")
    return 1


def _clean_worktree(allow_dirty: bool) -> bool:
    if allow_dirty:
        return True
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=_REPO, capture_output=True, text=True
    )
    if result.returncode != 0:
        print("unable to inspect git worktree")
        return False
    if result.stdout:
        print("working tree is dirty; release from a committed checkout")
        return False
    return True


def _run(command: tuple[str, ...]) -> bool:
    return subprocess.run(command, cwd=_REPO).returncode == 0


def _contains(relative_path: Path, needle: str) -> bool:
    path = _REPO / relative_path
    if needle in path.read_text(encoding="utf-8"):
        return True
    print(f"{relative_path} is missing required release setting: {needle}")
    return False


if __name__ == "__main__":
    raise SystemExit(main())
