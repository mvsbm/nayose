# Evidence-first matching UI

## User workflow

- **Search existing customers** searches resolved entities. **Match input information** submits a read-only recommendation request; it does not merge or modify customer records.
- The **日本語 / English** button translates navigation, forms, help, status, errors and evidence labels. The preference is stored locally. Customer names, addresses, IDs and other source values are not translated. Matching drafts and results survive language changes.
- Entity members use server-side pagination with stable record-ID ordering, an actual total and page-size controls. Expand a row for all 14 visible fields. Select two records, including across pages, to open the comparison view.
- Sample input comes from a visible record in the active dataset. Re-matching a known record demonstrates serving; it is not an accuracy evaluation.

## What evidence means

Exact match, normalized match, different and not comparable are distinct states. Missing values are not disagreements. The comparison includes raw and normalized values. These comparisons are not feature attributions, verified identity, or proof of a historical merge.

A recommendation includes the actual scored source records, their model input features, calibrated pair scores, alternative entities, thresholds and the reason for the outcome. Entity scores average up to the three highest pair scores. The runner-up gap is displayed as context, not a decision condition. Reranking is disabled on this serving path. Scoring errors fail visibly rather than substituting a retrieval score labeled as a probability.

The current bundle does not export historical merge paths. Entity detail states that limitation instead of assigning an invented AUTO/REVIEW status. Hidden synthetic field lineage is not included in operational entity evidence; evaluation remains a separate workflow.

## Backend boundaries

- `api/evidence.py`: visible-field whitelist and field comparison, reusing existing normalization.
- `api/retrieval.py`: bundle-aware query encoding and retrieval. SVD bundles use their saved `dense_svd.joblib`; neural bundles use their recorded model ID. Embedding dimensions are checked before ANN search. `NAYOSE_EXACT_MODE` and `NAYOSE_USE_DENSE` remain effective.
- `api/search.py`: entity search ranking from the loaded engine's embeddings
  (entity centroids over per-record vectors); typed-search hybrid — exact LIKE
  hits are always preserved, semantic similarity scores/order added once the
  engine is loaded (`NAYOSE_WARM_EMBED=1` or after a match request).
- `api/matching.py`: scoring orchestration and bounded background request lifecycle, using `api/engine.py.load_engine` (which performs the corpus fingerprint guard).
- `api/contracts.py`: response schemas.
- `frontend/ts/port.ts` / `infra.ts`: typed ports and transport; views contain no SQL or network calls.
- `frontend/ts/matching-state.ts`: matching lifecycle, polling and stale-run protection.
- `frontend/ts/i18n.ts`: bilingual message dictionary, independent of source data.

## API and lifecycle

- `GET /api/entity/{id}?run_id=...&offset=0&limit=25`
- `GET /api/match-sample?run_id=...`
- `POST /api/recommendations` with `{run_id, record}` returns HTTP 202 and a request ID.
- `GET /api/recommendations/{id}` reports actual stages, elapsed time, result or a safe error code.
- `POST /api/recommend` remains available for synchronous clients.

Stages are queued, validating, loading, retrieving, scoring, aggregating, complete or failed. No percentage or ETA is invented. Empty matching input completes without loading or waiting behind a model initialization. The browser polls for up to five minutes, then offers a status check without submitting a duplicate request.

The job registry is **in-memory, single-process**, with one background worker and at most 32 retained jobs. Completed results are eligible for eviction after 30 minutes or under capacity pressure. A restart loses job IDs. Multi-process or replicated deployment requires request affinity or a shared job store; this implementation is not a distributed task queue. Job results contain source data; use the same access restrictions as the dataset. Logs contain request IDs, stage timings and exception types, not input values.

## Verification

Focused tests: `tests/test_api.py`, `tests/test_matching.py`, `tests/test_arch_contract.py`, and `frontend/test/view.test.mjs`. Type-check with `npm --prefix frontend run typecheck`. Regenerate committed browser assets with `npm --prefix frontend run build` before serving the updated UI; then run `python scripts/audit_repo.py` and the relevant browser tests.

An existing smoke bundle and the 374,253-record bundle were exercised through the new backend. Both passed fingerprint verification and returned scored evidence using the saved SVD transform. The larger cold request took approximately 134 seconds, mostly initialization; this is an observation from the local machine, not a latency guarantee. Neural/CUDA serving was not validated by these probes.
