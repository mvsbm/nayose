"""Nayose web dashboard API — typed domain endpoints (Phase 2).

Architecture: Repository (runs) -> QueryService (DuckDB, server-owned SQL, cache)
-> RecommendService (fingerprint-guarded engine) -> thin FastAPI facade.
Raw SQL is NOT exposed to clients (only via NAYOSE_DEBUG_SQL=1 for debugging);
clients call typed operations (entities/review/metrics/confusion/entity/compare).
Local-only: bound to 127.0.0.1; row caps + tiny in-memory cache + endpoint stats.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

MODELS_DIR = Path(os.getenv("NAYOSE_MODELS_DIR", "models"))
DATA_DIR = Path(os.getenv("NAYOSE_DATA_DIR", "data/current"))
FRONTEND_DIR = Path(os.path.dirname(__file__)).parent / "frontend"
DEBUG_SQL = os.getenv("NAYOSE_DEBUG_SQL", "0") == "1"
MAX_ROWS = 10000
MAX_CACHE = 96

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

    def _cached(self, key: str, fn) -> Any:
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

    def entities(self, run_id: str, q: str, limit: int = 50) -> list[dict[str, Any]]:
        con, run_dir = self._open(run_id)
        try:
            esc_q = (
                q.replace("'", "''").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            where = (
                f"WHERE CAST(name_kanji AS VARCHAR) LIKE '%{esc_q}%' "
                f"OR CAST(name_kana AS VARCHAR) LIKE '%{esc_q}%' "
                f"OR CAST(phone AS VARCHAR) LIKE '%{esc_q}%' "
                f"OR CAST(postal_code AS VARCHAR) LIKE '%{esc_q}%' "
                f"OR CAST(nayose_id AS VARCHAR) LIKE '%{esc_q}%'"
                if q
                else ""
            )
            order = "ORDER BY record_count DESC" if not q else "ORDER BY name_kanji"
            df = con.execute(
                f"SELECT nayose_id,name_kanji,name_kana,phone,postal_code,record_count "
                f"FROM read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR) {where} {order} LIMIT {int(limit)}"
            ).fetchdf()
            return df.where(df.notna(), None).to_dict("records")
        finally:
            con.close()

    def entity(self, run_id: str, entity_id: str) -> dict[str, Any]:
        con, run_dir = self._open(run_id)
        try:
            ent = con.execute(
                f"SELECT * FROM read_parquet('{run_dir}/canonical_entities.parquet'::VARCHAR) WHERE nayose_id = '{entity_id.replace(chr(39), chr(39) + chr(39))}' LIMIT 1"
            ).fetchdf()
            if ent.empty:
                raise HTTPException(404, f"entity {entity_id!r} not found")
            members_df = con.execute(
                f"SELECT r.record_id,r.name_kana,r.postal_code,r.phone "
                f"FROM read_parquet('{self.data_dir}/reference_records.parquet'::VARCHAR) r "
                f"JOIN read_parquet('{run_dir}/record_to_nayose.parquet'::VARCHAR) m ON r.record_id = m.record_id "
                f"WHERE m.nayose_id = '{entity_id}' LIMIT 8"
            ).fetchdf()
            members = members_df.to_dict("records")
            return {
                "entity": ent.iloc[0].where(pd.notna(ent.iloc[0]), None).to_dict(),
                "members": members,
                "lineage": self._lineage(con, run_dir, entity_id),
            }
        finally:
            con.close()

    def _lineage(self, con, run_dir: Path, entity_id: str) -> dict[str, list[str]]:
        summary = run_dir / "lineage_summary.parquet"
        if summary.exists():
            df = con.execute(
                f"SELECT field,transformation FROM read_parquet('{run_dir}/lineage_summary.parquet'::VARCHAR) WHERE entity_id = '{entity_id}' ORDER BY count DESC"
            ).fetchdf()
        else:
            m = con.execute(
                f"SELECT record_id FROM read_parquet('{run_dir}/record_to_nayose.parquet'::VARCHAR) WHERE nayose_id = '{entity_id}' LIMIT 200"
            ).fetchdf()
            ids = ", ".join(f"'{r}'" for r in m["record_id"].astype(str)) or "''"
            df = con.execute(
                f"SELECT field,transformation,count(*) AS count FROM read_parquet('{self.data_dir}/ground_truth/field_lineage.parquet'::VARCHAR) "
                f"WHERE item_id IN ({ids}) GROUP BY field,transformation ORDER BY count DESC"
            ).fetchdf()
        out: dict[str, list[str]] = {}
        for _, r in df.iterrows():
            field = str(r["field"])
            label = str(r["transformation"])
            if label not in out.setdefault(field, []):
                out[field].append(label)
            if len(out[field]) >= 4:
                del out[field][4:]
        return out

    def review(self, run_id: str, min_p: float, offset: int, limit: int) -> dict[str, Any]:
        con, run_dir = self._open(run_id)
        try:
            total = int(
                con.execute(
                    f"SELECT count(*) AS c FROM read_parquet('{run_dir}/review_queue.parquet'::VARCHAR) WHERE pair_probability >= {float(min_p)}"
                ).fetchone()[0]
            )
            rows = (
                con.execute(
                    f"SELECT left_record_id,right_record_id,pair_probability,exact_count FROM read_parquet('{run_dir}/review_queue.parquet'::VARCHAR) "
                    f"WHERE pair_probability >= {float(min_p)} ORDER BY pair_probability DESC LIMIT {int(limit)} OFFSET {int(offset)}"
                )
                .fetchdf()
                .to_dict("records")
            )
            return {"total": total, "rows": rows}
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


class RecommendService:
    def __init__(self, repo: RunRepository, data_dir: Path):
        self.repo = repo
        self.data_dir = data_dir
        self._engines: dict[str, Any] = {}

    def recommend(self, run_id: str, record: dict[str, str]) -> dict[str, Any]:
        engine = self._engines.get(run_id)
        if engine is None:
            import nayose_inference as ni

            run_dir = self.repo.resolve(run_id)
            try:
                engine = ni.load_engine(model_dir=str(run_dir), data_dir=str(self.data_dir))
            except (RuntimeError, FileNotFoundError) as exc:
                raise HTTPException(409, "bundle not servable: unsupported/mismatched run") from exc
            self._engines[run_id] = engine
        result = engine.recommend(record)
        top = (
            result.candidates.head(8)
            if result.candidates is not None and len(result.candidates)
            else None
        )
        return {
            "action": result.action,
            "top_nayose_id": result.top_nayose_id,
            "top_probability": round(float(result.top_probability), 4),
            "margin": round(float(result.margin), 4),
            "diagnostics": getattr(result, "diagnostics", {}),
            "candidates": top.to_dict("records") if top is not None else [],
        }


RUNS = RunRepository(MODELS_DIR)
QUERIES = QueryService(RUNS, DATA_DIR)
RECOMMEND = RecommendService(RUNS, DATA_DIR)

app = FastAPI(title="Nayose — 照合監査ダッシュボード", version="0.5")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


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


@app.get("/api/entities")
def entities(q: str = "", run_id: str = "", limit: int = 50) -> dict[str, Any]:
    return op("entities", lambda: {"rows": QUERIES.entities(run_id, q, min(limit, 200))})


@app.get("/api/entity/{entity_id}")
def entity(entity_id: str, run_id: str = "") -> dict[str, Any]:
    return op("entity", lambda: QUERIES.entity(run_id, entity_id))


@app.get("/api/review")
def review(
    run_id: str = "", min_p: float = 0.0, offset: int = 0, limit: int = 200
) -> dict[str, Any]:
    return op("review", lambda: QUERIES.review(run_id, min_p, offset, min(limit, 500)))


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


class RecommendIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    record: dict[str, str]


@app.post("/api/recommend")
def recommend(payload: RecommendIn) -> dict[str, Any]:
    return op("recommend", lambda: RECOMMEND.recommend(payload.run_id, payload.record))


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
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("NAYOSE_DASH_PORT", "8000")))
