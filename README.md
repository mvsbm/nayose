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

### 3. Run Inference

```python
from nayose_inference import load_engine

engine = load_engine()
result = engine.recommend({
    "name_kanji": "山田太郎",
    "phone": "090-1234-5678",
})

print(result.action)           # AUTO_LINK
print(result.top_nayose_id)    # N000012345
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
│   ├── reference_records.parquet   # model-visible customer records (dirty)
│   ├── incoming_queries.parquet   # model-visible benchmark queries
│   ├── agencies.parquet
│   ├── ken_all.zip / ken_all.csv / persona_names.parquet   # cached real data
│   └── ground_truth/              # hidden truth (never model-visible)
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

## Performance

| Metric | Value |
|--------|-------|
| Load time | ~5 seconds |
| Inference | ~0.2 seconds/query |
| Training | ~34 minutes (214k records) |
| B³ F1 | 76.1% |

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
