# Nayose — Japanese Insurance Entity Resolution

Entity matching system for Japanese insurance customer data. Resolves duplicate records across policies, agencies, and channels.

## For contributors / agents

Read **ARCHITECTURE.md** (one page) and **AGENTS.md** (rules + "how to add a
view/endpoint/decision" checklists). One command for status:

```bash
python scripts/audit_repo.py       # ruff, mypy, tsc, npm test, pytest (+ E2E opt-in)
```

The original root-level app layer (`nayose_cli.py`) was retired and the engine
migrated to `api/engine.py`; all behavior lives in `api/`, `frontend/`,
`synthetic/`.

## Quick Start

> Reproducible environments: open this repo in **VSCode Dev Containers** (`.devcontainer/`)
> for a configured dev box, or pull the tagged image from GHCR. Git/CI/Docker/cloud
> workflow: **`docs/CLOUD.md`**; ops runbook: **`docs/OPERATIONS.md`**;
> GPU training: **`docs/REMOTE_GPU.md`**. Evidence-first matching and the
> Japanese/English UI: **[`docs/MATCHING_UI.md`](docs/MATCHING_UI.md)**.
> 5-minute stakeholder demo: **[`docs/DEMO.md`](docs/DEMO.md)**.

Both notebooks read `data/current` by default. The generator writes the visible
snapshots (`reference_records`, `incoming_queries`) plus core hidden truth
tables and clean policy lifecycle tables under `data/ground_truth/`.

### 1. Generate Training Data

```bash
source .venv/bin/activate
uv sync --extra matcher --extra performance --extra train --extra serve --extra dev
python scripts/generate_data.py --profile smoke --data-dir data --seed 42
# profiles: ~340 / ~800 / ~6,800 / ~22,700 / ~113,500 canonical entities
# one-time downloads, cached in data/raw/: Nemotron persona names, Japan Post ken_all, EDINET
```

For a large canonical layer, stream clean entities without retaining sabotaged
records, policies, and lineage in memory:

```bash
python scripts/generate_data.py --clean-only --n-parties 2000000 \
  --chunk-size 50000 --seed 42 --data-dir data
# writes data/clean_generations/clean_<timestamp>_<hash>/ and updates data/clean_current
```

The full generation path remains capped at 2,000,000 base entities because it
materializes all observations, policy lifecycle tables, and field lineage before
saving. The clean-only path is chunked and is the validated route for a 2M
canonical entity layer; it does not replace `data/current`.

### 2. Train / Evaluate the Matcher

```bash
# Supported headless path: executes the notebook, validates the bundle, and
# materializes the target-platform exact/ANN artifacts.
python scripts/train.py --data-dir data/current --models-dir models \
  --seed 42 --device cpu --model-family off
```

### 3. Run Inference

`scripts/train.py` executes `nayose.ipynb`, then writes a model bundle (models
+ portable indexes + target-platform ANN + run_manifest) into its output
directory. `NayoseEngine.load` **fingerprint-checks the bundle against
the reference data** and refuses stale combinations:

```python
from api.engine import load_engine

engine = load_engine()  # models/ + data/ (after a fresh nayose run)
result = engine.recommend({"name_kanji": "山田太郎", "phone": "090-1234-5678"})
print(result.action)  # AUTO_LINK
print(result.top_nayose_id)  # N000012345
```

Headless training and serving (the supported paths — using the same bundle
contract as the notebooks):

```bash
# then open http://127.0.0.1:8899
NAYOSE_DASH_PORT=8899 NAYOSE_WARM_EMBED=1 python -m api.main
```

## Project Structure

```
├── synthetic_data.ipynb        # Data generation pipeline
├── nayose.ipynb                # Model training & evaluation
├── api/                        # Serving (FastAPI) + matching engine
│   ├── main.py                 #   typed routes, composition, auth
│   ├── query_service.py        #   server-owned bundle reads
│   ├── admin.py                #   jobs, settings, and audit coordination
│   └── engine.py               #   trained-artifact loading + fingerprint guard
│
├── data/                       # Training data (gitignored)
│   ├── raw/                        # immutable reference data (ken_all, personas, EDINET)
│   ├── generations/gen_<ts>_<hash>/   # one immutable dataset product per run
│   │   ├── reference_records.parquet / incoming_queries.parquet / agencies.parquet
│   │   └── ground_truth/          # identity + clean policy truth tables
│   └── current -> generations/gen_<ts>_<hash>   # promoted generation (consumer-facing)
│       ├── record_truth.parquet
│       ├── party_truth.parquet
│       ├── relation_truth.parquet
│       ├── query_truth.parquet
│       ├── evaluation_truth.parquet
│       ├── field_lineage.parquet  # per-field provenance audit (1.9M rows @100k)
│       ├── policy_truth.parquet / policy_term_truth.parquet
│       ├── policy_event_truth.parquet / policy_party_truth.parquet
│       └── policy_risk_truth.parquet
│
└── models/                     # Trained artifacts (gitignored)
    ├── runs/run_<ts>_<hash>/   # immutable bundle + run_manifest.json
    ├── current -> runs/...     # validated serving alias
    ├── registry.json           # append-only run metadata
    └── decisions/<run>.json    # review state bound to that run manifest
```

## Architecture

| Component | Responsibility |
|-----------|----------------|
| `InferenceConfig` | Immutable serving settings |
| `CandidateIndex` | Candidate retrieval |
| `NayoseEngine` | Artifact loading, validation, and recommendation |
| `normalize_*()` | Field normalization |

## Model family

`NAYOSE_MODEL_FAMILY` defaults to `off`, the reproducible CPU/SVD baseline. Neural families are explicit opt-ins:

| Family | Embedding | Notes |
|---|---|---|
| `off` (default) | — | Deterministic SVD baseline |
| `f2llm_330m` | `codefuse-ai/F2LLM-v2-330M` | Recommended fixed dense candidate-retrieval model |
| `f2llm` | `codefuse-ai/F2LLM-v2-1.7B` (or `NAYOSE_EMBEDDING_MODEL`) | Optional dense retrieval; weights are downloaded and cached by the runtime |
| `f2llm_0.6b` | `codefuse-ai/F2LLM-v2-0.6B` | Smaller optional model |
| `ruri` | Ruri encoder/reranker path | Optional A/B entry |
| `qwen` | Qwen embedding/reranker path | Optional |

The neural paths are engineering alternatives, not a benchmark claim. Record the selected family, backend, device, and degraded status in the run manifest before comparing runs.

Optimizations for MPS + time complexity:

- embedding encode on **MPS in fp16**, batched (`NAYOSE_NEURAL_BATCH`);
- **reference embeddings cached on disk** keyed by (data fingerprint, model id) — encode once per generation, later runs are O(reload);
- **usearch (HNSW)** ANN index for dense retrieval instead of brute-force;
- exact blocks + character sparse retrieval remain as high-precision anchors.

> One-time cold-encode cost is paid once per immutable generation and cached by
> `(data_fingerprint, model_id)`. Use `f2llm_330m` for the production candidate
> path; benchmark the actual device before selecting batch sizes.

### Realism upgrades: kana-driven names + source-driven corruption (config-tuned)

Reading (kana) is the identity; observed kanji may diverge realistically:

- **同音異字 (homophone kanji)**: same kana reading, different kanji, drawn from
  the REAL name corpus (e.g. 前田道枝 → 前田美知恵, both マエダ ミチエ). Pools are
  built data-driven from the persona cache (`build_homophone_map`), no lexicon.
- **kana forms**: same reading rendered hiragana or half-width katakana.
- **digit transposes / email typos**: the classic typist mistakes.
- **Systematic (vintage/system) field availability**: legacy records never hold
  email (keep=0.0), paper rarely do (0.45), web always -- missingness is
  channel/system-driven, not uniformly random.
- **Source-document bundles**: a record typed from a channel's form carries the
  channel's characteristic disorders together (web: email+digit typos;
  agent_pc: homophone+digit; paper_ocr: OCR+kana-form; legacy: era+width+no email).

