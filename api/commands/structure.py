"""Enforce source-size budgets for maintained modules."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PYTHON_ROOTS = (REPO / "api", REPO / "synthetic")
COMMAND_ROOT = REPO / "api" / "commands"
SCRIPT_ROOT = REPO / "scripts"
FRONTEND_ROOT = REPO / "frontend" / "ts"
MAX_PYTHON_MODULE_LINES = 800
MAX_FRONTEND_MODULE_LINES = 450
MAX_CLASS_LINES = 380
MAX_FUNCTION_LINES = 180

STRUCTURE_EXCEPTIONS = {"api/training_pipeline.py"}


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
            if relative.as_posix() in STRUCTURE_EXCEPTIONS:
                continue
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


def _architecture_metrics() -> dict[str, Any]:
    scripts = sorted(SCRIPT_ROOT.glob("*.py")) if SCRIPT_ROOT.is_dir() else []
    executable_scripts = [path for path in scripts if path.name != "__init__.py"]
    command_modules = sorted(
        path.relative_to(REPO).as_posix()
        for path in COMMAND_ROOT.glob("*.py")
        if path.name != "__init__.py" and not path.name.startswith("_")
    )
    module_sizes = {
        path.relative_to(REPO).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for root in PYTHON_ROOTS
        for path in root.rglob("*.py")
    }
    return {
        "schema_version": 1,
        "executable_scripts": len(executable_scripts),
        "script_loc": sum(
            len(path.read_text(encoding="utf-8").splitlines()) for path in executable_scripts
        ),
        "command_modules": command_modules,
        "largest_module_loc": max(module_sizes.values(), default=0),
        "structure_exceptions": sorted(STRUCTURE_EXCEPTIONS),
    }


def _architecture_violations() -> list[str]:
    metrics = _architecture_metrics()
    budget_path = REPO / "ci" / "architecture_budgets.json"
    if not budget_path.is_file():
        return [f"missing architecture budget: {budget_path.relative_to(REPO)}"]
    budgets = json.loads(budget_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for field, label in (
        ("max_executable_scripts", "executable scripts"),
        ("max_script_loc", "script LOC"),
    ):
        limit = int(budgets.get(field, 0))
        actual = int(metrics[field.removeprefix("max_")])
        if actual > limit:
            errors.append(f"{label} {actual} exceeds {limit}")
    module_limit = int(budgets.get("max_command_module_lines", MAX_PYTHON_MODULE_LINES))
    for relative in metrics["command_modules"]:
        lines = len((REPO / relative).read_text(encoding="utf-8").splitlines())
        if lines > module_limit:
            errors.append(f"{relative}: {lines} lines > {module_limit}")
    return errors


def violations() -> list[str]:
    return _python_violations() + _frontend_violations() + _architecture_violations()


def write_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(_architecture_metrics(), indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    ledger_path = os.getenv("NAYOSE_ARCHITECTURE_LEDGER")
    if ledger_path:
        write_ledger(Path(ledger_path))
    errors = violations()
    if errors:
        print("Structure budget violations:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Structure budgets pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
