"""Deterministic, search-only name aliases derived from observed records."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from api.normalize import normalize_kana, normalize_name

ALIAS_COLUMNS = (
    "nayose_id",
    "record_id",
    "record_index",
    "alias",
    "normalized_alias",
    "field",
    "method",
    "confidence",
    "data_fingerprint",
)


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
            raw = _text(record.get(field, ""))
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


def _text(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def _to_hiragana(value: str) -> str:
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in value)


def _alias_rows(
    reference: pd.DataFrame,
    record_to_entity: dict[str, str],
    data_fingerprint: str,
    expander: NameVariantExpander,
) -> Iterator[dict[str, Any]]:
    columns = list(reference.columns)
    for record_index, values in enumerate(reference.itertuples(index=False, name=None)):
        record = dict(zip(columns, values, strict=True))
        record_id = str(record.get("record_id") or "")
        entity_id = str(record_to_entity.get(record_id) or "")
        if not entity_id:
            continue
        for variant in expander.expand(record):
            yield {
                "nayose_id": entity_id,
                "record_id": record_id,
                "record_index": record_index,
                "alias": variant.alias,
                "normalized_alias": variant.normalized_alias,
                "field": variant.field,
                "method": variant.method,
                "confidence": variant.confidence,
                "data_fingerprint": data_fingerprint,
            }


def build_entity_aliases(
    reference: pd.DataFrame,
    record_to_entity: dict[str, str],
    output: Path,
    *,
    data_fingerprint: str,
    expander: NameVariantExpander | None = None,
    chunk_size: int = 50_000,
) -> dict[str, Any]:
    """Materialize a provenance-rich alias table as an immutable bundle artifact."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if expander is None:
        homophones: dict[str, set[str]] = {}
        columns = list(reference.columns)
        for values in reference.itertuples(index=False, name=None):
            record = dict(zip(columns, values, strict=True))
            kana_value = _text(record.get("name_kana", ""))
            name_value = _text(record.get("name_kanji", ""))
            if kana_value and name_value:
                kana = normalize_kana(kana_value)
                name = normalize_name(name_value)
                if kana and name:
                    homophones.setdefault(kana, set()).add(name)
        expander = NameVariantExpander(homophones)
    if output.exists():
        existing = pd.read_parquet(output, columns=["data_fingerprint"])
        fingerprints = set(existing["data_fingerprint"].astype(str))
        if fingerprints and fingerprints != {data_fingerprint}:
            raise RuntimeError("alias artifact fingerprint does not match the reference generation")
        return {
            "path": output.name,
            "format": "parquet",
            "row_count": len(existing),
            "data_fingerprint": data_fingerprint,
            "columns": list(ALIAS_COLUMNS),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}.parquet")
    writer: Any = None
    row_count = 0
    try:
        rows = _alias_rows(reference, record_to_entity, data_fingerprint, expander)
        while True:
            chunk = list(_take(rows, chunk_size))
            if not chunk:
                break
            frame = pd.DataFrame.from_records(chunk, columns=ALIAS_COLUMNS)
            if writer is None:
                import pyarrow as pa  # type: ignore[import-untyped]
                import pyarrow.parquet as pq  # type: ignore[import-untyped]

                writer = pq.ParquetWriter(
                    temporary,
                    pa.Table.from_pandas(frame, preserve_index=False).schema,
                    compression="zstd",
                )
            import pyarrow as pa

            writer.write_table(pa.Table.from_pandas(frame, preserve_index=False))
            row_count += len(frame)
        if writer is None:
            pd.DataFrame(columns=ALIAS_COLUMNS).to_parquet(temporary, index=False)
        else:
            writer.close()
            writer = None
        os.replace(temporary, output)
    finally:
        if writer is not None:
            writer.close()
        temporary.unlink(missing_ok=True)
    return {
        "path": output.name,
        "format": "parquet",
        "row_count": row_count,
        "data_fingerprint": data_fingerprint,
        "columns": list(ALIAS_COLUMNS),
    }


def _take(values: Iterator[dict[str, Any]], count: int) -> Iterable[dict[str, Any]]:
    for _ in range(count):
        try:
            yield next(values)
        except StopIteration:
            return


def alias_texts(aliases: pd.DataFrame) -> list[str]:
    return [f"name={row.normalized_alias}" for row in aliases.itertuples(index=False)]


def alias_record_indices(aliases: pd.DataFrame) -> Iterable[int]:
    return (int(value) for value in aliases["record_index"].to_numpy())
