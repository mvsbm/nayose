# Architecture (one page)

Intent: a *local, auditable* entity-resolution tool — typed end-to-end, hermetic
tests, one-command status. Read this before touching code; `AGENTS.md` has the
rules, `README.md` the usage.

## The stack

```
data/raw/...  generations/gen_*/  current↙       immutable data products
synthetic/    engine + providers + noise + truth  generation application
api/commands/                                       canonical CLI commands
api/training/                                       isolated training application
models/runs/run_*/  current↙ + registry.json       fingerprint-bound bundles
data/operational.sqlite3                            jobs, decisions, audit, staging
frontend/                                           typed no-build dashboard
api/                                               serving package
```

## Server package boundaries

```
api/main.py                         process entrypoint and compatibility exports
api/composition.py                  composition root; wires concrete services/adapters
api/http/                           inbound HTTP adapter
  app.py                             FastAPI factory, middleware, lifespan
  routers/                          endpoint modules grouped by URL capability
  schemas.py/contracts.py            transport input/output types
api/application/                    use-case facades and orchestration
api/domain/                         pure identity, normalization, evidence, and scoring rules
api/infrastructure/                 outbound adapters
  bundles/                          model/data bundle catalog and publication
  matching/                         engine-bound scoring and decision adapters
  read_model/                       DuckDB bundle queries and projections
  persistence/                      SQLite and repository adapters
  retrieval/                        exact, sparse, dense, and fusion adapters
  runtime/                          engine, embeddings, model/device adapters
  process/                           bounded jobs and workflow persistence
  observability/                     endpoint and runtime telemetry
api/training/                       batch-training functional core and worker shell
```

Dependency direction is one-way:

```
HTTP routers → application services → domain rules
                         ↘ infrastructure ports/adapters
composition root → wires all concrete implementations
```

Routers parse HTTP, invoke one application operation, and translate domain
errors into HTTP responses. They do not contain SQL, filesystem, Torch, or
bundle-selection logic. The explicit router registry in
`api/http/routers/__init__.py` is the transport composition boundary; new routes
are added there deliberately rather than discovered dynamically.

The entity routes own both `/api/entities...` search and `/api/entity/...`
detail/graph endpoints. Administrative routes are split between `/api/admin/...`
and `/api/data-entry/...`; decisions remain separate because they have distinct
mutation, revision, audit, and authorization semantics.

## Server data flow

```
BundleRepository → QueryService → SearchService → RecommendationService
       ↓                ↓              ↓                  ↓
  release guard     DuckDB reads   exact/hybrid       fingerprint-guarded engine

OperationalStore → focused persistence/process adapters → HTTP application services
```

All queries are typed endpoints (`/api/entities`, `/api/entities/explorer`,
`/api/entity/{id}`, `/api/entity/{id}/graph`, `/api/review`, `/api/metrics`,
`/api/confusion`, `/api/compare`, `/api/recommend`, `/api/decisions`, and
`/api/stats`). Raw SQL is a debug door only (`NAYOSE_DEBUG_SQL=1`).

## Frontend boundaries

```
frontend/ts/   schema.ts (contract) · core.ts (pure logic)      ← no DOM, no fetch
port.ts        typed capability ports + Storage                   ← view boundary
infra/         ApiAdapter + memo cache; LocalStorage
store.ts       observable state + reversible decision commands
features/      one module per view
app.ts         composition root: boot({port, store, runs})
```

Views never call `fetch`, SQL, or `localStorage`; they use focused ports. New
data follows `port` method → server endpoint → `ApiAdapter` → mock/API tests.

## Data flow (one loop)

```
generate (synthetic/) → train (api/training/) → immutable bundle + field_lineage
→ API serves fingerprint-bound bundle via DuckDB → dashboard review/search
→ decisions are persisted in operational SQLite → next audit reads them
```

## Enforcement

```bash
python -m api.cli audit repo
```

This runs the repository's formatting, lint, type, frontend, and test gates.
