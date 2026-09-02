"""Shared pipeline orchestration and the canonical save contract.

One function each for "generate" and "save" so the notebook driver and the
`scripts/generate_data.py` CLI can never drift apart (DRY). Outputs match the
nayose.ipynb contract exactly.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import PROFILES, NoiseConfig, SyntheticConfig, default_data_dir
from .engine import (
    build_observations,
    generate_agencies,
    generate_parties,
    generate_queries,
)
from .noise import CUSTOMER_FIELDS
from .providers import AddressSource, BuildingComposer, CompanySource, PersonaSource
from .truth import (
    build_field_lineage,
    build_households,
    build_party_truth,
    build_query_tables,
    build_record_truth,
    build_relation_truth,
)

FIELDS = ["party_type", *CUSTOMER_FIELDS]
REFERENCE_COLS = ["record_id", "policy_id", "agency_id", "channel", "observed_at", *FIELDS]
QUERY_COLS = ["query_id", "agency_id", "channel", "observed_at", *FIELDS]

GRoundTables = dict[str, pd.DataFrame]


@dataclass(slots=True)
class PipelineResult:
    parties: pd.DataFrame
    observations: pd.DataFrame
    queries: pd.DataFrame
    agencies: pd.DataFrame
    ground_truth: GRoundTables
    query_meta: list[dict] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "parties": len(self.parties),
            "observations": len(self.observations),
            "queries": len(self.queries),
        }


def generate(cfg: SyntheticConfig, rng: np.random.Generator | None = None) -> PipelineResult:
    """Run the full pipeline: parties -> observations -> queries -> six truth tables."""
    rng = rng or np.random.default_rng(cfg.seed)
    noise = NoiseConfig()
    data_dir = cfg.output_dir or default_data_dir()

    persona = PersonaSource(data_dir)
    companies = CompanySource()
    address = AddressSource(data_dir)
    buildings = BuildingComposer()

    parties = generate_parties(cfg, rng, persona, companies, address, buildings)
    agencies = generate_agencies(cfg, rng)
    observations, party_of, record_lineage = build_observations(cfg, noise, parties, agencies, rng)

    name_dups = parties.name_kanji.value_counts()
    relation_of = build_households(len(parties), rng)
    party_truth = build_party_truth(parties, relation_of, name_dups)
    record_truth = build_record_truth(observations, party_of, party_truth)
    record_ids_by_truth = record_truth.groupby("truth_id").record_id.apply(list).to_dict()

    queries, query_meta, query_lineage = generate_queries(
        cfg,
        noise,
        parties,
        relation_of,
        party_truth,
        record_ids_by_truth,
        persona,
        companies,
        address,
        buildings,
        rng,
    )
    query_truth, evaluation_truth, query_lineage_df = build_query_tables(
        queries, query_meta, parties, party_of, record_ids_by_truth, query_lineage
    )
    record_lineage_df = build_field_lineage(observations, party_of, record_lineage, parties)
    field_lineage = pd.concat([record_lineage_df, query_lineage_df], ignore_index=True)

    return PipelineResult(
        parties=parties,
        observations=observations,
        queries=queries,
        agencies=agencies,
        ground_truth={
            "record_truth": record_truth,
            "party_truth": party_truth,
            "relation_truth": build_relation_truth(party_truth),
            "query_truth": query_truth,
            "evaluation_truth": evaluation_truth,
            "field_lineage": field_lineage,
        },
        query_meta=query_meta,
    )


def build_manifest(
    cfg: SyntheticConfig, counts: dict[str, int], sources: dict[str, str] | None = None
) -> dict:
    """Reproducibility manifest; mirrors what the notebook writes."""
    cfg_json = asdict(cfg)
    cfg_json["output_dir"] = str(cfg_json["output_dir"])
    return {
        "schema_version": "3.0",
        "created_at_utc": pd.Timestamp.utcnow().isoformat(),
        "config": cfg_json,
        "profile": PROFILES[cfg.profile],
        "counts": counts,
        "sources": sources
        or {
            "personas": "nvidia/Nemotron-Personas-Japan (CC-BY 4.0)",
            "postal": "Japan Post ken_all",
            "companies": "EDINET edinet2023",
        },
    }


def save_all(
    cfg: SyntheticConfig, result: PipelineResult, sources: dict[str, str] | None = None
) -> list[Path]:
    """Atomic-write every table + manifest; returns the written paths."""
    written: list[Path] = []
    data_dir = cfg.output_dir or default_data_dir()

    def save(frame: pd.DataFrame, stem: str, subdir: str = "") -> Path:
        path = data_dir / subdir / f"{stem}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)
        print(f"  saved {stem:<20} {len(frame):>9,} rows -> {path}")
        return path

    written.append(save(result.observations[REFERENCE_COLS], "reference_records"))
    written.append(save(result.queries[QUERY_COLS], "incoming_queries"))
    written.append(save(result.agencies, "agencies"))
    for stem, frame in result.ground_truth.items():
        written.append(save(frame, stem, "ground_truth"))

    manifest = build_manifest(cfg, result.counts, sources)
    manifest_path = data_dir / "generation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(manifest_path)
    print(f"  saved manifest config.profile={cfg.profile} -> {manifest_path}")
    print(f"  outputs written under {data_dir}")
    return written
