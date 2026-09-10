"""Entity search and fuzzy suggestion queries over an immutable bundle."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from api.entity_index import EntitySearchRepository
from api.normalize import (
    normalize_address,
    normalize_dob,
    normalize_email,
    normalize_kana,
    normalize_name,
    normalize_phone,
    normalize_postal,
    normalize_query_fields,
)
from api.query_context import (
    ENTITY_COLUMNS,
    FUZZY_SEARCH_CAP,
    BundleReader,
)
from api.search import _prefecture
from api.search_service import SearchFilters


class EntityQueries:
    def __init__(self, context: BundleReader):
        self.context = context
        self.index = EntitySearchRepository(context)

    def entities(
        self,
        run_id: str,
        q: str,
        limit: int = 50,
        filters: SearchFilters | None = None,
    ) -> list[dict[str, Any]]:
        return self.index.entities(run_id, q, limit, filters)

    def entity_page(
        self,
        run_id: str,
        q: str,
        offset: int,
        limit: int,
        sort: str,
        filters: SearchFilters | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return self.index.page(run_id, q, offset, limit, sort, filters, cursor=cursor)

    def normalized_query(self, q: str) -> dict[str, str]:
        return normalize_query_fields(q)

    def _nearest_choices(self, run_dir: Path, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        key = f"nearest-choices:{run_dir.resolve()}:{','.join(fields)}"

        def load() -> list[dict[str, Any]]:
            con = duckdb.connect()
            try:
                source = f"read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR)"
                available = {
                    str(column[0])
                    for column in con.execute(f"SELECT * FROM {source} LIMIT 0").description
                }
                select_cols = tuple(column for column in ENTITY_COLUMNS if column in available)
                frame = con.execute(
                    f"SELECT {','.join(select_cols)} FROM {source} "
                    f"ORDER BY record_count DESC LIMIT {FUZZY_SEARCH_CAP}"
                ).fetchdf()
            finally:
                con.close()
            rows = frame.where(pd.notna(frame), None).to_dict("records")
            kana_counts: dict[str, int] = {}
            for row in rows:
                kana = re.sub(r"\s+", "", str(row.get("name_kana") or ""))
                if kana:
                    kana_counts[kana] = kana_counts.get(kana, 0) + 1
            choices: list[dict[str, Any]] = []
            normalizers = {
                "name_kanji": normalize_name,
                "name_kana": normalize_kana,
                "name_romaji": normalize_name,
                "phone": normalize_phone,
                "postal_code": normalize_postal,
                "address": normalize_address,
                "email": normalize_email,
                "date_birth": normalize_dob,
                "party_type": normalize_name,
            }
            for row in rows:
                entity = {
                    "nayose_id": str(row.get("nayose_id") or ""),
                    "name_kanji": str(row.get("name_kanji") or ""),
                    "name_kana": str(row.get("name_kana") or ""),
                    "name_romaji": str(row.get("name_romaji") or ""),
                    "phone": str(row.get("phone") or ""),
                    "postal_code": str(row.get("postal_code") or ""),
                    "address": str(row.get("address") or ""),
                    "email": str(row.get("email") or ""),
                    "date_birth": str(row.get("date_birth") or ""),
                    "party_type": str(row.get("party_type") or ""),
                    "record_count": int(row.get("record_count") or 0),
                    "prefecture": _prefecture(str(row.get("address") or "")),
                    "twin_count": max(
                        0,
                        kana_counts.get(re.sub(r"\s+", "", str(row.get("name_kana") or "")), 1) - 1,
                    ),
                }
                for field in fields:
                    value = normalizers[field](str(entity[field]))
                    if len(value) >= 2:
                        choices.append({"text": value, "field": field, "entity": entity})
            return choices

        return self.context.cached(key, load)

    def nearest_entities(
        self,
        run_id: str,
        q: str,
        limit: int = 8,
        nearest_choices: Callable[[Path, tuple[str, ...]], list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        from rapidfuzz import fuzz, process

        raw = re.sub(r"\s+", "", q)
        if len(raw) < 2 or not any(char.isalnum() for char in raw):
            return []
        forms = {
            compact
            for value in [raw, *self.normalized_query(q).values()]
            if (compact := re.sub(r"\s+", "", value))
            and len(compact) >= 2
            and any(char.isalnum() for char in compact)
        }
        run_dir = self.context.repo.resolve(run_id)
        field_list = ["name_kanji", "name_kana", "name_romaji"]
        if any(char.isdigit() for char in q):
            field_list.extend(["phone", "postal_code", "date_birth"])
        if "@" in q:
            field_list.append("email")
        if any(
            token in q.casefold()
            for token in ("住所", "都", "道", "府", "県", "市", "区", "町", "street", "avenue")
        ):
            field_list.append("address")
        if any(
            token in q.casefold() for token in ("個人", "法人", "person", "company", "corporation")
        ):
            field_list.append("party_type")
        fields = tuple(dict.fromkeys(field_list))
        choices = [
            choice
            for choice in (nearest_choices or self._nearest_choices)(run_dir, fields)
            if len(choice["text"]) >= 2
        ]
        texts = [choice["text"] for choice in choices]
        best: dict[str, dict[str, Any]] = {}
        for form in forms:
            for _, score, index in process.extract(
                form, texts, scorer=fuzz.WRatio, limit=max(limit * 4, 20), score_cutoff=35
            ):
                choice = choices[index]
                entity = choice["entity"]
                entity_id = entity["nayose_id"]
                if score <= best.get(entity_id, {}).get("score", -1):
                    continue
                best[entity_id] = {
                    **entity,
                    "score": round(float(score) / 100, 4),
                    "kind": "nearest",
                    "signals": [choice["field"]],
                    "match_field": choice["field"],
                    "match_method": "fuzzy_nearest",
                }
        return sorted(
            best.values(), key=lambda row: (-row["score"], -row["record_count"], row["nayose_id"])
        )[:limit]

    def suggest(
        self, run_id: str, q: str, nearest: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """Return the strongest server-owned nearest-entity correction."""
        nearest = nearest if nearest is not None else self.nearest_entities(run_id, q, 1)
        if not nearest or nearest[0]["score"] < 0.60:
            return None
        hit = nearest[0]
        return {
            "term": hit["name_kanji"],
            "score": hit["score"],
            "method": hit["match_method"],
            "field": hit["match_field"],
            "nayose_id": hit["nayose_id"],
        }
