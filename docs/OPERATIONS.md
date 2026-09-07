# Operating the Nayose dashboard (data → train → serve → audit)

End-to-end runbook for serving the dashboard on a real-sized synthetic or
derived dataset. Everything is env-configured (`NAYOSE_*`), immutable
(generations + run bundles + `current` aliases), and reproducible from a seed.

## 1. Generate reference data (one immutable generation)

```bash
python scripts/generate_data.py --profile generate_only \
    --n-parties 250000 --seed 42 --data-dir data
# writes data/generations/gen_<ts>_<hash>/ and repoints data/current
```

- Profiles: `preflight | smoke | mac | large_mac | generate_only`.
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
python scripts/train.py --data-dir data/current --models-dir models \
    --seed 42 --pair-model lightgbm --device auto
```

`train.py` executes `nayose.ipynb` headlessly (fresh kernel, `NAYOSE_*`
overrides), then **verifies the bundle** before exiting 0:

- required tables present (`canonical_entities`, `record_to_nayose`,
  `review_queue`, `full_dataset_metrics`, `full_dataset_summary`,
  `pair_confusion_test`, `query_confusion`, `query_results`);
- `run_manifest.json.data_fingerprint == generation_manifest.data_fingerprint`
  (the same guard `NayoseEngine.load` enforces at serve time);
- prints counts, thresholds (`pair_auto/review`, `entity_auto/review`), and
  candidate/pair/query metrics.

Bundle: `models/runs/run_<ts>_<hash>/`, promoted via `models/current`.
Key knobs: `--device auto|mps|cpu|cuda`, `--pair-model auto|lightgbm|sklearn`,
`--model-family off|ruri|...`, `--exact-mode hybrid|fuzzy_only|exact_only`.
Pair models are saved as native LightGBM text boosters — never joblib-pickle
LightGBM alongside torch (MPS deadlock; see AGENTS.md).

The pipeline refuses loads above `NAYOSE_MAX_FULL_RECORDS` (default 100,000)
by design; raise it deliberately when the machine has headroom
(e.g. `NAYOSE_MAX_FULL_RECORDS=700000` for a ~620k-record snapshot).
On CPU boxes default to `--model-family off` (dense retrieval via SVD
fallback — fast, deterministic); set `--model-family ruri` on a GPU/MPS box
when you want neural cross-encoder reranking.

## 3. Serve

```bash
NAYOSE_DASH_PORT=8899 python -m api.main
# open http://127.0.0.1:8899
```

| Env | Effect |
|---|---|
| `NAYOSE_DASH_PORT` | bind port (default 8000) |
| `NAYOSE_API_TOKEN` | set → `/api/*` requires `Authorization: Bearer <token>` |
| `NAYOSE_WARM_EMBED=1` | preload recommend engine (embeddings + ANN) at startup |
| `NAYOSE_REVIEWER_DEFAULT` | reviewer identity when the `X-Reviewer` header is absent |
| `NAYOSE_DEBUG_SQL=1` | enable `POST /api/query` (SELECT/WITH only) for debugging |
| `NAYOSE_EXACT_MODE` | channel-attribution experiments (honored in graph+retrieval) |

- `GET /api/health` — readiness/hygiene probe (auth-exempt): current run,
  fingerprint, run count, endpoint stats, timestamps. Docker healthcheck uses it.
- `GET /api/admin/generation-config` — server-owned profiles, difficulties,
  corruption catalog, channel rules, and defaults.
- `POST /api/admin/generation-preview` — validates controls and returns a
  side-effect-free deterministic estimate; it never creates a generation.
- Reviewer identity: `PUT /api/decisions` accepts `X-Reviewer`; each stored
  decision records `{left, right, ts, reviewer}` under `models/decisions/<run>.json`.
- Admin and data-entry routes are local-first and have no separate admin token;
  when `NAYOSE_API_TOKEN` is configured they follow the same shared bearer
  policy as every other API route.

## 4. Audit review decisions (label feedback loop)

```bash
python scripts/audit_decisions.py --models-dir models --data-dir data/current
```

Scores operator decisions against hidden `record_truth`: per-reviewer accuracy,
wrong-decision samples, and an exported `models/decisions/promoted_<run>.parquet`
of verified duplicate pairs — the feedable label set for retraining. Truth is
read-only; nothing is mutated.

## 5. Retrain & drift

- Append a new query batch (or regenerate) → new fingerprint → new immutable
  generation → `train.py` against it → new bundle.
- Compare runs in the UI: `#/compare-runs` (metrics deltas), and inspect the
  manifest's `candidate/pair/query_metrics` in each `run_manifest.json`.
- `GET /api/stats` exposes per-endpoint timing/hits for exploitation noise.

## 6. Containerized serving

```bash
docker compose up -d --build     # mounts ./data and ./models at runtime
docker compose logs -f nayose
```

`Dockerfile` healthcheck curls `/api/health`; token/warmup via compose env.

## Notes

- `data/` and `models/` are gitignored runtime artifacts — regenerate, never commit.
- Seeds: `NAYOSE_SEED` (generation + training default 42); outputs are
  seed-reproducible. Do not change defaults silently.
- The web dashboard keeps F1–F5 views, no-build vanilla ESM, DuckDB
  SELECT/WITH-only over `bundle/` and `data/` roots — see AGENTS.md.