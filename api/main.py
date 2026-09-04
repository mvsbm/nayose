"""Nayose web dashboard API.

Design patterns: Repository (runs/data), Service (DuckDB queries, engine
recommend), Facade (thin FastAPI routes over the services). Contract-first:
Pydantic models on the wire, bundle schema validated by the engine's
fingerprint guard.

UI/UX principles this serves (see frontend/js): calm tech, progressive
disclosure, epistemic honesty (uncertainty-first), local-first (single local
process; parquet read-only via DuckDB), performance as respect.

Security posture: local tool bound to 127.0.0.1. The /query endpoint executes
a whitelisted subset of SQL (SELECT/WITH only) over two virtual roots
('bundle/' -> a run bundle dir, 'data/' -> the data dir); row caps enforced.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

MODELS_DIR = Path(os.getenv("NAYOSE_MODELS_DIR", "models"))
DATA_DIR = Path(os.getenv("NAYOSE_DATA_DIR", "data/current"))
FRONTEND_DIR = Path(os.path.dirname(__file__)).parent / "frontend"
MAX_ROWS = 10000


# --------------------------------------------------------------------------
# Repository: run bundles
# --------------------------------------------------------------------------
class RunRepository:
    def __init__(self, root: Path):
        self.root = root

    def list(self) -> list[dict[str, Any]]:
        runs = []
        current = (self.root / "current").resolve() if (self.root / "current").exists() else None
        runs_root = self.root / "runs"
        if runs_root.is_dir():
            for run in sorted(runs_root.iterdir(), key=lambda p: p.name, reverse=True):
                if not run.is_dir():
                    continue
                entry = {
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


# --------------------------------------------------------------------------
# Service: DuckDB read-only queries over parquet (virtual roots bundle/, data/)
# --------------------------------------------------------------------------
_FORBIDDEN = (
    "delete",
    "insert",
    "update",
    "drop",
    "alter",
    "create",
    "attach",
    "detach",
    "install",
    "load",
    "copy",
    "pragma",
    "call",
    "import",
)


class QueryService:
    def __init__(self, repo: RunRepository, data_dir: Path):
        self.repo = repo
        self.data_dir = data_dir

    def run_sql(self, run_id: str, sql: str) -> dict[str, Any]:
        head = sql.lstrip().lower()
        if not (head.startswith("select") or head.startswith("with")):
            raise HTTPException(400, "only SELECT/WITH queries are allowed")
        if any(token in sql.lower() for token in _FORBIDDEN):
            raise HTTPException(400, "forbidden SQL keyword")
        run_dir = self.repo.resolve(run_id)
        resolved = (
            sql.replace("'bundle/", f"'{run_dir}/")
            .replace('"bundle/', f'"{run_dir}/')
            .replace("'data/", f"'{self.data_dir}/")
            .replace('"data/', f'"{self.data_dir}/')
        )
        con = duckdb.connect()
        try:
            result = con.execute(resolved).fetchdf()
        except duckdb.Error as exc:  # pragma: no cover - mirrors DuckDB raising
            raise HTTPException(400, f"DuckDB error: {exc}") from exc
        finally:
            con.close()
        if len(result) > MAX_ROWS:
            result = result.head(MAX_ROWS)
        return {
            "columns": [str(c) for c in result.columns],
            "rows": result.fillna("").values.astype(object).tolist(),
            "row_count": len(result),
        }


# --------------------------------------------------------------------------
# Service: live recommendation (lazy engine per run, fingerprint-guarded)
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Facade (FastAPI)
# --------------------------------------------------------------------------
class QueryIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    sql: str = Field(..., max_length=8000)


class RecommendIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    record: dict[str, str]


RUNS = RunRepository(MODELS_DIR)
QUERIES = QueryService(RUNS, DATA_DIR)
RECOMMEND = RecommendService(RUNS, DATA_DIR)

app = FastAPI(title="Nayose — 照合監査ダッシュボード", version="0.4")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/runs", response_model=None)
def list_runs() -> list[dict[str, Any]]:
    return RUNS.list()


@app.post("/api/query", response_model=dict[str, Any])
def query(payload: QueryIn) -> dict[str, Any]:
    return QUERIES.run_sql(payload.run_id, payload.sql)


@app.post("/api/recommend", response_model=dict[str, Any])
def recommend(payload: RecommendIn) -> dict[str, Any]:
    return RECOMMEND.recommend(payload.run_id, payload.record)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("NAYOSE_DASH_PORT", "8000")))
