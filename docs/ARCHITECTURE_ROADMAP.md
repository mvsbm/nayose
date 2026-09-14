# Architecture delivery roadmap

This document converts the architecture review into an executable checklist from
Phase 0 through Phase 3. It is scoped to the current Nayose repository and keeps
the existing local-first design as the default. Each phase must pass its gate
before the next phase begins.

## Status and operating rules

- `[ ]` not started
- `[-]` in progress
- `[x]` verified complete
- `[!]` blocked or requires an explicit decision

This roadmap is a plan, not permission to weaken existing contracts. The
following invariants remain mandatory in every phase:

- Models and data are immutable, fingerprint-bound artifacts.
- A request never mixes data and model generations.
- Dense, fuzzy, and LLM retrieval discover candidates only; structured evidence
  and abstention control identity decisions.
- Hidden truth, policy data, and lineage columns never enter visible responses.
- Frontend views use `frontend/ts` ports and stores; generated `frontend/js` is
  updated by TypeScript compilation.
- `python -m api.cli` is the only batch entry point; implementation belongs in
  `api/`, `synthetic/`, or `api/commands/`.
- No new dependency is added without a `pyproject.toml` or
  `frontend/package.json` entry and a security/licensing review.
- Every change is independently testable and reversible.

## Target architecture

```text
                         immutable artifact store
                    data generations + model bundles
                                      |
                              ReleaseCatalog
                     one validated data/model snapshot
                                      |
                     +----------------+----------------+
                     |                                 |
              read-only API                       match gateway
       DuckDB + exact/fuzzy indexes          bounded warm inference workers
                     |                                 |
                     +----------------+----------------+
                                      |
                              typed API contracts
                                      |
                               frontend ports

     generator/trainer jobs -> durable job store/queue -> artifact publisher
                                      |
                    durable decisions, audit, and operational state
```

### Technology policy

- **Phase 0-2:** keep the FastAPI application, DuckDB, Parquet, and local
  filesystem. Use SQLite WAL for durable local operational state when a database
  is required.
- **Phase 3:** introduce PostgreSQL, a durable queue, shared artifact storage,
  and a reverse proxy only when measured workload or availability requirements
  justify them. Do not introduce microservices or Redis preemptively.
- Immutable analytical artifacts and transactional operational state are
  different storage classes and must not be forced into one abstraction.

## Repository ownership and naming

Prefer extending an existing owner module over creating a duplicate abstraction.
New modules must have one responsibility and follow the current layout:

| Concern | Primary owner | Planned additions or evolution |
|---|---|---|
| Runtime configuration | `api/infrastructure/runtime/performance.py`, new `api/runtime_config.py` if needed | Typed, validated configuration object; derive data root/snapshot consistently |
| Release and snapshots | `api/infrastructure/bundles/release.py`, `api/infrastructure/bundles/repositories.py` | `ReleaseCatalog` / `SnapshotResolver`; request-scoped snapshot value object |
| Read access | `api/infrastructure/read_model/bundle_reader.py`, `api/application/queries.py`, `api/infrastructure/read_model/*.py` | Read-only connection adapter and snapshot-bound query context |
| Search | `api/application/search.py`, `api/infrastructure/retrieval/entity_index.py`, `api/infrastructure/read_model/entity.py` | Offline exact/fuzzy indexes, typed candidate edges, cursor pagination |
| Matching lifecycle | `api/application/matching_runtime.py`, `api/application/matching.py`, `api/infrastructure/runtime/engine.py` | Warm engine handles, safe swap, bounded concurrency |
| Decisions | `api/infrastructure/persistence/decisions.py` | Transactional revisions, optimistic concurrency, manifest binding |
| Jobs | `api/infrastructure/process/admin_jobs.py`, `api/application/matching_runtime.py` | Durable job repository, worker protocol, retries and heartbeats |
| Audit | `api/application/admin.py` initially; extract `api/infrastructure/persistence/audit.py` when needed | Durable, tamper-evident, PII-safe audit events |
| API composition | `api/composition.py`, `api/http/app.py`, `api/main.py` | Dependency wiring, app factory, lifespan ownership, and process entrypoint |
| Batch workflows | `api/cli.py`, `api/commands/` | lifecycle, release, audit, and benchmark command families |
| Tests | `tests/` | Unit and fixture-backed integration tests; no external downloads |
| Operations docs | `docs/` | Update `OPERATIONS.md`, `CLOUD.md`, and this roadmap together |

Canonical command families:

