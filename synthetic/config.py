"""Configuration and data-driven presets for the synthetic generator.

All knobs live here as data (dataclasses / module dicts) so the notebook and
engine stay free of magic numbers. Env overrides exactly mirror the original
notebook's NAYOSE_* interface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

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

DEFAULT_EVENT_RATES: dict[str, float] = {
    "rename_stale": 0.06,
    "move_stale": 0.06,
    "contact_change": 0.05,
    "full_snapshot": 0.05,
    "contact_omission": 0.04,
    "paper_ocr": 0.10,
    "legacy_format": 0.08,
    "doc_damage": 0.04,
    "wrong_party_type": 0.03,
    "alpha_in_kana": 0.04,
    "combined_kana": 0.03,
    "kana_in_romaji": 0.03,
    "homophone_kanji": 0.12,
    "kana_form": 0.08,
    "digit_transpose": 0.06,
    "email_typo": 0.05,
    "shared_contact": 0.15,
}
EVENT_NAMES: tuple[str, ...] = tuple(DEFAULT_EVENT_RATES)
STALE_EVENT_NAMES: frozenset[str] = frozenset(
    {"rename_stale", "move_stale", "contact_change", "full_snapshot"}
)
DEFAULT_STALE_RATE = 0.09
CHANNEL_NAMES: tuple[str, ...] = ("web", "agent_pc", "paper_ocr", "legacy_migration")
NOISE_FIELDS: tuple[str, ...] = (
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "postal_code",
    "address",
    "phone",
    "email",
)

# Baseline contact realism (data, not code): email domains a privacy-safe but
# realistic form could plausibly carry, and per-channel contact styles. Real
# carrier/ISP suffixes are used for the shapes; nothing here is a person.
EMAIL_DOMAINS: dict[str, tuple[str, ...]] = {
    "modern": (
        "gmail.com",
        "yahoo.co.jp",
        "i.softbank.jp",
        "ezweb.ne.jp",
        "outlook.jp",
        "icloud.com",
    ),
    "isp": ("ocn.ne.jp", "biglobe.ne.jp", "so-net.ne.jp", "plala.or.jp", "nifty.com", "au.com"),
}

DEFAULT_LANDLINE_SHARE = 0.12  # party-level phone style mix (mobile vs fixed line)

# Policy lifecycle data (G1): a customer holds 1..n policies, each an event
# timeline (issued -> renewals -> claim/cancel). Observations attach to events.
POLICY_PARAMS: dict[str, Any] = {
    "mean_policies": 1.3,
    "renew_p": 0.55,
    "claim_rate": 0.18,
    "cancel_rate": 0.12,
    "coverage_rate": 0.45,  # family plan: a second household member on the policy
    "start_range": (1985, 2022),
    "history_rate": 0.60,  # share of parties with a truth change over time (G4)
}

CHANNEL_CONTACT_PROFILE: dict[str, dict[str, float]] = {
    # email_family: index into EMAIL_DOMAINS tuple (0=modern, 1=isp)
    # landline_share: probability the channel office's shared line is a fixed line
    "web": {"email_family": 0.0, "landline_share": 0.02},
    "agent_pc": {"email_family": 0.0, "landline_share": 0.06},
    "paper_ocr": {"email_family": 1.0, "landline_share": 0.20},
    "legacy_migration": {"email_family": 1.0, "landline_share": 0.35},
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
    mean_records_override: float | None = None
    poor_override: float | None = None
    catastrophic_override: float | None = None
    noise_event_rates: dict[str, float] = field(default_factory=dict)
    noise_band_scale: dict[str, float] = field(default_factory=dict)
    noise_missing_rates: dict[str, float] = field(default_factory=dict)
    noise_field_availability: dict[str, dict[str, float]] = field(default_factory=dict)
    noise_stale_rate: float | None = None
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
        if not 0 <= self.n_parties_override <= 2_000_000:
            raise ValueError("n_parties_override must be between 0 and 2,000,000")
        if self.mean_records_override is not None and not 1.0 <= self.mean_records_override <= 20.0:
            raise ValueError("mean_records_override must be between 1 and 20")
        try:
            snapshot = date.fromisoformat(self.snapshot_date)
            query = date.fromisoformat(self.query_date)
        except ValueError as exc:
            raise ValueError("snapshot_date and query_date must be ISO dates") from exc
        if query < snapshot:
            raise ValueError("query_date must not precede snapshot_date")
        if not 1 <= self.n_agencies <= 100:
            raise ValueError("n_agencies must be between 1 and 100")
        for name, value in (
            ("person_share", self.person_share),
            ("query_share", self.query_share),
            ("twin_rate", self.twin_rate),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if set(self.query_mix) != set(QUERY_MIX) or any(
            not 0.0 <= value <= 1.0 for value in self.query_mix.values()
        ):
            raise ValueError(
                f"query_mix keys must be {sorted(QUERY_MIX)} with values between 0 and 1"
            )
        if not np.isclose(sum(self.query_mix.values()), 1.0):
            raise ValueError("query_mix probabilities must sum to 1")
        for name, values, low, high, allowed in (
            ("noise_event_rates", self.noise_event_rates, 0.0, 1.0, set(EVENT_NAMES)),
            (
                "noise_band_scale",
                self.noise_band_scale,
                0.0,
                3.0,
                {"average", "poor", "catastrophic"},
            ),
            (
                "noise_missing_rates",
                self.noise_missing_rates,
                0.0,
                1.0,
                {"average", "poor", "catastrophic"},
            ),
        ):
            unknown = set(values) - allowed
            if unknown or any(not low <= value <= high for value in values.values()):
                raise ValueError(
                    f"{name} contains unknown or out-of-range values: {sorted(unknown)}"
                )
        for channel, values in self.noise_field_availability.items():
            if (
                channel not in CHANNEL_NAMES
                or set(values) - set(NOISE_FIELDS)
                or any(not 0.0 <= value <= 1.0 for value in values.values())
            ):
                raise ValueError(
                    "noise_field_availability contains an unknown or "
                    f"out-of-range channel/field: {channel}"
                )
        if self.noise_stale_rate is not None and not 0.0 <= self.noise_stale_rate <= 1.0:
            raise ValueError("noise_stale_rate must be between 0 and 1")
        profile = PROFILES[self.profile]
        diff = DIFFICULTY[self.difficulty]
        poor = self.poor_override if self.poor_override is not None else float(diff["poor"])
        catastrophic = (
            self.catastrophic_override
            if self.catastrophic_override is not None
            else float(diff["catastrophic"])
        )
        if not 0.0 <= catastrophic <= poor <= 1.0:
            raise ValueError(
                "catastrophic_override must be <= poor_override and both between 0 and 1"
            )
        object.__setattr__(self, "n_parties", self.n_parties_override or int(profile["n_parties"]))
        object.__setattr__(
            self, "mean_records", self.mean_records_override or float(profile["mean_records"])
        )
        object.__setattr__(self, "poor", poor)
        object.__setattr__(self, "catastrophic", catastrophic)
        object.__setattr__(self, "copy", float(diff["copy"]))
        object.__setattr__(self, "stale", float(diff["stale"]))


@dataclass(frozen=True, slots=True)
class NoiseConfig:
    """Correlated dirty-data event rates (data-driven, one place to tune)."""

    # per-record event probabilities at 'average' quality (scaled by band)
    event_rates: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_EVENT_RATES))
    # Vintage / system-driven field availability: channel -> field -> keep probability.
    # Missingness is SYSTEMATIC (a legacy system never had email), not random per field.
    field_availability: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "web": {},
            "agent_pc": {"email": 0.92, "name_romaji": 0.95},
            "paper_ocr": {"email": 0.45, "name_romaji": 0.70},
            "legacy_migration": {"email": 0.0, "name_romaji": 0.15},
        }
    )
    # Source-document error bundles: a record typed from a channel's form carries the
    # channel's characteristic disorders together (coherent document-level errors).
    channel_events: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "web": ("email_typo", "digit_transpose"),
            "agent_pc": ("homophone_kanji", "digit_transpose"),
            "paper_ocr": ("paper_ocr", "kana_form"),
            "legacy_migration": ("legacy_format",),
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
    stale_rate: float = DEFAULT_STALE_RATE  # baseline multiplier for stale-history events


def noise_for_config(cfg: SyntheticConfig) -> NoiseConfig:
    """Build the effective noise strategy from validated user overrides."""
    base = NoiseConfig()
    return NoiseConfig(
        event_rates={**base.event_rates, **cfg.noise_event_rates},
        field_availability={
            channel: {
                **base.field_availability.get(channel, {}),
                **cfg.noise_field_availability.get(channel, {}),
            }
            for channel in CHANNEL_NAMES
        },
        channel_events=base.channel_events,
        band_scale={**base.band_scale, **cfg.noise_band_scale},
        missing_rates={**base.missing_rates, **cfg.noise_missing_rates},
        channel_family=base.channel_family,
        vintage=base.vintage,
        stale_rate=cfg.noise_stale_rate if cfg.noise_stale_rate is not None else base.stale_rate,
    )


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
