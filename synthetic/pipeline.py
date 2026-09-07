"""Shared pipeline orchestration and the canonical save contract.

One function each for "generate" and "save" so the notebook driver and the
`scripts/generate_data.py` CLI can never drift apart (DRY). Outputs match the
nayose.ipynb contract exactly.

Storage layout (Decision A/B of the repo design):
    <output_dir>/raw/            immutable reference-data cache (ken_all, personas, EDINET)
    <output_dir>/generations/gen_<ts>_<config_hash8>/   one immutable dataset product
    <output_dir>/current ->      symlink to the latest generation (consumer-facing alias)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import PROFILES, SyntheticConfig, default_data_dir, noise_for_config
from .engine import (
    build_observations,
    generate_agencies,
    generate_parties,
    generate_queries,
)
from .noise import CUSTOMER_FIELDS
from .policy import build_policies, history_by_party, validate_temporal
from .providers import (
    AddressSource,
    BuildingComposer,
    CompanySource,
    PersonaSource,
    build_homophone_map,
)
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


def data_fingerprint(reference: pd.DataFrame) -> str:
    """Canonical fingerprint shared with nayose.ipynb / api.engine.py."""
    return hashlib.sha256(
        pd.util.hash_pandas_object(reference[REFERENCE_COLS], index=False).values.tobytes()
    ).hexdigest()


def generate(cfg: SyntheticConfig, rng: np.random.Generator | None = None) -> PipelineResult:
    """Run the full pipeline: parties -> observations -> queries -> six truth tables."""
    rng = rng or np.random.default_rng(cfg.seed)
    noise = noise_for_config(cfg)
    raw_dir = cfg.raw_dir or default_data_dir() / "raw"

    persona = PersonaSource(raw_dir)
    homophones = build_homophone_map(persona)
    companies = CompanySource()
    address = AddressSource(raw_dir)
    buildings = BuildingComposer()

    parties = generate_parties(cfg, rng, persona, companies, address, buildings)
    agencies = generate_agencies(cfg, rng)

    # domain context (household relations, policy aggregate roots, truth
    # timeline) is built BEFORE observations so records attach to real policies
    # and are date-anchored to their truth states.
    name_dups = parties.name_kanji.value_counts()
    relation_of = build_households(len(parties), rng)
    relation_members: dict[int, list[int]] = {}
    for party_idx, rel in relation_of.items():
        relation_members.setdefault(rel, []).append(party_idx)
    policies = build_policies(rng, parties, relation_of, relation_members)
    history = history_by_party(rng, parties, homophones, address, buildings)
    observations, party_of, record_lineage = build_observations(
        cfg,
        noise,
        parties,
        agencies,
        rng,
        homophones=homophones,
        policies=policies,
        history=history,
    )

    # G4 data contract: records must never carry a truth newer than their date
    temporal_violations = validate_temporal(observations, party_of, history, record_lineage)
    if temporal_violations:
        raise RuntimeError("temporal drift violated: " + " | ".join(temporal_violations[:10]))

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
    cfg_json["raw_dir"] = str(cfg_json["raw_dir"])
    return {
        "schema_version": "3.1",
        "created_at_utc": pd.Timestamp.utcnow().isoformat(),
        "config": cfg_json,
        "profile": PROFILES[cfg.profile],
        "effective_noise": asdict(noise_for_config(cfg)),
        "counts": counts,
        "sources": sources
        or {
            "personas": "nvidia/Nemotron-Personas-Japan (CC-BY 4.0)",
            "postal": "Japan Post ken_all",
            "companies": "EDINET edinet2023",
        },
    }


def _generation_id(cfg: SyntheticConfig) -> str:
    stamp = pd.Timestamp.utcnow().strftime("%Y%m%d-%H%M%S")
    config_hash = hashlib.sha256(
        json.dumps(asdict(cfg), sort_keys=True, default=str).encode()
    ).hexdigest()[:8]
    return f"gen_{stamp}_{config_hash}"


def save_all(
    cfg: SyntheticConfig, result: PipelineResult, sources: dict[str, str] | None = None
) -> list[Path]:
    """Write one immutable generation (tables + manifest) and point ``current`` at it."""
    written: list[Path] = []
    data_dir = cfg.output_dir or default_data_dir()
    gen_dir = data_dir / "generations" / _generation_id(cfg)
    gen_dir.mkdir(parents=True, exist_ok=True)

    def save(frame: pd.DataFrame, stem: str, subdir: str = "") -> Path:
        path = gen_dir / subdir / f"{stem}.parquet"
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
    manifest["data_fingerprint"] = data_fingerprint(result.observations[REFERENCE_COLS])
    manifest_path = gen_dir / "generation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(manifest_path)

    _point_current(data_dir, gen_dir)
    print(f"  generation written under {gen_dir}")
    return written


def _point_current(data_dir: Path, gen_dir: Path) -> None:
    """Atomically retarget the consumer-facing ``current`` alias at the newest generation."""
    current = data_dir / "current"
    tmp_link = data_dir / ".current.tmp"
    try:
        tmp_link.unlink(missing_ok=True)
        tmp_link.symlink_to(os.path.relpath(gen_dir, data_dir))
        os.replace(tmp_link, current)
        print(f"  current -> {gen_dir.relative_to(data_dir)}")
    except OSError:  # symlinks unavailable (some CI/Windows filesystems): mirror instead
        for item in gen_dir.rglob("*"):
            if item.is_file():
                dest = current / item.relative_to(gen_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(item.read_bytes())
        print(f"  (symlink unavailable; mirrored current/ -> {gen_dir})")
