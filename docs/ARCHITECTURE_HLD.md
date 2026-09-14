# Nayose High-Level Architecture

Status: proposed design review
Scope: local-first entity-resolution dashboard, immutable data/model products,
and evidence-first review workflow.

## 1. Executive decision

Nayose should remain a **modular monolith with an offline build pipeline**.
The current direction is sound: Parquet/DuckDB for immutable analytical data,
FastAPI for typed domain APIs, a typed frontend port, and a bounded matching
runtime. The architecture is not yet ideal because release selection, mutable
operational state, configuration, security, and lifecycle ownership are split
across several independent mechanisms.

The target is deliberately smaller than a distributed system:

- one authoritative release pointer selects a complete data/model pair;
- every request resolves one immutable snapshot and uses it throughout;
- the API owns read/search/matching orchestration, not storage details;
- SQLite WAL is the single-node source of truth for mutable operational state;
- generation and training remain offline subprocess jobs;
- semantic retrieval discovers candidates; evidence and abstention decide identity;
- scale-out is conditional on measured capacity failure, not a design default.

## 2. Goals and non-goals

### Goals

- Prevent data/model mixing during alias rotation or historical inspection.
- Preserve lineage, hidden-truth isolation, candidate provenance, and safe abstention.
- Support local single-user and authenticated trusted-LAN deployments with the same core.
- Bound memory, concurrency, query cost, and long-running work.
- Make operator decisions durable, versioned, attributable, and reversible.
- Keep the system understandable to a new maintainer.

### Non-goals for the current architecture

- Microservices, Redis, PostgreSQL, or a hosted queue before capacity evidence.
- Entity-centroid semantic profiles or uncontrolled graph unions.
- Treating dense/fuzzy/LLM similarity as an identity decision.
- Replacing immutable Parquet/DuckDB analytical storage with a transactional database.
- Cryptographic audit chains or signed exports before a concrete threat model requires them.
- Supporting full 2M sabotaged generation/serving without a capacity sign-off.

## 3. System context

```text
                         offline build plane
  raw reference data -> generator -> immutable generation
                                  -> trainer -> immutable model run
                                                    |
                                                    v
                                      release validator + publisher
                                                    |
                                                    v
                         immutable artifact store + release pointer
                                                    |
                 +----------------------------------+------------------+
                 |                                                     |
             browser                                               operator/CLI
                 |                                                     |
                 +---------------------- HTTPS -----------------------+
                                        |
                              FastAPI modular monolith
                 +------------------+------------------+---------------+
                 |                  |                  |               |
             read API           search API        match API       admin API
                 |                  |                  |               |
                 +------------------+---------+--------+---------------+
                                            |
                                request Snapshot context
                           +----------------+----------------+
                           |                                 |
                    read repositories                  match runtime
                 DuckDB + exact/indexed data       bounded EngineHandle
                           |                                 |
                           +----------------+----------------+
                                            |
                                  SQLite WAL operational store
                         decisions · jobs · audit · manual staging
```

The browser receives static assets from the API but never accesses files,
DuckDB, SQL, normalization, ranking, or model artifacts directly.

## 4. Trust and storage boundaries

There are two storage classes and they must not be conflated.

| Storage class | Contents | Mutability | Owner |
|---|---|---:|---|
| Immutable analytical artifacts | generations, truth/lineage, model runs, indexes, manifests | never modified after validation | generator/trainer and artifact repository |
| Transactional operational state | decisions, revisions, jobs, audit events, manual staging, release history | append/update under SQLite transaction | operational repository |

A manual record is not part of the active analytical snapshot. It is an
append-only staging observation and enters a future generation through the
normal generator/trainer path.

## 5. Release and snapshot consistency

### Authoritative release

The two independent `data/current` and `models/current` aliases must not be the
authoritative release boundary. They can remain compatibility views during
migration, but serving selects one small, atomically replaced release record:

```json
{
  "release_id": "rel_<id>",
  "generation_id": "gen_<id>",
  "run_id": "run_<id>",
  "data_fingerprint": "<sha256>",
  "data_path": "...",
  "model_path": "...",
  "schema_versions": {"data": "3.1", "model": "..."},
  "artifact_checksums": {"...": "..."}
}
```

The record is written only after all artifacts are complete, durable, and
validated. Publication is one atomic pointer replacement. The previous valid
pointer remains available for rollback. A failed publication cannot change the
active release. SQLite may record this publication for audit and rollback
history, but the pointer remains the sole artifact-selection authority.

### Request snapshot

`Snapshot` is an immutable value object containing at least:

```text
release_id, generation_id, run_id, data/model paths,
data_fingerprint, schema versions, runtime artifact identities
```

The application resolves it once at request start. Query, search, graph,
evaluation, and matching collaborators accept the snapshot rather than
independently reading environment variables or `current` aliases. An explicit
historical run is resolved by its fingerprint to the matching generation.

