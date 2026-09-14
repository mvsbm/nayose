# Operating the Nayose dashboard (data → train → serve → audit)

End-to-end runbook for serving the dashboard on a real-sized synthetic or
derived dataset. Everything is env-configured (`NAYOSE_*`), immutable
(generations + run bundles + `current` aliases), and reproducible from a seed.

## 1. Generate reference data (one immutable generation)

```bash
python -m api.cli data generate --profile generate_only \
    --n-parties 250000 --seed 42 --data-dir data
# writes data/generations/gen_<ts>_<hash>/ and repoints data/current
```

- Profiles are named by estimated canonical-entity size in the dashboard: `~340 | ~800 | ~6,800 | ~22,700 | ~113,500` (internal IDs remain `preflight | smoke | mac | large_mac | generate_only`).
- For a canonical-only large layer, use `python -m api.cli data generate --clean-only --n-parties 2000000 --chunk-size 50000`; this writes an immutable `clean_generations/` product and updates `clean_current` without replacing `data/current`.
- Full generation is capped at 2,000,000 base entities because observations, policies, and field lineage are currently materialized before saving. The clean-only stream is the implementation path for a 2M canonical layer; target-runner capacity sign-off is still required, and larger full generations require chunked policy/lineage orchestration first.
- Dirtiness: `--difficulty realistic` (default) | `debug | severe`.
- In the dashboard, **Admin → Data & training** exposes every registered
  corruption event, its probability, affected fields, example, channel
  retention, quality-band scaling, query mix, and training knobs. The server
  validates a dry preview before enabling **Generate data**; estimates include
  parties, records, queries, quality shares, and expected affected records.
- The reference embeddings cache is keyed by `(data_fingerprint, model_id)`
  inside the generation dir — an unchanged generation never recomputes.

## 2. Train + evaluate (one immutable run bundle)

```bash
python -m api.cli model train --data-dir data/current --models-dir models \
    --seed 42 --pair-model lightgbm --device mps --model-family f2llm_330m
```

`nayose model train` runs the checked-in Python training pipeline with `NAYOSE_*`
overrides, then **verifies the bundle** before exiting 0:

- required tables present (`canonical_entities`, `record_to_nayose`,
  `review_queue`, `full_dataset_metrics`, `full_dataset_summary`,
  `pair_confusion_test`, `query_confusion`, `query_results`);
- `run_manifest.json.data_fingerprint == generation_manifest.data_fingerprint`
  (the same guard `NayoseEngine.load` enforces at serve time);
- prints counts, thresholds (`pair_auto/review`, `entity_auto/review`), and
  candidate/pair/query metrics.

Bundle: `models/runs/run_<ts>_<hash>/`, promoted via `models/current`.
The production F2LLM profile is locked to `--device mps`, FP16, neural
batch 32, one process, dense retrieval, and `PYTORCH_ENABLE_MPS_FALLBACK=0`.
It fails closed if MPS or the exact 330M model is unavailable. The `off`
profile remains available for deterministic CPU-only test fixtures.
Key knobs: `--pair-model auto|lightgbm|sklearn`, `--model-family off|f2llm_330m|...`,
`--exact-mode hybrid|fuzzy_only|exact_only`.
Pair models are saved as native LightGBM text boosters — never joblib-pickle
LightGBM alongside torch (MPS deadlock; see AGENTS.md).

The pipeline refuses loads above `NAYOSE_MAX_FULL_RECORDS` (default 100,000)
by design; raise it deliberately when the machine has headroom
(e.g. `NAYOSE_MAX_FULL_RECORDS=700000` for a ~620k-record snapshot).
The serving path requires the exact and dense ANN artifacts recorded in the
bundle manifest; it never silently rebuilds or switches retrieval backends.

For an existing unpromoted bundle copied to another target platform, materialize
its native ANN artifact before packaging it:

```bash
python -m api.cli model materialize \
    --run-dir /staging/models/runs/run_<id> \
    --data-dir /staging/data/current \
    --index-backend polars
```

## 3. Serve

```bash
NAYOSE_DASH_PORT=8899 python -m api.main
# open http://127.0.0.1:8899
```

