"""Static contracts for frontend boundaries and CSS behavior.

The checks fail before runtime when views bypass the transport seam, adapter
and mock operations drift apart, layout leaks onto bare elements, or template
classes are missing from the stylesheet.
"""

import re
from pathlib import Path

from api.commands.structure import violations

REPO = Path(__file__).resolve().parents[1]
CSS = REPO / "frontend" / "styles.css"
FEATURES = [
    REPO / "frontend" / "ts" / name
    for name in (
        "features.ts",
        "entity-view.ts",
        "entity-explorer.ts",
        "match-view.ts",
        "evidence-view.ts",
    )
]
VIEWS = [*FEATURES, REPO / "frontend" / "ts" / "app.ts"]
INDEX = REPO / "frontend" / "index.html"
PORT_TS = REPO / "frontend" / "ts" / "port.ts"
INFRA_TS = REPO / "frontend" / "ts" / "infra.ts"
STORE_TS = REPO / "frontend" / "ts" / "store.ts"
VIEW_TEST = REPO / "frontend" / "test" / "view.test.mjs"

# Properties that establish stacking/sticky contexts. On bare element
# selectors these silently couple layout to element names (e.g. the old
# `header{position:sticky}` / `table{overflow:hidden}` collisions).
FORBIDDEN_ON_ELEMENTS = {
    "position",
    "z-index",
    "transform",
    "filter",
    "backdrop-filter",
    "-webkit-backdrop-filter",
}


def _iter_css_rules(css: str):
    """Yield (selector_head, body) for top-level CSS rules, skipping at-rules."""
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            return
        head = css[i:brace].strip()
        j, depth = brace + 1, 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1 : j - 1]
        i = j
        if head.startswith("@"):  # @media/@keyframes: responsive/animations, unguarded
            continue
        yield head, body