`NayoseEngine.load` continues to validate the fingerprint as a second defense.
A missing, stale, incomplete, or mismatched release fails closed.

## 6. Runtime request paths

### Read and search path

```text
HTTP request
  -> authenticate + validate request
  -> resolve Snapshot
  -> typed application service
  -> snapshot-bound repository
  -> typed response with release_id/fingerprint
```

Search is a candidate-discovery pipeline:

```text
QueryPlan
  -> exact/block retrieval
  -> sparse/fuzzy retrieval
  -> optional dense field views
  -> filter + merge by entity
  -> preserve all retrieval evidence/provenance
  -> rank for display
  -> SearchResponse
```

The current search contract should be retained, but extension points should be
kept only where there is a real alternate implementation. One simple retriever
contract and one candidate merger are preferable to a registry of speculative
strategies.

### Matching path

```text
input record
  -> normalize and validate
  -> candidate discovery
  -> pair verifier
  -> independent evidence families
  -> missingness/conflict/anchor policy
  -> constrained graph/entity aggregation
  -> AUTO_LINK | REVIEW | NO_MATCH | INSUFFICIENT_INPUT
```

Dense scores, fuzzy scores, and LLM output may affect candidate order and
features, but never bypass the evidence policy. Missing values remain unknown,
not disagreement. Every automatic link requires the configured calibrated
probability/margin and safe identity anchor policy.

### Administrative path

Generation and training are submitted as bounded subprocess jobs. The API
records job state and returns status; it does not execute training stages or
large generation work in the request thread. Publication occurs only through
the release validator/publisher after the complete pair passes validation.

## 7. Component ownership

| Boundary | Single responsibility | Target owner |
|---|---|---|
| Build pipeline | generate, train, materialize, validate artifacts | `synthetic/`, `api/commands/` |
| Release catalog | resolve and publish complete releases | `api/release.py` / focused catalog module |
| Artifact repository | locate immutable files and verify manifests | `api/repositories.py` and snapshot-bound context |
| Read repositories | execute server-owned bounded DuckDB/index queries | `api/*_queries.py` |
| Search service | interpret, retrieve, merge, rank, and build search response | `api/search_service.py` |
| Match service | load an `EngineHandle` and perform safe matching | `api/matching.py`, `api/engine.py` |
| Operational repository | transactional decisions, jobs, audit, and staging | focused SQLite-backed module |
| API routes | authentication, validation, mapping, status codes | thin modules extracted from `api/main.py` |
| Frontend transport | HTTP and serialization only | `frontend/ts/infra.ts` |
| Frontend state/views | commands, undo, rendering, navigation | `frontend/ts/store.ts` and feature modules |

`AdminService` currently combines admin actions, audit, jobs, generation
controls, and manual data entry. Those are separate reasons to change and
should converge on the owners above. `main.py` should become a composition
root plus thin route registration, not a second application service layer.

## 8. Mutable operational state

SQLite WAL is the local single-node operational boundary. The initial schema
should use focused tables, not JSON files plus sidecar metadata:

```text
release_history publication records and rollback history; not selection authority
jobs           state, parameters/reference, actor, heartbeat, result reference
decisions       current decision and optimistic version
decision_events append-only revisions/undo events
audit_events    authenticated actor, action, release, redacted details
manual_records append-only staging observations
```

Required behavior:

- decision save and its release binding commit in one transaction;
- concurrent edits use an expected version and return a conflict;
- undo writes a compensating event rather than deleting history;
- job states are explicit: queued, running, succeeded, failed, cancelled,
  expired/worker-lost;
- idempotency is unique within the job domain;
- audit details contain no PII by default;
- JSON/Parquet exports are derived artifacts, not mutable sources of truth.

A hash chain is not part of the minimum architecture. If tamper evidence later
becomes a requirement, add it to the operational repository as a tested policy,
not as an unrelated file-locking subsystem.

## 9. Lifecycle and resource policy

The app factory owns all resources through application lifespan:

```text
create config
  -> open repositories/store
  -> resolve active release
  -> optionally warm EngineHandle
  -> readiness
  -> serve requests
  -> stop admission
  -> drain requests/jobs
  -> close engine, encoders, DuckDB, store
```

There should be no module-level mutable service graph and no private attribute
mutation such as assigning an engine-reset callback after construction.
Configuration is parsed once into a validated object; contradictory data/model
roots are rejected.

The matching runtime has one bounded active engine per process/resource pool.
An engine is loaded and health-checked before swap; old resources are retired
only after users release the handle. A separate embedding process is justified
only by measured framework instability or memory isolation needs. It is not a
general-purpose concurrency solution.

Read/search and matching have separate admission limits. When the matching
budget or analytical query budget is full, the API returns an explicit retryable
response instead of creating unbounded work.

