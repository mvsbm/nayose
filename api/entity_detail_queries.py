"""Entity detail and sample-record queries."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import HTTPException

from api.evidence import compare_fields, visible_record
from api.query_context import BundleReader


class EntityDetailQueries:
    def __init__(self, context: BundleReader):
        self.context = context

    def entity(
        self, run_id: str, entity_id: str, offset: int = 0, limit: int = 25
    ) -> dict[str, Any]:
        con, snapshot = self.context.open(run_id)
        run_dir = snapshot.run_dir
        try:
            escaped_id = entity_id.replace(chr(39), chr(39) + chr(39))
            ent = con.execute(
                "SELECT * FROM read_parquet(?) WHERE nayose_id = ? LIMIT 1",
                [str(run_dir / "canonical_entities.parquet"), escaped_id],
            ).fetchdf()
            if ent.empty:
                raise HTTPException(404, f"entity {entity_id!r} not found")
            reference = str(snapshot.data_dir / "reference_records.parquet")
            mapping = str(run_dir / "record_to_nayose.parquet")
            total = con.execute(
                "SELECT count(*) FROM read_parquet(?) r "
                "JOIN read_parquet(?) m USING(record_id) "
                "WHERE m.nayose_id = ?",
                [reference, mapping, entity_id],
            ).fetchone()[0]
            members_df = con.execute(
                "SELECT r.* FROM read_parquet(?) r JOIN read_parquet(?) m USING(record_id) "
                "WHERE m.nayose_id = ? ORDER BY r.record_id LIMIT ? OFFSET ?",
                [reference, mapping, entity_id, limit, offset],
            ).fetchdf()
            members = [visible_record(row) for row in members_df.to_dict("records")]
            canonical = ent.iloc[0].where(pd.notna(ent.iloc[0]), None).to_dict()
            return {
                "entity": canonical,
                "members": members,
                "total": total,
                "offset": offset,
                "limit": limit,
                "comparisons": {m["record_id"]: compare_fields(canonical, m) for m in members},
                "merge_evidence_available": False,
            }
        finally:
            con.close()

    def match_sample(self, run_id: str) -> dict[str, Any]:
        con, snapshot = self.context.open(run_id)
        run_dir = snapshot.run_dir
        try:
            frame = con.execute(
                "SELECT r.* FROM read_parquet(?) r JOIN read_parquet(?) m USING(record_id) "
                "ORDER BY r.record_id LIMIT 1",
                [
                    str(snapshot.data_dir / "reference_records.parquet"),
                    str(run_dir / "record_to_nayose.parquet"),
                ],
            ).fetchdf()
            return {"record": visible_record(frame.iloc[0].to_dict()) if len(frame) else {}}
        finally:
            con.close()
