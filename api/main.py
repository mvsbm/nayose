"""Nayose web dashboard API — typed domain endpoints (Phase 2).

Architecture: Repository (runs) -> QueryService (DuckDB, server-owned SQL, cache)
-> RecommendService (fingerprint-guarded engine) -> thin FastAPI facade.
Raw SQL is NOT exposed to clients (only via NAYOSE_DEBUG_SQL=1 for debugging);
clients call typed operations (entities/review/metrics/confusion/entity/compare).
Local-only: bound to 127.0.0.1; row caps + tiny in-memory cache + endpoint stats.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import duckdb
import pandas as pd
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from api.admin import AdminService
from api.contracts import (
    AdminJob,
    EntityDetail,
    GenerationPreviewIn,
    MatchJob,
    MatchResult,
    SampleRecord,
)
from api.evidence import NORMALIZED_FIELDS, VISIBLE_FIELDS, compare_fields, visible_record
from api.matching import RecommendationService as RecommendService
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
from api.release import release_status, safe_run_id
from api.search import _prefecture
from api.search_service import (
    ExactRetriever,
    FuzzyRetriever,
    SearchFilters,
    SearchService,
    SemanticRetriever,
    SuggestionRetriever,
)

MODELS_DIR = Path(os.getenv("NAYOSE_MODELS_DIR", "models"))
DATA_DIR = Path(os.getenv("NAYOSE_DATA_DIR", "data/current"))
DATA_ROOT = Path(os.getenv("NAYOSE_DATA_ROOT", "data"))
MANUAL_DIR = Path(os.getenv("NAYOSE_MANUAL_DIR", "data/manual"))
FRONTEND_DIR = Path(os.path.dirname(__file__)).parent / "frontend"
DEBUG_SQL = os.getenv("NAYOSE_DEBUG_SQL", "0") == "1"
MAX_ROWS = 10000
MAX_CACHE = 96
FUZZY_SEARCH_CAP = 500_000
ENTITY_COLUMNS = (
    "nayose_id",
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "party_type",
    "phone",
    "postal_code",
    "address",
    "email",
    "record_count",
    "policy_count",
    "agency_count",
    "first_seen",
    "last_seen",
)
ENTITY_SEARCH_COLUMNS = (
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "party_type",
    "phone",
    "postal_code",
    "address",
    "email",
    "nayose_id",
)
SEARCH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "or",
        "the",
        "with",
        "customer",
        "customers",
        "顧客",
        "の",
        "に",
        "で",
        "を",
    }
)
# Hybrid search: exact LIKE hits are never buried under embedding noise.
EXACT_SEARCH_CAP = 8
# Opt-in bearer auth: when set, every /api/* call must send
# `Authorization: Bearer <token>` (health probe is exempt). Unset = localhost dev.
# Read lazily so env changes are honored without restart.
# Opt-in model warmup: preload the recommend engine (embeddings + ANN) during
# startup so the first real request is not a cold start.
WARM_EMBED = os.getenv("NAYOSE_WARM_EMBED", "0") == "1"
DEFAULT_REVIEWER = os.getenv("NAYOSE_REVIEWER_DEFAULT", "web")

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "full_dataset_summary": ["metric", "value"],
    "full_dataset_metrics": ["section", "metric", "value"],
    "pair_confusion_test": ["metric", "value"],
    "query_confusion": ["scenario", "action", "expected_no", "expected_yes"],
    "review_queue": ["left_record_id", "right_record_id"],
    "canonical_entities": ["nayose_id", "name_kanji", "name_kana", "record_count"],
    "record_to_nayose": ["record_id", "nayose_id"],
}


class RunRepository:
    def __init__(self, root: Path):
        self.root = root

    def list(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        current = (self.root / "current").resolve() if (self.root / "current").exists() else None
        runs_root = self.root / "runs"
        if runs_root.is_dir():
            for run in sorted(runs_root.iterdir(), key=lambda p: p.name, reverse=True):
                if not run.is_dir():
                    continue
                entry: dict[str, Any] = {
                    "id": run.name,
                    "path": str(run),
                    "current": bool(current and run.resolve() == current),
                }
                manifest = run / "run_manifest.json"
                if manifest.exists():
                    meta = json.loads(manifest.read_text(encoding="utf-8"))
                    entry["created"] = meta.get("created_at_utc", "")
                    entry["fingerprint"] = str(meta.get("data_fingerprint", ""))[:12]
                runs.append(entry)
        return runs

    def resolve(self, run_id: str) -> Path:
        if not run_id:
            current = (
                (self.root / "current").resolve() if (self.root / "current").exists() else None
            )
            if current is not None and current.is_dir():
                return current
            raise HTTPException(404, "no current run (train first: scripts/train.py)")
        candidate = (self.root / "runs" / run_id).resolve()
        base = self.root.resolve()
        if base not in candidate.parents or not candidate.is_dir():
            raise HTTPException(404, f"unknown run {run_id!r}")
        return candidate


class Stats:
    def __init__(self):
        self.counts: dict[str, list[float]] = {}

    def record(self, name: str, seconds: float) -> None:
        row = self.counts.setdefault(name, [0.0, 0.0])
        row[0] += 1
        row[1] += seconds

    def reset(self) -> None:
        """Test isolation: endpoint stats are module-global; each test must
        start from a clean slate (order/parallel independent)."""
        self.counts.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {"endpoint": k, "hits": int(v[0]), "total_ms": round(v[1] * 1000, 1)}
            for k, v in sorted(self.counts.items())
        ]


STATS = Stats()


class QueryService:
    """Server-owned SQL over the run bundle + data parquet (fastapi facade calls
    typed operations only). A tiny LRU cache avoids re-scanning hot tables."""

    def __init__(self, repo: RunRepository, data_dir: Path):
        self.repo = repo
        self.data_dir = data_dir
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = Lock()

    def _cached(self, key: str, fn) -> Any:
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < 300:
                return hit[1]
            value = fn()
            self._cache[key] = (time.time(), value)
            if len(self._cache) > MAX_CACHE:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]
            return value

    def _open(self, run_id: str):
        run_dir = self.repo.resolve(run_id)
        return duckdb.connect(), run_dir

    def _query_tokens(self, q: str) -> tuple[str, ...]:
        tokens = tuple(token for token in re.split(r"\s+", q.strip()) if token)
        useful = tuple(token for token in tokens if token.casefold() not in SEARCH_STOPWORDS)
        return useful or tokens

    def entities(
        self,
        run_id: str,
        q: str,
        limit: int = 50,
        filters: SearchFilters | None = None,
    ) -> list[dict[str, Any]]:
        con, run_dir = self._open(run_id)
        try:
            source = f"read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR)"
            available = {
                str(column[0])
                for column in con.execute(f"SELECT * FROM {source} LIMIT 0").description
            }
            search_cols = tuple(column for column in ENTITY_SEARCH_COLUMNS if column in available)
            select_cols = tuple(column for column in ENTITY_COLUMNS if column in available)
            where_conditions: list[str] = []
            matched: tuple[str, ...] = ()
            patterns: tuple[str, ...] = ()
            if q:
                patterns = tuple(
                    r"\s*".join(re.escape(char) for char in token)
                    for token in self._query_tokens(q)
                )
                conditions: list[str] = []
                safe_patterns = tuple(pattern.replace("'", "''") for pattern in patterns)
                ref = str(self.data_dir / "reference_records.parquet")
                mapping = str(run_dir / "record_to_nayose.parquet")
                for safe_pattern in safe_patterns:
                    field_condition = " OR ".join(
                        f"regexp_matches(CAST({col} AS VARCHAR), '(?i).*{safe_pattern}.*')"
                        for col in search_cols
                    )
                    field_condition += (
                        f" OR nayose_id IN (SELECT DISTINCT m.nayose_id FROM read_parquet('{ref}') r "
                        f"JOIN read_parquet('{mapping}') m USING(record_id) WHERE "
                        f"regexp_matches(CAST(r.policy_id AS VARCHAR), '(?i).*{safe_pattern}.*') OR "
                        f"regexp_matches(CAST(r.agency_id AS VARCHAR), '(?i).*{safe_pattern}.*'))"
                    )
                    conditions.append(f"({field_condition})")
                where_conditions.extend(conditions)
                matched = search_cols
            if filters and filters.party_type and "party_type" in available:
                party_type = filters.party_type.replace("'", "''")
                where_conditions.append(
                    f"lower(CAST(party_type AS VARCHAR)) = lower('{party_type}')"
                )
            if filters and filters.min_record_count is not None and "record_count" in available:
                where_conditions.append(f"record_count >= {max(0, int(filters.min_record_count))}")
            where = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
            if q:
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
                priority_cases = []
                for rank, column in enumerate(priority_columns):
                    if column not in search_cols:
                        continue
                    condition = " OR ".join(
                        f"regexp_matches(CAST({column} AS VARCHAR), '(?i).*{pattern}.*')"
                        for pattern in safe_patterns
                    )
                    priority_cases.append(f"WHEN ({condition}) THEN {rank}")
                order = f"ORDER BY CASE {' '.join(priority_cases)} ELSE 99 END, name_kanji"
            else:
                order = "ORDER BY record_count DESC"
            df = con.execute(
                f"SELECT {','.join(select_cols)} FROM {source} {where} {order} LIMIT {int(limit)}"
            ).fetchdf()
            rows = df.where(df.notna(), None).to_dict("records")
            if not q:
                return rows
            twin = self._twin_counts(con, run_dir)
            for row in rows:
                row["kind"] = "exact"
                row["signals"] = [
                    col
                    for col in matched
                    if any(re.search(pattern, str(row.get(col, ""))) for pattern in patterns)
                ]
                row["twin_count"] = max(
                    0, twin.get(str(row.get("name_kana", "")).replace(" ", ""), 1) - 1
                )
                row["prefecture"] = _prefecture(str(row.get("address", "")))
            return rows
        finally:
            con.close()

    def _twin_counts(self, con, run_dir: Path) -> dict[str, int]:
        """kana reading → distinct kanji spellings in the canonical corpus."""
        df = con.execute(
            f"SELECT name_kana, count(distinct name_kanji) AS n FROM read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR) "
            f"WHERE name_kana != '' GROUP BY 1"
        ).fetchall()
        return {str(k).replace(" ", ""): int(n) for k, n in df}

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

        return self._cached(key, load)

    def nearest_entities(self, run_id: str, q: str, limit: int = 8) -> list[dict[str, Any]]:
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
        run_dir = self.repo.resolve(run_id)
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
            choice for choice in self._nearest_choices(run_dir, fields) if len(choice["text"]) >= 2
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

    def entity(
        self, run_id: str, entity_id: str, offset: int = 0, limit: int = 25
    ) -> dict[str, Any]:
        con, run_dir = self._open(run_id)
        try:
            ent = con.execute(
                f"SELECT * FROM read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR) WHERE nayose_id = '{entity_id.replace(chr(39), chr(39) + chr(39))}' LIMIT 1"
            ).fetchdf()
            if ent.empty:
                raise HTTPException(404, f"entity {entity_id!r} not found")
            reference = str(self.data_dir / "reference_records.parquet")
            mapping = str(run_dir / "record_to_nayose.parquet")
            total = con.execute(
                "SELECT count(*) FROM read_parquet(?) r JOIN read_parquet(?) m USING(record_id) WHERE m.nayose_id = ?",
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
        con, run_dir = self._open(run_id)
        try:
            frame = con.execute(
                "SELECT r.* FROM read_parquet(?) r JOIN read_parquet(?) m USING(record_id) "
                "ORDER BY r.record_id LIMIT 1",
                [
                    str(self.data_dir / "reference_records.parquet"),
                    str(run_dir / "record_to_nayose.parquet"),
                ],
            ).fetchdf()
            return {"record": visible_record(frame.iloc[0].to_dict()) if len(frame) else {}}
        finally:
            con.close()

    def review(
        self, run_id: str, min_p: float, offset: int, limit: int, nayose: str = ""
    ) -> dict[str, Any]:
        con, run_dir = self._open(run_id)
        try:
            scope = ""
            if nayose:
                mapping = str(run_dir / "record_to_nayose.parquet")
                esc_n = nayose.replace("'", "''")
                scope = (
                    f" AND (left_record_id IN (SELECT record_id FROM read_parquet('{mapping}') WHERE nayose_id='{esc_n}') "
                    f"OR right_record_id IN (SELECT record_id FROM read_parquet('{mapping}') WHERE nayose_id='{esc_n}'))"
                )
            total = int(
                con.execute(
                    f"SELECT count(*) AS c FROM read_parquet('{run_dir}/review_queue.parquet'::VARCHAR) WHERE pair_probability >= {float(min_p)}{scope}"
                ).fetchone()[0]
            )
            rows = (
                con.execute(
                    f"SELECT left_record_id,right_record_id,pair_probability,exact_count FROM read_parquet('{run_dir}/review_queue.parquet'::VARCHAR) "
                    f"WHERE pair_probability >= {float(min_p)}{scope} ORDER BY pair_probability DESC LIMIT {int(limit)} OFFSET {int(offset)}"
                )
                .fetchdf()
                .to_dict("records")
            )
            # score-band histogram over the WHOLE queue: shows how much work is
            # hidden below the current filter and where the risk concentrates
            buckets = [
                [float(lo), int(count)]
                for lo, count in con.execute(
                    f"SELECT floor(pair_probability * 20) / 20 AS lo, count(*) AS c "
                    f"FROM read_parquet('{run_dir}/review_queue.parquet'::VARCHAR) GROUP BY 1 ORDER BY 1"
                ).fetchall()
            ]
            pair_auto = None
            manifest = run_dir / "run_manifest.json"
            if manifest.exists():
                thresholds = json.loads(manifest.read_text(encoding="utf-8")).get("thresholds", {})
                pair_auto = thresholds.get("pair_auto")
            return {
                "total": total,
                "rows": rows,
                "histogram": buckets,
                "pair_auto_threshold": round(float(pair_auto), 4) if pair_auto else None,
            }
        finally:
            con.close()

    def metrics(self, run_id: str) -> dict[str, Any]:
        con, run_dir = self._open(run_id)

        def fetch(stem: str, cols: str = "metric,value"):
            return con.execute(
                f"SELECT {cols} FROM read_parquet('{run_dir}/{stem}.parquet'::VARCHAR)"
            ).fetchdf()

        try:
            summary = {
                r["metric"]: float(r["value"]) for _, r in fetch("full_dataset_summary").iterrows()
            }
            metrics = {
                r["metric"]: float(r["value"])
                for _, r in fetch("full_dataset_metrics", "section,metric,value").iterrows()
                if r["section"] == "incoming_query"
            }
            p, r = summary.get("b3_precision", 0), summary.get("b3_recall", 0)
            b3 = 2 * p * r / (p + r) if p + r > 0 else 0
            return {
                "kpis": [
                    ["candidate recall", summary.get("candidate_pair_recall", 0)],
                    ["B3 F1", b3],
                    ["top-1 identity", metrics.get("top1_identity_accuracy_existing", 0)],
                    ["MRR", metrics.get("mean_reciprocal_rank_existing", 0)],
                    ["review", metrics.get("review_rate", 0)],
                    ["no-match", metrics.get("no_match_rate", 0)],
                ],
                "extras": {
                    "entities": summary.get("predicted_entities", 0),
                    "edges": summary.get("candidate_edges", 0),
                    "singleton": summary.get("singleton_entity_rate", 0),
                },
            }
        finally:
            con.close()

    def confusion(self, run_id: str) -> dict[str, Any]:
        con, run_dir = self._open(run_id)

        def fetch(stem: str, extra: str = ""):
            return con.execute(
                f"SELECT * FROM read_parquet('{run_dir}/{stem}.parquet'::VARCHAR) {extra}"
            ).fetchdf()

        try:
            pairs = {
                r["metric"]: float(r["value"]) for _, r in fetch("pair_confusion_test").iterrows()
            }
            qc = fetch("query_confusion")
            scenarios: list[dict[str, Any]] = []
            for scenario, g in qc.groupby("scenario"):
                scenarios.append(
                    {
                        "scenario": str(scenario),
                        "actions": {str(r.action): int(r.expected_yes) for _, r in g.iterrows()},
                    }
                )
            decisions: dict[str, list[dict[str, Any]]] = {}
            qr = fetch("query_results", "WHERE scenario IS NOT NULL LIMIT 20000")
            for scenario, g in qr.groupby("scenario"):
                decisions[str(scenario)] = g[
                    [
                        "query_id",
                        "action",
                        "top_nayose_id",
                        "top_probability",
                        "identity_top1_correct",
                    ]
                ].to_dict("records")
            return {"pairs": pairs, "scenarios": scenarios, "decisions": decisions}
        finally:
            con.close()

    def compare(self, run_id: str, left: str, right: str) -> list[dict[str, Any]]:
        con, run_dir = self._open(run_id)
        try:
            df = con.execute(
                f"SELECT * FROM read_parquet('{self.data_dir}/reference_records.parquet'::VARCHAR) WHERE record_id IN ('{left}','{right}')"
            ).fetchdf()
            return df.to_dict("records")
        finally:
            con.close()

    # --- kept ONLY for debugging (NAYOSE_DEBUG_SQL=1) ---
    def run_sql(self, run_id: str, sql: str) -> dict[str, Any]:
        con, run_dir = self._open(run_id)
        try:
            resolved = (
                sql.replace("'bundle/", f"'{run_dir}/")
                .replace('"bundle/', f'"{run_dir}/')
                .replace("'data/", f"'{self.data_dir}/")
                .replace('"data/', f'"{self.data_dir}/')
            )
            df = con.execute(resolved).fetchdf().head(MAX_ROWS)
            return {
                "columns": [str(c) for c in df.columns],
                "rows": df.where(df.notna(), None).astype(object).values.tolist(),
                "row_count": len(df),
            }
        finally:
            con.close()


class DatasetService:
    TABLES = {
        "reference": ("reference_records.parquet", "Reference records"),
        "queries": ("incoming_queries.parquet", "Incoming queries"),
        "agencies": ("agencies.parquet", "Agencies"),
    }
    VISIBLE_COLUMNS = {
        "reference": [*VISIBLE_FIELDS],
        "queries": [
            "query_id",
            *[field for field in VISIBLE_FIELDS if field != "record_id"],
        ],
        "agencies": [
            "agency_id",
            "channel",
            "missing_factor",
            "ocr_factor",
            "format_factor",
            "shared_phone",
            "shared_email",
        ],
    }

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def _generation(self, generation_id: str) -> Path:
        base = (self.root / "generations").resolve()
        candidate = (base / generation_id).resolve()
        if base not in candidate.parents or not candidate.is_dir():
            raise HTTPException(404, f"unknown generation {generation_id!r}")
        return candidate

    def _manifest(self, generation: Path) -> dict[str, Any]:
        path = generation / "generation_manifest.json"
        if not path.exists():
            raise HTTPException(404, "generation_manifest_missing")
        return json.loads(path.read_text(encoding="utf-8"))

    def generations(self) -> list[dict[str, Any]]:
        generations = self.root / "generations"
        current = (self.root / "current").resolve() if (self.root / "current").exists() else None
        if not generations.is_dir():
            return []
        rows = []
        for generation in sorted(generations.iterdir(), key=lambda path: path.name, reverse=True):
            if not generation.is_dir() or not generation.name.startswith("gen_"):
                continue
            manifest = self._manifest(generation)
            config = manifest.get("config", {})
            rows.append(
                {
                    "id": generation.name,
                    "current": bool(current and generation.resolve() == current),
                    "created_at": manifest.get("created_at_utc", ""),
                    "fingerprint": str(manifest.get("data_fingerprint", ""))[:12],
                    "snapshot_date": config.get("snapshot_date", ""),
                    "query_date": config.get("query_date", ""),
                    "counts": manifest.get("counts", {}),
                }
            )
        return rows

    def _columns(self, generation: Path, table: str) -> list[str]:
        filename, _ = self.TABLES[table]
        path = generation / filename
        if not path.exists():
            raise HTTPException(404, f"dataset_table_missing:{table}")
        con = duckdb.connect()
        try:
            available = [
                str(row[0])
                for row in con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{path}'::VARCHAR)"
                ).fetchall()
            ]
        finally:
            con.close()
        allowed = self.VISIBLE_COLUMNS[table]
        return [column for column in allowed if column in available]

    def summary(self, generation_id: str) -> dict[str, Any]:
        generation = self._generation(generation_id)
        manifest = self._manifest(generation)
        current = (self.root / "current").resolve() if (self.root / "current").exists() else None
        tables = []
        con = duckdb.connect()
        try:
            for table, (filename, label) in self.TABLES.items():
                path = generation / filename
                if not path.exists():
                    continue
                count_row = con.execute(
                    f"SELECT count(*) FROM read_parquet('{path}'::VARCHAR)"
                ).fetchone()
                rows = int(count_row[0]) if count_row else 0
                tables.append(
                    {
                        "id": table,
                        "label": label,
                        "rows": rows,
                        "columns": self._columns(generation, table),
                    }
                )
        finally:
            con.close()
        return {
            "id": generation.name,
            "current": bool(current and generation.resolve() == current),
            "created_at": manifest.get("created_at_utc", ""),
            "fingerprint": str(manifest.get("data_fingerprint", ""))[:12],
            "config": manifest.get("config", {}),
            "counts": manifest.get("counts", {}),
            "tables": tables,
        }

    def records(
        self, generation_id: str, table: str, query: str, offset: int, limit: int
    ) -> dict[str, Any]:
        if table not in self.TABLES:
            raise HTTPException(422, f"unknown dataset table {table!r}")
        generation = self._generation(generation_id)
        filename, _ = self.TABLES[table]
        path = generation / filename
        columns = self._columns(generation, table)
        quoted = ",".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns)
        source = f"read_parquet('{path}'::VARCHAR)"
        where = ""
        params: list[Any] = []
        if query.strip():
            where = " WHERE " + " OR ".join(
                f'CAST("{column.replace(chr(34), chr(34) * 2)}" AS VARCHAR) ILIKE ?'
                for column in columns
            )
            params.extend([f"%{query.strip()}%"] * len(columns))
        con = duckdb.connect()
        try:
            count_row = con.execute(f"SELECT count(*) FROM {source}{where}", params).fetchone()
            total = int(count_row[0]) if count_row else 0
            rows = con.execute(
                f"SELECT {quoted} FROM {source}{where} LIMIT ? OFFSET ?",
                [*params, min(limit, 100), max(offset, 0)],
            ).fetchdf()
        finally:
            con.close()
        return {
            "table": table,
            "columns": columns,
            "rows": rows.where(pd.notna(rows), None).to_dict("records"),
            "total": total,
            "offset": max(offset, 0),
            "limit": min(limit, 100),
        }


class DecisionsStore:
    """Operational review state bound to the immutable model manifest."""

    def __init__(self):
        self._lock = Lock()

    def _path(self, run_id: str) -> Path:
        root = (MODELS_DIR / "decisions").resolve()
        root.mkdir(parents=True, exist_ok=True)
        current = (MODELS_DIR / "current").resolve() if (MODELS_DIR / "current").exists() else None
        value = run_id or (current.name if current else "")
        safe = safe_run_id(value)
        path = (root / f"{safe}.json").resolve()
        if root not in path.parents:
            raise ValueError("invalid decision path")
        return path

    def _manifest(self, run_id: str) -> tuple[Path, dict[str, Any]]:
        safe = safe_run_id(run_id)
        candidates = [MODELS_DIR / "runs" / safe, MODELS_DIR / safe]
        current = MODELS_DIR / "current"
        if current.exists() and current.resolve().name == safe:
            candidates.append(current.resolve())
        for run_dir in candidates:
            path = run_dir / "run_manifest.json"
            if path.exists():
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if not manifest.get("data_fingerprint"):
                    break
                return path, manifest
        raise ValueError("run manifest with data fingerprint is required")

    def _binding_path(self, path: Path) -> Path:
        return path.with_suffix(".meta.json")

    def load(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            path = self._path(run_id)
            if not path.exists():
                return []
            manifest_path, manifest = self._manifest(run_id or path.stem)
            binding_path = self._binding_path(path)
            if not binding_path.exists():
                raise ValueError("decision file is not bound to a model manifest")
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if binding.get("data_fingerprint") != manifest.get("data_fingerprint"):
                raise ValueError("decision data fingerprint does not match the model")
            if binding.get("manifest_sha256") != manifest_sha256:
                raise ValueError("decision model manifest has changed")
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, run_id: str, items: list[dict[str, Any]]) -> None:
        with self._lock:
            path = self._path(run_id)
            manifest_path, manifest = self._manifest(run_id or path.stem)
            binding = {
                "run_id": path.stem,
                "data_fingerprint": manifest["data_fingerprint"],
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            }
            tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
            meta = self._binding_path(path)
            meta_tmp = meta.with_name(f".{meta.name}.tmp.{os.getpid()}")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            meta_tmp.write_text(json.dumps(binding, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            os.replace(meta_tmp, meta)


RUNS = RunRepository(MODELS_DIR)
QUERIES = QueryService(RUNS, DATA_DIR)
RECOMMEND = RecommendService(RUNS, DATA_DIR)


def build_search_service(queries: QueryService, recommend: RecommendService) -> SearchService:
    """Factory Method for the server composition root."""
    return SearchService(
        exact=ExactRetriever(queries),
        fuzzy=FuzzyRetriever(queries),
        semantic=SemanticRetriever(recommend),
        suggest=SuggestionRetriever(queries),
    )


SEARCH_SERVICE = build_search_service(QUERIES, RECOMMEND)


def current_search_service() -> SearchService:
    """Keep the composition root aligned with test or runtime repository swaps."""
    global SEARCH_SERVICE
    if (
        getattr(SEARCH_SERVICE.exact, "service", None) is not QUERIES
        or getattr(SEARCH_SERVICE.semantic, "service", None) is not RECOMMEND
    ):
        SEARCH_SERVICE = build_search_service(QUERIES, RECOMMEND)
    return SEARCH_SERVICE


DECISIONS = DecisionsStore()
ADMIN = AdminService(DATA_ROOT, MODELS_DIR, MANUAL_DIR)
ADMIN._engine_reset = lambda: RECOMMEND.reset_engine()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup hooks: optional recommend-engine warmup (readiness = warm)."""
    if WARM_EMBED:
        runs = RUNS.list()
        current = next((r for r in runs if r.get("current")), None)
        if current is not None:
            t0 = time.perf_counter()
            try:
                RECOMMEND.recommend(current["id"], {"name_kanji": "ウォームアップ"})
                print(
                    f"warmup: engine ready for {current['id']} in {time.perf_counter() - t0:.1f}s"
                )
            except Exception as exc:  # serve degraded; recommend endpoint reports 409
                print(f"warmup failed ({type(exc).__name__}): {exc}")
    yield


