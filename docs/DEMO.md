# Demo runbook — show Nayose to stakeholders in 5 minutes

The point of this repo is a *realistic, explainable* insurance entity-resolution
pipeline: hidden ground truth drives corrupt-but-coherent records, a model is
trained and evaluated against that truth, and the dashboard shows the same
serving path with per-field evidence. This runbook makes that demo repeatable.

## 1. Fast demo on a small bundle (~5 min, CPU only)

```bash
source .venv/bin/activate
# (a) generate a ~2k-record synthetic corpus with hidden ground truth
python scripts/generate_data.py --profile smoke --seed 7 --data-dir /tmp/demo_data
# (b) headless train (LightGBM + TF-IDF/SVD), writes models/current
python scripts/train.py --data-dir /tmp/demo_data --models-dir /tmp/demo_models \
  --seed 7 --device cpu --model-family off --prune-aborted
# (c) serve with the engine PRELOADED (no cold-start hiccup during the demo)
NAYOSE_WARM_EMBED=1 NAYOSE_MODELS_DIR=/tmp/demo_models \
NAYOSE_DATA_DIR=/tmp/demo_data/current NAYOSE_DASH_PORT=8899 \
python -m api.main
# open http://127.0.0.1:8899
```

`--model-family off` keeps torch out of the picture; the smoke bundle still gets
a real calibrated pair model (candidate recall ≈0.99) and real metrics.

## 2. Full-scale demo (already trained in this workspace)

The repo workspace ships a trained **374,253-record** bundle:

```bash
NAYOSE_WARM_EMBED=1 python -m api.main   # serves data/current + models/current
# open http://127.0.0.1:8899  (approx 70–130s cold engine load; keep the tab open
# or rely on warmup so the point-to-照合 click is instant)
```

## 3. The 5-minute script (what to click, in order)

| # | Where | What to say |
|---|---|---|
| 1 | `#/search` | Two tasks are separated: search existing entities vs. match new input. Give the 入力情報の照合 form a spin — **サンプルデータを入力** fills a real record from the corpus, and 候補を照合 shows the true serving path. |
| 2 | Match results | Point at the **backend timeline** (入力確認→検索→スコア→完了 with real elapsed ms), the **thresholds** (自動 x% / 確認 y%), and the runner-up gap **labeled as context, not a decision rule**. |
| 3 | Candidate evidence | The per-field comparison table shows 一致 / 正規化後に一致 / 相違 / 比較できません — the reason a match is *suggested*, with raw and normalized values. |
| 4 | `#/entity/N…` | Canonical summary + **paged member records** with provenance (channel/観測日/契約/代理店), expandable full records and select-two → 比較. |
| 5 | `#/review` | The operational queue: 既読 is *reading progress*, not a merge approval — decisions persist server-side and survive reload. |
| 6 | `#/metrics` + `#/confusion` | Evaluation against the **hidden ground truth**: pair recall, B3-F1, precision, scenario×action breakdown (evaluation data was never used to build labels — it measures the model honestly). |
| 7 | 日本語 / English button | Show the toggle; every label flips, customer data never gets translated. |

## 4. What to say about trust (and what NOT to claim)

Say:
- The pipeline is **seed-reproducible** and fingerprint-guarded — a bundle refuses
  to serve against a different dataset.
- Every visible answer is either **server-owned data** (records, lineage-visible
  provenance) or **model output with disclosed inputs and thresholds** — no
  invented explanations.
- Metrics are measured on **held-out test splits** from the synthetic generator.

Do not claim:
- "This person is identified with 97% certainty" — the score is a calibrated
  pair-model aggregate, explicitly not identity verification.
- "The merge happened because the address changed" — the bundle does not record
  historical merge paths; the UI says so honestly.
- Any GPU/speedup numbers — the 3090 box has not been benchmarked yet.

## 5. Demo pitfalls (each has a cause + fix)

| Pitfall | Cause | Fix |
|---|---|---|
| Clicking 照合 "does nothing" for ~1–2 min | cold engine load (374k bundle) | `NAYOSE_WARM_EMBED=1` + keep the tab warm; or demo the smoke bundle |
| 401s with `NAYOSE_API_TOKEN` set | the web UI previously sent no token | the transport now attaches `Authorization: Bearer <nayose.token>` from browser localStorage — set it on the demo machine (`localStorage.setItem("nayose.token", "…")`) or run the GUI behind a proxy that injects it |
| エラー: モデルを読み込めません | `models/current` and `data/current` fingerprint mismatch | train/serve against the SAME generation — `scripts/train.py` points `models/current` at the generation it trained on |
| Search finds nothing for a typed name | bundle-specific nameset | use サンプルデータを入力 or search by entity ID (`N000…`) |

## 6. After the demo

- Audit exports: `python scripts/audit_decisions.py --models-dir <m> --data-dir <d>`
  writes ground-truth-confirmed pairs to `models/decisions/promoted_<run>.parquet`.
- Full gates: `python scripts/audit_repo.py`; browser E2E: `NAYOSE_E2E=1 npm --prefix frontend run test:e2e`
  (strict mode: any page error / API non-2xx fails the journey).
- Remote GPU training: `docs/REMOTE_GPU.md`; ops: `docs/OPERATIONS.md`; deployment: `docs/CLOUD.md`.