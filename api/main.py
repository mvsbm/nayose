"""Nayose web dashboard API — typed domain endpoints (Phase 2).

Architecture: Repository (runs) -> QueryService (DuckDB, server-owned SQL, cache)
-> RecommendService (fingerprint-guarded engine) -> typed FastAPI routes.
Raw SQL is NOT exposed to clients (only via NAYOSE_DEBUG_SQL=1 for debugging);
clients call typed operations (entities/review/metrics/confusion/entity/compare).
Local-only: bound to 127.0.0.1; row caps + tiny in-memory cache + endpoint stats.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from api.admin import AdminService
from api.contracts import (
    AdminJob,
    DatasetRowsOut,
    EntityDetail,
    EntityExplorerPage,
    EntityGraph,
    GenerationPreviewIn,
    MatchJob,
    MatchResult,
    SampleRecord,
)
from api.dataset_service import DatasetService
from api.decisions import DecisionsStore
from api.entity_graph_repository import EntityNotFoundError
from api.entity_index import EntityCursorError
from api.evidence import NORMALIZED_FIELDS, VISIBLE_FIELDS
from api.matching import RecommendationService as RecommendService
from api.normalize import normalize_query_fields
from api.performance import RuntimeProfile
from api.query_context import BundleSnapshotError
from api.query_service import QueryService
from api.release import release_status
from api.repositories import RunRepository
from api.search_service import (
    EntitySearchRequest,
    ExactPageRetriever,
    ExactRetriever,
    FuzzyRetriever,
    SearchFilters,
    SearchService,
    SemanticRetriever,
    SuggestionRetriever,
)
from api.telemetry import Stats

MODELS_DIR = Path(os.getenv("NAYOSE_MODELS_DIR", "models"))
DATA_DIR = Path(os.getenv("NAYOSE_DATA_DIR", "data/current"))
DATA_ROOT = Path(os.getenv("NAYOSE_DATA_ROOT", "data"))
MANUAL_DIR = Path(os.getenv("NAYOSE_MANUAL_DIR", "data/manual"))
FRONTEND_DIR = Path(os.path.dirname(__file__)).parent / "frontend"
DEBUG_SQL = os.getenv("NAYOSE_DEBUG_SQL", "0") == "1"
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


STATS = Stats()


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
        paged_exact=ExactPageRetriever(queries),
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


DECISIONS = DecisionsStore(MODELS_DIR)
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


@app.exception_handler(BundleSnapshotError)
async def bundle_snapshot_error(_request: Request, exc: BundleSnapshotError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)


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
                result = fn(*args, **kwargs)
            except Exception:
                STATS.record(name, time.perf_counter() - t0, error=True)
                raise
            STATS.record(name, time.perf_counter() - t0)
            return result

        return wrapper

    return deco


def op(name: str, fn):
    """Run + record timing/hits; keeps endpoint signatures visible to FastAPI."""
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception:
        STATS.record(name, time.perf_counter() - t0, error=True)
        raise
    STATS.record(name, time.perf_counter() - t0)
    return result


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


@app.get("/api/entities/explorer", response_model=EntityExplorerPage)
def entity_explorer(
    q: str = "",
    run_id: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
    sort: Literal["relevance", "records", "name", "last_seen"] = "relevance",
    party_type: str = "",
    min_record_count: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Page the same server-owned exact index used by entity search."""

    def run() -> dict[str, Any]:
        filters = SearchFilters(
            party_type=party_type.strip() or None,
            min_record_count=min_record_count if min_record_count > 0 else None,
        )
        try:
            return current_search_service().explore(
                EntitySearchRequest(
                    run_id=run_id,
                    query=q,
                    offset=offset,
                    limit=limit,
                    sort=sort,
                    filters=filters,
                    cursor=cursor,
                )
            )
        except EntityCursorError as exc:
            raise HTTPException(422, str(exc)) from exc

    return op("entity_explorer", run)


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


@app.get("/api/entity/{entity_id}/graph", response_model=EntityGraph)
def entity_graph(
    entity_id: str,
    run_id: str = "",
    record_limit: int = Query(50, ge=1, le=100),
    potential_limit: int = Query(20, ge=1, le=50),
    include_potential: bool = False,
) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        try:
            return QUERIES.entity_graph(
                run_id, entity_id, record_limit, potential_limit, include_potential
            )
        except EntityNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    return op("entity_graph", run)


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
    runtime = RuntimeProfile.resolve(os.getenv("NAYOSE_DEVICE", "auto"))
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
        "runtime": asdict(runtime),
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


@app.get("/api/admin/generations/{generation_id}/records", response_model=DatasetRowsOut)
def admin_generation_records(
    generation_id: str,
    table: str = Query(default="reference"),
    q: str = Query(default="", max_length=100),
    filters: str = Query(default="", max_length=2_000),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    count: bool = Query(default=True),
    after: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    column_filters: dict[str, str] | None = None
    if filters.strip():
        try:
            decoded = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "invalid_dataset_filters") from exc
        if not isinstance(decoded, dict):
            raise HTTPException(422, "dataset_filters_must_be_object")
        column_filters = {str(key): str(value) for key, value in decoded.items()}
    return op(
        "admin_generation_records",
        lambda: DatasetService(DATA_ROOT).records(
            generation_id,
            table,
            q,
            offset,
            limit,
            column_filters,
            include_total=count,
            after=after,
        ),
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
