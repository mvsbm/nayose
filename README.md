# Nayose — Japanese Insurance Entity Resolution

Entity matching system for Japanese insurance customer data. Resolves duplicate records across policies, agencies, and channels.

## Quick Start

Both notebooks share `data/` by default. The generator writes the visible
snapshots (`reference_records`, `incoming_queries`) plus the six hidden truth
tables under `data/ground_truth/` exactly as `nayose.ipynb` consumes them.

### 1. Generate Training Data

```bash
source .venv/bin/activate
# profiles: preflight / smoke / mac / large_mac / generate_only (100k default)
NAYOSE_PROFILE=mac jupyter nbconvert --to notebook --execute synthetic_data.ipynb
# one-time downloads, cached in data/: Nemotron persona names, Japan Post ken_all, EDINET
```

### 2. Train / Evaluate the Matcher

```bash
# CPU/SVD preflight (off) or neural (ruri) on MPS/CUDA; UI off for batch
NAYOSE_MODEL_FAMILY=off NAYOSE_DEVICE=cpu NAYOSE_UI=0     jupyter nbconvert --to notebook --execute nayose.ipynb
```

### 3. Run Inference

`nayose.ipynb` writes a model bundle (models + index + run_manifest) into its
output directory. `NayoseEngine.load` **fingerprint-checks the bundle against
the reference data** and refuses stale combinations:

```python
from nayose_inference import load_engine

engine = load_engine()  # models/ + data/ (after a fresh nayose run)
# or point at a specific bundle + generation:
# engine = load_engine(model_dir="nayose_output/run_20260902_abcdef", data_dir="data")

result = engine.recommend(
    {
        "name_kanji": "山田太郎",
        "phone": "090-1234-5678",
    }
)

print(result.action)  # AUTO_LINK
print(result.top_nayose_id)  # N000012345
print(result.top_probability)  # 0.856
```

CLI:

```bash
python nayose_cli.py --name "山田太郎" --phone "090-1234-5678"
python nayose_cli.py --interactive
```

## Project Structure

```
├── synthetic_data.ipynb        # Data generation pipeline
├── nayose.ipynb                # Model training & evaluation
├── nayose_inference.py         # Standalone inference engine
├── nayose_cli.py               # CLI interface
│
├── data/                       # Training data (gitignored)
│   ├── raw/                        # immutable reference data (ken_all, personas, EDINET)
│   ├── generations/gen_<ts>_<hash>/   # one immutable dataset product per run
│   │   ├── reference_records.parquet / incoming_queries.parquet / agencies.parquet
│   │   └── ground_truth/          # six hidden truth tables + field lineage
│   └── current -> generations/gen_<ts>_<hash>   # promoted generation (consumer-facing)
│       ├── record_truth.parquet
│       ├── party_truth.parquet
│       ├── relation_truth.parquet
│       ├── query_truth.parquet
│       ├── evaluation_truth.parquet
│       └── field_lineage.parquet  # per-field provenance audit (1.9M rows @100k)
│
└── models/                     # Trained artifacts (gitignored)
    ├── pair_verifier.joblib    # LightGBM pair matching model
    ├── pair_calibrator.joblib  # Probability calibration
    ├── entity_classifier.joblib
    ├── entity_scaler.joblib
    ├── entity_calibrator.joblib
    ├── tfidf_vectorizer.joblib # TF-IDF text vectorizer
    ├── reference_tfidf.npz     # Sparse TF-IDF matrix
    ├── entity_index.parquet    # Canonical entity forms
    ├── record_entity_mapping.parquet
    ├── benchmark_results.parquet
    ├── scenario_metrics.parquet
    ├── training_metrics.parquet
    └── training_manifest.json
```

## Architecture

| Component | Pattern | Responsibility |
|-----------|---------|---------------|
| `InferenceConfig` | Configuration Object | Immutable settings |
| `CandidateIndex` | SRP | Candidate retrieval |
| `NayoseEngine` | Facade | Single entry point |
| `normalize_*()` | SRP | Field normalization |

## Model family (SOTA path)

`NAYOSE_MODEL_FAMILY` (default `f2llm`) selects the dense embedding path:

| Family | Embedding | Notes |
|---|---|---|
| `f2llm` (default) | `codefuse-ai/F2LLM-v2-1.7B` (Qwen3-based, dim 2048) | SOTA semantic retrieval; weights auto-cache (~3.2 GB); offline fallback to `-0.6B` |
| `f2llm_0.6b` | `codefuse-ai/F2LLM-v2-0.6B` | ~3–4× faster encode, dim 1024 |
| `ruri` | F2LLM-v2-0.6B + ruri cross-encoder reranker | legacy/A-B entry |
| `qwen` | Qwen3-Embedding/Reranker-0.6B | optional |
| `off` | — | SVD fallback baseline |

Optimizations for MPS + time complexity:

- embedding encode on **MPS in fp16**, batched (`NAYOSE_NEURAL_BATCH`);
- **reference embeddings cached on disk** keyed by (data fingerprint, model id) — encode once per generation, later runs are O(reload);
- **usearch (HNSW)** ANN index for dense retrieval instead of brute-force;
- exact blocks + character sparse retrieval remain as high-precision anchors.

> One-time cold-encode cost (Apple MPS, 1.7B): ~50–120 ms/text → ~2–4 h for 100k records, paid once per generation and cached. Use `f2llm_0.6b` for quick exploratory runs.

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
Entity-consensus (centroid) embeddings over observed records are the planned
upgrade for clean-like anchoring without truth leakage.

## Tunable parameters (hyperparameters) — all env-overridable

| Group | Env | Default | Meaning |
|---|---|---|---|
| Data gen | `NAYOSE_SEED` | 42 | RNG seed (reproducibility) |
| | `NAYOSE_PROFILE` | generate_only | preflight/smoke/mac/large_mac/generate_only (size) |
| | `NAYOSE_DIFFICULTY` | realistic | debug/realistic/severe (dirtiness rates) |
| | `NAYOSE_N_PARTIES` | 0 | override party count |
| Retrieval | `NAYOSE_EXACT_BLOCK_CAP` | 80 | block-exhaustive cap (skips oversized blocks) |
| | `NAYOSE_SPARSE_TOPK` / `NAYOSE_DENSE_TOPK` | 24 / 16 | k per retrieval channel |
| | `NAYOSE_CANDIDATE_CAP` | 80 | max candidate records per query |
| | `NAYOSE_SPARSE_BATCH` / `NAYOSE_NEURAL_BATCH` | 512 / 16 | batch sizes |
| Embeddings | `NAYOSE_EMBEDDING_MODEL` | F2LLM-v2-1.7B | dense encoder (0.6B/1.7B) |
| | `NAYOSE_DEVICE` | auto | mps/cuda/cpu |
| | `NAYOSE_USE_DENSE` | 1 | dense channel on/off |
| | `NAYOSE_SVD_DIM` | 32 | SVD fallback dims (`off` model) |
| Pair model | `NAYOSE_PAIR_MODEL` | auto | auto/lightgbm/sklearn |
| | `NAYOSE_NEG_PER_POS` | 6 | negatives per positive in training sample |
| | `NAYOSE_MODEL_THREADS` | 2 | worker threads |
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
