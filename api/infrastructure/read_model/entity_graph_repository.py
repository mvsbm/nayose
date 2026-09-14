"""Persistence boundary for Entity Explorer relationship snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from api.infrastructure.read_model.bundle_reader import BundleReader


class EntityNotFoundError(LookupError):
    """Raised when a requested entity is absent from the selected bundle."""


@dataclass(frozen=True)
class EntitySummary:
    entity_id: str
    name: str
    record_count: int
    party_type: str


@dataclass(frozen=True)
class SourceRecord:
    record_id: str
    name: str
    channel: str
    observed_at: str
    policy_id: str
    agency_id: str


@dataclass(frozen=True)
class PotentialMatch:
    other_entity_id: str
    other_entity_name: str
    left_record_id: str
    right_record_id: str
    pair_probability: float | None
    exact_count: int | None


@dataclass(frozen=True)
class EntityGraphSnapshot:
    entity: EntitySummary
    records: tuple[SourceRecord, ...]
    records_truncated: bool
    potential_matches: tuple[PotentialMatch, ...]
    potential_matches_available: bool
    potential_matches_truncated: bool


class EntityGraphRepository(Protocol):
    def snapshot(
        self,
        run_id: str,
        entity_id: str,
        record_limit: int,
        potential_limit: int,
        include_potential: bool,
    ) -> EntityGraphSnapshot: ...


class DuckDbEntityGraphRepository:
    """Load only visible source fields and model-candidate evidence."""

    def __init__(self, context: BundleReader):
        self.context = context

    def snapshot(
        self,
        run_id: str,
        entity_id: str,
        record_limit: int,
        potential_limit: int,
        include_potential: bool,
    ) -> EntityGraphSnapshot:
        con, snapshot = self.context.open(run_id)
        run_dir = snapshot.run_dir
        try:
            entity = self._entity(con, run_dir, entity_id)
            records = self._records(con, snapshot.data_dir, run_dir, entity_id, record_limit)
            available = self._potential_available(con, run_dir)
            potential_matches = (
                self._potential_matches(con, run_dir, entity_id, potential_limit)
                if include_potential and available
                else []
            )
            return EntityGraphSnapshot(
                entity=entity,
                records=tuple(records[:record_limit]),
                records_truncated=len(records) > record_limit,
                potential_matches=tuple(potential_matches[:potential_limit]),
                potential_matches_available=available,
                potential_matches_truncated=len(potential_matches) > potential_limit,
            )
        finally:
            con.close()

    @staticmethod
    def _entity(con: Any, run_dir: Path, entity_id: str) -> EntitySummary:
        row = con.execute(
            "SELECT nayose_id, name_kanji, record_count, party_type "
            "FROM read_parquet(?) WHERE nayose_id = ? LIMIT 1",
            [str(run_dir / "canonical_entities.parquet"), entity_id],
        ).fetchone()
        if row is None:
            raise EntityNotFoundError(f"entity {entity_id!r} not found")
        return EntitySummary(
            entity_id=str(row[0]),
            name="" if row[1] is None else str(row[1]),
            record_count=int(row[2] or 0),
            party_type="" if row[3] is None else str(row[3]),
        )

    def _records(
        self, con: Any, data_dir: Path, run_dir: Path, entity_id: str, limit: int
    ) -> list[SourceRecord]:
        rows = con.execute(
            "SELECT r.record_id, r.name_kanji, r.channel, r.observed_at, "
            "r.policy_id, r.agency_id FROM read_parquet(?) r "
            "JOIN read_parquet(?) m USING(record_id) "
            "WHERE m.nayose_id = ? ORDER BY r.record_id LIMIT ?",
            [
                str(data_dir / "reference_records.parquet"),
                str(run_dir / "record_to_nayose.parquet"),
                entity_id,
                limit + 1,
            ],
        ).fetchall()
        return [
            SourceRecord(
                record_id=self._text(row[0]),
                name=self._text(row[1]),
                channel=self._text(row[2]),
                observed_at=self._text(row[3]),
                policy_id=self._text(row[4]),
                agency_id=self._text(row[5]),
            )
            for row in rows
        ]

    @staticmethod
    def _potential_available(con: Any, run_dir: Path) -> bool:
        path = run_dir / "review_queue.parquet"
        if not path.exists():
            return False
        columns = {
            str(row[0])
            for row in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
        }
        return "pair_probability" in columns

    def _potential_matches(
        self, con: Any, run_dir: Path, entity_id: str, limit: int
    ) -> list[PotentialMatch]:
        queue = run_dir / "review_queue.parquet"
        mapping = run_dir / "record_to_nayose.parquet"
        columns = {
            str(row[0])
            for row in con.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(queue)]
            ).fetchall()
        }
        exact_count = "q.exact_count" if "exact_count" in columns else "NULL"
        rows = con.execute(
            "SELECT q.left_record_id, q.right_record_id, q.pair_probability, "
            f"{exact_count}, left_map.nayose_id, right_map.nayose_id, "
            "CASE WHEN left_map.nayose_id = ? THEN right_map.nayose_id "
            "ELSE left_map.nayose_id END AS other_entity_id "
            "FROM read_parquet(?) q "
            "JOIN read_parquet(?) left_map ON q.left_record_id = left_map.record_id "
            "JOIN read_parquet(?) right_map ON q.right_record_id = right_map.record_id "
            "WHERE (left_map.nayose_id = ? OR right_map.nayose_id = ?) "
            "AND left_map.nayose_id != right_map.nayose_id "
            "ORDER BY q.pair_probability DESC NULLS LAST LIMIT ?",
            [entity_id, str(queue), str(mapping), str(mapping), entity_id, entity_id, limit + 1],
        ).fetchall()
        labels = self._entity_labels(con, run_dir, {str(row[6]) for row in rows if row[6]})
        return [
            PotentialMatch(
                other_entity_id=str(row[6]),
                other_entity_name=labels.get(str(row[6]), ""),
                left_record_id=self._text(row[0]),
                right_record_id=self._text(row[1]),
                pair_probability=self._float(row[2]),
                exact_count=self._integer(row[3]),
            )
            for row in rows
            if row[6]
        ]

    @staticmethod
    def _entity_labels(con: Any, run_dir: Path, entity_ids: set[str]) -> dict[str, str]:
        ids = sorted(entity_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = con.execute(
            f"SELECT nayose_id, name_kanji FROM read_parquet(?) "
            f"WHERE nayose_id IN ({placeholders})",
            [str(run_dir / "canonical_entities.parquet"), *ids],
        ).fetchall()
        return {str(row[0]): "" if row[1] is None else str(row[1]) for row in rows}

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _float(value: Any) -> float | None:
        return None if value is None else float(value)

    @staticmethod
    def _integer(value: Any) -> int | None:
        return None if value is None else int(value)
