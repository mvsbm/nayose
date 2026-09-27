"""Adapter from immutable Nayose generation products to benchmark contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from api.benchmarking.contracts import (
    NO_MATCH_ACTIONS,
    VISIBLE_FIELDS,
    BenchmarkDataset,
    BenchmarkEntity,
    BenchmarkQuery,
)
from api.domain.fingerprint import data_fingerprint


class NayoseBenchmarkAdapter:
    """Build benchmark cases without copying or modifying generation artifacts."""

    def __init__(self, generation_dir: Path, run_dir: Path, split_policy: str = "entity"):
        if split_policy not in {"entity", "source"}:
            raise ValueError("split_policy must be 'entity' or 'source'")
        self.generation_dir = generation_dir.expanduser().resolve()
        self.run_dir = run_dir.expanduser().resolve()
        self.split_policy = split_policy

    def load(self, *, require_all_splits: bool = True) -> BenchmarkDataset:
        reference = self._table(self.generation_dir / "reference_records.parquet")
        incoming = self._table(self.generation_dir / "incoming_queries.parquet")
        evaluation = self._table(self.generation_dir / "ground_truth/evaluation_truth.parquet")
        query_truth = self._table(self.generation_dir / "ground_truth/query_truth.parquet")
        record_truth = self._table(self.generation_dir / "ground_truth/record_truth.parquet")
        mapping = self._table(self.run_dir / "record_to_nayose.parquet")
        canonical = self._table(self.run_dir / "canonical_entities.parquet")
        manifests = self._validate_fingerprint(reference)
        self._require_query_coverage(incoming, evaluation, query_truth)
        entities = tuple(self._entities(canonical))
        truth_entities, record_entities = self._truth_entities(record_truth, mapping)
        split_assignments = _entity_split_assignments(evaluation, truth_entities, record_entities)
        queries = tuple(
            self._queries(
                incoming,
                evaluation,
                truth_entities,
                record_entities,
                self.split_policy,
                split_assignments,
            )
        )
        dataset = BenchmarkDataset(
            name="nayose-synthetic",
            version=str(manifests["generation"].get("created_at_utc", "unknown")),
            license="Nayose-generated synthetic data",
            entities=entities,
            queries=queries,
            metadata={
                "generation_dir": str(self.generation_dir),
                "run_dir": str(self.run_dir),
                "data_fingerprint": manifests["fingerprint"],
                "generation_created_at": str(manifests["generation"].get("created_at_utc", "")),
                "run_created_at": str(manifests["run"].get("created_at_utc", "")),
                "split_policy": self.split_policy,
            },
        )
        dataset.validate(require_all_splits=require_all_splits)
        return dataset

    def _validate_fingerprint(self, reference: pd.DataFrame) -> dict[str, Any]:
        generation = self._manifest(self.generation_dir / "generation_manifest.json")
        run = self._manifest(self.run_dir / "run_manifest.json")
        fingerprint = data_fingerprint(reference)
        expected_generation = str(generation.get("data_fingerprint", ""))
        expected_run = str(run.get("data_fingerprint", ""))
        if expected_generation and expected_generation != fingerprint:
            raise ValueError("generation fingerprint does not match reference_records")
        if expected_run and expected_run != fingerprint:
            raise ValueError("run fingerprint does not match reference_records")
        return {"generation": generation, "run": run, "fingerprint": fingerprint}

    @staticmethod
    def _entities(canonical: pd.DataFrame):
        if "nayose_id" not in canonical.columns:
            raise ValueError("canonical_entities is missing nayose_id")
        for row in canonical.to_dict("records"):
            entity_id = str(row.get("nayose_id") or "")
            if not entity_id:
                raise ValueError("canonical entity ID cannot be empty")
            yield BenchmarkEntity(entity_id, _visible_values(row))

    @staticmethod
    def _truth_entities(
        record_truth: pd.DataFrame, mapping: pd.DataFrame
    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        required_truth = {"record_id", "truth_id"}
        required_mapping = {"record_id", "nayose_id"}
        if not required_truth <= set(record_truth.columns):
            raise ValueError("record_truth is missing required columns")
        if not required_mapping <= set(mapping.columns):
            raise ValueError("record_to_nayose is missing required columns")
        joined = record_truth[["record_id", "truth_id"]].merge(
            mapping[["record_id", "nayose_id"]], on="record_id", how="inner"
        )
        by_truth: dict[str, set[str]] = {}
        by_record: dict[str, set[str]] = {}
        for row in joined.to_dict("records"):
            truth_id = str(row["truth_id"])
            record_id = str(row["record_id"])
            entity_id = str(row["nayose_id"])
            if truth_id and entity_id:
                by_truth.setdefault(truth_id, set()).add(entity_id)
            if record_id and entity_id:
                by_record.setdefault(record_id, set()).add(entity_id)
        return by_truth, by_record

    @staticmethod
    def _queries(
        incoming: pd.DataFrame,
        evaluation: pd.DataFrame,
        truth_entities: dict[str, set[str]],
        record_entities: dict[str, set[str]],
        split_policy: str,
        split_assignments: dict[str, str],
    ):
        if "query_id" not in incoming.columns or "query_id" not in evaluation.columns:
            raise ValueError("query tables are missing query_id")
        evaluation_by_id = evaluation.set_index("query_id").to_dict("index")
        for row in incoming.to_dict("records"):
            query_id = str(row.get("query_id") or "")
            label = evaluation_by_id.get(query_id)
            if not query_id or label is None:
                continue
            target_ids = _query_target_ids(label, truth_entities, record_entities)
            expected_action = str(label.get("expected_action") or "")
            if not expected_action:
                expected_action = "EXISTING" if target_ids else "NEW_CUSTOMER"
            related = _entity_ids_from_truth(
                label.get("related_reference_truth_ids"), truth_entities
            )
            source_split = _canonical_split(str(label.get("split") or ""))
            split = (
                split_assignments.get(next(iter(target_ids), ""), source_split)
                if split_policy == "entity"
                else source_split
            )
            channel = str(row.get("channel") or "unknown")
            scenario = str(label.get("scenario") or "unknown")
            hard_case = str(label.get("hard_case") or "none")
            yield BenchmarkQuery(
                query_id=query_id,
                target_entity_id=next(iter(target_ids), None),
                split=_canonical_split(split),
                fields=_visible_values(row),
                slice_name=f"scenario={scenario}|channel={channel}|hard_case={hard_case}",
                expected_action=expected_action,
                target_entity_ids=frozenset(target_ids),
                related_entity_ids=frozenset(related),
            )

    @staticmethod
    def _require_query_coverage(
        incoming: pd.DataFrame, evaluation: pd.DataFrame, query_truth: pd.DataFrame
    ) -> None:
        incoming_ids = set(incoming["query_id"].astype(str))
        evaluation_ids = set(evaluation["query_id"].astype(str))
        truth_ids = set(query_truth["query_id"].astype(str))
        if incoming_ids != evaluation_ids or incoming_ids != truth_ids:
            raise ValueError("synthetic query tables do not cover the same query IDs")

    @staticmethod
    def _table(path: Path) -> pd.DataFrame:
        if not path.is_file():
            raise FileNotFoundError(path)
        return pd.read_parquet(path).fillna("")

    @staticmethod
    def _manifest(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"manifest must be an object: {path}")
        return value


def _visible_values(row: dict[str, Any]) -> dict[str, str]:
    return {
        field: str(row.get(field) or "").strip()
        for field in VISIBLE_FIELDS
        if str(row.get(field) or "").strip()
    }


def _entity_ids_from_records(value: object, record_entities: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    for record_id in _string_list(value):
        result.update(record_entities.get(record_id, set()))
    return result


def _entity_ids_from_truth(value: object, truth_entities: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    for truth_id in _string_list(value):
        result.update(truth_entities.get(truth_id, set()))
    return result


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError("benchmark truth list is not valid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("benchmark truth list must be an array")
    return [str(item) for item in parsed if str(item).strip()]


def _canonical_split(split: str) -> str:
    return {"calibration": "validation", "val": "validation"}.get(split, split)


def _query_target_ids(
    label: dict[str, Any],
    truth_entities: dict[str, set[str]],
    record_entities: dict[str, set[str]],
) -> set[str]:
    expected_action = str(label.get("expected_action") or "")
    eligible_ids = _entity_ids_from_records(
        label.get("eligible_reference_record_ids"), record_entities
    )
    if not expected_action:
        return eligible_ids
    if expected_action in NO_MATCH_ACTIONS:
        return set()
    target_truth = str(label.get("target_truth_id") or "")
    return set(truth_entities.get(target_truth, set())) or eligible_ids


def _entity_split_assignments(
    evaluation: pd.DataFrame,
    truth_entities: dict[str, set[str]],
    record_entities: dict[str, set[str]],
) -> dict[str, str]:
    components: list[set[str]] = []
    for label in evaluation.to_dict("records"):
        target_ids = _query_target_ids(label, truth_entities, record_entities)
        if not target_ids:
            continue
        overlapping = [component for component in components if component & target_ids]
        merged = set(target_ids)
        for component in overlapping:
            merged.update(component)
            components.remove(component)
        components.append(merged)
    assignments: dict[str, str] = {}
    for component in components:
        split = _split_for_entities(component)
        assignments.update({entity_id: split for entity_id in component})
    return assignments


def _split_for_entities(entity_ids: set[str]) -> str:
    key = "|".join(sorted(entity_ids)).encode("utf-8")
    bucket = hashlib.sha256(key).digest()[0] % 10
    return "train" if bucket < 6 else "validation" if bucket < 8 else "test"
