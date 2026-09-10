# Architecture (one page)

Intent: a *local, auditable* entity-resolution tool — typed end-to-end, hermetic
tests, one-command status. Read this before touching code; AGENTS.md has the
rules, README.md the usage.

## The stack

```
data/raw/...  generations/gen_*/  current↙   immutable real data + generation products
synthetic/    engine + providers + noise + truth + pipeline      (generator + CLI contract)
synthetic_data.ipynb                                              (thin generation/report driver)
nayose.ipynb                                                      (training reference; scripts/train.py is the headless adapter)
models/runs/run_*/  current↙  + registry.json                     (matcher bundles)
models/decisions/<run>.json                                       (operational review state)
frontend/     typed TS app with explicit ports (no bundler; tsc -> ESM in js/)
api/main.py   FastAPI: typed domain endpoints (+ debug-only raw SQL)
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
DecisionsStore (models/decisions/<run>.json) → typed FastAPI routes (op()-timed)
```
All queries are typed endpoints (`/api/entities`, `/api/entities/explorer`,
`/api/entity/{id}`, `/api/entity/{id}/graph`, `/api/review`, `/api/metrics`,
`/api/confusion`, `/api/compare`,
`/api/recommend`, `/api/decisions`, `/api/stats`). Raw SQL is a debug door only
(`NAYOSE_DEBUG_SQL=1`).

## Data flow (one loop)

```
generate (synthetic/) → run matcher (scripts/train.py / nayose.ipynb) → immutable bundle + generation field_lineage
→ API serves bundle parquet via DuckDB → Web views (search/review/audit)
→ decisions written back (models/decisions) → next audit reads them
```

## Enforcement (definition of done for ANY change)

```bash
python scripts/audit_repo.py     # tsc + npm test + E2E(opt-in) + pytest + ruff + mypy
```
Every acceptance criterion the repo has is runnable in one command (see AGENTS.md
"How to add…" for step-by-step paths).