- `python -m api.cli release validate`: validate a complete data/model release;
  never mutate aliases.
- `python -m api.cli benchmark runtime`: measure repeatable latency, memory, and
  process behavior over a fingerprint-bound bundle.
- Job recovery remains in the durable worker service; add no recovery script.

Do not create generic modules named `utils.py`, `helpers.py`, or `common.py`.

## Phase 0 — release, consistency, and security safety

**Objective:** make every served result belong to one validated snapshot and
make unsafe deployment configurations fail closed.

### 0.1 Establish requirements and capacity targets

- [ ] Record supported deployment modes: local single-user, trusted LAN, and
  hosted multi-user.
- [ ] Define expected concurrent users, read/search RPS, recommendation RPS,
  generation frequency, and maximum dataset sizes.
- [ ] Define p50/p95/p99 latency targets separately for exact search,
  semantic search, recommendation, and administrative reads.
- [ ] Define availability, RPO, RTO, retention, and backup requirements for
  each deployment mode.
- [ ] Define memory and disk budgets for 100K, 500K, and 2M records.
- [x] Record the current limitation explicitly: the clean-only 2M path is an
  implementation route, not a capacity certification; full sabotaged generation
  and full matcher serving require a separate target-runner sign-off.
- [ ] Add these assumptions to `docs/OPERATIONS.md` without presenting
  estimates as measured results.

**Acceptance:** a reviewed capacity/SLO table exists and every later benchmark
uses the same workload definitions.

### 0.2 Introduce one snapshot contract

- [ ] Add a typed snapshot value object containing release ID, run ID,
  generation ID/path, model path, data fingerprint, schema versions, and
  runtime artifact identities.
- [ ] Add `SnapshotResolver` behind an interface; explicit historical runs must
  resolve their data generation by fingerprint.
- [ ] Make `BundleReader`, entity queries, graph queries, evaluation queries,
  and matching receive the resolved snapshot rather than independently reading
  `DATA_DIR` or a `current` symlink.
- [ ] Derive `DATA_DIR`, `DATA_ROOT`, and generation roots from one validated
  runtime configuration object. Reject contradictory environment settings.
- [ ] Return the snapshot/release ID and fingerprint in relevant API metadata.
- [ ] Keep `NayoseEngine.load` fingerprint validation as a second defense;
  never remove it.

**Acceptance:** an old run cannot read current-generation records; a request
that starts during alias rotation observes one fixed snapshot; mismatches return
an explicit 409/503-style domain error.

### 0.3 Make publication atomic and recoverable

- [ ] Validate generation completeness, manifest presence, schema, checksums,
  fingerprint, model compatibility, and target-platform ANN artifacts before
  publication.
- [x] Implement `python -m api.cli release validate` as a read-only preflight command.
- [ ] Publish a release manifest containing the complete data/model pair.
- [ ] Atomically update one release pointer after all artifacts are durable.
- [ ] Preserve the previous release pointer for rollback.
- [ ] Add crash/fault tests for every publication step, including partial files,
  stale manifests, interrupted alias updates, and rollback.
- [ ] Document local filesystem publication and object-storage publication
  separately; do not describe object-store versions as POSIX symlinks.

**Acceptance:** no partial release becomes visible, rollback is one pointer
change, and a failed publication leaves the previous release serviceable.

### 0.4 Close security boundaries

- [ ] Fail startup when `NAYOSE_BIND_HOST` is non-loopback and no authenticated
  deployment mode is configured.
- [ ] Separate liveness from authenticated readiness; do not expose filesystem
  paths or detailed fingerprints on a public health endpoint.
- [ ] Replace client-supplied `X-Reviewer`/`X-Actor` trust with an authenticated
  principal. Keep role authorization distinct from authentication.
- [ ] Remove `/api/query` from production builds, or enforce a read-only SQL
  AST allowlist over approved bundle/data roots with a row/time cap.
- [ ] Validate all admin paths against configured roots; accept IDs and known
  enums rather than arbitrary filesystem paths.
- [ ] Add request body, field, list, pagination, timeout, and concurrency caps
  to every endpoint, especially decisions, imports, admin jobs, and review.
- [ ] Add rate limiting and retry/backoff guidance at the reverse-proxy or API
  boundary.
- [ ] Add TLS, security headers, trusted-host configuration, and secret-manager
  guidance for shared deployments.
- [ ] Mount PII-bearing data read-only where possible; define encryption,
  retention, backup, and log-redaction policy.

