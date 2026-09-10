"""Server-owned entity index queries for exact search and paging."""

from __future__ import annotations

import base64
import json
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


class EntityCursorError(ValueError):
    pass


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
        cursor: str | None = None,
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
            order_spec = self._order_spec(q, selected_sort, search_columns, patterns, available)
            cursor_values = (
                self._decode_cursor(
                    cursor,
                    q,
                    selected_sort,
                    filters,
                    len(order_spec),
                    order_spec[0][0].startswith(("CASE ", "COALESCE(CAST(record_count")),
                )
                if cursor
                else None
            )
            cursor_params: list[Any] = []
            if cursor_values is not None:
                cursor_sql, cursor_params = self._cursor_clause(order_spec, cursor_values)
                where = f"{where} AND {cursor_sql}" if where else f"WHERE {cursor_sql}"
                where_params = [*where_params, *cursor_params]
            count = (
                con.execute(f"SELECT count(*) FROM {source} {where}", where_params).fetchone()
                if count_total and cursor_values is None
                else None
            )
            total = int(count[0]) if count else -1 if cursor_values is not None else 0
            select_order_params: list[Any] = []
            cursor_select = []
            for index, (expression, _direction, expression_params) in enumerate(order_spec):
                cursor_select.append(f"{expression} AS __entity_cursor_{index}")
                select_order_params.extend(expression_params)
            order = "ORDER BY " + ", ".join(
                f"__entity_cursor_{index} {direction}"
                for index, (_expression, direction, _params) in enumerate(order_spec)
            )
            fetch_limit = safe_limit + 1 if cursor_values is not None else safe_limit
            pagination_params: list[Any] = [*select_order_params, *where_params, fetch_limit]
            if cursor_values is None:
                pagination_params.append(safe_offset)
                pagination = " OFFSET ?"
            else:
                pagination = ""
            frame = con.execute(
                f"SELECT {','.join((*select_columns, *cursor_select))} FROM {source} "
                f"{where} {order} LIMIT ?{pagination}",
                pagination_params,
            ).fetchdf()
            has_more = (
                len(frame) > safe_limit
                if cursor_values is not None
                else safe_offset + len(frame) < total
            )
            next_cursor = None
            if has_more and len(frame):
                next_cursor = self._encode_cursor(
                    q,
                    selected_sort,
                    filters,
                    [
                        self._json_value(frame.iloc[-1][f"__entity_cursor_{index}"])
                        for index in range(len(order_spec))
                    ],
                )
                frame = frame.iloc[:safe_limit]
            visible_frame = frame.loc[:, list(select_columns)]
            rows = visible_frame.where(pd.notna(visible_frame), None).to_dict("records")
            if q.strip():
                self._decorate_exact(rows, search_columns, patterns, con, run_dir)
            return {
                "rows": rows,
                "total": total,
                "total_is_exact": cursor_values is None and count_total,
                "has_more": has_more,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "offset": 0 if cursor_values is not None else safe_offset,
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
    def _order_spec(
        q: str,
        sort: str,
        search_columns: tuple[str, ...],
        patterns: tuple[str, ...],
        available: set[str],
    ) -> list[tuple[str, str, list[str]]]:
        if (
            sort == "records" or not q.strip() and sort == "relevance"
        ) and "record_count" in available:
            return [
                ("COALESCE(CAST(record_count AS BIGINT), -1)", "DESC", []),
                ("CAST(nayose_id AS VARCHAR)", "ASC", []),
            ]
        if sort == "name" and "name_kanji" in available:
            return [
                ("COALESCE(lower(CAST(name_kanji AS VARCHAR)), '')", "ASC", []),
                ("CAST(nayose_id AS VARCHAR)", "ASC", []),
            ]
        if sort == "last_seen" and "last_seen" in available:
            return [
                ("COALESCE(CAST(last_seen AS VARCHAR), '')", "DESC", []),
                ("CAST(nayose_id AS VARCHAR)", "ASC", []),
            ]
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
            return [("CAST(nayose_id AS VARCHAR)", "ASC", [])]
        return [
            (f"CASE {' '.join(cases)} ELSE 99 END", "ASC", params),
            ("COALESCE(CAST(name_kanji AS VARCHAR), '')", "ASC", []),
            ("CAST(nayose_id AS VARCHAR)", "ASC", []),
        ]

    @classmethod
    def _cursor_clause(
        cls,
        order_spec: list[tuple[str, str, list[str]]],
        values: tuple[Any, ...],
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for index, (expression, direction, expression_params) in enumerate(order_spec):
            parts: list[str] = []
            for previous, (previous_expression, _previous_direction, previous_params) in enumerate(
                order_spec[:index]
            ):
                parts.append(f"{previous_expression} = ?")
                params.extend(previous_params)
                params.append(values[previous])
            operator = ">" if direction == "ASC" else "<"
            parts.append(f"{expression} {operator} ?")
            params.extend(expression_params)
            params.append(values[index])
            clauses.append("(" + " AND ".join(parts) + ")")
        return "(" + " OR ".join(clauses) + ")", params

    @staticmethod
    def _encode_cursor(q: str, sort: str, filters: SearchFilters | None, values: list[Any]) -> str:
        payload = {
            "version": 1,
            "query": q.strip(),
            "sort": sort,
            "filters": filters.as_dict() if filters else {},
            "values": values,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(
        cursor: str,
        q: str,
        sort: str,
        filters: SearchFilters | None,
        expected_values: int,
        first_is_int: bool,
    ) -> tuple[Any, ...]:
        if len(cursor) > 512:
            raise EntityCursorError("entity_cursor_too_long")
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        except (ValueError, json.JSONDecodeError):
            raise EntityCursorError("invalid_entity_cursor") from None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise EntityCursorError("invalid_entity_cursor")
        if (
            payload.get("query") != q.strip()
            or payload.get("sort") != sort
            or payload.get("filters") != (filters.as_dict() if filters else {})
        ):
            raise EntityCursorError("stale_entity_cursor")
        values = payload.get("values")
        if not isinstance(values, list) or len(values) != expected_values:
            raise EntityCursorError("invalid_entity_cursor")
        if not values or isinstance(values[0], bool):
            raise EntityCursorError("invalid_entity_cursor")
        if any(isinstance(value, bool) or not isinstance(value, (int, str)) for value in values):
            raise EntityCursorError("invalid_entity_cursor")
        if first_is_int and not isinstance(values[0], int):
            raise EntityCursorError("invalid_entity_cursor")
        return tuple(values)

    @staticmethod
    def _json_value(value: Any) -> Any:
        return value.item() if hasattr(value, "item") else value

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