Tuning: every rate/table lives in `NoiseConfig` in `synthetic/config.py`
(`event_rates`, `field_availability`, `channel_events`, `band_scale`,
`missing_rates`) and the twin rate via `NAYOSE_TWIN_RATE`. Truth stays
canonical (party_truth) and lineage-exact; records are explainable derivations.

## Hardness engineering (organized, truth-driven chaos)

The synthetic dirty data is **driven from truth, not random noise**:
every observed record is a *logical, explainable derivation* of its canonical
entity (name renames, moves, re-contacts, pre-update snapshots, OCR scans,
legacy era formats), and the truth tables + `field_lineage` remain the exact
answer for each value.

Hardness levers (all in `synthetic/noise.py` / `engine.py`):

- **Coherent events**: `rename_stale`, `move_stale`, `contact_change`,
  `full_snapshot` (pre-move/pre-re-contact record using the entity's old
  values), `contact_omission` (phone+email missing together), `paper_ocr`,
  `legacy_format`, `doc_damage` — each internally consistent and labelled.
- **Twin entities** (`NAYOSE_TWIN_RATE`, default 0.15): additional customers
  with the SAME name/kana/romaji but different dob/address/contacts —
  `hard_case=twin`, exact-name anchors become ambiguous.
- **Channel attribution**: `NAYOSE_EXACT_MODE = hybrid | fuzzy_only | exact_only`
  — run the matcher with exact blocks disabled to measure how much retrieval
  relies on fuzzy (sparse+dense) evidence.
