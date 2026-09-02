# AGENTS.md — contributor contract

## Commands

```bash
source .venv/bin/activate
uv pip install -e . --no-deps        # (optional) editable install for scripts, if missing
python -m pytest tests/ -q           # unit tests (fast, no external data)
ruff check .                          # lint (config in pyproject.toml)
ruff format --check .                 # formatting
mypy synthetic tests                  # type check (engine + tests only)
python scripts/generate_data.py --profile smoke   # headless pipeline (CLI == notebook contract)
```

## Layout

| Path | Responsibility |
|---|---|
| `synthetic/` | Engine: `config.py` (presets/rates, data), `providers.py` (real data sources), `noise.py` (correlated dirty events + lineage), `truth.py` (six ground-truth tables), `engine.py` (orchestration), `pipeline.py` (generate + the single save contract) |
| `tests/` | Unit tests only — must never require external data or downloads |
| `scripts/generate_data.py` | Headless generation CLI; writes the exact same tables as the notebook |
| `synthetic_data.ipynb` / `nayose.ipynb` | Thin drivers / reports (do **not** add logic here) |
| `data/raw` | Immutable reference-data cache (ken_all, personas, EDINET) — never mutate |
| `data/generations/gen_*` + `data/current` | Immutable dataset products; `current` is the consumer alias |
| `models/runs/run_*` + `models/current` + `registry.json` | Immutable model bundles & promotion alias |
| `data/`, `models/` | Gitignored runtime artifacts (regenerate, never commit) |

## Contract (do not break)

- The generator writes to a new `data/generations/gen_<ts>_<hash>/` and points
  `data/current` at it: `reference_records`, `incoming_queries` + six hidden
  tables under `ground_truth/` (`record_truth`, `party_truth`,
  `relation_truth`, `query_truth`, `evaluation_truth`, `field_lineage`) +
  `generation_manifest.json` (with `data_fingerprint`).
- The matcher reads `data/current` and writes bundles under
  `models/runs/run_<ts>_<hash>`, promoting `models/current`.
- `NayoseEngine.load` refuses a bundle whose fingerprint doesn't match the
  reference — never disable that guard.
- Visible tables must never leak hidden columns
  (`truth_id`, `scenario`, `split`, `record_quality`, `transformation`, ...).
- Every record/query field must have a lineage row
  (`field_lineage`: item_id × field, canonical vs observed, owner, transformation).
- Notebook cell 9 and `tests/` enforce this; run them before changing the schema.

## Do not

- Do not add logic to notebooks (thin driver rule).
- Do not hardcode datasets/rates in engine code — extend `config.py`/providers.
- Do not commit `data/`, `models/`, `*.executed.ipynb`, or environment files.
- Do not install new deps into the shared `.venv` without adding them to
  `pyproject.toml` first.
- Do not change random-seed defaults silently — outputs must be seed-reproducible.
- Dirtying must stay **truth-driven and logical** (coherent events, exact
  lineage); never add label noise — truth tables are the ground truth.
- New corruption must register as an event in `noise.py` + `config.py` rates
  (OCP), and be explainable from the entity's truth values.
- Keep `NAYOSE_EXACT_MODE` honored in graph + retrieval when running
  channel-attribution experiments; keep the HARDNESS telemetry print.
- Do not joblib-pickle the LightGBM pair model for new bundles — write the
  native text booster (`booster_.save_model`); pickle+torch(MPS) deadlock.
  For pure-sklearn serving bundles use `NAYOSE_PAIR_MODEL=sklearn`.
- Embeddings are cached per (data fingerprint, model id) inside the generation
  dir; do not recompute them for an unchanged generation.