app = FastAPI(title="Nayose — 照合監査ダッシュボード", version="3.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    """Apply the optional deployment-wide bearer token.

    Admin and data-entry are not a separate trust tier; they use this same
    transport-level policy when a deployment configures ``NAYOSE_API_TOKEN``.
    """
    token = os.getenv("NAYOSE_API_TOKEN", "")
    if (
        token
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/health"
        and request.headers.get("authorization", "") != f"Bearer {token}"
    ):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


def timed(name: str):
    def deco(fn):
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                STATS.record(name, time.perf_counter() - t0)

        return wrapper

    return deco


def op(name: str, fn):
    """Run + record timing/hits; keeps endpoint signatures visible to FastAPI."""
    t0 = time.perf_counter()
    try:
        return fn()
    finally:
        STATS.record(name, time.perf_counter() - t0)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    return RUNS.list()


@app.get("/api/normalize")
def normalize_preview(text: str = "") -> dict[str, str]:
    normalized = normalize_query_fields(text)
    return {
        field: normalized[field]
        for field in (
            "name_kanji",
            "name_kana",
            "phone",
            "postal_code",
            "address",
            "email",
            "date_birth",
        )
    }


@app.get("/api/entities")
def entities(
    q: str = "",
    run_id: str = "",
    limit: int = 50,
    semantic: bool = True,
    party_type: str = "",
    min_record_count: int = 0,
) -> dict[str, Any]:
    """Search through the server-owned composable retrieval pipeline."""

    def run() -> dict[str, Any]:
        filters = SearchFilters(
            party_type=party_type.strip() or None,
            min_record_count=min_record_count if min_record_count > 0 else None,
        )
        return current_search_service().search(
            run_id,
            q,
            min(limit, 200),
            filters=filters,
            semantic=semantic,
        )

    return op("entities", run)


@app.get("/api/entity/{entity_id}", response_model=EntityDetail)
def entity(
    entity_id: str,
    run_id: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    return op("entity", lambda: QUERIES.entity(run_id, entity_id, offset, limit))


@app.get("/api/match-sample", response_model=SampleRecord)
def match_sample(run_id: str = "") -> dict[str, Any]:
    return op("match_sample", lambda: QUERIES.match_sample(run_id))


@app.get("/api/review")
def review(
    run_id: str = "", min_p: float = 0.0, offset: int = 0, limit: int = 200, nayose: str = ""
) -> dict[str, Any]:
    return op(
        "review",
        lambda: QUERIES.review(run_id, min_p, offset, min(limit, 500), nayose),
    )


@app.get("/api/metrics")
def metrics(run_id: str = "") -> dict[str, Any]:
    return op("metrics", lambda: QUERIES.metrics(run_id))


@app.get("/api/confusion")
def confusion(run_id: str = "") -> dict[str, Any]:
    return op("confusion", lambda: QUERIES.confusion(run_id))


@app.get("/api/compare")
def compare(run_id: str = "", left: str = "", right: str = "") -> dict[str, Any]:
    return op("compare", lambda: {"rows": QUERIES.compare(run_id, left, right)})


@app.get("/api/stats")
def stats() -> list[dict[str, Any]]:
    return STATS.snapshot()


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Readiness/hygiene probe (auth-exempt by design for liveness checks)."""
    runs = RUNS.list()
    current = next((r for r in runs if r.get("current")), runs[0] if runs else None)
    release = release_status(DATA_ROOT, MODELS_DIR)
    return {
        "status": "ok"
        if current is not None and release.compatible
        else "degraded"
        if current
        else "no_run",
        "run": current["id"] if current else None,
        "fingerprint": release.model_fingerprint[:12] if release.model_fingerprint else "",
        "data_fingerprint": release.data_fingerprint,
        "model_fingerprint": release.model_fingerprint,
        "fingerprint_match": release.compatible,
        "release": release.as_dict(),
        "run_count": len(runs),
        "data_dir": str(DATA_DIR),
        "models_dir": str(MODELS_DIR),
        "now_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 (this runtime lacks datetime.UTC)
        "stats": STATS.snapshot(),
        "engine_warm": RECOMMEND.engine_ready(current["id"] if current else "")
        if release.compatible
        else False,
    }


class AdminJobIn(BaseModel):
    action: str = Field(..., max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)


class AdminActionIn(BaseModel):
    action: str = Field(..., max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)


class RegisterIn(BaseModel):
    record: dict[str, Any]


class ImportIn(BaseModel):
    rows: list[dict[str, Any]]
    dry_run: bool = False


class UndoIn(BaseModel):
    record_ids: list[str]


@app.get("/api/admin/capable")
def admin_capable() -> dict[str, bool]:
    """Expose the always-available local-first admin surface."""
    return {"capable": ADMIN.capable()}


@app.get("/api/admin/state")
def admin_state() -> dict[str, Any]:
    return op(
        "admin_state",
        lambda: ADMIN.state(RECOMMEND.engine_ready),
    )


@app.get("/api/admin/generation-config")
def admin_generation_config() -> dict[str, Any]:
    return op("admin_generation_config", ADMIN.generation_config)


@app.post("/api/admin/generation-preview")
def admin_generation_preview(payload: GenerationPreviewIn) -> dict[str, Any]:
    return op(
        "admin_generation_preview", lambda: ADMIN.generation_preview(payload.controls.model_dump())
    )


@app.get("/api/admin/generations")
def admin_generations() -> list[dict[str, Any]]:
    return op("admin_generations", lambda: DatasetService(DATA_ROOT).generations())


@app.get("/api/admin/generations/{generation_id}/summary")
def admin_generation_summary(generation_id: str) -> dict[str, Any]:
    return op(
        "admin_generation_summary",
        lambda: DatasetService(DATA_ROOT).summary(generation_id),
    )


@app.get("/api/admin/generations/{generation_id}/records")
def admin_generation_records(
    generation_id: str,
    table: str = Query(default="reference"),
    q: str = Query(default="", max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return op(
        "admin_generation_records",
        lambda: DatasetService(DATA_ROOT).records(generation_id, table, q, offset, limit),
    )


@app.post("/api/admin/jobs", status_code=202, response_model=AdminJob)
def admin_job(payload: AdminJobIn, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    return op(
        "admin_job",
        lambda: ADMIN.start(payload.action, payload.params, (x_actor or "admin").strip()[:64]),
    )


@app.get("/api/admin/jobs/{job_id}", response_model=AdminJob)
def admin_job_status(job_id: str) -> dict[str, Any]:
    return op("admin_job_status", lambda: ADMIN.status(job_id))


@app.post("/api/admin/actions")
def admin_action(
    payload: AdminActionIn, x_actor: str | None = Header(default=None)
) -> dict[str, Any]:
    return op(
        "admin_action",
        lambda: ADMIN.action(payload.action, payload.params, (x_actor or "admin").strip()[:64]),
    )


@app.get("/api/data-entry")
def manual_list() -> dict[str, Any]:
    return op("manual_list", lambda: {"count": ADMIN._manual_count(), "rows": ADMIN.manual_list()})


@app.post("/api/data-entry/register")
def manual_register(
    payload: RegisterIn, x_actor: str | None = Header(default=None)
) -> dict[str, Any]:
    return op(
        "manual_register",
        lambda: ADMIN.manual_register(payload.record, (x_actor or "admin").strip()[:64]),
    )


@app.post("/api/data-entry/import")
def manual_import(payload: ImportIn, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    return op(
        "manual_import",
        lambda: ADMIN.manual_import(
            payload.rows, payload.dry_run, (x_actor or "admin").strip()[:64]
        ),
    )


@app.post("/api/data-entry/undo")
def manual_undo(payload: UndoIn, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    return op(
        "manual_undo",
        lambda: ADMIN.manual_undo(payload.record_ids, (x_actor or "admin").strip()[:64]),
    )


class RecommendIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    record: dict[str, str]
    thresholds: dict[str, float] | None = None  # session-only decision preview

    @field_validator("record")
    @classmethod
    def validate_record(cls, record: dict[str, str]) -> dict[str, str]:
        if set(record) - set(VISIBLE_FIELDS) or any(len(v) > 1000 for v in record.values()):
            raise ValueError("unsupported field or input too long")
        return {k: v.strip() for k, v in record.items() if k in NORMALIZED_FIELDS}

    @field_validator("thresholds")
    @classmethod
    def validate_thresholds(cls, thresholds: dict[str, float] | None) -> dict[str, float] | None:
        if thresholds is None:
            return None
        known = set(thresholds) - {"auto", "review"}
        if known:
            raise ValueError(f"unsupported threshold keys: {sorted(known)}")
        if any(not (0.0 <= v <= 1.0) for v in thresholds.values()):
            raise ValueError("thresholds must be in [0, 1]")
        if (
            thresholds.get("auto") is not None
            and thresholds.get("review") is not None
            and thresholds["auto"] < thresholds["review"]
        ):
            raise ValueError("auto threshold must be >= review threshold")
        return thresholds


@app.post("/api/recommendations", status_code=202, response_model=MatchJob)
def start_recommendation(payload: RecommendIn) -> dict[str, Any]:
    return op(
        "recommendation_start",
        lambda: RECOMMEND.start(payload.run_id, payload.record, payload.thresholds),
    )


@app.get("/api/recommendations/{job_id}", response_model=MatchJob)
def recommendation_status(job_id: str) -> dict[str, Any]:
    return op("recommendation_status", lambda: RECOMMEND.status(job_id))


@app.post("/api/recommend", response_model=MatchResult)
def recommend(payload: RecommendIn) -> dict[str, Any]:
    return op(
        "recommend", lambda: RECOMMEND.recommend(payload.run_id, payload.record, payload.thresholds)
    )


class DecisionsIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    items: list[dict[str, str]] = Field(default_factory=list)


@app.get("/api/decisions")
def decisions_get(run_id: str = "") -> dict[str, Any]:
    try:
        items = op("decisions", lambda: DECISIONS.load(run_id))
    except ValueError as exc:
        raise HTTPException(422, "invalid run_id") from exc
    return {"items": items}


@app.put("/api/decisions")
def decisions_put(
    payload: DecisionsIn, x_reviewer: str | None = Header(default=None)
) -> dict[str, Any]:
    """Persist review decisions with reviewer identity (audit trail)."""
    reviewer = (x_reviewer or DEFAULT_REVIEWER).strip()[:64] or DEFAULT_REVIEWER
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    items = [
        {
            "left": str(i.get("left", "")),
            "right": str(i.get("right", "")),
            "ts": str(i.get("ts", "") or now),
            "reviewer": reviewer,
        }
        for i in payload.items
    ]
    try:
        op("decisions_save", lambda: DECISIONS.save(payload.run_id, items))
    except ValueError as exc:
        raise HTTPException(422, "invalid run_id") from exc
    return {"saved": len(items)}


class QueryIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    sql: str = Field(..., max_length=8000)


@app.post("/api/query", response_model=dict[str, Any])
def debug_sql(payload: QueryIn) -> dict[str, Any]:
    if not DEBUG_SQL:
        raise HTTPException(403, "raw SQL is disabled (set NAYOSE_DEBUG_SQL=1 to enable)")
    if not (payload.sql.lstrip().lower().startswith(("select", "with"))):
        raise HTTPException(400, "only SELECT/WITH queries are allowed")
    return QUERIES.run_sql(payload.run_id, payload.sql)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("NAYOSE_BIND_HOST", "127.0.0.1"),
        port=int(os.getenv("NAYOSE_DASH_PORT", "8000")),
    )
