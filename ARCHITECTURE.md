# Architecture (one page)

Intent: a *local, auditable* entity-resolution tool — typed end-to-end, hermetic
tests, one-command status. Read this before touching code; AGENTS.md has the
rules, README.md the usage.

## The stack

```
data/raw/...  generations/gen_*/  current↙   immutable real data + generation products
synthetic/    engine + providers + noise + truth + pipeline      (generator + CLI contract)
api/commands/data.py                                           (data command implementation)
api/commands/lifecycle.py + api/training/                         (training and evaluation application)
models/runs/run_*/  current↙  + registry.json                     (matcher bundles)
data/operational.sqlite3                                        (jobs, decisions, audit, manual staging)
frontend/     typed TS app with explicit ports (no bundler; tsc -> ESM in js/)
api/app.py + api/routers/   FastAPI transport, lifecycle, and typed domain endpoints
api/main.py   compatibility entrypoint and composition root
```

## Frontend boundaries

```
frontend/ts/   schema.ts (contract) · core.ts (pure logic)      ← no DOM, no fetch
port.ts   typed capability ports + Storage (the only thing views may depend on)
infra/    ApiAdapter (typed endpoints) + memo cache; LocalStorage
store.ts  observable state + reversible decision commands
features/ one module per view (search/review/entity/entity-explorer/confusion/metrics/compare…)
app.ts    composition root: boot({port, store, runs}); NO import side effects
```

Rules that keep it strict:
- views never call `fetch`/SQL/localStorage; they call `QueryPort` only.
- new data need → new `port` method → server endpoint → ApiAdapter → (MockPort + API test).
- every shared fact has ONE owner: bundle schema lives in `schema.ts` +
  `REQUIRED_COLUMNS`, and a CI test fails if they drift.

## Server data flow

```
RunRepository (bundles) → QueryService (DuckDB, owned SQL, per-run LRU cache)
                        → SearchService (shared query plan + exact/hybrid strategies)
                        → RecommendService (lazy, fingerprint-guarded engine)
EntityGraphRepository → EntityGraphProjector → typed graph response
OperationalStore (SQLite WAL) → focused repositories → typed FastAPI routes (timed_operation)
```
All queries are typed endpoints (`/api/entities`, `/api/entities/explorer`,
`/api/entity/{id}`, `/api/entity/{id}/graph`, `/api/review`, `/api/metrics`,
`/api/confusion`, `/api/compare`,
`/api/recommend`, `/api/decisions`, `/api/stats`). Raw SQL is a debug door only
(`NAYOSE_DEBUG_SQL=1`).

## Data flow (one loop)

```
generate (synthetic/) → run matcher (nayose model train) → immutable bundle + generation field_lineage
→ API serves bundle parquet via DuckDB → Web views (search/review/audit)
→ decisions written back (operational SQLite) → next audit reads them
```

## Enforcement (definition of done for ANY change)

```bash
python -m api.cli audit repo     # tsc + npm test + E2E(opt-in) + pytest + ruff + mypy
```
Every acceptance criterion the repo has is runnable in one command (see AGENTS.md
"How to add…" for step-by-step paths).