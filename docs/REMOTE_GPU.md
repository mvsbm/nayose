# Training/serving on the GPU box (RTX 3090 · Ryzen 7 7700X · 64 GB)

The dashboard's heavy stages map cleanly onto this machine: CUDA accelerates
the neural stages that were CPU-only locally, 16 threads cut LightGBM, and
64 GB comfortably holds the pair-feature matrices for the 623k-record
generation. Everything below is the existing repo — no code changes needed.

## 1. Get the repo + dependencies

```bash
# on the remote (VSCode Remote terminal or ssh)
git clone <your-repo-url> nayose && cd nayose
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[matcher,neural,train,serve,dev]"
```

- `neural` pulls torch — make sure it links CUDA on this box. If `pip` picked
  a CPU wheel:
  ```bash
  pip install --index-url https://download.pytorch.org/whl/cu121 torch
  python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
  # expect: True NVIDIA GeForce RTX 3090
  ```

## 2. Get the data

Two options — pick one:

**a) Copy the existing generations (fast, ~0.9 GB):**
```bash
rsync -avL marvinma@<your-mac>:nayose/data/ ./data/   # preserves current alias + all generations
```
(`-L` resolves the `data/current` symlink; alternatively use `-a` and fix the
link remotely.)

**b) Regenerate on the box (deterministic — same seed/outputs):**
```bash
python scripts/generate_data.py --profile generate_only --n-parties 250000 \
    --seed 42 --data-dir data
```

## 3. Train the full 623k snapshot with neural retrieval + reranking

```bash
# the 250k-party generation is pinned for both training and serving (see below)
GEN=data/generations/gen_20260904-160304_ff650a4a
NAYOSE_MAX_FULL_RECORDS=700000 \
NAYOSE_MODEL_THREADS=14 \
OMP_NUM_THREADS=14 OPENBLAS_NUM_THREADS=14 \
nohup python scripts/train.py \
    --data-dir $GEN \
    --models-dir models --seed 42 --device cuda --model-family ruri \
    > train.log 2>&1 &
tail -f train.log
```

- `--device cuda`: dense embeddings (ruri-v3-130m) + cross-encoder rerank
  (ruri-v3-reranker-310m) run on the 3090 in fp16 — the stages that were the
  long pole on CPU (~623k encodes ≈ minutes; 12k rerank pairs ≈ a minute).
- `--model-family ruri` keeps the neural path enabled (bundle gets real dense
  vectors + `reference_embeddings.f16.npy`, manifest reports `neural_enabled`).
- `14` threads leaves headroom on the 8-core/16-thread 7700X.
- Estimate, not a guarantee: ~45–90 min for the full 623k run (the previous
  local full run on the pack was interrupted, so these are upper bounds from
  stage measurements, not a completed 623k training).
- Models are saved as native LightGBM text boosters — no pickle/torch
  deadlock issue on CUDA either.

To validate the pipeline on the box first, run the same command against a
`--profile smoke` generation in a temp dir — 1–2 min end-to-end.

## 4. Serve on the remote

Serve the **same pinned generation** you trained with (`$GEN` above); a
remote `data/current` may point at a different generation, and the bundle's
fingerprint guard refuses mismatched data (that is by design).

```bash
NAYOSE_DASH_PORT=8899 \
NAYOSE_MODELS_DIR=models NAYOSE_DATA_DIR=$GEN \
NAYOSE_API_TOKEN=$(openssl rand -hex 16) \
NAYOSE_WARM_EMBED=1 \
nohup python -m api.main > serve.log 2>&1 &
curl -s localhost:8899/api/health
```

- `NAYOSE_WARM_EMBED=1` preloads the engine (embeddings + ANN) at startup.
- The API binds `127.0.0.1` by default → reach it from your laptop with
  **VSCode Remote: Ports panel → forward 8899**, then open
  `http://localhost:8899`. (Or `ssh -L 8899:localhost:8899 your@dawnlight.net`.)
- For non-tunneled public access set `NAYOSE_BIND_HOST=0.0.0.0` behind a
  reverse proxy/TLS — always keep `NAYOSE_API_TOKEN` set on a shared box.

## 5. Audit + retrain loop (same as local)

```bash
python scripts/audit_decisions.py --models-dir models --data-dir $GEN
```

## Expected speedups vs the M-series laptop

> Estimates for the 3090 column — not yet measured on the remote box. Validate
> a `--profile smoke` CUDA run before quoting any of these as facts.

| Stage | laptop (measured) | 3090/7700X (estimate) | gain (estimate) |
|---|---|---|---|
| dense encode 623k (ruri-130m) | ~40 min CPU | ~3–5 min CUDA | ~10× |
| rerank 12k pairs (310m) | >1 h CPU (hung) | ~1–2 min CUDA | ~30× |
| LightGBM 260 rounds | 2–8 threads | 14 threads | ~2–3× |
| pandas pair-feature build | 1 core | 1 core, ~2× faster IPC | ~2× |
| whole 623k run | multi-hour (practically stuck) | ~45–90 min | — |