**Acceptance:** security tests cover auth fail-open cases, path traversal, raw
SQL/file reads, spoofed identities, oversized bodies, and rate limits.

### 0.5 Strengthen contracts and tests

- [ ] Add explicit API response models for search, review, metrics, confusion,
  compare, runs, and decisions.
- [ ] Add a response `schema_version` where compatibility will matter.
- [ ] Type candidate edges with `record_id`, `entity_id`, channel, view, rank,
  score, alias, confidence, and evidence source.
- [ ] Add contract tests for snapshot binding and hidden-column exclusion.
- [ ] Add migration/compatibility tests for old valid bundles and invalid
  manifests.

**Phase 0 gate:**

- [x] `python -m api.cli release validate` passes on a valid fixture.
- [ ] Historical-run and alias-rotation tests pass.
- [ ] Security boundary tests pass.
- [ ] `source .venv/bin/activate && python -m api.cli audit repo` passes after
  all Phase 0 changes.
- [ ] A rollback drill succeeds without mutating immutable artifacts.

## Phase 1 — reliable single-node operation

**Objective:** preserve the simple local deployment while making writes,
long-running work, caching, and inference lifecycle durable and observable.

### 1.1 Durable operational state

- [ ] Choose SQLite WAL as the single-node operational store and define the
  PostgreSQL adapter boundary for Phase 3.
- [ ] Store decisions, decision revisions, reviewer identity, snapshot binding,
  and optimistic-concurrency version in one transaction.
- [ ] Replace destructive manual undo with a compensating event or tombstone.
- [ ] Store manual records in an append-only staging table/log; materialize them
  into a future immutable generation.
- [ ] Make duplicate validation fail closed when the reference snapshot cannot
  be opened.
- [ ] Replace process-local audit locking with transactional writes.
- [ ] Add event IDs, authenticated actor IDs, timestamps, release IDs, and
  redacted structured details to audit events.
- [ ] Add integrity protection such as a hash chain or signed export and define
  rotation/retention.

**Acceptance:** concurrent writes are serialized transactionally, crash recovery
never leaves decision data without its binding, and undo is auditable.

### 1.2 Durable jobs and back pressure

- [ ] Define a durable job state machine shared by admin and matching jobs:
  queued, running, progress, succeeded, failed, cancelled, expired.
- [ ] Persist input parameters, snapshot/release ID, actor, attempt count,
  heartbeat, error class, and result reference.
- [ ] Replace in-memory-only job status with a repository interface and SQLite
  implementation.
- [ ] Add a bounded queue, admission control, idempotency key, retry policy,
  cancellation, and stale-worker recovery.
- [ ] Keep generation/training in subprocess workers, never in the API request
  process.
- [ ] Limit one expensive generation/training job per resource pool while
  permitting bounded read traffic.
- [x] Keep job recovery in the durable worker service; no operator script is needed;
  recovery must be idempotent.

**Acceptance:** API restart does not erase jobs, duplicate submission does not
run work twice, and queue saturation returns a clear retryable response.

### 1.3 Safe engine lifecycle and resource isolation

- [ ] Define an immutable `EngineHandle` bound to one snapshot and runtime
  contract.
- [ ] Warm the selected engine before readiness reports ready; do not perform a
  multi-minute model load inside the first user request.
- [ ] Swap engines atomically only after load, schema, fingerprint, and health
  checks pass.
- [ ] Retire old engines after all in-flight requests and search preparation
  tasks release their handles.
- [ ] Prevent a shared query encoder from serving a different model than the
  request snapshot.
- [ ] Add bounded inference concurrency and explicit CPU/GPU/memory budgets.
- [ ] Ensure engine replacement releases old ANN, model, encoder, and mapped
  resources deterministically.
- [ ] Add tests for concurrent recommendation, search preparation, reload, and
  shutdown.

**Acceptance:** no cross-model encoder use, no use-after-retire behavior, and a
reload does not cause an unbounded memory spike.

### 1.4 Correct cache behavior

- [ ] Change `BundleReader.cached()` so cache-miss I/O runs outside the global
  lock.
- [ ] Add single-flight loading for the same key and safe negative/error cache
  behavior.
- [ ] Key caches by release ID/fingerprint, operation, and complete parameters.
- [ ] Bound caches by bytes or object cost, not only entry count.
- [ ] Do not cache massive fuzzy-choice lists as ordinary request objects.
- [ ] Add cache hit, miss, eviction, load time, and size telemetry.
- [ ] Add TTL/invalidation for frontend health/run metadata; never cache release
  state forever.