## 10. Security and failure behavior

| Situation | Required behavior |
|---|---|
| loopback local mode | auth may be omitted; bind remains loopback |
| LAN/hosted mode | startup fails without configured authentication and trusted-host policy |
| missing/invalid release | readiness is degraded; matching fails closed |
| release rotation | in-flight request continues on its resolved snapshot |
| model unavailable | exact/read paths remain available; matching returns typed unavailable error |
| API restart | finished job state remains; interrupted work becomes worker-lost, not silently retried |
| invalid decision version | return conflict; preserve prior decision |
| SQLite unavailable | mutation fails closed; no success response is emitted |
| debug SQL | disabled in production; if retained, root-confined, AST-validated, bounded, and authenticated |
| unauthenticated actor/reviewer header | never treated as identity in a shared deployment |

Health must separate liveness from authenticated readiness. Public liveness must
not expose filesystem paths, detailed artifact metadata, or operational stats.

## 11. Current architecture audit

### High priority

1. **No single release transaction.** Generation and model aliases are
   published independently. Fingerprint checks prevent many mismatches, but do
   not give every request one release decision or atomic rollback.
2. **Mutable state is split across file formats.** Decisions use JSON and a
   sidecar binding, audit uses JSONL, and manual records rewrite Parquet. These
   are difficult to transact together and make crash semantics more complex.
3. **Security is optional at the application boundary.** Bearer auth is opt-in,
   actor/reviewer headers are trusted, `/api/query` remains a debug door, and
   the container binds to `0.0.0.0`.
4. **Lifecycle ownership is unclear.** Global services are created during
   import, `main.py` swaps global search services for tests, and application
   shutdown does not centrally close every resource.

### Medium priority

5. **Admin and runtime responsibilities are broad.** `AdminService`,
   `AdminJobRunner`, and `MatchingJobRunner` duplicate state/lifecycle logic.
6. **Configuration has multiple roots and environment readers.** `DATA_DIR`,
   `DATA_ROOT`, model paths, and runtime defaults can
   describe different snapshots.
7. **Search is partially over-factored.** The candidate contract and query plan
   are useful; the number of protocols, rank strategies, and extension
   registries exceeds the currently demonstrated backend variability.
8. **Neural runtime isolation is complex and process-global.** The spawned
   encoder and global configuration solve real platform risks, but their need,
   ownership, and memory budget must be demonstrated by benchmarks.
9. **Analytical reads still scan Parquet in important paths.** Indexing and
   bounded query policies are performance work, not reasons to introduce a
   distributed database now.

### Known limitations, not immediate architecture failures

- A full 2M sabotaged generation and its serving footprint are not capacity
  validated.
- ANN artifacts are platform-specific and currently not portable across all
  documented targets.
- The promoted model is a degraded SVD baseline; neural promotion remains gated.
- Graph/cardinality/temporal constraints are not yet a complete domain model.

## 12. Phased implementation order

1. **Freeze this boundary.** Define deployment modes, SLOs, memory/disk budgets,
   and the release-pointer schema.
2. **Implement snapshot resolution.** Make all request paths consume one
   snapshot; preserve the engine fingerprint guard.
3. **Implement release validation/publication.** Add read-only validation,
   atomic pointer rotation, rollback, and fault tests.
4. **Move mutable state to SQLite.** Decisions, job state, audit events, and
   manual staging become transactional; remove JSON/Parquet mutation paths.
5. **Harden the boundary.** Require auth outside loopback, separate principals
   from roles, remove or constrain debug SQL, and validate request limits.
6. **Simplify composition/lifecycle.** Add an app factory and lifespan; extract
   route groups and remove private/global service mutation.
7. **Measure before optimizing.** Run indexed-read, cache, engine, and load
   benchmarks on approved dataset sizes.
8. **Only then implement Phase 2 retrieval/index work.** Candidate-discovery
   gains require same-fingerprint identity and hard-slice evidence.
9. **Reconsider Phase 3 only if the measurements show one node cannot meet the
   approved targets.**

## 13. Design review checklist

- [ ] Every request has one resolved `Snapshot`.
- [ ] One release pointer is authoritative and rollback is one pointer change.
- [ ] Immutable artifacts are never rewritten by API actions.
- [ ] Mutable operational state has one transactional owner.
- [ ] Routes do not own SQL, model loading, or filesystem policy.
- [ ] Search remains candidate discovery; decisions remain evidence-controlled.
- [ ] Frontend has one transport seam and no data access bypass.
- [ ] Every resource has an application-lifespan owner.
- [ ] Security behavior differs explicitly between loopback and shared modes.
- [ ] No abstraction exists only for a hypothetical future implementation.
- [ ] Capacity, SLO, RPO, and RTO claims are measured or marked unknown.
