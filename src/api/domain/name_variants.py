"""Deterministic, search-only name aliases derived from observed records."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from api.domain.normalize import normalize_kana, normalize_name


@dataclass(frozen=True, slots=True)
class NameVariant:
    alias: str
    normalized_alias: str
    field: str
    method: str
    confidence: float


class NameVariantExpander:
    """Expand observed names without merging or rewriting entity profiles."""

    def __init__(self, homophones: Mapping[str, Iterable[str]] | None = None):
        self.homophones = homophones or {}

    def expand(self, record: dict[str, Any]) -> tuple[NameVariant, ...]:
        variants: list[NameVariant] = []
        seen: set[tuple[str, str]] = set()
        for field, normalizer, method, confidence in (
            ("name_kanji", normalize_name, "observed_kanji", 1.0),
            ("name_kana", normalize_kana, "observed_kana", 1.0),
            ("name_romaji", normalize_name, "observed_romaji", 1.0),
        ):
            raw = text_value(record.get(field, ""))
            normalized = normalizer(raw)
            if normalized and (field, normalized) not in seen:
                variants.append(NameVariant(raw, normalized, field, method, confidence))
                seen.add((field, normalized))
            if field == "name_kana" and normalized:
                hiragana = _to_hiragana(normalized)
                if hiragana != normalized and (field, hiragana) not in seen:
                    variants.append(NameVariant(raw, hiragana, field, "kana_script", 0.8))
                    seen.add((field, hiragana))
                for homophone in self.homophones.get(normalized, ()):
                    homophone_normalized = normalize_name(str(homophone))
                    if homophone_normalized and ("name_kanji", homophone_normalized) not in seen:
                        variants.append(
                            NameVariant(
                                str(homophone),
                                homophone_normalized,
                                "name_kanji",
                                "homophone_kanji",
                                0.65,
                            )
                        )
                        seen.add(("name_kanji", homophone_normalized))
        return tuple(variants)


def text_value(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def _to_hiragana(value: str) -> str:
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in value)