- **Hardness telemetry**: the run prints e.g.
  `HARDNESS: 37% of TRUE pairs carry ZERO exact-block anchors and must be found fuzzily`
  and the generator prints name-duplication/twin stats; the derive target is
  retrieval-hard data where dense embeddings visibly matter.

Encoding decision: index the **normalized observed** record (distribution
matches serve-time dirty queries; `party_truth` is hidden at deploy time).
F2LLM candidate discovery also materializes separate record, name, address, and
search-only alias views; aliases retain source-record provenance and never
rewrite entity profiles. Record candidates are fused with exact and sparse
channels before structured pair verification, so dense similarity never becomes
an identity decision by itself.

## Tunable parameters (hyperparameters) — all env-overridable

| Group | Env | Default | Meaning |
|---|---|---|---|
| Data gen | `NAYOSE_SEED` | 42 | RNG seed (reproducibility) |
| | `NAYOSE_PROFILE` | generate_only | 300/700/6,000/20,000/100,000 base entities |
| | `NAYOSE_N_PARTIES` | 0 | full-generation base entity override; hard cap 2,000,000 |
| | `NAYOSE_DIFFICULTY` | realistic | debug/realistic/challenge/severe (dirtiness rates) |
| Retrieval | `NAYOSE_EXACT_BLOCK_CAP` | 80 | block-exhaustive cap (skips oversized blocks) |
| | `NAYOSE_DENSE_VIEWS` | 1 | materialize field-aware F2LLM name/address/alias artifacts |
| | `NAYOSE_SPARSE_TOPK` / `NAYOSE_DENSE_TOPK` | 24 / 16 | k per retrieval channel |
| | `NAYOSE_CANDIDATE_CAP` | 80 | max candidate records per query |
| | `NAYOSE_SPARSE_BATCH` / `NAYOSE_NEURAL_BATCH` | 512 / 16 | batch sizes |
| Embeddings | `NAYOSE_EMBEDDING_MODEL` | F2LLM-v2-1.7B | dense encoder (0.6B/1.7B) |
| | `NAYOSE_DEVICE` | auto | mps/cuda/cpu; explicit unavailable devices fail fast |
| | `NAYOSE_USE_DENSE` | 1 | dense channel on/off; bundle artifacts remain strict |
| | `NAYOSE_CPU_THREADS` | auto | per-process numerical thread budget |
| | `NAYOSE_PROCESS_WORKERS` | 1 | process count used to divide CPU threads |
| | `NAYOSE_INDEX_BACKEND` | polars | bulk index materialization backend |
| | `NAYOSE_SVD_DIM` | 32 | SVD dimensions for the `off` model |
| Pair model | `NAYOSE_PAIR_MODEL` | auto | auto/lightgbm/sklearn |
| | `NAYOSE_NEG_PER_POS` | 6 | negatives per positive in training sample |
| | `NAYOSE_MODEL_THREADS` | auto | bounded model-training threads |
| Calibration/decision | `NAYOSE_PAIR_AUTO_PRECISION` | 0.985 | Wilson lower-bound target (train-set-proof) |
| | `NAYOSE_PAIR_REVIEW_PRECISION` | 0.80 | review threshold target |
| | `NAYOSE_ENTITY_AUTO_PRECISION` | 0.97 | entity auto precision target |
| | `NAYOSE_ENTITY_MARGIN` | 0.06 | margin over second entity |
| | `NAYOSE_ENTITY_OVERRIDE` | 0 | force manifest thresholds off |

