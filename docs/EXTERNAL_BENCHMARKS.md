# External dirty-data benchmarks

Nayose accepts optional, local benchmark datasets through the manifest-driven CSV adapter. Benchmark data is not part of the repository, production bundles, or the default test suite.

## Dataset contract

A benchmark directory contains `benchmark.json`, an entity CSV, and a query CSV. The manifest maps source columns to Nayose-visible fields:

```json
{
  "name": "febrl",
  "version": "1.0",
  "license": "dataset-license-id",
  "entities_file": "entities.csv",
  "queries_file": "queries.csv",
  "columns": {
    "entity_id": "master_id",
    "query_id": "record_id",
    "target_entity_id": "master_id",
    "split": "split",
    "slice": "corruption_family",
    "name_kanji": "name",
    "address": "address",
    "phone": "phone"
  }
}
```

Entity rows are the candidate identities. Query rows contain the noisy observation and its target entity. Supported split names are `train`, `validation`/`val`, `calibration`, and `test`; validation and calibration are reported as `validation`.

The adapter rejects path traversal, missing provenance metadata, hidden-label mappings, duplicate IDs, and target entities shared across splits. Store downloaded data outside Git, record its license and checksums in the manifest, and never use customer PII as a benchmark fixture.

## Run

```bash
nayose benchmark external --dataset-dir /secure/benchmarks/febrl
nayose benchmark external --dataset-dir /secure/benchmarks/febrl \
  --output /secure/benchmark-reports/febrl.json

# Evaluate a deterministic compound-corruption stress view
nayose benchmark external --dataset-dir /secure/benchmarks/febrl \
  --stress --seed 42 --output /secure/benchmark-reports/febrl-stress.json
```

The included scorer is a transparent normalization baseline, not the production model. Reports include per-split and corruption-slice Top-1, Recall@K, MRR, candidate recall, and candidate volume. Production model evaluation uses the fingerprint-bound synthetic product directly:

```bash
nayose benchmark resolution --data-dir data/current --model-dir models/current
```

Production reports include final decision quality, false auto-link rate, safe abstention rate, and identity ranking. Production model evaluation should also be run against the external adapter to measure out-of-domain behavior.
