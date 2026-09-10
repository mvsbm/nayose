"""Server-owned entity index queries for exact search and paging."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from api.query_context import (
    ENTITY_COLUMNS,
    ENTITY_SEARCH_COLUMNS,
    SEARCH_STOPWORDS,
    BundleReader,
    BundleSnapshot,
)
from api.search import _prefecture
from api.search_service import SearchFilters


class EntitySearchRepository:
    """Read canonical entities using one exact-search implementation."""

    _SORTS = frozenset({"relevance", "records", "name", "last_seen"})

    def __init__(self, context: BundleReader):
        self.context = context

    def entities(
        self,
        run_id: str,
        q: str,
        limit: int = 50,
        filters: SearchFilters | None = None,
    ) -> list[dict[str, Any]]:
        return self.page(run_id, q, 0, limit, "relevance", filters, count_total=False)["rows"]

    def page(
        self,
        run_id: str,
        q: str,
        offset: int,
        limit: int,
        sort: str = "relevance",
        filters: SearchFilters | None = None,
        count_total: bool = True,
    ) -> dict[str, Any]:
        safe_offset = max(0, int(offset))
        safe_limit = min(max(1, int(limit)), 200)
        selected_sort = sort if sort in self._SORTS else "relevance"
        con, snapshot = self.context.open(run_id)
        run_dir = snapshot.run_dir
        try:
            source = f"read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR)"
            available = self._available_columns(con, source)
            search_columns = tuple(
                column for column in ENTITY_SEARCH_COLUMNS if column in available
            )
            select_columns = tuple(column for column in ENTITY_COLUMNS if column in available)
            where, where_params, patterns = self._where_clause(
                snapshot, q, search_columns, available, filters
            )
            order, order_params = self._order_clause(q, selected_sort, search_columns, patterns)
            count = (
                con.execute(f"SELECT count(*) FROM {source} {where}", where_params).fetchone()
                if count_total
                else None
            )
            total = int(count[0]) if count else 0
            frame = con.execute(
                f"SELECT {','.join(select_columns)} FROM {source} {where} {order} LIMIT ? OFFSET ?",
                [*where_params, *order_params, safe_limit, safe_offset],
            ).fetchdf()
            rows = frame.where(pd.notna(frame), None).to_dict("records")
            if q.strip():
                self._decorate_exact(rows, search_columns, patterns, con, run_dir)
            return {
                "rows": rows,
                "total": total,
                "offset": safe_offset,
                "limit": safe_limit,
                "query": q.strip(),
                "sort": selected_sort,
                "filters": filters.as_dict() if filters else {},
            }
        finally:
            con.close()

    def _where_clause(
        self,
        snapshot: BundleSnapshot,
        q: str,
        search_columns: tuple[str, ...],
        available: set[str],
        filters: SearchFilters | None,
    ) -> tuple[str, list[Any], tuple[str, ...]]:
        run_dir = snapshot.run_dir
        conditions: list[str] = []
        params: list[Any] = []
        patterns = tuple(self._pattern(token) for token in self._query_tokens(q))
        if q.strip():
            reference = str(snapshot.data_dir / "reference_records.parquet")
            mapping = str(run_dir / "record_to_nayose.parquet")
            for pattern in patterns:
                field_sql, field_params = self._match_sql(search_columns, pattern)
                conditions.append(
                    f"({field_sql} OR nayose_id IN (SELECT DISTINCT m.nayose_id "
                    f"FROM read_parquet('{reference}') r "
                    f"JOIN read_parquet('{mapping}') m USING(record_id) "
                    f"WHERE regexp_matches(CAST(r.policy_id AS VARCHAR), ?) "
                    f"OR regexp_matches(CAST(r.agency_id AS VARCHAR), ?)))"
                )
                params.extend([*field_params, pattern, pattern])
        if filters and filters.party_type and "party_type" in available:
            conditions.append("lower(CAST(party_type AS VARCHAR)) = lower(?)")
            params.append(filters.party_type)
        if filters and filters.min_record_count is not None and "record_count" in available:
            conditions.append("record_count >= ?")
            params.append(max(0, int(filters.min_record_count)))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return where, params, patterns

    @staticmethod
    def _match_sql(columns: tuple[str, ...], pattern: str) -> tuple[str, list[str]]:
        clauses = [f"regexp_matches(CAST({column} AS VARCHAR), ?)" for column in columns]
        return (" OR ".join(clauses) or "FALSE"), [pattern] * len(clauses)

    @staticmethod
    def _order_clause(
        q: str,
        sort: str,
        search_columns: tuple[str, ...],
        patterns: tuple[str, ...],
    ) -> tuple[str, list[str]]:
        if sort == "records" or not q.strip() and sort == "relevance":
            return "ORDER BY record_count DESC NULLS LAST, nayose_id", []
        if sort == "name":
            return "ORDER BY lower(CAST(name_kanji AS VARCHAR)) ASC NULLS LAST, nayose_id", []
        if sort == "last_seen":
            return "ORDER BY last_seen DESC NULLS LAST, nayose_id", []
        priority_columns = (
            "name_kanji",
            "name_kana",
            "name_romaji",
            "phone",
            "email",
            "postal_code",
            "date_birth",
            "party_type",
            "address",
        )
        cases: list[str] = []
        params: list[str] = []
        for rank, column in enumerate(priority_columns):
            if column not in search_columns:
                continue
            clauses = [f"regexp_matches(CAST({column} AS VARCHAR), ?)" for _ in patterns]
            cases.append(f"WHEN ({' OR '.join(clauses)}) THEN {rank}")
            params.extend(patterns)
        if not cases:
            return "ORDER BY nayose_id", []
        return f"ORDER BY CASE {' '.join(cases)} ELSE 99 END, name_kanji, nayose_id", params

    @staticmethod
    def _query_tokens(q: str) -> tuple[str, ...]:
        tokens = tuple(token for token in re.split(r"\s+", q.strip()) if token)
        useful = tuple(token for token in tokens if token.casefold() not in SEARCH_STOPWORDS)
        return useful or tokens

    @staticmethod
    def _pattern(token: str) -> str:
        separator = r"\s*"
        return f"(?i).*{separator.join(re.escape(char) for char in token)}.*"

    @staticmethod
    def _available_columns(con: Any, source: str) -> set[str]:
        return {
            str(column[0]) for column in con.execute(f"SELECT * FROM {source} LIMIT 0").description
        }

    def _decorate_exact(
        self,
        rows: list[dict[str, Any]],
        search_columns: tuple[str, ...],
        patterns: tuple[str, ...],
        con: Any,
        run_dir: Path,
    ) -> None:
        twin_counts = self._twin_counts(con, run_dir)
        for row in rows:
            row["kind"] = "exact"
            row["signals"] = [
                column
                for column in search_columns
                if any(re.search(pattern, str(row.get(column, ""))) for pattern in patterns)
            ]
            kana = str(row.get("name_kana", "")).replace(" ", "")
            row["twin_count"] = max(0, twin_counts.get(kana, 1) - 1)
            row["prefecture"] = _prefecture(str(row.get("address", "")))

    @staticmethod
    def _twin_counts(con: Any, run_dir: Path) -> dict[str, int]:
        source = f"read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR)"
        values = con.execute(
            f"SELECT name_kana, count(distinct name_kanji) AS n FROM {source} "
            "WHERE name_kana != '' GROUP BY 1"
        ).fetchall()
        return {str(k).replace(" ", ""): int(n) for k, n in values}