LightGBM internals (n_estimators=260, lr=0.035, num_leaves=31, …) live in the
matcher notebook's `train_pair_model`; the sklearn HistGB backend is the
MPS-serving alternative.

## Confusion matrices & evaluation discipline

- **Unseen test set**: edges/queries with `pair_split`/`query_split == test` are
  never used for training or threshold calibration (also reported per-split).
- Every run saves `pair_confusion_test.parquet` (TP/FP/TN/FN, precision,
  recall, specificity, F1, AUC @ auto threshold) and `query_confusion.parquet`
  (predicted action × expected-existence per scenario) into the bundle, and
  prints both with the run.
- Candidate (retrieval) recall is reported *separately* from classifier
  precision exactly so retrieval ease is not mistaken for matching accuracy.

## Web dashboard (F1-F5, modern frontend)

A no-build modern frontend (`frontend/`) + thin API (`api/main.py`), served
locally by one process. DuckDB executes read-only SQL over the run bundle
parquets (single local process; swap the data adapter to DuckDB-WASM later).
The interface uses progressive disclosure and shows verdict uncertainty with
lineage in the flow. Routes are URL-addressable (`#/entity/:id`,
`#/compare/:l/:r`), the palette is keyboard accessible, and reduced-motion and
AA contrast requirements are covered by the frontend checks.

```bash
NAYOSE_MODELS_DIR=models NAYOSE_DATA_DIR=data/current \
    .venv/bin/python -m api.main        # → http://127.0.0.1:8000
```

Views: 検索（既定画面）・照合テスト (live `/api/recommend`) · レビュー待機
（件数・確率フィルタ・既読保存）・レコード比較（パンくず・エンティティ連携）・
混同行列（ヒート・内訳ドリル）・指標（バー付き）・エンティティ探索（検索・
サーバー側ページング・代表情報・出所関係）・管理（複数世代のデータ閲覧、
全体/列フィルタ、固定列、ページング）。
Frontend boundaries: `frontend/ts/schema.ts` + `core.ts` (pure schema/core) ·
`port.ts` (typed capability and storage interfaces) · `infra.ts` (ApiAdapter +
cache, LocalStorageStore) · feature modules (views depend only on ports; the
admin dataset grid is isolated in `dataset-grid.ts`) ·
`store.ts` (observable state with reversible decisions) · `app.ts` assembly
(`boot()` injects port/store/runs; **no import side effects**).

