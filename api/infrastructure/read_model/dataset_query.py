"""Typed SQL builder for bounded, immutable dataset pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DatasetTableDescriptor:
    id: str
    filename: str
    label: str
    description: str
    visible_columns: tuple[str, ...]
    cursor_column: str
    manifest_count_key: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetPageQuery:
    sql: str
    params: tuple[Any, ...]
    cursor_column: str


class DatasetQueryBuilder:
    """Builds one safe page query from validated table/filter inputs."""

    def __init__(
        self,
        source: str,
        columns: tuple[str, ...],
        cursor_column: str,
        search: str,
        filters: dict[str, str],
    ) -> None:
        if not columns or cursor_column not in columns:
            raise ValueError("dataset projection must include its cursor column")
        self.source = source
        self.columns = columns
        self.cursor_column = cursor_column
        self.search = search
        self.filters = filters

    @staticmethod
    def quote_identifier(value: str) -> str:
        escaped = value.replace('"', '""')
        return f'"{escaped}"'

    def _where(self, after: str | None = None) -> tuple[str, tuple[Any, ...]]:
        quote = self.quote_identifier
        clauses: list[str] = []
        params: list[Any] = []
        if self.search:
            clauses.append(
                "("
                + " OR ".join(
                    f"CAST({quote(column)} AS VARCHAR) ILIKE ?" for column in self.columns
                )
                + ")"
            )
            params.extend([f"%{self.search}%"] * len(self.columns))
        for column, value in self.filters.items():
            clauses.append(f"CAST({quote(column)} AS VARCHAR) ILIKE ?")
            params.append(f"%{value}%")
        if after is not None:
            clauses.append(f"CAST({quote(self.cursor_column)} AS VARCHAR) > ?")
            params.append(after)
        return (f" WHERE {' AND '.join(clauses)}" if clauses else ""), tuple(params)

    def page(self, limit: int, after: str | None = None, offset: int = 0) -> DatasetPageQuery:
        where, params = self._where(after)
        projection = ",".join(self.quote_identifier(column) for column in self.columns)
        order = self.quote_identifier(self.cursor_column)
        pagination = " LIMIT ?"
        page_params: tuple[Any, ...] = (*params, limit)
        if after is None:
            pagination += " OFFSET ?"
            page_params = (*page_params, max(offset, 0))
        return DatasetPageQuery(
            sql=f"SELECT {projection} FROM {self.source}{where} ORDER BY {order} ASC{pagination}",
            params=page_params,
            cursor_column=self.cursor_column,
        )

    def count(self) -> tuple[str, tuple[Any, ...]]:
        where, params = self._where()
        return f"SELECT count(*) FROM {self.source}{where}", params