def _decls(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in body.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _selectors(head: str) -> list[str]:
    return [s.strip() for s in head.split(",")]


def test_source_structure_budgets():
    assert not violations()


def test_bare_element_selectors_carry_no_layout():
    css = CSS.read_text(encoding="utf-8")
    violations: list[str] = []
    for head, body in _iter_css_rules(css):
        decls = _decls(body)
        bad = [p for p in FORBIDDEN_ON_ELEMENTS if p in decls]
        if not bad:
            continue
        for sel in _selectors(head):
            if re.fullmatch(r"[a-z][a-z0-9]*", sel):  # bare element, e.g. `header`
                violations.append(f"{head} declares {bad} on bare selector {sel!r}")
    assert not violations, (
        "layout/stacking must live on component classes, not element selectors:\n  "
        + "\n  ".join(violations)
    )


def test_no_sticky_table_cells():
    css = CSS.read_text(encoding="utf-8")
    for head, body in _iter_css_rules(css):
        if "th" in head or "td" in head:
            assert _decls(body).get("position") != "sticky", (
                f"table cells must not be sticky ({head}): sticky headers belong to a "
                "dedicated header-row component; `table{overflow:hidden}` makes the "
                "table a scroll container and the offset breaks (historical overlap bug)"
            )


def _css_classes(css: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # comment tokens are not classes
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", css))


def _used_classes() -> set[str]:
    used: set[str] = set()
    for path in (*VIEWS, INDEX):
        src = path.read_text(encoding="utf-8")
        for m in re.findall(r'class="([^"]*)"', src):
            for tok in m.split():
                if re.fullmatch(r"[a-zA-Z][\w-]*", tok):
                    used.add(tok)
        # class= built via `${cond ? "x" : ""}` AND classList.toggle/add/remove
        used.update(re.findall(r'class="\$\{[^}]*\? "([\w-]+)"', src))
        used.update(re.findall(r'classList\.(?:toggle|add|remove)\("([^"]+)"\)', src))
    return used


def test_class_dictionary_sync():
    """Every class referenced by views must exist in styles.css."""
    defined = _css_classes(CSS.read_text(encoding="utf-8"))
    used = _used_classes()
    missing = sorted(used - defined)
    assert not missing, (
        "classes used in templates without a styles.css rule (template/style drift): "
        + ", ".join(missing)
    )


REGISTRY_TAGS = ("block", "sub", "state", "utility")


def _registry() -> dict[str, set[str]]:
    """Parse the component registry comment (styles.css) — single source."""
    css = CSS.read_text(encoding="utf-8")
    reg: dict[str, set[str]] = {tag: set() for tag in REGISTRY_TAGS}
    for tag in REGISTRY_TAGS:
        m = re.search(rf"@({tag}) ([a-z][\w-]*(?: [a-z][\w-]*)*)", css)
        assert m, f"registry tag @{tag} missing from styles.css"
        reg[tag] = set(m.group(2).split())
    return reg


def test_component_registry_covers_all_css_classes():
    """Composite guard: every CSS class is a declared, categorized component.

    New styling must extend the registry (add the class to a bucket), not
    sprout silently — the taxonomy is the design-system contract.
    """
    defined = _css_classes(CSS.read_text(encoding="utf-8"))
    reg = _registry()
    covered = set().union(*reg.values())
    assert covered == defined, (
        f"registry drift: unregistered classes {sorted(defined - covered)}; "
        f"ghost entries {sorted(covered - defined)}"
    )
    blocks = reg["block"]
    for sub in sorted(reg["sub"]):
        assert any(sub.startswith(b + "-") for b in blocks), (
            f"sub-component {sub!r} must be <block>-prefixed (Composite naming)"
        )


def _method_names(src: str, block_open: str, block_close: str = r"\n\}") -> set[str]:
    if block_open.startswith("interface "):
        name = block_open.split()[1]
        opening = rf"interface {re.escape(name)}(?:\s+extends[^{{]+)?\s*{{"
    else:
        opening = re.escape(block_open)
    m = re.search(opening + r"(.*?)" + block_close, src, re.S)
    assert m, f"block {block_open!r} not found"
    return set(re.findall(r"^\s{2}(?:async\s+)?(\w+)\(", m.group(1), re.M))


def test_ports_complete_across_adapter_and_mock():
    """Every focused port operation must exist on both adapter and mock."""
    port_src = PORT_TS.read_text(encoding="utf-8")
    interfaces = (
        "SearchPort",
        "EntityDetailPort",
        "ReviewPort",
        "MetricsPort",
        "ConfusionPort",
        "EntityExplorerPort",
        "ComparePort",
        "CompareRunsPort",
        "MatchingPort",
        "DataEntryPort",
        "DatasetPort",
        "AdminPort",
        "RunPort",
        "SystemPort",
        "DecisionPort",
        "RecommendationPort",
    )
    port = set().union(
        *(_method_names(port_src, f"interface {name} {{", "\n}") for name in interfaces)
    )
    adapter_head = "class ApiAdapter implements WorkbenchPort {"
    adapter = _method_names(INFRA_TS.read_text(encoding="utf-8"), adapter_head, "\n}")
    mock = _method_names(VIEW_TEST.read_text(encoding="utf-8"), "const mockPort = {", r"\n\};")
    assert port <= adapter, (
        f"WorkbenchPort methods missing from ApiAdapter: {sorted(port - adapter)}"
    )
    assert port <= mock, f"WorkbenchPort methods missing from MockPort: {sorted(port - mock)}"


def test_views_depend_only_on_ports():
    """Views reach the world through ports and Store only."""
    allowed = {
        "./port.js",
        "./store.js",
        "./core.js",
        "./schema.js",
        "./shared.js",
        "./match-view.js",
        "./entity-view.js",
        "./entity-explorer.js",
        "./evidence-view.js",
        "./search-workspace.js",
        "./lifecycle.js",
        "./panel-layout.js",
    }
    for path in FEATURES:
        src = path.read_text(encoding="utf-8")
        imports = re.findall(r'from "(\./[^"]+)"', src)
        forbidden = [i for i in imports if i not in allowed]
        assert not forbidden, f"{path.name} must not import {forbidden} (port boundary)"
        for needle in ("fetch(", "localStorage", "new ApiAdapter"):
            assert needle not in src, (
                f"{path.name} must not call {needle!r} directly (port boundary)"
            )


def test_single_transport_seam():
    """Exactly one fetch-bearing module exists in frontend/ts: http.ts.

    ApiAdapter owns domain mapping while the HTTP helper owns authentication,
    timeout, and cancellation behavior.
    """
    carriers = []
    for path in sorted((REPO / "frontend" / "ts").glob("*.ts")):
        src = path.read_text(encoding="utf-8")
        if "fetch(" in src:
            carriers.append(path.name)
    assert carriers == ["http.ts"], (
        f"fetch must live ONLY in http.ts (the transport seam); found {carriers}"
    )


def test_no_root_legacy_modules_in_api():
    """The retired root-level engine and obsolete CLI must stay deleted.

    API modules use the shared primitives and api/engine.py loader instead of
    recreating legacy implementations.
    """
    assert not (REPO / "nayose_inference.py").exists(), "legacy root engine must stay retired"
    assert not (REPO / "nayose_cli.py").exists(), "obsolete CLI must stay deleted"
    for path in sorted((REPO / "api").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "nayose_inference" not in src and "nayose_cli" not in src, (
            f"{path.name}: reference to a root-level legacy module — import from api/engine.py"
        )


def _i18n_entries() -> dict[str, tuple[str, str]]:
    src = (REPO / "frontend" / "ts" / "i18n.ts").read_text(encoding="utf-8")
    block = src[src.index("const messages") : src.index("export function translate")]
    entries: dict[str, tuple[str, str]] = {}
    pattern = re.compile(r'\b(\w+): \["((?:[^"\\\\]|\\.)*)", "((?:[^"\\\\]|\\.)*)"\]')
    for match in pattern.finditer(block):
        entries[match.group(1)] = (match.group(2), match.group(3))
    assert entries, "i18n: no messages parsed"
    return entries


def test_i18n_dictionary_is_complete_and_bilingual():
    """Every label used in views exists in i18n.ts with both locales."""
    entries = _i18n_entries()
    for path in (*FEATURES, REPO / "frontend" / "ts" / "app.ts"):
        src = path.read_text(encoding="utf-8")
        for key in re.findall(r'(?<![A-Za-z0-9_])(?:t|store\.t|translate)\((?:"|\')(\w+)', src):
            assert key in entries, f"{path.name}: label key {key!r} missing from i18n.ts"


def test_matching_stage_literals_have_i18n_labels():
    """State-machine parity: the server's job stages must be user-readable."""
    contracts = (REPO / "api" / "contracts.py").read_text(encoding="utf-8")
    match = re.search(r"Stage = Literal\[(.*?)\]", contracts, re.S)
    assert match, "contracts.py must declare the Stage Literal"
    stages = set(re.findall(r'"(\w+)"', match.group(1)))
    missing = stages - set(_i18n_entries())
    assert not missing, f"matching stages without an i18n label: {sorted(missing)}"


E2E = REPO / "frontend" / "test" / "e2e.test.mjs"


def test_e2e_journeys_use_harness_primitives_only():
    """Raw Playwright waits, reloads, and context creation belong to the
    shared e2e harness, keeping browser journeys isolated and deterministic.
    """
    src = E2E.read_text(encoding="utf-8")
    banned = [
        "chromium.launch",
        ".waitForFunction(",
        ".reload(",
        "newContext(",
        "newPage(",
        'from "playwright"',
    ]
    hits = [needle for needle in banned if needle in src]
    assert not hits, (
        "e2e.test.mjs must use harness primitives only (navigateFresh/waitVisible/"
        f"waitBodyText/waitHash/waitLang/waitCount/runJourney); found {hits}"
    )
    assert 'from "./e2e-util.mjs"' in src, "e2e suite must import the harness"


def test_e2e_selectors_are_semantic_hooks():
    """Journeys select by ids and semantic data-* hooks, never structural paths
    under #view. This keeps selectors stable as view markup changes.
    """
    src = E2E.read_text(encoding="utf-8")
    for match in re.findall(r'"([^"]*#view\s+[a-z][a-z0-9]*[^"]*)"', src):
        raise AssertionError(
            f"structural selector under #view: {match!r} — use data-* hooks "
            "([data-entity], [data-member], [data-select]) or stable ids"
        )
    for match in re.findall(r'"([^"]*#view\s+[a-z-]+[^\]]+)"', src):
        raise AssertionError(f"ambiguous selector under #view: {match!r}")
