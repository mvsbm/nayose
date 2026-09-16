"""Generate deterministic Mermaid dependency diagrams from Python and TypeScript imports.

Usage:
    python scripts/generate_architecture_diagrams.py
    python scripts/generate_architecture_diagrams.py --check
"""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_OUTPUT = _REPO / "docs/generated/architecture.md"
_PYTHON_ROOTS = (_REPO / "src/api", _REPO / "src/data_generation")
_TYPESCRIPT_ROOT = _REPO / "frontend/ts"


@dataclass(frozen=True, slots=True)
class Module:
    name: str
    package: str
    imports: tuple[str, ...]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when generated output is stale")
    args = parser.parse_args()
    content = render(collect_modules())
    if args.check:
        if not _OUTPUT.is_file() or _OUTPUT.read_text(encoding="utf-8") != content:
            print(f"architecture diagram is stale: regenerate {_OUTPUT.relative_to(_REPO)}")
            return 1
        return 0
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(content, encoding="utf-8")
    print(f"wrote {_OUTPUT.relative_to(_REPO)}")
    return 0


def collect_modules() -> tuple[Module, ...]:
    modules = [_python_module(path, root) for root in _PYTHON_ROOTS for path in root.rglob("*.py")]
    modules.extend(_typescript_module(path) for path in _TYPESCRIPT_ROOT.glob("*.ts"))
    return tuple(sorted(modules, key=lambda item: item.name))


def _python_module(path: Path, root: Path) -> Module:
    relative = path.relative_to(_REPO / "src").with_suffix("")
    name = ".".join(relative.parts)
    imports: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        tree = ast.Module(body=[], type_ignores=[])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    internal = tuple(
        sorted(item for item in imports if item.startswith(("api.", "data_generation.")))
    )
    return Module(name, name.rsplit(".", 1)[0], internal)


def _typescript_module(path: Path) -> Module:
    name = f"frontend.ts.{path.stem}"
    imports: set[str] = set()
    source = path.read_text(encoding="utf-8")
    for match in re.finditer(r"from\s+[\"'](\.[^\"']+)[\"']", source):
        target = (path.parent / match.group(1)).resolve()
        if target.suffix != ".ts":
            target = target.with_suffix(".ts")
        if target.is_file():
            imports.add(f"frontend.ts.{target.stem}")
    return Module(name, "frontend.ts", tuple(sorted(imports)))


def render(modules: tuple[Module, ...]) -> str:
    python = tuple(item for item in modules if item.name.startswith(("api.", "data_generation.")))
    typescript = tuple(item for item in modules if item.name.startswith("frontend."))
    return (
        """# Generated architecture diagrams

This file is generated from imports in `src/` and `frontend/ts/`.
Regenerate with `python scripts/generate_architecture_diagrams.py`; CI fails if it is stale.

## HLD: subsystem dependencies

```mermaid
graph TD
"""
        + _hld_edges(python)
        + """\n```

## LLD: internal Python module dependencies

```mermaid
graph LR
"""
        + _module_edges(python)
        + """\n```

## Frontend module dependencies

```mermaid
graph LR
"""
        + _module_edges(typescript)
        + """\n```
"""
    )


def _hld_edges(modules: tuple[Module, ...]) -> str:
    packages = {_subsystem(item.name) for item in modules}
    edges: set[tuple[str, str]] = set()
    for item in modules:
        source = _subsystem(item.name)
        for imported in item.imports:
            target = _subsystem(imported)
            if target in packages and target != source:
                edges.add((source, target))
    return "\n".join(
        f'    {_node(source)}["{source}"] --> {_node(target)}["{target}"]'
        for source, target in sorted(edges)
    )


def _subsystem(name: str) -> str:
    parts = name.split(".")
    if parts[0] == "api" and len(parts) > 1:
        return ".".join(parts[:2])
    if parts[0] == "frontend":
        return "frontend"
    return parts[0]


def _module_edges(modules: tuple[Module, ...]) -> str:
    known = {item.name for item in modules}
    edges: set[tuple[str, str]] = set()
    for item in modules:
        for imported in item.imports:
            target = imported if imported in known else _nearest_module(imported, known)
            if target and target != item.name:
                edges.add((item.name, target))
    return "\n".join(
        f'    {_node(source)}["{source}"] --> {_node(target)}["{target}"]'
        for source, target in sorted(edges)
    )


def _nearest_module(imported: str, known: set[str]) -> str | None:
    candidates = [name for name in known if imported == name or imported.startswith(f"{name}.")]
    return max(candidates, key=len, default=None)


def _node(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


if __name__ == "__main__":
    raise SystemExit(main())