| Env | Effect |
|---|---|
| `NAYOSE_DASH_PORT` | bind port (default 8000) |
| `NAYOSE_AUTH_MODE` | `local_bearer` (default) or `trusted_proxy`; trusted proxy mode requires an authenticated edge proxy |
| `NAYOSE_API_TOKEN` | bearer credential for `local_bearer`; required when `NAYOSE_BIND_HOST` is non-loopback |
| `NAYOSE_AUTH_SUBJECT_HEADER` / `NAYOSE_AUTH_SCOPES_HEADER` | trusted-proxy identity headers; proxy must strip client-supplied values; scopes are `read review admin` |
| `NAYOSE_WARM_EMBED=1` | preload recommend engine (embeddings + ANN) at startup |
| `NAYOSE_REVIEWER_DEFAULT` | reviewer identity when the `X-Reviewer` header is absent |
| `NAYOSE_DEBUG_SQL=1` | enable `POST /api/query` (SELECT/WITH only) for debugging |
| `NAYOSE_EXACT_MODE` | initial channel-attribution mode; admin settings update the application-owned value without mutating process environment |
| `NAYOSE_CPU_THREADS` | explicit per-process numerical thread budget; default uses CPU affinity/process count |
| `NAYOSE_PROCESS_WORKERS` | API process count used to divide the CPU budget |
| `NAYOSE_DEVICE` | `mps` for neural serving; no CPU/CUDA fallback |
| `NAYOSE_OPERATIONAL_DB` | SQLite WAL path for jobs, decisions, audit events, and manual staging; defaults to `<data root>/operational.sqlite3` |

- `GET /api/health` — minimal auth-exempt liveness/readiness probe with status and timestamp; Docker healthcheck uses it.
- `GET /api/health/details` — authenticated operational details: release identity,
  fingerprints, runtime, engine state, run count, and endpoint telemetry.
  Every response includes `X-Request-ID`; clients may provide a bounded ASCII ID for correlation.
- `GET /api/admin/generation-config` — server-owned profiles, difficulties,
  corruption catalog, channel rules, and defaults.
- `POST /api/admin/generation-preview` — validates controls and returns a
  side-effect-free deterministic estimate; it never creates a generation.
- Reviewer identity: `PUT /api/decisions` accepts `X-Reviewer` only for unauthenticated loopback use. Authenticated bearer and trusted-proxy requests use the authenticated subject, so a client cannot impersonate a reviewer. Review mutations require the `review` scope; admin/data-entry mutations require `admin`. Decisions are stored in the configured operational SQLite database and bound to the model fingerprint and manifest hash.
- `trusted_proxy` is valid only behind a reverse proxy that strips the configured identity headers before adding verified OIDC/JWT claims. Do not expose the application directly in this mode.

## 4. Audit review decisions (label feedback loop)

```bash
python -m api.cli audit decisions --models-dir models --data-dir data/current
```

Scores operator decisions against hidden `record_truth`: per-reviewer accuracy,
wrong-decision samples, and an exported `models/exports/promoted_<run>.parquet`
of verified duplicate pairs. The SQLite database is the operational source of truth; the export is an explicit review artifact, and training
does not consume it automatically. Truth is read-only; nothing is mutated.

## 5. Retrain & drift

- Append a new query batch (or regenerate) → new fingerprint → new immutable
  generation → `train.py` against it → new bundle.
- Compare runs in the UI: `#/compare-runs` (metrics deltas), and inspect the
  manifest's `candidate/pair/query_metrics` in each `run_manifest.json`.
- `GET /api/stats` exposes per-endpoint timing/hits for exploitation noise.

## 6. Containerized serving

```bash
NAYOSE_API_TOKEN="$(openssl rand -hex 32)" docker compose up -d --build
# mounts ./data and ./models at runtime; shared binding is authenticated
docker compose logs -f nayose
```

`Dockerfile` healthcheck curls `/api/health`; token/warmup via compose env.
The image installs the frozen `uv.lock` dependency set.

## Notes

- `data/` and `models/` are gitignored runtime artifacts — regenerate, never commit.
- Seeds: `NAYOSE_SEED` (generation + training default 42); outputs are
  seed-reproducible. Do not change defaults silently.
- The web dashboard keeps F1–F5 views, no-build vanilla ESM, DuckDB
  SELECT/WITH-only over `bundle/` and `data/` roots — see AGENTS.md.