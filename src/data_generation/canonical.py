"""Streaming writer for the clean canonical entity layer."""

from __future__ import annotations

import hashlib
import json
import os
import time
from itertools import islice
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nayose.config import SyntheticConfig, default_data_dir

from .generator import iter_clean_party_rows
from .sources import AddressSource, BuildingComposer, CompanySource, PersonaSource

CLEAN_COLUMNS = [
    "entity_id",
    "party_type",
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "postal_code",
    "address",
    "phone",
    "email",
]


def _replace_alias(alias: Path, target: Path) -> None:
    relative_target = os.path.relpath(target, alias.parent)
    temporary = alias.with_name(f".{alias.name}.tmp")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(relative_target, target_is_directory=True)
    temporary.replace(alias)


def write_clean_entities(
    cfg: SyntheticConfig,
    chunk_size: int = 50_000,
) -> dict[str, object]:
    """Write exactly ``cfg.n_parties`` clean entities with bounded output memory."""
    if not 1 <= chunk_size <= 250_000:
        raise ValueError("chunk_size must be between 1 and 250,000")

    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - project dependency guard
        raise RuntimeError("pyarrow is required for clean streaming generation") from exc

    output_dir = cfg.output_dir or default_data_dir()
    raw_dir = cfg.raw_dir or output_dir / "raw"
    clean_root = output_dir / "clean_generations"
    clean_root.mkdir(parents=True, exist_ok=True)
    config_payload = {
        "profile": cfg.profile,
        "seed": cfg.seed,
        "n_entities": cfg.n_parties,
        "person_share": cfg.person_share,
        "twin_rate": cfg.twin_rate,
        "snapshot_date": cfg.snapshot_date,
    }
    config_hash = hashlib.sha256(
        json.dumps(config_payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]
    generation_dir = clean_root / f"clean_{time.strftime('%Y%m%d-%H%M%S')}_{config_hash}"
    generation_dir.mkdir()
    output_path = generation_dir / "clean_entities.parquet"

    persona = PersonaSource(raw_dir)
    companies = CompanySource()
    address = AddressSource(raw_dir)
    buildings = BuildingComposer()
    rng = np.random.default_rng(cfg.seed)
    fingerprint = hashlib.sha256()
    writer: Any = None
    written = 0
    started = time.perf_counter()

    try:
        stream = iter_clean_party_rows(
            cfg,
            rng,
            persona,
            companies,
            address,
            buildings,
            target_rows=cfg.n_parties,
        )
        while written < cfg.n_parties:
            rows: list[dict[str, str]] = []
            for offset, row in enumerate(islice(stream, min(chunk_size, cfg.n_parties - written))):
                row["entity_id"] = f"T{written + offset:010d}"
                rows.append(row)
            if not rows:
                raise RuntimeError("clean entity stream ended before reaching the requested count")
            frame = pd.DataFrame(rows, columns=CLEAN_COLUMNS)
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(output_path, table.schema, compression="zstd")
            writer.write_table(table)
            fingerprint.update(
                pd.util.hash_pandas_object(frame[CLEAN_COLUMNS], index=False).values.tobytes()
            )
            written += len(frame)
            if written == cfg.n_parties or written % max(chunk_size * 10, 1) == 0:
                elapsed = time.perf_counter() - started
                rate = written / max(elapsed, 1e-9)
                print(f"  [clean entities] {written:,}/{cfg.n_parties:,} ({rate:,.0f}/s)")
    finally:
        if writer is not None:
            writer.close()

    manifest = {
        "schema_version": "clean-1",
        "artifact": "clean_entities",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": config_payload,
        "counts": {"entities": written},
        "columns": CLEAN_COLUMNS,
        "data_fingerprint": fingerprint.hexdigest(),
        "notes": {
            "en": "Canonical entities only; no record sabotage, policies, or hidden labels.",
            "ja": "正規エンティティのみ。レコード破損、保険契約、隠しラベルは含みません。",
        },
    }
    (generation_dir / "clean_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _replace_alias(output_dir / "clean_current", generation_dir)
    print(f"clean_current -> {generation_dir}")
    return {"generation_dir": str(generation_dir), "path": str(output_path), **manifest}