**Acceptance:** concurrent cache misses do not serialize unrelated requests and
cache memory remains below its configured budget.

### 1.5 Operational observability

- [ ] Replace aggregate-only `Stats` with counters, error counts, latency
  histograms, p95/p99, in-flight work, queue depth, and cache metrics.
- [ ] Attach correlation ID, request ID, release ID, run ID, and fingerprint to
  structured logs without logging customer PII.
- [ ] Trace search stages and matching stages separately.
- [ ] Add alerts for fingerprint mismatch, model unavailable, queue saturation,
  stale jobs, memory pressure, and release age.
- [ ] Add a dashboard/runbook section for each alert and its rollback action.

**Phase 1 gate:**

- [ ] API restart recovery test passes for decisions and jobs.
- [ ] Engine reload and cache contention tests pass.
- [ ] Local soak test meets the Phase 0 SLOs without memory growth.
- [ ] Operational runbook documents backup, restore, rollback, and recovery.
- [ ] Full repository audit passes after the phase is complete.

## Phase 2 — large-dataset performance and portability

**Objective:** make the read and candidate-discovery paths predictable at the
supported dataset sizes without weakening identity safety.

### 2.1 Offline exact and fuzzy indexes

- [ ] Build normalized field indexes during bundle materialization for name,
  kana, phone, email, postal code, DOB, policy ID, and agency ID.
- [ ] Store index metadata with row counts, field normalization version,
  fingerprint, schema version, and artifact checksum.
- [ ] Replace repeated `regexp_matches()` full scans with index lookups where
  semantics permit; retain a bounded fallback with explicit telemetry.
- [ ] Replace explorer/review `OFFSET` pagination with stable keyset cursors.
- [ ] Include tie-break columns in every cursor and reject stale/invalid cursors.
- [ ] Replace the 500,000-row fuzzy linear choice list with a bounded n-gram or
  equivalent indexed search strategy.
- [ ] Preserve complete retrieval provenance before truncating response rows.

**Acceptance:** search latency does not grow linearly with corpus size for the
indexed query classes, and every returned candidate retains complete evidence.

### 2.2 Dense artifact memory and ANN portability

- [ ] Change record-level dense loading to memory mapping where supported.
- [ ] Stream ANN construction in chunks or shards; never require a second full
  float32 matrix for a 2M corpus.
- [ ] Define shard routing and merge semantics for top-k results.
- [ ] Bind every dense artifact to fingerprint, model ID, dimensions, dtype,
  index backend, platform, and library version.
- [ ] Validate Linux x86_64, Linux ARM64, macOS ARM64, CPU, CUDA, and MPS
  artifacts or document unsupported combinations.
- [ ] Add artifact corruption, truncated mmap, wrong-platform, wrong-dimension,
  and wrong-version tests.
- [ ] Measure index build time, peak RSS, disk footprint, warm load time, and
  query latency.

**Acceptance:** the documented dataset size either meets the memory budget or
is explicitly rejected before materialization; no silent brute-force fallback
occurs.

### 2.3 Retrieval and entity aggregation quality

- [ ] Make `QueryPlan` executable by all retrievers; do not re-interpret raw
  text independently in each backend.
- [ ] Implement explicit populated-field dense-view selection and verify that
  corpus/query text shapes remain identical.
- [ ] Add the typed candidate-edge provenance contract.
- [ ] Evaluate SVD, sparse-only, dense-only, full-record, name, address, alias,
  and hybrid retrieval on the same fingerprint.
- [ ] Report stratified Recall@1/5/20, candidate recall, entity accuracy,
  auto-link precision, false-auto rate, latency, memory, and provenance traces.
- [ ] Add channel agreement, missingness-aware aggregation, hard-negative
  training, and twin/household hard slices without allowing dense evidence to
  make identity decisions.
- [ ] Keep the tightened promotion gate as a required release check.

**Acceptance:** every candidate-discovery improvement is accompanied by
same-fingerprint safety and hard-slice results; no model is promoted from
aggregate retrieval gains alone.

### 2.4 Query and dataset read optimization

- [ ] Precompute generation counts, summary statistics, score histograms, and
  schema descriptors in manifests or compact summary artifacts.
- [ ] Avoid exact total counts on every filtered page; make exact counts an
  explicit bounded operation where needed.
- [ ] Configure DuckDB read-only mode, memory limit, temp directory, and thread
  budget per process.