API (typed domain endpoints; raw SQL only behind `NAYOSE_DEBUG_SQL=1`):
`/api/runs` · `/api/entities?q=` · `/api/entities/explorer` ·
`/api/entity/{id}` (lineage) · `/api/entity/{id}/graph` ·
`/api/review?min_p=&offset=` · `/api/metrics` · `/api/confusion` ·
`/api/compare` · `/api/recommend` (fingerprint-guarded) · `/api/admin/generations`
· `/api/admin/generations/{id}/summary` · `/api/admin/generations/{id}/records`
(global/column filters, bounded pages, optional keyset `after` cursor) · `/api/stats`
(per-endpoint hits+ms). Caching: server-side per-run LRU + client adapter memo;
field lineage remains in the immutable generation's hidden `field_lineage` table.

**Cross-run diff** (`#/compare-runs`): pick run A/B, `ポート.metricsFor(run)` →
side-by-side KPI table with Δ (green/red deltas, tabular numerals).

**Decision persistence (server-side)**: review 既読 is a Command in the store,
mirrored to `models/decisions/<run>.json` via `GET/PUT /api/decisions`
(debounced) — decisions survive across machines/sessions; localStorage remains
the offline fallback; `取り消し` undoes via the command history.

E2E (real browser, opt-in): `NAYOSE_E2E=1 NAYOSE_E2E_MODELS=… NAYOSE_E2E_DATA=…
npm --prefix frontend run test:e2e` — starts the server, drives Chromium
(Playwright), asserts search→entity→review persistence→cross-run diff and the
⌘K palette flow.

Testing policy (hermetic, deterministic, enforced):
- TS unit: `frontend/test/core.test.mjs` (pure core, node:test).
- TS functional: `frontend/test/view.test.mjs` — boots the app with a MockPort
  in jsdom; asserts real flows (default route, search, palette keyboard,
  review check+undo, confusion drill). No network.
- Python: API functional tests over a fake bundle + cross-language schema
  contract test (schema.ts ↔ REQUIRED_COLUMNS) + `tests/test_frontend_gate.py`
  runs `npm test` (skips when node/npm absent).

Frontend is **strict TypeScript** compiled to ESM (`tsc`), no bundler:

```bash
(cd frontend && npm install)          # once (typescript)
(cd frontend && npm run typecheck)    # strict type gate
(cd frontend && npm run build)        # ts/ -> js/ (committed, served directly)
```

Contracts are Rust-disciplined: `frontend/ts/schema.ts` mirrors
`api/main.py REQUIRED_COLUMNS`, and `tests/test_schema_contract.py` fails CI
if they drift; the data adapter validates shapes at runtime before render.
Never hand-edit `frontend/js/` (generated).

| View | Content |
|---|---|
| F1 指標 | candidate recall / B³ F1 / top-1 / MRR / review / no-match |
| F2 混同行列 | pair verifier (unseen test) + query-decision confusion |
| F3 レビュー待機 | uncertain twin/edge queue |
| F4 エンティティ | cluster cards: canonical + members + field-lineage summary |
| F5 検索 | debounced fuzzy search over names/phone/postal, capped results |

Design: task-first progressive disclosure, light theme + Japanese labels
(Noto Sans JP), semantic HTML (`role` landmarks, `aria-live`, AA contrast,
`focus-visible`, container queries, reduced-motion).

Front-end checklist mapping (thedaviddias/front-end-checklist):
ticked where applicable (HTML semantics, CSS responsiveness, a11y,
performance: no chart library, schema-validated at render); single-file
inline CSS/JS is a documented exception for a local offline artifact;
web-deployment-only rules (HTTP caching, CDN/SRI, SEO crawlers, PWA) are
not-applicable to a file — mapping maintained in AGENTS.md.

## Dense vs sparse — measured at scale

A/B on regenerated 100k-hard data (113,499 parties, 24,522 same-name twin rows,
249,201 records; fuzzy-only retrieval, ruri-v3-130m, exact blocks off):

