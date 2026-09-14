"""Cross-language contract test: frontend/ts/schema.ts must mirror
src/api/http/contracts.py REQUIRED_COLUMNS exactly (Rust-style: drift fails the
tests, never fails in a browser). Pure file-parse — no TS toolchain needed."""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _py_contract() -> dict[str, list[str]]:
    mod = ast.parse((REPO / "src" / "api" / "http" / "contracts.py").read_text(encoding="utf-8"))
    for node in mod.body:
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        if "REQUIRED_COLUMNS" not in {t.id for t in targets}:
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            raise AssertionError("REQUIRED_COLUMNS must be a dict literal")
        contract: dict[str, list[str]] = {}
        for key, val in zip(value.keys, value.values, strict=True):
            if not (isinstance(key, ast.Constant) and isinstance(val, ast.List)):
                raise AssertionError("REQUIRED_COLUMNS entries must be str -> list")
            contract[str(key.value)] = [
                str(item.value) for item in val.elts if isinstance(item, ast.Constant)
            ]
        return contract
    raise AssertionError("REQUIRED_COLUMNS not found")


def _ts_contract() -> dict[str, list[str]]:
    src = (REPO / "frontend" / "ts" / "schema.ts").read_text(encoding="utf-8")
    block = re.search(r"export const SCHEMA = \{(.*?)\} as const;", src, re.S)
    assert block, "SCHEMA const not found"
    contract: dict[str, list[str]] = {}
    for name, cols in re.findall(r"^\s{2}(\w+): \[(.*?)\] as const,", block.group(1), re.M | re.S):
        contract[name] = re.findall(r'"([^"]+)"', cols)
    return contract


def test_schema_contract_sync():
    assert _py_contract() == _ts_contract()


def test_schema_has_expected_tables():
    ts = _ts_contract()
    for table in (
        "full_dataset_summary",
        "canonical_entities",
        "record_to_nayose",
        "query_confusion",
    ):
        assert table in ts, table
