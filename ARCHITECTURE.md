# Architecture (one page)

Intent: a *local, auditable* entity-resolution tool — typed end-to-end, hermetic
tests, one-command status. Read this before touching code; `AGENTS.md` has the
rules, `README.md` the usage.

## The stack

```
src/                                                  installable Python packages
  api/                                                serving, CLI, and training package
  data_generation/                                    data-generation package
frontend/                                              typed no-build dashboard
tests/                                                 unit, integration, and contract tests
ci/                                                    quality and budget configuration
docs/                                                  architecture and operations
data/                                                  runtime data products (gitignored)
  raw/                                                immutable source cache
  generations/                                        immutable generated snapshots
  current -> generations/gen_<timestamp>_<hash>/      active data alias
models/                                                runtime model products (gitignored)
  runs/                                               immutable model bundles
  current -> runs/run_<timestamp>_<hash>/             active model alias
```

## Server package boundaries

```
src/api/main.py                         process entrypoint and compatibility exports
src/api/composition.py                  composition root; wires concrete services/adapters
src/api/http/                           inbound HTTP adapter
  app.py                             FastAPI factory, middleware, lifespan
  routers/                          endpoint modules grouped by URL capability
  schemas.py/contracts.py            transport input/output types
src/api/application/                    use-case facades and orchestration
src/api/domain/                         pure identity, normalization, evidence, and scoring rules
src/api/infrastructure/                 outbound adapters
  bundles/                          model/data bundle catalog and publication
  matching/                         engine-bound scoring and decision adapters
  read_model/                       DuckDB bundle queries and projections
  persistence/                      SQLite and repository adapters
  retrieval/                        exact, sparse, dense, and fusion adapters
  runtime/                          engine, embeddings, model/device adapters
  process/                           bounded jobs and workflow persistence
  observability/                     endpoint and runtime telemetry
src/api/training/                       batch-training functional core and worker shell
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
`src/api/http/routers/__init__.py` is the transport composition boundary; new routes
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
frontend/ts/   schema.ts/core.ts (contracts and pure logic)     ← no DOM, no fetch
frontend/ts/port.ts      typed capability ports + storage boundary
frontend/ts/infra.ts    ApiAdapter + memo cache; HTTP adapter only
frontend/ts/store.ts    observable state + reversible decision commands
frontend/ts/*-view.ts   one module per view
frontend/ts/app.ts      composition root: boot({port, store, runs})
```

Views never call `fetch`, SQL, or `localStorage`; they use focused ports. New
data follows `port` method → server endpoint → `ApiAdapter` → mock/API tests.

## Data flow (one loop)

```
generate (src/data_generation/) → train (src/api/training/) → immutable bundle + field_lineage
→ API serves fingerprint-bound bundle via DuckDB → dashboard review/search
→ decisions are persisted in operational SQLite → next audit reads them
```

## Enforcement

```bash
python -m api.cli audit repo
```

This runs the repository's formatting, lint, type, frontend, and test gates.