| Metric | sparse+dense | sparse-only |
|---|---|---|
| Candidate recall | 99.34% | 99.12% |
| Top-1 identity (existing) | **90.9%** | 79.8% |
| MRR | **0.933** | 0.809 |
| No-match rate | 16.9% | 24.1% |
| B³ F1 | 0.971 | 0.970 |
| Embedding encode | 47 min once (cached) | 0 |

The dense-vs-sparse identity gap grows with scale+hardness (smoke +2.8pts,
mac +5.6, 100k **+11.1**): sparse char-ngrams saturate retrieval on small data
but cannot disambiguate same-name twins at scale. Dense embeddings are the
ranking/robustness layer; keep them on by default.

## Structure (surface)

```
frontend/            no-build ESM app (index.html, styles.css, js/app.js)
api/main.py          FastAPI: static + /api/runs + /api/query (DuckDB) + /api/recommend
tests/test_api.py    API contract tests (self-contained fake bundle)
```

## Model saving / inference (run bundles)

`nayose.ipynb` writes each train/evaluate into an immutable model bundle and promotes it via a symlink:

```
models/
├── registry.json                     # run_id → bundle, config/data hashes, backends, stage metrics
├── runs/run_<ts>_<hash>/             # frames + models + reference_sparse_matrix + embeddings + run_manifest
└── current -> runs/run_<ts>_<hash>   # promoted bundle (consumer-facing)
```

`NayoseEngine.load(model_dir="models/current", data_dir="data/current")`:
1. **fingerprint-checks** bundle vs reference (refuses stale data↔model pairings);
2. loads sparse TF-IDF + **F2LLM dense embeddings with usearch HNSW**;
3. loads the pair verifier — **native LightGBM text booster** when available, else joblib;
4. serves `recommend()` = normalize → blocks ∪ sparse ∪ dense retrieval → calibrated pair probabilities → entity decision.

> MPS note: joblib-restored LightGBM and sentence-transformers encoding deadlock in one process on Apple Silicon. Serve the pair model via the native text booster (default when trained with LightGBM), or train with `NAYOSE_PAIR_MODEL=sklearn` for a pure-sklearn serving bundle. Both are recorded in `registry.json`.

## Performance

| Metric | Value |
|--------|-------|
| Bundle load | ~0.6 s (guard passes, usearch index ready) |
| Inference (warm) | ~0.1 s/query |
| Inference (cold, first) | ~12 s (1.7B encoder load, cached engine) |
| Training (smoke, f2llm+MPS) | ~48 s end-to-end (embeddings cached) |
| B³ F1 (smoke) | 96.8–96.9 %; top-1 identity 96.8 % (f2llm) vs 94.7 % (off) |

## Dependencies

```
numpy pandas scipy scikit-learn rapidfuzz lightgbm joblib
pykakasi phonenumbers jaconv pyarrow faker(optional legacy) datasets
```

## Engine package & tests

The generator logic lives in `synthetic/` (providers / noise / truth / engine;
data-driven config in `synthetic/config.py`) - the notebook is a thin driver.

```bash
uv pip install pytest && python -m pytest tests/ -q
```

## Data sources (real, cached in data/)

| Source | Use | Licence / credit |
|---|---|---|
| NVIDIA Nemotron-Personas-Japan | names + sex/age/prefecture/occupation (demographic skew) | CC-BY 4.0 |
| Japan Post ken_all | ~119k postal towns, all 47 prefectures | Japan Post postal-code data |
| EDINET edinet2023 | 4,253 real company legal names | EDINET (FSA) |

## Dirty-data realism

- Correlated **events** (rename/move/contact stale, paper-OCR bursts, legacy
  format incl. era dates + half-width kana, correlated missing) scaled by
  quality band × agency factors; every value carries a lineage label.
- Orthographic consistency: kana/romaji are derived from the kanji, so names
  never disagree across spellings (82% of field cells stay clean).
- Benchmark validated end-to-end: smoke profile -> nayose.ipynb B3-F1 ≈ 96.5%,
  candidate recall ≈ 99.8% (model `off`, CPU).