- [ ] Add a query semaphore/back-pressure policy for expensive analytical reads.
- [ ] Add cancellation and deadline propagation from HTTP request to query and
  worker layers.
- [ ] Benchmark cold cache, warm cache, concurrent reads, and alias rotation.

**Phase 2 gate:**

- [ ] `python -m api.cli benchmark runtime` produces reproducible reports for
  100K, 500K, and a target-runner-approved 2M path.
- [ ] Memory, p95/p99 latency, throughput, and error budgets meet the approved
  capacity table.
- [ ] ANN portability checks pass for every supported target.
- [ ] Retrieval and identity promotion gates pass on fresh same-fingerprint
  data.
- [ ] Full repository audit passes.

## Phase 3 — scale-out and hosted availability

**Objective:** add horizontal capacity and failover without reintroducing local
state, mixed snapshots, or unsafe writes.

Phase 3 is conditional. Start only after Phase 2 measurements show that one
node cannot meet the approved workload or availability target.

### 3.1 Shared artifact and release storage

- [ ] Choose versioned object storage for immutable generations, model bundles,
  indexes, and manifests.
- [ ] Implement an artifact adapter behind the existing repository boundary;
  local filesystem remains a test/development adapter.
- [ ] Use checksums, content length, encryption, retention, and lifecycle rules.
- [ ] Store a versioned release manifest as the authoritative data/model pair.
- [ ] Make publishers single-writer and idempotent.
- [ ] Add staged upload, validation, promotion, rollback, and garbage
  collection with release pinning.
- [ ] Keep active releases from being deleted while API workers hold them.

**Acceptance:** a new API replica can fetch and validate any active release
without local symlink assumptions; rollback works across replicas.

### 3.2 Stateless API replicas

- [ ] Remove request/job/decision state from process memory.
- [ ] Resolve a release once per request and use only that snapshot.
- [ ] Add readiness that verifies artifact availability and warm-engine state;
  liveness must not depend on the model store.
- [ ] Set per-replica CPU, memory, file-descriptor, DuckDB, and inference
  concurrency limits.
- [ ] Verify that replicas do not each load unbounded copies of the same dense
  artifacts.
- [ ] Add graceful shutdown and connection draining.

**Acceptance:** multiple replicas pass concurrent consistency tests and a
replica can be removed without losing jobs, decisions, or audit events.

### 3.3 Durable queue and dedicated workers

- [ ] Choose a durable queue appropriate to the deployment; document delivery
  semantics, visibility timeouts, retries, and dead-letter handling.
- [ ] Run generation/training workers separately from API replicas.
- [ ] Run matcher workers separately when model memory or GPU scheduling
  warrants it.
- [ ] Pass immutable job input references and release IDs, not large request
  payloads through queue messages.
- [ ] Make workers idempotent and publish results only after validation.
- [ ] Add queue depth, age, retry, dead-letter, and worker utilization metrics.
- [ ] Apply back pressure at API admission and queue publication.

**Acceptance:** worker failure, duplicate delivery, retry, and dead-letter
scenarios are recoverable without duplicate publication or lost decisions.

### 3.4 Transactional operational database

- [ ] Implement the PostgreSQL adapter for decisions, jobs, audit, and release
  metadata using the same focused interfaces used by SQLite.
- [ ] Define migrations under a repository-owned migration directory; do not
  edit production schemas manually.
- [ ] Add primary keys, unique idempotency keys, foreign keys, revision checks,
  indexes, and retention jobs.
- [ ] Back up and restore the database; test point-in-time recovery if required.
- [ ] Ensure audit records are durable before returning success for mutations.

**Acceptance:** concurrent multi-replica decision updates have deterministic
conflict behavior and restore drills meet the approved RPO/RTO.

### 3.5 Network edge and deployment

- [ ] Put TLS termination, request limits, compression, rate limiting, and
  access logging at a managed reverse proxy/load balancer.
- [ ] Route only to healthy replicas using readiness checks.
- [ ] Configure rolling deployment, connection draining, and automatic rollback
  on health or promotion-gate failure.
- [ ] Separate read-only artifact mounts/cache from writable operational state.
- [ ] Remove recursive startup `chown` of large immutable data/model volumes;
  provision ownership and writable paths ahead of time.
- [ ] Build minimal serving images with only serving dependencies and a pinned
  lockfile.
- [ ] Scan images and dependencies; generate SBOMs and sign release images.
- [ ] Define network policies, secret rotation, least-privilege service
  accounts, and encrypted artifact/database access.

