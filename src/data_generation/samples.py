"""Deterministic illustrative records for the generation preview."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from nayose.config import CHANNEL_NAMES, NOISE_FIELDS, NoiseConfig, SyntheticConfig

from .corruption import RecordCorruptor

SAMPLE_PARTY: dict[str, str] = {
    "party_type": "person",
    "name_kanji": "佐藤 太郎",
    "name_kana": "サトウ タロウ",
    "name_romaji": "Sato Taro",
    "date_birth": "1988-04-12",
    "postal_code": "100-0001",
    "address": "東京都千代田区千代田1-2-3",
    "phone": "090-1234-5678",
    "email": "sato.taro@example.jp",
    "old_name_kanji": "佐藤 一郎",
    "old_name_kana": "サトウ イチロウ",
    "old_name_romaji": "Sato Ichiro",
    "old_postal_code": "150-0001",
    "old_address": "東京都渋谷区神宮前4-5-6",
    "old_phone": "080-9876-5432",
    "old_email": "sato.ichiro@example.jp",
}
SAMPLE_AGENCY: dict[str, Any] = {
    "agency_id": "A-preview",
    "channel": "paper_ocr",
    "missing_factor": 1.0,
    "ocr_factor": 0.5,
    "format_factor": 1.0,
    "shared_phone": "03-5555-0100",
    "shared_email": "office-preview@example.jp",
}
SAMPLE_CHANNEL_AGENCIES: dict[str, dict[str, Any]] = {
    channel: {**SAMPLE_AGENCY, "channel": channel} for channel in CHANNEL_NAMES
}
SAMPLE_FIELDS = ("party_type", *NOISE_FIELDS)
EXAMPLE_MATRIX: tuple[tuple[str, str], ...] = (
    ("average", "web"),
    ("average", "paper_ocr"),
    ("poor", "agent_pc"),
    ("poor", "legacy_migration"),
    ("catastrophic", "paper_ocr"),
    ("catastrophic", "legacy_migration"),
)


def _values(values: dict[str, str]) -> dict[str, str]:
    return {field: str(values.get(field, "")) for field in SAMPLE_FIELDS}


def _sample_once(
    cfg: SyntheticConfig,
    noise: NoiseConfig,
    quality: str,
    channel: str,
    seed: int,
) -> dict[str, Any]:
    canonical = _values(SAMPLE_PARTY)
    result = RecordCorruptor(cfg, noise).corrupt(
        pd.Series(SAMPLE_PARTY),
        SAMPLE_CHANNEL_AGENCIES[channel],
        quality,
        np.random.default_rng(seed),
    )
    observed = _values(result.values)
    labels: dict[str, list[str]] = {}
    for field, label in result.lineage:
        labels.setdefault(field, [])
        if label not in labels[field]:
            labels[field].append(label)
    changes = [
        {
            "field": field,
            "before": canonical[field],
            "after": observed[field],
            "transformations": labels.get(field, []),
        }
        for field in SAMPLE_FIELDS
        if canonical[field] != observed[field] or field in labels
    ]
    return {
        "quality": quality,
        "channel": channel,
        "canonical": canonical,
        "observed": observed,
        "changes": changes,
    }


def build_sample(
    cfg: SyntheticConfig,
    noise: NoiseConfig,
    quality: str,
    channel: str = "paper_ocr",
) -> dict[str, Any]:
    best = _sample_once(cfg, noise, quality, channel, cfg.seed)

    def score(sample: dict[str, Any]) -> tuple[int, int]:
        event_changes = sum(
            1
            for change in sample["changes"]
            if any(label != "missing" for label in change["transformations"])
        )
        return event_changes, len(sample["changes"])

    for offset in range(1, 96):
        candidate = _sample_once(cfg, noise, quality, channel, cfg.seed + offset * 104729)
        if score(candidate) > score(best):
            best = candidate
    return best


def build_samples(cfg: SyntheticConfig, noise: NoiseConfig) -> dict[str, dict[str, Any]]:
    return {
        quality: build_sample(cfg, noise, quality)
        for quality in ("average", "poor", "catastrophic")
    }


def build_examples(cfg: SyntheticConfig, noise: NoiseConfig) -> list[dict[str, Any]]:
    return [build_sample(cfg, noise, quality, channel) for quality, channel in EXAMPLE_MATRIX]
