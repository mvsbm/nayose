# Capacity and operating limits

This system is intentionally a local-first modular monolith. Scale-out is a measured
response, not a default dependency.

## Current budget

The serving process owns one matching executor and one admin executor. Matching
admission is bounded to 32 retained job snapshots and the admin executor accepts one
active operation. Jobs are persisted in SQLite WAL; SQLite is not a distributed queue.
A matching job has a 300-second cooperative deadline. Administrative subprocesses are
cancellable and are terminated before their worker is released.

The current benchmark command is:

```bash
source .venv/bin/activate
python -m api.cli benchmark runtime --model-dir models/current --data-dir data/current --iterations 20
```

A 20-iteration run on the development machine (10 available CPUs, MPS, current
fingerprint `6bfd4118c4fd...`) measured:

| Measurement | Result |
|---|---:|
| cold bundle load | 7.34 s |
| warm recommendation p50 | 32 ms |
| warm recommendation p95 | 34 ms |
| warm recommendation max | 36 ms |
| peak resident memory | 2.94 GiB |
| reference rows | 374,677 |
| record dense vector on disk | 640.3 MiB |
| process workers | 1 |

The promoted bundle also contains name, address, and alias vectors plus ANN indexes;
the complete dense artifact set is approximately 8.65 GiB on disk. These values are
a baseline, not an SLO. Repeat the benchmark after changing the bundle, device,
thread budget, or hardware. It measures one serial client and does not claim
multi-user capacity.

## CPU contract

Every Nayose process applies one native-thread policy before numerical modules are
loaded. Let `A` be CPUs visible through affinity/cgroup quota and `W` be the configured
process count. Configuration fails closed when `W > A`; otherwise each process receives
at most

```text
T = floor(A / W), with T >= 1
```

`OMP_NUM_THREADS`, OpenBLAS, MKL, BLIS, VecLib, NumExpr, Torch, LightGBM, and
scikit-learn are bounded by that partition. Therefore, for supported processes that
honor the policy, the configured native-thread ceiling is `W × T <= A` (apart from
non-native scheduler threads). This is a resource-safety bound, not a proof of minimum
wall time: workload shape, memory bandwidth, I/O, device kernels, and the OS scheduler
still determine throughput. Linux cgroup CPU quotas are the authoritative hard limit
when available; macOS measurements report policy and observed CPU time instead.

## Locked F2LLM MPS profile

Production F2LLM-v2-330M execution is fixed to Apple MPS, FP16, batch size 32,
one process, dense retrieval enabled, and `PYTORCH_ENABLE_MPS_FALLBACK=0`. The
canonical CLI also aligns Torch's packaged `libomp.dylib` before training so
LightGBM remains on the tested single-runtime path.

On this development Mac, encoding all 27,216 query texts produced 896-dimensional,
finite vectors in 723.7 seconds (about 37.6 queries/second). Equivalent CPU work
was projected at roughly 10–12 hours. This is an observed development measurement,
not an SLO; reference embeddings remain generation-local and fingerprint-bound.

The current promoted bundle contains record, name, address, and alias dense views,
plus the in-memory inverted sparse strategy. Larger-corpus sparse variants and
materialized CSC artifacts require separate capacity measurements; neither 2M
serving nor a multi-user target has been certified.

Retrieval decisions are evaluated separately from serving health and can be reproduced
with the same-fingerprint ablation evaluator:

```bash
python -m api.cli audit retrieval --model-dir models/current --data-dir data/current \
  --batch-size 32 --output /tmp/retrieval-ablation.json
```

It reports Recall@1/5/20, entity accuracy, candidate recall, auto-link precision,
false-auto rate, scenario strata, latency semantics, and bounded provenance traces.
The full 27,216-query run for fingerprint `6bfd4118c4fd...` completed in 1,104 s
with batch size 64. Sparse retrieval reached candidate recall 0.7123, Recall@1
0.6633, Recall@5 0.6997, and auto-link precision 0.9643; hybrid reached candidate
recall 0.6969, Recall@1 0.6682, Recall@5 0.6732, and auto-link precision 0.9650.
The evaluator reports unavailable modes rather than silently substituting another
mode. The promoted field-hybrid report for this fingerprint used batch size 32 and
completed in 970,495 ms (16.2 minutes), with candidate recall 0.7687, Recall@1
0.6747, Recall@5 0.7510, Recall@20 0.7687, auto-link precision 0.9665, and a
0.9995 new-customer no-match rate. This is an ablation comparison, not a retrained
replacement for the promoted baseline. Sparse, dense, and hybrid channels are
retrieved in bounded batches; batch-mode latency is reported as amortized per-query
latency. Sparse retrieval uses the highest-weight 64 query features by default, with
the direct CSR strategy retained for oversized matrices. The JSON report is
`/tmp/nayose-full-field-hybrid-final.json`.

## Release targets

Before a shared deployment is approved, measure and record:

- p95 and p99 latency for warm search, recommendation, entity paging, and review reads;
- cold startup time and peak resident memory for the largest supported generation;
- concurrent read load, queued job depth, job duration, cancellation latency, and 429 rate;
- operational database write contention and recovery time after process termination;
- release rollback time and the number of requests served by the old release during rotation.

Initial acceptance targets for a small single-node deployment are p95 warm
recommendation below 1 second, p95 read endpoints below 500 ms, startup below 30
seconds, and no unbounded queue growth. Set the final values from production-like
measurements rather than treating these provisional limits as guarantees.

## Scale-out trigger

Introduce a separate worker pool or shared operational database only when a load report
shows that bounded local execution cannot meet the agreed latency, throughput, memory,
or recovery targets. The first changes should be one shared queue and one PostgreSQL
operational store; replicas and distributed caches come later if measurements require
them. Until then, removing Redis, Kafka, Kubernetes, and microservices keeps release
selection, audit coupling, and failure behavior inspectable.

At smaller scale, remove admin subprocess parallelism, dense retrieval, the debug SQL
adapter, historical release retention, and nonessential telemetry before removing the
release pointer, fingerprint checks, SQLite transaction boundary, or audit trail.
