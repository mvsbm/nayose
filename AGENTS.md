# AGENTS.md — contributor contract

## Commands

```bash
source .venv/bin/activate
uv pip install -e '.[matcher,performance,train,serve]'  # reproducible fast serving/training extras
python -m pytest tests/ -q           # unit tests (fast, no external data)
ruff check .                          # lint (config in pyproject.toml)
ruff format --check .                 # formatting
mypy synthetic tests                  # type check (engine + tests only)
python scripts/generate_data.py --profile smoke   # headless pipeline (CLI == notebook contract)
python scripts/train.py --data-dir data/current --models-dir models --seed 42   # headless training + target-platform artifacts
python scripts/benchmark_runtime.py --iterations 20                         # cold/warm latency benchmark
python scripts/audit_decisions.py                # decision audit + promoted labels (label feedback loop)
python -m api.main                              # serve the dashboard (NAYOSE_DASH_PORT, NAYOSE_API_TOKEN, NAYOSE_WARM_EMBED)
curl http://127.0.0.1:8000/api/health            # readiness probe (auth-exempt)
```

## Layout

| Path | Responsibility |
|---|---|
| `synthetic/` | Engine: `config.py` (presets/rates/data incl. email domains, channel contact profiles, POLICY_PARAMS), `providers.py` (real data sources; persona Han-initial sanitizer), `realism.py` (phone styles + email domains), `policy.py` (policy aggregate roots + truth timeline states + temporal validation), `noise.py` (correlated dirty events + lineage; agent profiles consumed), `truth.py` (party, relationship, policy, and query ground-truth tables), `engine.py` (orchestration), `pipeline.py` (generate + single save contract) |
| `api/` | Serving: `main.py` (typed FastAPI routes + token gate), `contracts.py` (response schemas incl. admin job stages), `evidence.py` (visible fields + field comparison), `normalize.py` + `features.py` (shared primitives), `retrieval.py` (bundle-aware query encoding), `search.py` (embedding entity search), `query_service.py` + `repositories.py` (server-owned bundle reads), `matching.py` (scored evidence + async jobs + session threshold overrides), `admin.py` (generate/train jobs, settings, and audit coordination), `manual_store.py` (locked atomic parquet data entry), `decisions.py` (bound review state), `telemetry.py` (endpoint timing), `engine.py` (trained-artifact loading + fingerprint guard). |
| `tests/` | Unit tests only — must never require external data or downloads |
| `scripts/generate_data.py` | Headless generation CLI; writes the exact same tables as the notebook |
| `synthetic_data.ipynb` / `nayose.ipynb` | Thin drivers / reports (do **not** add logic here) |
| `data/raw` | Immutable reference-data cache (ken_all, personas, EDINET) — never mutate |
| `data/generations/gen_*` + `data/current` | Immutable sabotaged dataset products; `current` is the matcher consumer alias |
| `data/clean_generations/clean_*` + `data/clean_current` | Immutable streamable canonical entity products; clean-only source layer |
| `models/runs/run_*` + `models/current` + `registry.json` | Immutable model bundles, CPU-neutral indexes, and target-platform ANN artifacts |
| `data/`, `models/` | Gitignored runtime artifacts (regenerate, never commit) |

## Contract (do not break)

- The generator writes to a new `data/generations/gen_<ts>_<hash>/` and points
  `data/current` at it: `reference_records`, `incoming_queries` + core hidden
  tables under `ground_truth/` (`record_truth`, `party_truth`,
  `relation_truth`, `query_truth`, `evaluation_truth`, `field_lineage`) plus
  clean policy lifecycle tables (`policy_truth`, `policy_term_truth`,
  `policy_event_truth`, `policy_party_truth`, `policy_risk_truth`) and
  `generation_manifest.json` (with `data_fingerprint`).
- The matcher reads `data/current` and writes bundles under
  `models/runs/run_<ts>_<hash>`, promoting `models/current`.
- `NayoseEngine.load` refuses a bundle whose fingerprint doesn't match the
  reference — never disable that guard.
- Visible tables must never leak hidden columns
  (`truth_id`, `scenario`, `split`, `record_quality`, `transformation`, ...).
