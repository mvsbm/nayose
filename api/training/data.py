from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from api.domain.fingerprint import data_fingerprint
from api.domain.normalize import normalize_frame
from api.training.config import TrainingConfig
from api.training.contracts import NormalizedCorpus, TruthSnapshot, VisibleSnapshot

VISIBLE_FIELDS = (
    "party_type",
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "postal_code",
    "address",
    "phone",
    "email",
)
REFERENCE_REQUIRED = (
    "record_id",
    "policy_id",
    "agency_id",
    "channel",
    "observed_at",
    *VISIBLE_FIELDS,
)
QUERY_REQUIRED = ("query_id", "agency_id", "channel", "observed_at", *VISIBLE_FIELDS)
FORBIDDEN_VISIBLE = frozenset(
    {
        "truth_id",
        "target_truth_id",
        "relation_id",
        "scenario",
        "hard_case",
        "split",
        "record_quality",
        "quality_band",
        "transformation",
    }
)
FREQUENCY_COLUMNS = (
    "n_name_loose",
    "n_kana",
    "n_dob",
    "n_postal",
    "n_address",
    "n_phone",
    "n_email",
    "n_phone_suffix8",
)


def _find_table(root: Path, stem: str, hidden: bool) -> Path:
    roots = (root / "ground_truth", root) if hidden else (root,)
    for candidate_root in roots:
        parquet = candidate_root / f"{stem}.parquet"
        if parquet.is_file():
            return parquet
        csv = candidate_root / f"{stem}.csv.gz"
        if csv.is_file():
            return csv
    raise FileNotFoundError(f"missing {stem} under {', '.join(str(item) for item in roots)}")


def read_table(root: Path, stem: str, *, hidden: bool = False) -> pd.DataFrame:
    path = _find_table(root, stem, hidden)
    frame = (
        pd.read_parquet(path)
        if path.suffix == ".parquet"
        else pd.read_csv(path, keep_default_na=False)
    ).fillna("")
    return frame


def _require_columns(frame: pd.DataFrame, required: tuple[str, ...], name: str) -> None:
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} is missing required columns: {sorted(missing)}")
    leaked = FORBIDDEN_VISIBLE.intersection(frame.columns)
    if leaked:
        raise ValueError(f"hidden columns leaked into {name}: {sorted(leaked)}")


def load_visible_snapshot(config: TrainingConfig) -> VisibleSnapshot:
    root = config.data_dir.resolve()
    manifest = json.loads((root / "generation_manifest.json").read_text(encoding="utf-8"))
    reference = read_table(root, "reference_records")
    incoming = read_table(root, "incoming_queries")
    _require_columns(reference, REFERENCE_REQUIRED, "reference_records")
    _require_columns(incoming, QUERY_REQUIRED, "incoming_queries")
    if not reference["record_id"].is_unique or not incoming["query_id"].is_unique:
        raise ValueError("reference record IDs and query IDs must be unique")
    if len(reference) > config.max_full_match_records:
        raise ValueError(
            f"reference rows {len(reference):,} exceed safety cap {config.max_full_match_records:,}"
        )
    fingerprint = data_fingerprint(reference)
    expected = str(manifest.get("data_fingerprint", ""))
    if expected and expected != fingerprint:
        raise ValueError("generation manifest fingerprint does not match reference_records")
    return VisibleSnapshot(reference, incoming, manifest, fingerprint)


def normalize_corpus(snapshot: VisibleSnapshot) -> NormalizedCorpus:
    reference = normalize_frame(snapshot.reference, "record_id")
    incoming = normalize_frame(snapshot.incoming, "query_id")
    frequencies = {
        column: reference.loc[reference[column].ne(""), column].value_counts().to_dict()
        for column in FREQUENCY_COLUMNS
    }
    return NormalizedCorpus(reference, incoming, frequencies)


def load_truth(root: Path, incoming: pd.DataFrame, reference: pd.DataFrame) -> TruthSnapshot:
    record_truth = read_table(root, "record_truth", hidden=True)
    evaluation_truth = read_table(root, "evaluation_truth", hidden=True)
    query_truth = read_table(root, "query_truth", hidden=True)
    party_truth = read_table(root, "party_truth", hidden=True)
    relation_truth = read_table(root, "relation_truth", hidden=True)
    lineage_path = root / "ground_truth" / "field_lineage.parquet"
    field_lineage = (
        pd.read_parquet(lineage_path).fillna("") if lineage_path.is_file() else pd.DataFrame()
    )
    if set(record_truth["record_id"]) != set(reference["record_id"]):
        raise ValueError("record_truth does not cover the reference snapshot")
    if set(evaluation_truth["query_id"]) != set(incoming["query_id"]):
        raise ValueError("evaluation_truth does not cover incoming queries")
    if set(query_truth["query_id"]) != set(incoming["query_id"]):
        raise ValueError("query_truth does not cover incoming queries")
    return TruthSnapshot(
        record_truth,
        evaluation_truth,
        query_truth,
        party_truth,
        relation_truth,
        field_lineage,
    )
