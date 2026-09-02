"""Configuration and data-driven presets for the synthetic generator.

All knobs live here as data (dataclasses / module dicts) so the notebook and
engine stay free of magic numbers. Env overrides exactly mirror the original
notebook's NAYOSE_* interface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    """data/ living at the repository root, independent of the working directory."""
    return REPO_ROOT / "data"


# --------------------------------------------------------------------------
# Size / difficulty presets (data, not code)
# --------------------------------------------------------------------------
PROFILES: dict[str, dict[str, float]] = {
    "preflight": {"n_parties": 300, "mean_records": 2.4},
    "smoke": {"n_parties": 700, "mean_records": 2.5},
    "mac": {"n_parties": 6_000, "mean_records": 2.7},
    "large_mac": {"n_parties": 20_000, "mean_records": 2.5},
    "generate_only": {"n_parties": 100_000, "mean_records": 2.2},
}

DIFFICULTY: dict[str, dict[str, float]] = {
    "debug": {"poor": 0.10, "catastrophic": 0.005, "copy": 0.02, "stale": 0.12},
    "realistic": {"poor": 0.33, "catastrophic": 0.04, "copy": 0.09, "stale": 0.34},
    "severe": {"poor": 0.43, "catastrophic": 0.07, "copy": 0.12, "stale": 0.40},
}

QUERY_MIX: dict[str, float] = {
    "existing_stable": 0.20,
    "existing_changed": 0.25,
    "existing_damaged": 0.20,
    "new_customer": 0.15,
    "related_only": 0.15,
    "insufficient": 0.05,
}


@dataclass(frozen=True, slots=True)
class SyntheticConfig:
    """Immutable configuration for synthetic data generation."""

    seed: int = int(os.getenv("NAYOSE_SEED", "42"))
    profile: str = os.getenv("NAYOSE_PROFILE", "generate_only")
    difficulty: str = os.getenv("NAYOSE_DIFFICULTY", "realistic")
    output_dir: Path | None = None  # resolved in __post_init__ (env NAYOSE_DATA_DIR or repo data/)
    raw_dir: Path | None = (
        None  # immutable reference-data cache (env NAYOSE_RAW_DIR or repo data/raw)
    )
    n_parties_override: int = int(os.getenv("NAYOSE_N_PARTIES", "0"))
    snapshot_date: str = os.getenv("NAYOSE_SNAPSHOT_DATE", "2025-12-31")
    query_date: str = os.getenv("NAYOSE_QUERY_DATE", "2026-08-25")
    n_agencies: int = 18
    person_share: float = 0.90
    query_share: float = 0.16
    twin_rate: float = float(
        os.getenv("NAYOSE_TWIN_RATE", "0.15")
    )  # same-name twin entities (hard cases)
    lineage_mode: str = "changed_plus_queries"
    query_mix: dict[str, float] = field(default_factory=lambda: dict(QUERY_MIX))

    @property
    def snapshot_year(self) -> int:
        return int(self.snapshot_date[:4])

    # resolved profile/difficulty values (populated after construction)
    n_parties: int = 0
    mean_records: float = 2.2
    poor: float = 0.33
    catastrophic: float = 0.04
    copy: float = 0.09
    stale: float = 0.34

    def __post_init__(self) -> None:
        if self.output_dir is None:
            env_dir = os.getenv("NAYOSE_DATA_DIR")
            object.__setattr__(self, "output_dir", Path(env_dir) if env_dir else default_data_dir())
        if self.raw_dir is None:
            env_raw = os.getenv("NAYOSE_RAW_DIR")
            object.__setattr__(
                self, "raw_dir", Path(env_raw) if env_raw else REPO_ROOT / "data" / "raw"
            )
        if self.profile not in PROFILES:
            raise ValueError(f"Unknown profile {self.profile!r}; choose from {list(PROFILES)}")
        if self.difficulty not in DIFFICULTY:
            raise ValueError(
                f"Unknown difficulty {self.difficulty!r}; choose from {list(DIFFICULTY)}"
            )
        if not np.isclose(sum(self.query_mix.values()), 1.0):
            raise ValueError("query_mix probabilities must sum to 1")
        profile = PROFILES[self.profile]
        diff = DIFFICULTY[self.difficulty]
        object.__setattr__(self, "n_parties", self.n_parties_override or int(profile["n_parties"]))
        object.__setattr__(self, "mean_records", float(profile["mean_records"]))
        object.__setattr__(self, "poor", float(diff["poor"]))
        object.__setattr__(self, "catastrophic", float(diff["catastrophic"]))
        object.__setattr__(self, "copy", float(diff["copy"]))
        object.__setattr__(self, "stale", float(diff["stale"]))


@dataclass(frozen=True, slots=True)
class NoiseConfig:
    """Correlated dirty-data event rates (data-driven, one place to tune)."""

    # per-record event probabilities at 'average' quality (scaled by band)
    event_rates: dict[str, float] = field(
        default_factory=lambda: {
            "rename_stale": 0.06,  # whole name replaced by old name (recent rename)
            "move_stale": 0.06,  # address + postal replaced by old values together
            "contact_change": 0.05,  # phone + email replaced by old values together
            "full_snapshot": 0.05,  # coherent pre-move/pre-re-contact snapshot of the truth
            "contact_omission": 0.04,  # phone+email missing together (scanned-card omission)
            "paper_ocr": 0.10,  # OCR confusion burst on names / address
            "legacy_format": 0.08,  # era dates + half-width + old phone/postal shapes
            "doc_damage": 0.04,  # correlated missing: whole name trio or contact trio
            "wrong_party_type": 0.03,
            "alpha_in_kana": 0.04,
            "combined_kana": 0.03,
            "kana_in_romaji": 0.03,
        }
    )
    # per-band severity multipliers  (light / medium / heavy)
    band_scale: dict[str, float] = field(
        default_factory=lambda: {
            "average": 0.35,
            "poor": 0.75,
            "catastrophic": 1.6,
        }
    )
    missing_rates: dict[str, float] = field(
        default_factory=lambda: {
            "average": 0.05,
            "poor": 0.18,
            "catastrophic": 0.50,
        }
    )
    # channel → dominant corruption family (vintage / width / ocr)
    channel_family: dict[str, str] = field(
        default_factory=lambda: {
            "web": "modern",
            "agent_pc": "modern",
            "paper_ocr": "ocr",
            "legacy_migration": "legacy",
        }
    )
    # observed_at vintage windows per channel
    vintage: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {
            "web": (2010, 2024),
            "agent_pc": (2005, 2024),
            "paper_ocr": (1995, 2016),
            "legacy_migration": (1980, 2005),
        }
    )
    stale_rate: float = 0.09  # per-field fallback stale probability


@dataclass(frozen=True, slots=True)
class DataSources:
    """Paths / registration points for the real datasets."""

    persona_dataset: str = "nvidia/Nemotron-Personas-Japan"
    persona_split: str = "train"
    persona_cache: str = "persona_names.parquet"
    edinet_dataset: str = "kawagoshi-llm-team/edinet2023"
    edinet_split: str = "train"
    japan_post_zip_url: str = (
        "https://www.post.japanpost.jp/service/search/zipcode/download/kogaki/zip/ken_all.zip"
    )
    postal_zip_name: str = "ken_all.zip"
    postal_csv_name: str = "ken_all.csv"