- Every record/query field must have a lineage row
  (`field_lineage`: item_id × field, canonical vs observed, owner, transformation).
- The notebook preflight invariants and `tests/` enforce this; run them before changing the schema.
- `tests/test_arch_contract.py` checks frontend port parity, the CSS class
  dictionary, no layout/stacking on bare element selectors, and no sticky table
  cells. It ships with the suite — drift fails `pytest`, never the browser.

## How to add a feature (repeatable path)

New VIEW (#/route):
1. `port.ts`: add the focused capability method(s) the view needs.
2. `api/main.py`: add the typed endpoint and call the relevant server-owned
   service (`api/query_service.py` for DuckDB reads); wrap with
   `op("name", lambda: ...)`; add a fixture-backed test in `tests/test_api.py`.
3. `ts/infra.ts`: implement the port method on `ApiAdapter` (+ memo key).
4. `ts/features.ts`: new `mountX(el, port, store)`; register in `app.ts` VIEWS +
   palette nav; add the sidebar link in `index.html`.
5. Tests: `MockPort` in `test/view.test.mjs` + a flow test; server test above.
6. `python scripts/audit_repo.py` — all gates green.

New decision/state (e.g., review verdicts):
- Store it in `Store` + `port.saveDecisions/loadDecisions` (server: models/decisions).
- Commands live in `store.ts`; keep undo (never mutate without a Command).

FIX a bug:
- Write the failing test first (unit `core`, functional `view`, API, or E2E).
- Reproduce, fix at the layer that owns the fact (port/backend/view), no shotgun edits.

Definition of done (EVERY change):
- `python scripts/audit_repo.py` PASS (ruff, mypy, tsc, npm test, pytest incl.
  schema-contract + frontend gate). E2E (NAYOSE_E2E=1) when touch server/urls/views.
- No new raw SQL on the client; no new dependencies without pyproject/package.json entry.

## Do not

- Do not add logic to notebooks (thin driver rule).
- Testing pyramid: unit (pure) + jsdom view tests are the workhorse; Playwright
  E2E (`frontend/test/e2e.test.mjs`, opt-in `NAYOSE_E2E=1`) is for stories that
  cross the real server⇄browser⇄persistence seam. `e2e-util.mjs` copies the
  trained bundle into a temp root (`NAYOSE_E2E_MODELS`/`NAYOSE_E2E_DATA`/`PORT`
  override) so repo artifacts are never mutated; CI runs E2E on the smoke
  bundle it just trains. A new user story needs a jsdom view test AND (when it
  spans the server) an E2E journey. E2E selectors use ids and semantic
  `data-*` hooks (e.g. `data-entity`), never `.class` chains.
- Bilingual labels go through `frontend/ts/i18n.ts` with BOTH locales; every
  matching/admin job `Stage` in `api/contracts.py` must have a label.
  `tests/test_arch_contract.py` enforces dictionary completeness and stage
  parity (drift fails the gate).
- Admin workspace (`#/admin`) and data entry (`#/data-entry`) are always OPEN
  as local-first surfaces; they have no separate admin credential. If the
  deployment is exposed beyond the trust boundary, use the shared
  `NAYOSE_API_TOKEN` bearer policy. Every admin operation is recorded in the append-only audit log
  (`models/admin_audit.jsonl`) regardless of auth. Manual entries live
  OUTSIDE the immutable generation (`data/manual`, atomic parquet writes) and
  are labeled un-trained — they must never break the fingerprint contract.
- `MemoProxy` caches reads; MUTATION-ADJACENT reads (manualList, adminState,
  loadDecisions) are pass-through — never cache state that an action mutates.
- E2E authoring (enforced by `tests/test_arch_contract.py`): journeys call only
  harness primitives from `frontend/test/e2e-util.mjs` — never raw
  `waitForFunction`/`reload`/context creation; every journey runs in its own
  fresh context via `runJourney`; selectors use ids and semantic `data-*`
  hooks, never structural paths under `#view`; assert domain semantics (e.g.
  an entity item exists), not incidental artifact counts.
- The repo-root engine (`nayose_inference.py`) and CLI (`nayose_cli.py`) are
  RETIRED: the engine lives at `api/engine.py`, the CLI is deleted. `api/` must
  never reference root-level legacy modules; shared normalization/features
  live in `api/normalize.py`/`api/features.py` and the engine re-exports them.
  `tests/test_arch_contract.py` enforces this boundary.
- Evidence-first UI: match results must show actual scored records, per-field
  comparisons, thresholds and the decision reason — never invented explanations
  or hidden synthetic labels. UI labels go through `frontend/ts/i18n.ts` (ja/en
  dictionary), never hardcoded Japanese strings; the language toggle must not
  translate customer data.
- Do not hardcode datasets/rates in engine code — extend `config.py`/providers.
- Do not commit `data/`, `models/`, `*.executed.ipynb`, or environment files.
- Do not install new deps into the shared `.venv` without adding them to
  `pyproject.toml` first.
- Do not change random-seed defaults silently — outputs must be seed-reproducible.
- Dirtying must stay **truth-driven and logical** (coherent events, exact
  lineage); never add label noise — truth tables are the ground truth.
- New corruption must register as an event in `noise.py` + `config.py` rates
  (OCP), and be explainable from the entity's truth values.
- Name realism: reading (kana) is identity; observed kanji may diverge through
  the data-driven homophone pools (`build_homophone_map`) and kana-form events.
- Missingness is SYSTEMATIC: tune `field_availability` per channel; legacy has
  no email (keep=0.0). Keep `channel_events` as the source-document bundles.
- Frontend is strict-TS only: edit `frontend/ts/`, run `npm --prefix frontend run
  typecheck`, keep `frontend/js/` generated (build & commit). Keep
  `frontend/ts/schema.ts` in sync with `api/main.py REQUIRED_COLUMNS`
  (test_schema_contract.py enforces).
- Frontend boundaries are explicit: views depend only on `port.ts` interfaces;
  state flows through `store.ts` reversible commands; `app.ts` assembles
  `boot({port, store, runs})` with no import side effects. New views → new
  feature module + focused port method; never call fetch/SQL/localStorage
  directly in views.
- Decision state persists server-side under `models/decisions/<run>.json`
  (GET/PUT /api/decisions); review updates remain undoable on the client.
- New port operations need ApiAdapter + MockPort implementations and a
  fixture-backed API test (server) + a mock-port view test (frontend).
- Server API is typed domain endpoints (no raw SQL to clients; `NAYOSE_DEBUG_SQL=1`
  only for debugging). Every endpoint records stats; keep the fake-bundle API
  tests green for new endpoints. Run `npm test` (tsc + node:test) — pytest's
  frontend gate enforces it.
- The web dashboard (`frontend/` + `api/main.py`) must keep the F1-F5 views,
  validate inputs, and stay no-build (vanilla ESM). DuckDB queries are
  SELECT/WITH-only over 'bundle/' and 'data/' roots with a row cap; the data
  adapter may move to DuckDB-WASM by swapping one function. URL-as-state and
  the a11y/AA + reduced-motion rules are non-negotiable.
- Keep `NAYOSE_EXACT_MODE` honored in graph + retrieval when running
  channel-attribution experiments; keep the HARDNESS telemetry print.
- Do not joblib-pickle the LightGBM pair model for new bundles — write the
  native text booster (`booster_.save_model`); pickle+torch(MPS) deadlock.
  For pure-sklearn serving bundles use `NAYOSE_PAIR_MODEL=sklearn`.
- Embeddings are cached per (data fingerprint, model id) inside the generation
  dir; do not recompute them for an unchanged generation.
## Retired (do not reintroduce)

- `nayose_cli.py` — obsolete CLI app layer, deleted. The engine moved from
  `nayose_inference.py` to `api/engine.py` (artifact loading + fingerprint
  guard). `api/engine.py` carries historical long lines/defensive
  naming; it is excluded from mypy and gets `E501/E402/B905/B007` per-file
  ruff ignores in pyproject.toml — new logic goes into `api/` + `frontend/` +
  `synthetic/`, and the arch contract forbids root-level legacy references.