**Acceptance:** deployment can scale replicas, survive one replica failure,
roll back the release, and preserve auditability without manual filesystem
repair.

### 3.6 Hosted operations and disaster recovery

- [ ] Define dashboards and alerts for API SLOs, queue health, model load,
  artifact fetches, release age, database health, and resource saturation.
- [ ] Test backup restore, artifact loss, queue loss, database failover, and
  complete-region or host failure according to the availability target.
- [ ] Document incident response, release rollback, model rollback, data
  rollback, and credential rotation.
- [ ] Run a periodic disaster-recovery drill and record results.

**Phase 3 gate:**

- [ ] Multi-replica load and consistency tests pass.
- [ ] Failure injection and rollback drills pass.
- [ ] Security review and dependency/image scans pass.
- [ ] RPO/RTO and availability targets are demonstrated, not inferred.
- [ ] Full repository audit and deployment smoke test pass.

## Cross-cutting definition of done

For every implementation slice:

- [ ] Create a short-lived branch named `<type>/<short-description>`.
- [ ] Write the failing unit, contract, or integration test first for bug fixes.
- [ ] Keep one responsibility per module, service, and commit.
- [ ] Use ports/interfaces at API, storage, queue, and model boundaries.
- [ ] Prefer composition and adapters over inheritance or global singletons.
- [ ] Keep configuration typed, validated, and centralized.
- [ ] Add API, storage, migration, and rollback documentation with the change.
- [ ] Update `frontend/ts` first and compile generated `frontend/js` when the
  frontend contract changes.
- [ ] Verify no secrets, runtime artifacts, or generated data/models are staged.
- [ ] Run the smallest relevant tests after each increment, then the full
  repository audit before merge.
- [ ] Use E2E only for changes crossing the real server/browser/persistence
  boundary; keep unit and fixture-backed tests as the workhorse.
- [ ] Record measured latency, throughput, memory, and safety metrics for every
  performance or model change.

## Suggested dependency order

```text
0.1 requirements
  -> 0.2 snapshot contract
  -> 0.3 atomic publication
  -> 0.4 security
  -> 0.5 contracts/tests

0.x
  -> 1.1 durable state
  -> 1.2 durable jobs
  -> 1.3 engine lifecycle
  -> 1.4 cache
  -> 1.5 observability

1.x
  -> 2.1 exact/fuzzy indexes
  -> 2.2 dense memory/ANN
  -> 2.3 retrieval evaluation
  -> 2.4 query optimization

2.x capacity decision
  -> 3.1 artifact storage
  -> 3.2 API replicas
  -> 3.3 workers/queue
  -> 3.4 operational database
  -> 3.5 network/deployment
  -> 3.6 disaster recovery
```

## Git execution checklist

The current repository contains unrelated uncommitted work. Do not use
`git add -A`, `git reset --hard`, or broad cleanup commands.

### Before each change

- [ ] `git status --short`
- [ ] `git diff --stat`
- [ ] `git diff --check`
- [ ] Confirm the branch contains only the intended workstream or create a
  short-lived branch from the appropriate base.
- [ ] Identify the exact files to change and their existing owner.

### Commit sequence

Use focused Conventional Commits, for example:

1. `docs: add architecture delivery roadmap`
2. `test(release): cover snapshot publication failures`
3. `feat(api): bind queries to release snapshots`
4. `feat(ops): persist jobs and decisions transactionally`
5. `perf(search): add indexed cursor retrieval`
6. `feat(deploy): add durable worker deployment`

- [ ] One logical change per commit.
- [ ] Subject is under 72 characters and explains the intent.
- [ ] `git diff --cached` contains only the commit's files.
- [ ] No unrelated pre-existing changes are staged.
- [ ] Focused tests pass before committing.
- [ ] Commit body records the reason, safety invariant, and verification.
- [ ] Review `git show --stat --check HEAD` after committing.
- [ ] Rebase only after preserving local work and only when a remote/base is
  available.
- [ ] Never commit `data/`, `models/`, `.env`, or credentials.

## Explicit non-goals

- [ ] Do not split the application into microservices before Phase 2 capacity
  evidence.
- [ ] Do not replace Parquet/DuckDB immutable analytical storage with a
  transactional database merely for architectural fashion.
- [ ] Do not make dense similarity, LLM output, or fuzzy similarity an identity
  decision.
- [ ] Do not make `current` aliases authoritative independently of a complete
  fingerprint-bound release.
- [ ] Do not trade away lineage, hidden-truth isolation, or false-auto safety
  for throughput.
