#!/usr/bin/env python3
"""Enforce source-size budgets for maintained modules."""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = (REPO / "api", REPO / "synthetic", REPO / "scripts")
FRONTEND_ROOT = REPO / "frontend" / "ts"
MAX_PYTHON_MODULE_LINES = 800
MAX_FRONTEND_MODULE_LINES = 450
MAX_CLASS_LINES = 380
MAX_FUNCTION_LINES = 180


def _length(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    start = node.lineno
    end = getattr(node, "end_lineno", start)
    return end - start + 1


def _python_violations() -> list[str]:
    violations: list[str] = []
    for root in PYTHON_ROOTS:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(REPO)
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > MAX_PYTHON_MODULE_LINES:
                violations.append(f"{relative}: {lines} lines > {MAX_PYTHON_MODULE_LINES}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and _length(node) > MAX_CLASS_LINES:
                    violations.append(
                        f"{relative}:{node.lineno} class {node.name} has {_length(node)} lines"
                    )
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and _length(node) > MAX_FUNCTION_LINES
                ):
                    violations.append(
                        f"{relative}:{node.lineno} function {node.name} has {_length(node)} lines"
                    )
    return violations


def _frontend_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(FRONTEND_ROOT.glob("*.ts")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_FRONTEND_MODULE_LINES:
            relative = path.relative_to(REPO)
            violations.append(f"{relative}: {lines} lines > {MAX_FRONTEND_MODULE_LINES}")
    return violations


def violations() -> list[str]:
    return _python_violations() + _frontend_violations()


def main() -> int:
    errors = violations()
    if errors:
        print("Structure budget violations:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Structure budgets pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
