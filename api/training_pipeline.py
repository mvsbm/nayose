"""Headless entity-resolution training pipeline.

This module is the executable training authority. It writes immutable,
fingerprint-bound model artifacts without an interactive runtime.
"""

from __future__ import annotations


def run() -> None:
    # ===== TRAINING STAGE 1 =====

    # 1. Runtime, dependencies, immutable configuration, and device preflight

    import gc
    import hashlib
    import json
    import math
    import os
    import platform
    import subprocess
    import sys
    import time
    from collections.abc import Iterable
    from contextlib import contextmanager, nullcontext
    from dataclasses import asdict, dataclass
    from importlib import metadata, util
    from pathlib import Path
    from typing import Any

    from api.embedding_cache import (
        EMBEDDING_FIELD_SCHEMA_VERSION,
        EMBEDDING_PREPROCESSING_VERSION,
        cache_paths,
        model_revision,
        text_fingerprint,
    )
    from api.embedding_cache import (
        load as load_embedding_cache,
    )
    from api.embedding_cache import (
        save as save_embedding_cache,
    )
    from api.embedding_config import (
        F2LLM_330M_MODEL_ID,
        F2LLM_MPS_BATCH_SIZE,
        F2LLM_MPS_DEVICE,
        F2LLM_MPS_TORCH_DTYPE,
    )
    from api.hard_negatives import select_hard_negatives
    from api.normalize import normalize_address, normalize_frame, normalize_kana, normalize_name
    from api.performance import (
        configure_mps_runtime,
        configure_torch_threads,
        model_thread_budget,
        process_worker_count,
        require_mps_device,
    )
    from api.performance import (
        resolve_device as resolve_compatibility_device,
    )
    from api.sparse_retrieval import DEFAULT_QUERY_FEATURE_CAP, SparseCandidateBackend
    from api.training_retrieval import CandidateGraphConfig, build_candidate_graph
    from api.work_ledger import WorkflowLedger

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
    configure_mps_runtime()

    CORE_REQUIRED = {
        "numpy": "numpy",
        "pandas": "pandas",
        "scipy": "scipy",
        "scikit-learn": "sklearn",
        "rapidfuzz": "rapidfuzz",
        "joblib": "joblib",
    }
    missing_core = [
        package for package, module in CORE_REQUIRED.items() if util.find_spec(module) is None
    ]
    if missing_core:
        command = "python -m pip install -U " + " ".join(missing_core)
        raise RuntimeError(
            f"Missing required packages: {missing_core}. Install them in the configured environment: {command}"
        )

    import joblib
    import numpy as np
    import pandas as pd
    from scipy.sparse import csr_matrix
    from scipy.special import expit
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score
    from sklearn.neighbors import NearestNeighbors
    from sklearn.preprocessing import StandardScaler
    from sklearn.preprocessing import normalize as l2_normalize

    lgb = None

    try:
        import resource as process_resource
    except ImportError:
        process_resource = None

    def report(value: Any) -> None:
        """Write a bounded diagnostic view for the headless runtime."""
        if isinstance(value, pd.DataFrame):
            print(value.head(10).to_string(index=False))
        else:
            print(value)

    def text(value: Any) -> str:
        return "" if value is None else str(value).strip()

    # ===== TRAINING STAGE 2 =====

    def banner(title: str) -> None:
        print("\n" + "=" * 96)
        print(title)
        print("=" * 96)

    def rss_mib() -> float:
        if process_resource is None:
            return float("nan")
        usage = process_resource.getrusage(process_resource.RUSAGE_SELF)
        value = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
        return float(value) / 2**20

    STAGES: list[dict[str, Any]] = []
    resource_sampling_enabled = True

    @contextmanager
    def stage(name: str):
        sample_resources = (
            resource_sampling_enabled
            and name != "COMPUTE STRUCTURED FEATURES FOR EVERY CANDIDATE EDGE"
        )
        before, started = (rss_mib() if sample_resources else 0.0), time.perf_counter()
        banner(name)
        ledger_span = workflow_ledger.span(name, "pipeline") if sample_resources else nullcontext()
        with ledger_span:
            yield
        elapsed = time.perf_counter() - started
        after = rss_mib() if sample_resources else before
        row = {
            "stage": name,
            "seconds": elapsed,
            "rss_before_mib": before,
            "rss_after_mib": after,
            "rss_delta_mib": after - before,
            "resource_sampled": sample_resources,
        }
        STAGES.append(row)
        print(
            f"✓ {name} completed in {elapsed:,.2f}s; RSS={after:,.1f} MiB ({after - before:+,.1f})"
        )

    @dataclass(frozen=True, slots=True)
    class Config:
        seed: int = int(os.getenv("NAYOSE_SEED", "42"))
        data_dir: Path = Path(os.getenv("NAYOSE_DATA_DIR", "data/current"))
        output_dir: Path = Path(os.getenv("NAYOSE_OUTPUT_DIR", "models"))
        device: str = os.getenv("NAYOSE_DEVICE", F2LLM_MPS_DEVICE)
        model_family: str = os.getenv(
            "NAYOSE_MODEL_FAMILY", "off"
        )  # off (default) | ruri | f2llm | f2llm_0.6b | qwen
        strict_backends: bool = os.getenv("NAYOSE_STRICT_BACKENDS", "0") == "1"
        launch_ui: bool = os.getenv("NAYOSE_UI", "0") == "1"
        decision_profile: str = os.getenv(
            "NAYOSE_DECISION_PROFILE", "audit"
        )  # audit | conservative
        dataframe_backend: str = (
            "pandas"  # Explicit current implementation; Polars is not required.
        )
        use_dense: bool = os.getenv("NAYOSE_USE_DENSE", "1") == "1"
        dense_profile: str = os.getenv("NAYOSE_DENSE_PROFILE", "record_hybrid")
        exact_mode: str = os.getenv(
            "NAYOSE_EXACT_MODE", "hybrid"
        )  # hybrid | fuzzy_only | exact_only
        allow_svd_dense_fallback: bool = os.getenv("NAYOSE_ALLOW_SVD_FALLBACK", "1") == "1"
        svd_dimensions: int = int(os.getenv("NAYOSE_SVD_DIM", "32"))
        low_memory_models: bool = os.getenv("NAYOSE_LOW_MEMORY_MODELS", "1") == "1"
        model_threads: int = model_thread_budget()
        pair_model_backend: str = os.getenv(
            "NAYOSE_PAIR_MODEL", "auto"
        )  # auto | lightgbm | sklearn

        exact_block_cap: int = int(os.getenv("NAYOSE_EXACT_BLOCK_CAP", "80"))
        sparse_top_k: int = int(os.getenv("NAYOSE_SPARSE_TOPK", "24"))
        dense_top_k: int = int(os.getenv("NAYOSE_DENSE_TOPK", "16"))
        candidate_cap_per_record: int = int(os.getenv("NAYOSE_CANDIDATE_CAP", "80"))
        sparse_batch_size: int = int(os.getenv("NAYOSE_SPARSE_BATCH", "512"))
        neural_batch_size: int = int(os.getenv("NAYOSE_NEURAL_BATCH", str(F2LLM_MPS_BATCH_SIZE)))
        rerank_top_k: int = int(os.getenv("NAYOSE_RERANK_TOPK", "5"))
        rerank_pair_limit: int = int(os.getenv("NAYOSE_RERANK_LIMIT", "12000"))
        embedding_dim: int = int(os.getenv("NAYOSE_EMBED_DIM", "256"))

        training_negatives_per_positive: int = int(os.getenv("NAYOSE_NEG_PER_POS", "6"))
        pair_auto_precision_target: float = float(os.getenv("NAYOSE_PAIR_AUTO_PRECISION", "0.985"))
        pair_review_precision_target: float = float(
            os.getenv("NAYOSE_PAIR_REVIEW_PRECISION", "0.80")
        )
        entity_auto_precision_target: float = float(
            os.getenv("NAYOSE_ENTITY_AUTO_PRECISION", "0.97")
        )
        entity_review_threshold: float = float(os.getenv("NAYOSE_ENTITY_REVIEW_THRESHOLD", "0.20"))
        entity_margin_threshold: float = float(os.getenv("NAYOSE_ENTITY_MARGIN", "0.06"))
        minimum_evidence_families: int = int(os.getenv("NAYOSE_MIN_EVIDENCE_FAMILIES", "2"))
        minimum_reference_density: float = float(os.getenv("NAYOSE_MIN_REFERENCE_DENSITY", "0.0"))
        max_full_match_records: int = int(os.getenv("NAYOSE_MAX_FULL_RECORDS", "100000"))
        save_all_candidate_pairs: bool = os.getenv("NAYOSE_SAVE_ALL_CANDIDATES", "0") == "1"

    CFG = Config()
    CFG.output_dir.mkdir(parents=True, exist_ok=True)
    RNG = np.random.default_rng(CFG.seed)

    MODELS = {
        "off": {
            "embedding": None,
            "reranker": None,
            "dimension": None,
            "query_prefix": "",
            "document_prefix": "",
        },
        # F2LLM embedding path: CodeFuse F2LLM-v2 Qwen3-based retriever on MPS.
        # dimension is auto-detected; weights are cached by Hugging Face.
        "f2llm": {
            "embedding": os.getenv("NAYOSE_EMBEDDING_MODEL", F2LLM_330M_MODEL_ID),
            "reranker": os.getenv("NAYOSE_RERANKER_MODEL", "") or None,
            "dimension": None,
            "query_prefix": "",
            "document_prefix": "",
        },
        "f2llm_0.6b": {
            "embedding": "codefuse-ai/F2LLM-v2-0.6B",
            "reranker": None,
            "dimension": None,
            "query_prefix": "",
            "document_prefix": "",
        },
        # Ruri path: ruri-v3-130m encoder (ModernBERT-Ja, ~8ms/text on MPS).
        # + ruri-v3-reranker-310m cross-encoder; same lab/lineage, top JMTEB quality
        "ruri": {
            "embedding": "cl-nagoya/ruri-v3-130m",
            "reranker": "cl-nagoya/ruri-v3-reranker-310m",
            "dimension": 512,
            "query_prefix": "",
            "document_prefix": "",
        },
        "qwen": {
            "embedding": "Qwen/Qwen3-Embedding-0.6B",
            "reranker": "Qwen/Qwen3-Reranker-0.6B",
            "dimension": CFG.embedding_dim,
            "query_prefix": "Instruct: Match this Japanese insurance customer to an existing party.\nQuery: ",
            "document_prefix": "Document: ",
        },
    }
    assert CFG.model_family in MODELS, f"Unknown model_family={CFG.model_family!r}"
    assert CFG.decision_profile in {"audit", "conservative"}
    assert CFG.dense_profile in {"record_hybrid", "field_aware"}
    if CFG.model_family == "f2llm":
        if CFG.device != F2LLM_MPS_DEVICE:
            raise RuntimeError(
                "F2LLM-v2-330M is locked to Apple MPS; CPU/CUDA/auto fallback is disabled"
            )
        if MODELS[CFG.model_family]["embedding"] != F2LLM_330M_MODEL_ID:
            raise RuntimeError(
                f"F2LLM-v2-330M is locked to {F2LLM_330M_MODEL_ID}; custom models are disabled"
            )
        if CFG.neural_batch_size != F2LLM_MPS_BATCH_SIZE:
            raise RuntimeError(f"F2LLM-v2-330M requires neural batch size {F2LLM_MPS_BATCH_SIZE}")
        if not CFG.use_dense or CFG.allow_svd_dense_fallback:
            raise RuntimeError("F2LLM-v2-330M requires dense retrieval without an SVD fallback")

    # ===== TRAINING STAGE 3 =====

    # Run bundle (Decision B): one immutable directory per train/evaluate run.
    import hashlib as _hashlib
    from dataclasses import asdict as _asdict

    RUN_TIMESTAMP = str(pd.Timestamp.utcnow().strftime("%Y%m%d-%H%M%S"))
    RUN_CONFIG_HASH = _hashlib.sha256(
        json.dumps(_asdict(CFG), sort_keys=True, default=str).encode()
    ).hexdigest()[:8]
    RUN_ID = f"run_{RUN_TIMESTAMP}_{RUN_CONFIG_HASH}"
    OUTPUT_RUN = CFG.output_dir / "runs" / RUN_ID
    OUTPUT_RUN.mkdir(parents=True, exist_ok=True)
    workflow_ledger = WorkflowLedger(
        "training",
        workflow_id=RUN_ID,
        metadata={"model_family": CFG.model_family, "data_dir": str(CFG.data_dir)},
    )
    print(f"Run bundle: {OUTPUT_RUN}")

    # ===== TRAINING STAGE 4 =====

    def package_version(name: str) -> str:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "not installed"

    def resolve_device(requested: str) -> tuple[str, dict[str, Any]]:
        diagnostics = {
            "requested": requested,
            "torch": package_version("torch"),
            "mps_available": False,
            "cuda_available": False,
        }
        if CFG.model_family != "off":
            selected = F2LLM_MPS_DEVICE if requested == "auto" else requested
            selected = require_mps_device(selected)
            import torch

            diagnostics["mps_available"] = True
            diagnostics["cuda_available"] = bool(torch.cuda.is_available())
            x = torch.randn(32, 32, device="mps")
            diagnostics["mps_matrix_test"] = float((x @ x.T).mean().cpu())
            return selected, diagnostics
        selected = resolve_compatibility_device(requested)
        if selected == "mps":
            import torch

            x = torch.randn(32, 32, device="mps")
            diagnostics["mps_available"] = True
            diagnostics["mps_matrix_test"] = float((x @ x.T).mean().cpu())
        return selected, diagnostics

    DEVICE, DEVICE_DIAGNOSTICS = resolve_device(CFG.device)
    BACKENDS = {
        "lightgbm": util.find_spec("lightgbm") is not None,
        "sentence_transformers": util.find_spec("sentence_transformers") is not None,
        "usearch": util.find_spec("usearch") is not None,
        "gradio": util.find_spec("gradio") is not None,
        "pyarrow": util.find_spec("pyarrow") is not None,
        "polars": util.find_spec("polars") is not None,
    }
    NEURAL_ENABLED = CFG.model_family != "off" and BACKENDS["sentence_transformers"]
    if CFG.model_family != "off" and not NEURAL_ENABLED:
        raise RuntimeError(
            f"{CFG.model_family} requested, but sentence-transformers is unavailable; "
            "neural fallback is disabled"
        )
    banner("RUNTIME PREFLIGHT")
    print(f"Python:      {sys.version.split()[0]}")
    print(f"Platform:    {platform.platform()}")
    print(f"Device:      {DEVICE}")
    print(
        f"DataFrame:   {CFG.dataframe_backend} (Polars is optional and not required in this revision)"
    )
    print(f"Model family:{CFG.model_family} ({'enabled' if NEURAL_ENABLED else 'disabled'})")
    print(
        f"Decision:    {CFG.decision_profile} — audit reports a best-effort fallback when a small calibration set cannot prove the requested Wilson bound; conservative abstains."
    )
    print("Installed versions:")
    for package in [
        "numpy",
        "pandas",
        "polars",
        "scikit-learn",
        "rapidfuzz",
        "lightgbm",
        "torch",
        "sentence-transformers",
        "transformers",
        "usearch",
        "gradio",
        "joblib",
        "pyarrow",
    ]:
        print(f"  {package:<22} {package_version(package)}")
    print("Optional backend availability:")
    report(pd.DataFrame([BACKENDS]))
    print(
        "Configuration (all ordinary behavior is controlled here or by NAYOSE_* environment variables):"
    )
    report(
        pd.DataFrame(
            [{**asdict(CFG), "data_dir": str(CFG.data_dir), "output_dir": str(CFG.output_dir)}]
        ).T.rename(columns={0: "value"})
    )
    print("Device diagnostics:")
    print(json.dumps(DEVICE_DIAGNOSTICS, indent=2, ensure_ascii=False))

    # ===== TRAINING STAGE 6 =====

    configure_torch_threads(CFG.model_threads)
    # 2. Load model-visible data first; hidden truth remains isolated until training/evaluation cells
    VISIBLE_FIELDS = [
        "party_type",
        "name_kanji",
        "name_kana",
        "name_romaji",
        "date_birth",
        "postal_code",
        "address",
        "phone",
        "email",
    ]
    REFERENCE_REQUIRED = [
        "record_id",
        "policy_id",
        "agency_id",
        "channel",
        "observed_at",
        *VISIBLE_FIELDS,
    ]
    QUERY_REQUIRED = ["query_id", "agency_id", "channel", "observed_at", *VISIBLE_FIELDS]
    FORBIDDEN_VISIBLE = {
        "truth_id",
        "target_truth_id",
        "relation_id",
        "scenario",
        "hard_case",
        "split",
        "record_quality",
        "quality_band",
        "transformation",
    }

    def read_table(stem: str, *, hidden: bool = False) -> pd.DataFrame:
        roots = [CFG.data_dir / "ground_truth", CFG.data_dir] if hidden else [CFG.data_dir]
        parquet_path = next(
            (r / f"{stem}.parquet" for r in roots if (r / f"{stem}.parquet").exists()), None
        )
        csv_path = (
            None
            if parquet_path is not None
            else next(
                (r / f"{stem}.csv.gz" for r in roots if (r / f"{stem}.csv.gz").exists()), None
            )
        )
        if parquet_path is not None:
            frame, source = pd.read_parquet(parquet_path), parquet_path
        elif csv_path is not None:
            frame, source = pd.read_csv(csv_path, keep_default_na=False), csv_path
        else:
            raise FileNotFoundError(
                f"Missing {stem}: expected under {'/'.join(str(r) for r in roots)}"
            )
        frame = frame.fillna("")
        print(
            f"{'hidden' if hidden else 'visible':<7} {stem:<22} rows={len(frame):>9,} cols={len(frame.columns):>2} source={source.name}"
        )
        return frame

    with stage("LOAD MATCHER-VISIBLE SNAPSHOTS"):
        generation_manifest = json.loads(
            (CFG.data_dir / "generation_manifest.json").read_text(encoding="utf-8")
        )
        reference_raw = read_table("reference_records")
        incoming_raw = read_table("incoming_queries")

        missing_reference = set(REFERENCE_REQUIRED) - set(reference_raw.columns)
        missing_queries = set(QUERY_REQUIRED) - set(incoming_raw.columns)
        assert not missing_reference, f"Reference schema is missing {missing_reference}"
        assert not missing_queries, f"Query schema is missing {missing_queries}"
        assert not FORBIDDEN_VISIBLE.intersection(reference_raw.columns), (
            "Hidden truth leaked into reference_records"
        )
        assert not FORBIDDEN_VISIBLE.intersection(incoming_raw.columns), (
            "Hidden truth leaked into incoming_queries"
        )
        assert reference_raw.record_id.is_unique and incoming_raw.query_id.is_unique
        assert len(reference_raw) <= CFG.max_full_match_records, (
            f"Loaded {len(reference_raw):,} records, above the configured reference safety cap "
            f"{CFG.max_full_match_records:,}. Raise NAYOSE_MAX_FULL_RECORDS deliberately after checking memory."
        )

        data_fingerprint = hashlib.sha256(
            pd.util.hash_pandas_object(
                reference_raw[REFERENCE_REQUIRED], index=False
            ).values.tobytes()
        ).hexdigest()
        print(
            f"Generation profile: {generation_manifest.get('config', {}).get('profile', 'unknown')}"
        )
        print(
            f"Reference snapshot: {len(reference_raw):,} records; incoming benchmark: {len(incoming_raw):,} queries"
        )
        print(f"Reference data fingerprint: {data_fingerprint[:20]}…")
        print(
            "Reference date range:",
            reference_raw.observed_at.min(),
            "→",
            reference_raw.observed_at.max(),
        )
        print(
            "Incoming date range: ",
            incoming_raw.observed_at.min(),
            "→",
            incoming_raw.observed_at.max(),
        )
        possible_pairs = len(reference_raw) * (len(reference_raw) - 1) // 2
        estimated_union = (
            len(reference_raw) * (CFG.sparse_top_k + (CFG.dense_top_k if CFG.use_dense else 0)) / 2
        )
        dense_f16_mib = (
            len(reference_raw)
            * (MODELS[CFG.model_family]["dimension"] or CFG.svd_dimensions)
            * 2
            / 2**20
        )
        capacity_plan = pd.DataFrame(
            {
                "value": [
                    len(reference_raw),
                    possible_pairs,
                    int(estimated_union),
                    dense_f16_mib,
                    CFG.max_full_match_records,
                ],
                "meaning": [
                    "loaded reference observations",
                    "pairs avoided by candidate generation",
                    "rough fuzzy-candidate upper estimate",
                    "dense-vector payload only (MiB, f16)",
                    "configured reference safety cap",
                ],
            },
            index=[
                "records",
                "possible_all_pairs",
                "estimated_fuzzy_candidates",
                "dense_vector_mib",
                "safety_cap",
            ],
        )
        print("Capacity plan before any index is built:")
        report(capacity_plan)
        print(
            "Visible reference and incoming-query samples suppressed; "
            "customer fields are not written to the training log."
        )

    # ===== TRAINING STAGES 8-9 =====

    with stage("NORMALIZE ALL MODEL-VISIBLE FIELDS"):
        reference = normalize_frame(reference_raw, "record_id")
        incoming = normalize_frame(incoming_raw, "query_id")
        frequency_columns = [
            "n_name_loose",
            "n_kana",
            "n_dob",
            "n_postal",
            "n_address",
            "n_phone",
            "n_email",
            "n_phone_suffix8",
        ]
        VALUE_FREQUENCY = {
            column: reference.loc[reference[column].ne(""), column].value_counts().to_dict()
            for column in frequency_columns
        }

        idempotence_examples = [
            "髙橋　美咲",
            "ﾀｶﾊｼ ﾐｻｷ",
            "東京都文京区後楽一丁目2番3号",
            "０９０－１２３４－５６７８",
        ]
        for value in idempotence_examples:
            assert normalize_name(normalize_name(value)) == normalize_name(value)
        assert normalize_kana(normalize_kana("たかはし ﾐｻｷ")) == normalize_kana("たかはし ﾐｻｷ")
        assert normalize_address(
            normalize_address("東京都文京区後楽一丁目2番3号")
        ) == normalize_address("東京都文京区後楽一丁目2番3号")

        audit_columns = [
            "record_id",
            "name_kanji",
            "n_name_strict",
            "n_name_loose",
            "name_kana",
            "n_kana",
            "address",
            "n_address",
            "phone",
            "n_phone",
            "populated_fields",
        ]
        print("Normalization views validated; customer-field samples are suppressed:")
        print(f"  audited fields: {len(audit_columns)}; records: {len(reference):,}")
        print("Populated identity-field distribution:")
        report(reference.populated_fields.value_counts().sort_index().rename("records").to_frame())
        print("Normalized-value frequency summary; values are suppressed:")
        report(
            pd.DataFrame(
                {
                    "field": frequency_columns,
                    "distinct_values": [
                        len(VALUE_FREQUENCY[column]) for column in frequency_columns
                    ],
                }
            )
        )

    # ===== TRAINING STAGE 11 =====

    # 4. Immutable hybrid candidate index: capped exact blocks, character sparse retrieval, neural or labelled SVD dense retrieval
    from dataclasses import field as dataclass_field

    BLOCK_SPECS = {
        "exact_email": ("n_email",),
        "exact_phone": ("n_phone",),
        "name_dob": ("n_name_loose", "n_dob"),
        "kana_dob": ("n_kana", "n_dob"),
        "signature_postal": ("n_name_signature", "n_postal"),
        "kana_postal": ("n_kana", "n_postal"),
        "name_address": ("n_name_loose", "n_address"),
        "phone_suffix_name": ("n_phone_suffix8", "n_name_loose"),
    }

    def joined_key(row: pd.Series | Any, columns: tuple[str, ...]) -> str:
        values = [
            text(getattr(row, column) if hasattr(row, column) else row[column])
            for column in columns
        ]
        return "¦".join(values) if all(values) else ""

    def make_block_indexes(
        frame: pd.DataFrame,
    ) -> tuple[dict[str, dict[str, np.ndarray]], pd.DataFrame]:
        indexes, audit_rows = {}, []
        positions = np.arange(len(frame), dtype=np.int32)
        for name, columns in BLOCK_SPECS.items():
            keys = frame[list(columns)].astype(str).agg("¦".join, axis=1)
            keys = keys.where(frame[list(columns)].ne("").all(axis=1), "")
            valid = pd.DataFrame({"key": keys, "position": positions}).query("key != ''")
            groups = valid.groupby("key", sort=False).position.apply(
                lambda values: values.to_numpy(np.int32)
            )
            indexes[name] = groups.to_dict()
            sizes = groups.map(len) if len(groups) else pd.Series(dtype=int)
            audit_rows.append(
                {
                    "block": name,
                    "keys": len(groups),
                    "records_covered": int(keys.ne("").sum()),
                    "multi_record_keys": int((sizes > 1).sum()),
                    "largest_block": int(sizes.max() if len(sizes) else 0),
                    "blocks_over_cap": int((sizes > CFG.exact_block_cap).sum()),
                }
            )
        return indexes, pd.DataFrame(audit_rows)

    def load_embedding_model():
        if not NEURAL_ENABLED:
            return None
        from sentence_transformers import SentenceTransformer

        spec = MODELS[CFG.model_family]
        model_name = spec["embedding"]
        import torch

        kwargs: dict[str, Any] = {
            "device": require_mps_device(DEVICE),
            "model_kwargs": {"torch_dtype": torch.float16},
        }
        if CFG.model_family == "qwen":
            kwargs["truncate_dim"] = CFG.embedding_dim
        print(f"Loading embedding model {model_name} on {DEVICE} …")
        return SentenceTransformer(model_name, **kwargs)

    def reference_embeddings_dim(model) -> int:
        try:
            return model.get_embedding_dimension()
        except Exception:
            return model.get_sentence_embedding_dimension()

    def encode_texts(
        model, texts: Iterable[str], *, query: bool, show_progress_bar: bool = True
    ) -> np.ndarray:
        spec = MODELS[CFG.model_family]
        raw = list(texts)
        encode_kwargs = dict(
            batch_size=CFG.neural_batch_size,
            show_progress_bar=show_progress_bar,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        if CFG.model_family == "qwen":
            # Qwen supports a task-specific query instruction; documents are encoded without it.
            if query:
                encode_kwargs["prompt"] = (
                    "Given a Japanese insurance customer record, retrieve records belonging to the same legal person or company."
                )
            prepared = raw
        else:
            prefix = spec["query_prefix"] if query else spec["document_prefix"]
            prepared = [prefix + value for value in raw]
        return model.encode(prepared, **encode_kwargs).astype(np.float32, copy=False)

    def cached_query_embeddings(model, query_texts: list[str]) -> np.ndarray:
        model_id = str(MODELS[CFG.model_family]["embedding"])
        dimensions = reference_embeddings_dim(model)
        vector_path, metadata_path = cache_paths(CFG.data_dir, model_id, "query")
        expected = {
            "model_id": model_id,
            "model_revision": model_revision(model),
            "data_fingerprint": data_fingerprint,
            "query_text_fingerprint": text_fingerprint(query_texts),
            "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
            "field_schema_version": EMBEDDING_FIELD_SCHEMA_VERSION,
            "rows": len(query_texts),
            "dimensions": dimensions,
            "device": DEVICE,
            "torch_dtype": F2LLM_MPS_TORCH_DTYPE,
            "batch_size": CFG.neural_batch_size,
        }
        cached = load_embedding_cache(vector_path, metadata_path, expected)
        if cached is not None:
            print(f"Query embeddings cache hit: {vector_path.name} ({cached.shape})")
            return cached
        vectors = np.empty((len(query_texts), dimensions), dtype=np.float32)
        chunk_size = 1024
        started = time.perf_counter()
        total = len(query_texts)
        for start in range(0, total, chunk_size):
            stop = min(start + chunk_size, total)
            vectors[start:stop] = encode_texts(
                model,
                query_texts[start:stop],
                query=True,
                show_progress_bar=False,
            )
            elapsed = time.perf_counter() - started
            rate = stop / max(elapsed, 1e-9)
            eta = int((total - stop) / max(rate, 1e-9))
            print(
                f"  [query embed] {stop:,}/{total:,} ({100.0 * stop / total:.0f}%) "
                f"eta {eta // 60:02d}:{eta % 60:02d}",
                flush=True,
            )
        save_embedding_cache(vector_path, metadata_path, vectors, expected)
        print(
            f"Query embeddings cached: {vector_path.name} ({vectors.shape}, "
            f"encode {time.perf_counter() - started:.1f}s)"
        )
        return np.load(vector_path, mmap_mode="r", allow_pickle=False)

    def release_mps() -> None:
        gc.collect()
        try:
            import torch

            if DEVICE == "mps":
                torch.mps.empty_cache()
        except Exception:
            pass

    @dataclass(slots=True)
    class CandidateIndex:
        reference: pd.DataFrame
        block_indexes: dict[str, dict[str, np.ndarray]]
        vectorizer: TfidfVectorizer
        sparse_matrix: csr_matrix
        sparse_backend: SparseCandidateBackend
        reference_embeddings: np.ndarray | None = None
        dense_nn: Any | None = None
        dense_backend: str = "disabled"
        svd: TruncatedSVD | None = None
        _query_model: Any | None = dataclass_field(default=None, repr=False)

        def exact_candidates(self, query_row: pd.Series) -> dict[int, set[str]]:
            found: dict[int, set[str]] = {}
            for name, columns in BLOCK_SPECS.items():
                key = joined_key(query_row, columns)
                if not key:
                    continue
                ids = self.block_indexes[name].get(key, np.empty(0, dtype=np.int32))
                if len(ids) > CFG.exact_block_cap:
                    continue
                for index in ids:
                    found.setdefault(int(index), set()).add(name)
            return found

        def sparse_candidates(
            self, query_texts: list[str], k: int
        ) -> tuple[np.ndarray, np.ndarray]:
            if k <= 0 or not query_texts:
                return np.empty((len(query_texts), 0), dtype=np.int32), np.empty(
                    (len(query_texts), 0), dtype=np.float32
                )
            results = self.sparse_backend.retrieve_many(query_texts, k)
            width = max((len(indexes) for indexes, _ in results), default=0)
            indexes = np.full((len(results), width), -1, dtype=np.int32)
            scores = np.zeros((len(results), width), dtype=np.float32)
            for row, (row_indexes, row_scores) in enumerate(results):
                indexes[row, : len(row_indexes)] = row_indexes
                scores[row, : len(row_scores)] = row_scores
            return indexes, scores

        def dense_reference_candidates(
            self, start: int, stop: int, k: int
        ) -> tuple[np.ndarray, np.ndarray]:
            if self.reference_embeddings is None or self.dense_nn is None:
                return np.empty((stop - start, 0), dtype=np.int32), np.empty(
                    (stop - start, 0), dtype=np.float32
                )
            vectors = self.reference_embeddings[start:stop]
            if self.dense_backend == "usearch":
                matches = self.dense_nn.search(vectors, min(k, len(self.reference)))
                indexes = np.asarray(matches.keys, dtype=np.int32)
                scores = 1.0 - np.asarray(matches.distances, dtype=np.float32)
                if indexes.ndim == 1:
                    indexes, scores = indexes.reshape(1, -1), scores.reshape(1, -1)
                return indexes, scores
            distances, indexes = self.dense_nn.kneighbors(
                vectors, n_neighbors=min(k, len(self.reference))
            )
            return indexes.astype(np.int32), (1.0 - distances).astype(np.float32)

        def dense_candidates(self, query_texts: list[str], k: int) -> tuple[np.ndarray, np.ndarray]:
            if (
                k <= 0
                or not query_texts
                or self.reference_embeddings is None
                or self.dense_nn is None
            ):
                return np.empty((len(query_texts), 0), dtype=np.int32), np.empty(
                    (len(query_texts), 0), dtype=np.float32
                )
            if self.dense_backend == "tfidf_svd_degraded":
                vectors = l2_normalize(
                    self.svd.transform(self.vectorizer.transform(query_texts))
                ).astype(np.float32)
            else:
                if self._query_model is None:
                    self._query_model = load_embedding_model()
                vectors = cached_query_embeddings(self._query_model, query_texts)
            if self.dense_backend == "usearch":
                matches = self.dense_nn.search(vectors, min(k, len(self.reference)))
                keys = np.asarray(matches.keys, dtype=np.int32)
                scores = 1.0 - np.asarray(matches.distances, dtype=np.float32)
                if keys.ndim == 1:  # usearch returns 1-D for a single (1,d) query row
                    keys, scores = keys.reshape(1, -1), scores.reshape(1, -1)
            else:
                distances, keys = self.dense_nn.kneighbors(
                    vectors, n_neighbors=min(k, len(self.reference))
                )
                scores = 1.0 - distances
            if CFG.low_memory_models and self._query_model is not None:
                self._query_model = None
                release_mps()
            return keys.astype(np.int32), scores.astype(np.float32)

    with stage("BUILD IMMUTABLE HYBRID CANDIDATE INDEX"):
        block_indexes, block_audit = make_block_indexes(reference)
        print("Exact block audit; common blocks above the cap are ignored rather than exploded:")
        report(block_audit)

        vectorizer = TfidfVectorizer(
            analyzer="char",
            ngram_range=(2, 4),
            min_df=1,
            max_df=0.995,
            max_features=300_000,
            sublinear_tf=True,
            dtype=np.float32,
            norm="l2",
        )
        sparse_matrix = vectorizer.fit_transform(reference.retrieval_text)
        sparse_backend = SparseCandidateBackend(
            sparse_matrix,
            vectorizer,
            batch_size=CFG.sparse_batch_size,
            query_feature_cap=DEFAULT_QUERY_FEATURE_CAP,
        )
        sparse_mib = (
            sparse_matrix.data.nbytes + sparse_matrix.indices.nbytes + sparse_matrix.indptr.nbytes
        ) / 2**20
        print(
            f"Sparse matrix: shape={sparse_matrix.shape}, nnz={sparse_matrix.nnz:,}, memory≈{sparse_mib:,.1f} MiB"
        )

        reference_embeddings, dense_nn, dense_backend, svd = None, None, "disabled", None
        embedding_model_revision_value: str | None = None
        if CFG.use_dense and NEURAL_ENABLED:
            spec = MODELS[CFG.model_family]
            model_id = str(spec["embedding"])
            model_slug = Path(model_id).name
            cache_meta = CFG.data_dir / f"reference_embeddings_{model_slug}_meta.json"
            cache_vec = CFG.data_dir / f"reference_embeddings_{model_slug}.npy"
            ret_texts = reference.retrieval_text.tolist()
            text_fp = text_fingerprint(ret_texts)
            compute_dtype = F2LLM_MPS_TORCH_DTYPE if DEVICE == F2LLM_MPS_DEVICE else "float32"
            model: Any | None = None
            dimensions = int(spec["dimension"] or 0)
            cache_provenance: dict[str, Any] | None = None
            if CFG.strict_backends:
                model = load_embedding_model()
                dimensions = reference_embeddings_dim(model)
                cache_provenance = {
                    "model_id": model_id,
                    "model_revision": model_revision(model),
                    "data_fingerprint": data_fingerprint,
                    "text_fingerprint": text_fp,
                    "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
                    "field_schema_version": EMBEDDING_FIELD_SCHEMA_VERSION,
                    "rows": len(reference),
                    "dimensions": dimensions,
                    "device": DEVICE,
                    "torch_dtype": compute_dtype,
                    "batch_size": CFG.neural_batch_size,
                }
            cached = None
            if cache_vec.exists() and cache_meta.exists():
                meta = json.loads(cache_meta.read_text(encoding="utf-8"))
                base_match = (
                    meta.get("model_id") == model_id
                    and meta.get("data_fingerprint") == data_fingerprint
                    and meta.get("rows") == len(reference)
                )
                if CFG.strict_backends:
                    if cache_provenance is not None:
                        cached = load_embedding_cache(cache_vec, cache_meta, cache_provenance)
                elif base_match:
                    cached = np.load(cache_vec, mmap_mode=None)
                    print(
                        f"WARN: legacy reference embedding cache accepted outside strict mode: {cache_vec.name}"
                    )
                if cached is not None:
                    print(f"Dense embeddings cache hit: {cache_vec.name} ({cached.shape})")
            if cached is not None:
                reference_embeddings = cached
            else:
                if model is None:
                    model = load_embedding_model()
                    dimensions = reference_embeddings_dim(model)
                if cache_provenance is None:
                    cache_provenance = {
                        "model_id": model_id,
                        "model_revision": model_revision(model),
                        "data_fingerprint": data_fingerprint,
                        "text_fingerprint": text_fp,
                        "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
                        "field_schema_version": EMBEDDING_FIELD_SCHEMA_VERSION,
                        "rows": len(reference),
                        "dimensions": dimensions,
                        "device": DEVICE,
                        "torch_dtype": compute_dtype,
                        "batch_size": CFG.neural_batch_size,
                    }
                t_encode = time.perf_counter()
                n_tot = len(ret_texts)
                bs = CFG.neural_batch_size
                reference_embeddings = np.empty((n_tot, dimensions), dtype=np.float32)
                n_print = max(1, n_tot // 20)
                first = time.perf_counter()
                for start in range(0, n_tot, bs):
                    stop = min(start + bs, n_tot)
                    reference_embeddings[start:stop] = model.encode(
                        ret_texts[start:stop],
                        batch_size=stop - start,
                        show_progress_bar=False,
                        normalize_embeddings=True,
                        convert_to_numpy=True,
                    )
                    if (stop // bs) % max(1, n_print // bs) == 0 or stop == n_tot:
                        elapsed = time.perf_counter() - first
                        rate = stop / max(elapsed, 1e-9)
                        eta = int((n_tot - stop) / max(rate, 1e-9))
                        print(
                            f"  [embed] {stop:,}/{n_tot:,} ({100.0 * stop / n_tot:.0f}%) eta {eta // 60:02d}:{eta % 60:02d}",
                            flush=True,
                        )
                print(
                    f"Neural dense vectors: shape={reference_embeddings.shape}, float32 memory={reference_embeddings.nbytes / 2**20:,.1f} MiB (encode {time.perf_counter() - t_encode:.0f}s)"
                )
                save_embedding_cache(cache_vec, cache_meta, reference_embeddings, cache_provenance)
                print(f"Dense embeddings cached -> {cache_vec} (+meta)")
            if cache_provenance is not None:
                embedding_model_revision_value = str(cache_provenance["model_revision"])
            if model is not None:
                del model
                release_mps()
            if BACKENDS["usearch"]:
                from usearch.index import Index

                dense_nn = Index(ndim=reference_embeddings.shape[1], metric="cos", dtype="f16")
                dense_nn.add(
                    np.arange(len(reference_embeddings), dtype=np.int64), reference_embeddings
                )
                dense_backend = "usearch"
            else:
                dense_nn = NearestNeighbors(
                    metric="cosine", algorithm="brute", n_jobs=CFG.model_threads
                ).fit(reference_embeddings)
                dense_backend = "sklearn_exact_neural"
            release_mps()
        elif CFG.use_dense and CFG.allow_svd_dense_fallback:
            dimensions = max(
                2, min(CFG.svd_dimensions, sparse_matrix.shape[0] - 1, sparse_matrix.shape[1] - 1)
            )
            svd = TruncatedSVD(n_components=dimensions, random_state=CFG.seed)
            reference_embeddings = l2_normalize(svd.fit_transform(sparse_matrix)).astype(np.float32)
            dense_nn = NearestNeighbors(
                metric="cosine", algorithm="brute", n_jobs=CFG.model_threads
            ).fit(reference_embeddings)
            dense_backend = "tfidf_svd_degraded"
            print(
                f"DEGRADED dense path: TF-IDF→SVD {reference_embeddings.shape}; this tests plumbing but is not a substitute for Qwen/Ruri."
            )
        else:
            print(
                "Dense retrieval disabled by configuration; exact and sparse retrieval remain active."
            )

        candidate_index = CandidateIndex(
            reference,
            block_indexes,
            vectorizer,
            sparse_matrix,
            sparse_backend,
            reference_embeddings,
            dense_nn,
            dense_backend,
            svd,
        )
        print(f"Candidate index ready. dense_backend={dense_backend}")

    # ===== TRAINING STAGES 13-14 =====

    with stage("GENERATE FULL REFERENCE CANDIDATE GRAPH"):
        candidate_pairs = build_candidate_graph(
            candidate_index,
            CandidateGraphConfig(
                exact_mode=CFG.exact_mode,
                exact_block_cap=CFG.exact_block_cap,
                sparse_top_k=CFG.sparse_top_k,
                dense_top_k=CFG.dense_top_k,
                sparse_batch_size=CFG.sparse_batch_size,
                candidate_cap_per_record=CFG.candidate_cap_per_record,
            ),
        )
        possible_pairs = len(reference) * (len(reference) - 1) // 2
        reduction = 1.0 - len(candidate_pairs) / max(possible_pairs, 1)
        print(
            f"All-pairs comparisons avoided: {possible_pairs:,} → {len(candidate_pairs):,} candidates ({reduction:.6%} reduction)"
        )
        print(
            f"Candidate pairs per record: {2 * len(candidate_pairs) / max(len(reference), 1):,.1f} average"
        )
        print("Retrieval-channel coverage:")
        report(
            pd.DataFrame(
                {
                    "pairs": [
                        len(candidate_pairs),
                        int(candidate_pairs.exact_count.gt(0).sum()),
                        int(candidate_pairs.sparse_score.gt(0).sum()),
                        int(candidate_pairs.dense_score.gt(0).sum()),
                    ]
                },
                index=["union", "exact block", "character sparse", "dense"],
            )
        )
        degree = pd.concat([candidate_pairs.left_index, candidate_pairs.right_index]).value_counts()
        print("Candidate degree distribution (how many candidate neighbours each record receives):")
        report(
            degree.quantile([0, 0.25, 0.5, 0.75, 0.9, 0.99, 1])
            .rename("candidate_degree")
            .to_frame()
        )
        print("Top retrieval candidates before truth or pair labels are loaded:")
        report(candidate_pairs.head(10))

    if NEURAL_ENABLED and reference_embeddings is not None:
        candidate_index.reference_embeddings = None
        candidate_index.dense_nn = None
        reference_embeddings = None
    candidate_index.sparse_backend = None
    candidate_index.sparse_matrix = None
    candidate_index.vectorizer = None
    sparse_backend = None
    sparse_matrix = None
    vectorizer = None
    gc.collect()
    resource_sampling_enabled = False

    # ===== TRAINING STAGES 16-17 =====

    from api.features import PAIR_FEATURE_COLS, compute_pair_features_frame

    PAIR_FEATURES = PAIR_FEATURE_COLS

    def build_pair_features(
        left: pd.DataFrame, right: pd.DataFrame, retrieval: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        return compute_pair_features_frame(
            left,
            right,
            retrieval,
            frequency_maps=VALUE_FREQUENCY,
        )

    # ===== TRAINING STAGE 18 =====

    def load_hidden_truth() -> tuple[
        pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame
    ]:
        record_truth = read_table("record_truth", hidden=True)
        evaluation_truth = read_table("evaluation_truth", hidden=True)
        query_truth = read_table("query_truth", hidden=True)
        party_truth = read_table("party_truth", hidden=True)
        relation_truth = read_table("relation_truth", hidden=True)
        assert set(record_truth.record_id) == set(reference.record_id)
        assert set(evaluation_truth.query_id) == set(incoming.query_id)
        assert set(query_truth.query_id) == set(incoming.query_id)
        return record_truth, evaluation_truth, query_truth, party_truth, relation_truth

    with stage("LOAD HIDDEN TRUTH AND AUDIT CANDIDATE RECALL"):
        record_truth, evaluation_truth, query_truth, party_truth, relation_truth = (
            load_hidden_truth()
        )
        truth_by_record = record_truth.set_index("record_id").truth_id
        split_by_truth = (
            party_truth.set_index("truth_id")["split"].to_dict()
            if "split" in party_truth
            else record_truth.groupby("truth_id").split.first().to_dict()
        )
        candidate_pairs["left_truth_id"] = candidate_pairs.left_record_id.map(truth_by_record)
        candidate_pairs["right_truth_id"] = candidate_pairs.right_record_id.map(truth_by_record)
        candidate_pairs["label"] = candidate_pairs.left_truth_id.eq(
            candidate_pairs.right_truth_id
        ).astype(np.int8)
        candidate_pairs["pair_split"] = [
            split_by_truth.get(left, "")
            if split_by_truth.get(left, "") == split_by_truth.get(right, "")
            else "cross"
            for left, right in zip(
                candidate_pairs.left_truth_id, candidate_pairs.right_truth_id, strict=False
            )
        ]

        truth_group_sizes = record_truth.groupby("truth_id").size()
        true_pairs_total = int(((truth_group_sizes * (truth_group_sizes - 1)) // 2).sum())
        true_pairs_retrieved = int(candidate_pairs.label.sum())
        candidate_pair_recall = true_pairs_retrieved / max(true_pairs_total, 1)
        entities_with_multiple = set(truth_group_sizes[truth_group_sizes > 1].index)
        retrieved_entities = set(candidate_pairs.loc[candidate_pairs.label.eq(1), "left_truth_id"])
        entity_candidate_recall = len(retrieved_entities & entities_with_multiple) / max(
            len(entities_with_multiple), 1
        )

        print(f"True reference pairs:       {true_pairs_total:,}")
        print(f"Retrieved true pairs:       {true_pairs_retrieved:,}")
        print(f"Candidate pair recall:      {candidate_pair_recall:.3%}")
        print(f"Multi-record entity recall: {entity_candidate_recall:.3%}")

        # Mode-independent dataset hardness: share of TRUE pairs with zero shared
        # exact-block anchor (computed from the block index + truth, not the graph).
        def record_anchor_sets(ref: pd.DataFrame, blk: dict) -> dict[int, set[str]]:
            per: dict[int, set[str]] = {}
            for name, groups in blk.items():
                for key, positions in groups.items():
                    tag = f"{name}:{key}"
                    for p in positions:
                        per.setdefault(int(p), set()).add(tag)
            return per

        anchors = record_anchor_sets(reference, block_indexes)
        pos_of = {rid: i for i, rid in enumerate(reference.record_id.tolist())}
        unanchored = total_pairs = 0
        for _, group in record_truth.groupby("truth_id", sort=False):
            members = group.record_id.tolist()
            if len(members) < 2:
                continue
            limit = min(len(members), 6)
            pids = [pos_of.get(r, -1) for r in members[:limit]]
            pids = [p for p in pids if p >= 0]
            for a in range(len(pids)):
                for b in range(a + 1, len(pids)):
                    total_pairs += 1
                    if not (anchors.get(pids[a], set()) & anchors.get(pids[b], set())):
                        unanchored += 1
        unanchored_rate = unanchored / max(total_pairs, 1)
        print(
            f"DATASET HARDNESS (mode-independent): {100.0 * unanchored_rate:.1f}% of TRUE pairs "
            f"share ZERO exact-block anchors (fuzzy-only needed); mode={CFG.exact_mode}"
        )
        true_edges = candidate_pairs.loc[candidate_pairs.label.eq(1)]
        true_edges.exact_count.ge(1).mean() if len(true_edges) else float("nan")
        if CFG.exact_mode == "fuzzy_only":
            print(
                f"RUN: fuzzy-only candidate recall = {candidate_pair_recall:.3%} (exact blocks disabled)"
            )
        elif CFG.exact_mode == "exact_only":
            print(
                f"RUN: exact-only candidate recall = {candidate_pair_recall:.3%} (fuzzy disabled)"
            )
        # Precision stress: same-name CROSS-entity edges (twins impose disambiguation)
        lname = candidate_pairs.left_index.map(lambda p: reference.iloc[p].name_kanji)
        rname = candidate_pairs.right_index.map(lambda p: reference.iloc[p].name_kanji)
        neg = candidate_pairs.label.eq(0)
        same_name_cross = (
            (neg & lname.eq(rname) & lname.ne("")).mean() if neg.any() else float("nan")
        )
        print(
            f"PRECISION-STRESS: {100.0 * same_name_cross:.1f}% of candidate edges are "
            f"same-name CROSS-entity (twin disambiguation load; mode={CFG.exact_mode})"
        )
        print("Recall by hidden test slice (used for evaluation only, never as a feature):")
        slice_table = (
            candidate_pairs.loc[candidate_pairs.label.eq(1)]
            .groupby("pair_split")
            .size()
            .rename("retrieved_true_pairs")
            .to_frame()
        )
        slice_table["total_true_pairs"] = [
            int(
                (
                    (
                        truth_group_sizes[
                            [
                                tid
                                for tid in truth_group_sizes.index
                                if split_by_truth.get(tid) == split_name
                            ]
                        ]
                        * (
                            truth_group_sizes[
                                [
                                    tid
                                    for tid in truth_group_sizes.index
                                    if split_by_truth.get(tid) == split_name
                                ]
                            ]
                            - 1
                        )
                    )
                    // 2
                ).sum()
            )
            if split_name != "cross"
            else 0
            for split_name in slice_table.index
        ]
        slice_table["recall"] = (
            slice_table.retrieved_true_pairs / slice_table.total_true_pairs.replace(0, np.nan)
        )
        report(slice_table)

    del party_truth, relation_truth
    evaluation_truth = None
    query_truth = None
    record_truth = record_truth[["record_id", "truth_id", "split", "hard_case"]].copy()
    candidate_pairs["pair_split"] = candidate_pairs["pair_split"].astype("category")
    candidate_pairs["left_record_id"] = candidate_pairs["left_record_id"].astype("category")
    candidate_pairs["right_record_id"] = candidate_pairs["right_record_id"].astype("category")
    candidate_pairs["exact_blocks"] = candidate_pairs["exact_blocks"].astype("category")
    candidate_pairs.drop(columns=["left_truth_id", "right_truth_id"], inplace=True)
    for column in ("left_index", "right_index"):
        candidate_pairs[column] = candidate_pairs[column].astype(np.int32)
    for column in ("exact_count", "sparse_rank", "dense_rank", "channel_count"):
        candidate_pairs[column] = candidate_pairs[column].astype(np.int16)
    for column in ("sparse_score", "dense_score", "retrieval_score"):
        candidate_pairs[column] = candidate_pairs[column].astype(np.float32)
    gc.collect()

    # ===== TRAINING STAGE 20 =====

    # 7. Entity-disjoint verifier training, optional neural reranking, calibration, and edge decisions

    feature_batch_size = int(os.getenv("NAYOSE_PAIR_FEATURE_BATCH", "100000"))
    if feature_batch_size < 1:
        raise ValueError("NAYOSE_PAIR_FEATURE_BATCH must be positive")

    def sample_training_rows(split_name: str) -> np.ndarray:
        split_values = candidate_pairs["pair_split"].to_numpy()
        labels = candidate_pairs["label"].to_numpy(dtype=np.int8, copy=False)
        split_rows = np.flatnonzero(split_values == split_name)
        positive = split_rows[labels[split_rows] == 1]
        negative = split_rows[labels[split_rows] == 0]
        negative_limit = min(
            len(negative), max(len(positive) * CFG.training_negatives_per_positive, 500)
        )
        if len(negative) > negative_limit:
            hard = select_hard_negatives(
                candidate_pairs,
                reference,
                negative,
                negative_limit,
                frequency_maps=VALUE_FREQUENCY,
            )
        else:
            hard = negative
        selected = np.unique(np.concatenate([positive, hard]))
        RNG.shuffle(selected)
        print(
            f"{split_name:<11} selected rows={len(selected):,}; positives={int(labels[selected].sum()):,}; negatives={int((labels[selected] == 0).sum()):,}"
        )
        return selected

    def feature_frame(rows: np.ndarray) -> pd.DataFrame:
        batch = candidate_pairs.iloc[rows]
        left_batch = reference.iloc[batch.left_index.to_numpy()].reset_index(drop=True)
        right_batch = reference.iloc[batch.right_index.to_numpy()].reset_index(drop=True)
        values = build_pair_features(left_batch, right_batch, batch)
        assert np.isfinite(values.to_numpy(dtype=np.float32, copy=False)).all()
        return values

    def train_pair_model(train_features: pd.DataFrame, train_index: np.ndarray):
        x, y = (
            train_features,
            candidate_pairs.loc[train_index, "label"].to_numpy(),
        )
        if len(np.unique(y)) < 2:
            raise RuntimeError("Pair training data contains only one class")
        use_lightgbm = CFG.pair_model_backend in {"auto", "lightgbm"} and BACKENDS["lightgbm"]
        if CFG.pair_model_backend == "lightgbm" and not BACKENDS["lightgbm"]:
            raise RuntimeError("NAYOSE_PAIR_MODEL=lightgbm requested, but LightGBM is unavailable")
        if use_lightgbm:
            import lightgbm as lgb

            model = lgb.LGBMClassifier(
                objective="binary",
                n_estimators=260,
                learning_rate=0.035,
                num_leaves=31,
                max_depth=-1,
                min_child_samples=25,
                subsample=0.85,
                colsample_bytree=0.85,
                reg_alpha=0.2,
                reg_lambda=0.8,
                random_state=CFG.seed,
                verbosity=-1,
                n_jobs=CFG.model_threads,
                force_col_wise=True,
            )
            model.fit(x, y)
            backend = "lightgbm"
        else:
            from sklearn.ensemble import HistGradientBoostingClassifier

            model = HistGradientBoostingClassifier(
                max_iter=220,
                learning_rate=0.045,
                max_leaf_nodes=31,
                l2_regularization=0.8,
                random_state=CFG.seed,
            )
            model.fit(x, y)
            backend = "sklearn_hist_gradient_boosting"
        return model, backend

    def pair_base_probability(model, features: pd.DataFrame) -> np.ndarray:
        return np.asarray(model.predict_proba(features[PAIR_FEATURES])[:, 1], dtype=np.float64)

    def serialize_record(row: pd.Series) -> str:
        return " | ".join(
            [
                f"種別={row.party_type or '[MISSING]'}",
                f"氏名漢字={row.name_kanji or '[MISSING]'}",
                f"氏名カナ={row.name_kana or '[MISSING]'}",
                f"ローマ字={row.name_romaji or '[MISSING]'}",
                f"生年月日={row.date_birth or '[MISSING]'}",
                f"郵便番号={row.postal_code or '[MISSING]'}",
                f"住所={row.address or '[MISSING]'}",
                f"電話={row.phone or '[MISSING]'}",
                f"メール={row.email or '[MISSING]'}",
            ]
        )

    def _load_cross_encoder(model_name: str):
        from sentence_transformers import CrossEncoder

        kwargs: dict[str, Any] = {"device": DEVICE, "trust_remote_code": True}
        if DEVICE in {"mps", "cuda"}:
            try:
                import torch

                kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
            except Exception:
                pass
        return CrossEncoder(model_name, **kwargs)

    def _rerank_predict(model, examples: list[str]) -> np.ndarray:
        """Length-sorted batched cross-encoder scoring (less MPS padding waste)."""
        lens = np.asarray(
            [len(t) for t in model.tokenizer(examples, add_special_tokens=False)["input_ids"]]
        )
        order = np.argsort(lens)
        out = np.empty(len(examples), dtype=np.float32)
        bs = max(1, CFG.neural_batch_size // 2)
        t0 = time.perf_counter()
        n_tot = len(examples)
        n_print = max(1, n_tot // 20)
        for start in range(0, n_tot, bs):
            idx = order[start : start + bs]
            vals = np.asarray(
                model.predict([examples[i] for i in idx], batch_size=bs, show_progress_bar=False)
            ).reshape(-1)
            out[idx] = vals
            if start % max(1, bs * n_print) < bs or start + bs >= n_tot:
                done = min(start + bs, n_tot)
                elapsed = time.perf_counter() - t0
                eta = int((n_tot - done) / max(done / max(elapsed, 1e-9), 1e-9))
                print(
                    f"  [rerank] {done:,}/{n_tot:,} ({100.0 * done / n_tot:.0f}%) eta {eta // 60:02d}:{eta % 60:02d}",
                    flush=True,
                )
        return out

    def rerank_pairs(left: pd.DataFrame, right: pd.DataFrame) -> np.ndarray:
        if not NEURAL_ENABLED or not MODELS[CFG.model_family]["reranker"] or not len(left):
            return np.zeros(len(left), dtype=np.float32)
        model_name = MODELS[CFG.model_family]["reranker"]
        print(f"Loading reranker {model_name} on {DEVICE} for {len(left):,} selected pairs …")
        model = _load_cross_encoder(model_name)
        instruction = "Determine whether the two Japanese insurance customer records refer to the same legal person or company."
        examples = [
            (instruction + "\nRecord A: " + serialize_record(a), "Record B: " + serialize_record(b))
            for (_, a), (_, b) in zip(left.iterrows(), right.iterrows(), strict=False)
        ]
        scores = _rerank_predict(model, examples)
        if scores.min(initial=0) < 0 or scores.max(initial=1) > 1:
            scores = expit(scores)
        del model
        gc.collect()
        try:
            import torch

            if DEVICE == "mps":
                torch.mps.empty_cache()
        except Exception:
            pass
        return scores.astype(np.float32)

    def logit_clip(probability: np.ndarray) -> np.ndarray:
        probability = np.clip(probability, 1e-6, 1 - 1e-6)
        return np.log(probability / (1 - probability))

    def wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
        if total == 0:
            return 0.0
        p = successes / total
        denominator = 1 + z * z / total
        center = p + z * z / (2 * total)
        radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
        return (center - radius) / denominator

    # ===== TRAINING STAGE 21 =====

    def threshold_for_precision(
        scores: np.ndarray, labels: np.ndarray, target: float, minimum_predictions: int = 8
    ) -> tuple[float, pd.DataFrame, str]:
        """Choose a threshold and state whether the requested precision bound was actually proved."""
        order = np.argsort(-scores)
        ordered_scores, ordered_labels = scores[order], labels[order].astype(int)
        tp = np.cumsum(ordered_labels)
        count = np.arange(1, len(labels) + 1)
        precision = tp / count
        recall = tp / max(int(ordered_labels.sum()), 1)
        lower = np.array([wilson_lower(int(a), int(b)) for a, b in zip(tp, count, strict=False)])
        f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-12)
        curve = pd.DataFrame(
            {
                "threshold": ordered_scores,
                "predictions": count,
                "true_positives": tp,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "wilson_lower_95": lower,
            }
        )
        eligible = np.flatnonzero((lower >= target) & (count >= minimum_predictions))
        if len(eligible):
            position, status = int(eligible[-1]), "TARGET_MET"
        elif CFG.decision_profile == "audit" and len(curve):
            valid = np.flatnonzero(count >= min(minimum_predictions, len(count)))
            position = int(valid[np.argmax(f1[valid])]) if len(valid) else int(np.argmax(f1))
            status = "AUDIT_F1_FALLBACK_TARGET_NOT_PROVEN"
        else:
            return 1.000001, curve, "TARGET_NOT_MET_ABSTAIN"
        return float(ordered_scores[position]), curve, status

    feature_index = {name: index for index, name in enumerate(PAIR_FEATURES)}
    with stage("TRAIN ENTITY-DISJOINT PAIR VERIFIER"):
        train_index = sample_training_rows("train")
        calibration_index = sample_training_rows("calibration")
        test_index = candidate_pairs.index[candidate_pairs.pair_split.eq("test")].to_numpy()
        train_features = feature_frame(train_index)
        pair_model, pair_model_backend = train_pair_model(train_features, train_index)
        base_probability = np.empty(len(candidate_pairs), dtype=np.float64)
        for start in range(0, len(candidate_pairs), feature_batch_size):
            stop = min(start + feature_batch_size, len(candidate_pairs))
            base_probability[start:stop] = pair_base_probability(
                pair_model,
                feature_frame(np.arange(start, stop, dtype=np.int64)),
            )
        candidate_pairs["base_probability"] = base_probability
        print(f"Verifier backend: {pair_model_backend}")

        rerank_selected = candidate_pairs.nlargest(
            min(CFG.rerank_pair_limit, len(candidate_pairs)),
            ["base_probability", "retrieval_score"],
        ).index
        candidate_pairs["reranker_score"] = 0.0
        if NEURAL_ENABLED and len(rerank_selected):
            selected_pairs = candidate_pairs.loc[rerank_selected]
            rerank_left = reference.iloc[selected_pairs.left_index.to_numpy()].reset_index(
                drop=True
            )
            rerank_right = reference.iloc[selected_pairs.right_index.to_numpy()].reset_index(
                drop=True
            )
            candidate_pairs.loc[rerank_selected, "reranker_score"] = rerank_pairs(
                rerank_left, rerank_right
            )
        candidate_pairs["reranker_present"] = (
            candidate_pairs.index.isin(rerank_selected).astype(np.float32)
            if NEURAL_ENABLED
            else 0.0
        )

        calibration_x = np.column_stack(
            [
                logit_clip(candidate_pairs.loc[calibration_index, "base_probability"].to_numpy()),
                candidate_pairs.loc[calibration_index, "reranker_score"].to_numpy(),
                candidate_pairs.loc[calibration_index, "reranker_present"].to_numpy(),
            ]
        )
        calibration_y = candidate_pairs.loc[calibration_index, "label"].to_numpy()
        if len(np.unique(calibration_y)) >= 2:
            pair_calibrator = LogisticRegression(C=1.0, max_iter=1000, random_state=CFG.seed).fit(
                calibration_x, calibration_y
            )
            stacked_x = np.column_stack(
                [
                    logit_clip(candidate_pairs.base_probability.to_numpy()),
                    candidate_pairs.reranker_score.to_numpy(),
                    candidate_pairs.reranker_present.to_numpy(),
                ]
            )
            candidate_pairs["pair_probability"] = pair_calibrator.predict_proba(stacked_x)[:, 1]
            calibrator_backend = "platt_logistic_with_optional_reranker"
        else:
            pair_calibrator = None
            candidate_pairs["pair_probability"] = candidate_pairs.base_probability
            calibrator_backend = "identity_fallback"

        auto_threshold, auto_curve, auto_threshold_status = threshold_for_precision(
            candidate_pairs.loc[calibration_index, "pair_probability"].to_numpy(),
            calibration_y,
            CFG.pair_auto_precision_target,
        )
        review_threshold, review_curve, review_threshold_status = threshold_for_precision(
            candidate_pairs.loc[calibration_index, "pair_probability"].to_numpy(),
            calibration_y,
            CFG.pair_review_precision_target,
        )
        review_threshold = (
            min(review_threshold, auto_threshold - 1e-6)
            if auto_threshold <= 1
            else review_threshold
        )
        review_threshold = max(0.0, review_threshold)

        print(f"Calibration backend: {calibrator_backend}")
        print(
            f"AUTO edge threshold:   {auto_threshold:.6f} — {auto_threshold_status} (requested Wilson lower bound ≥ {CFG.pair_auto_precision_target:.1%})"
        )
        print(
            f"REVIEW edge threshold: {review_threshold:.6f} — {review_threshold_status} (requested Wilson lower bound ≥ {CFG.pair_review_precision_target:.1%})"
        )
        selected_curve_rows = (
            pd.concat(
                [
                    auto_curve.iloc[
                        (auto_curve.threshold - auto_threshold).abs().argsort()[:1]
                    ].assign(decision="AUTO"),
                    review_curve.iloc[
                        (review_curve.threshold - review_threshold).abs().argsort()[:1]
                    ].assign(decision="REVIEW"),
                ],
                ignore_index=True,
            )
            if len(auto_curve) and len(review_curve)
            else pd.DataFrame()
        )
        print("Threshold audit rows at the selected cutoffs:")
        report(selected_curve_rows)

        hard_conflict = np.empty(len(candidate_pairs), dtype=bool)
        sufficient_evidence = np.empty(len(candidate_pairs), dtype=bool)
        name_anchor = np.empty(len(candidate_pairs), dtype=bool)
        identity_anchor = np.empty(len(candidate_pairs), dtype=bool)
        for start in range(0, len(candidate_pairs), feature_batch_size):
            stop = min(start + feature_batch_size, len(candidate_pairs))
            batch_features = feature_frame(np.arange(start, stop, dtype=np.int64))
            hard_conflict[start:stop] = (
                batch_features.type_conflict.to_numpy(dtype=np.float32) == 1
            ) | (batch_features.dob_conflict.to_numpy(dtype=np.float32) == 1)
            sufficient_evidence[start:stop] = (
                batch_features.evidence_family_count.to_numpy(dtype=np.float32)
                >= CFG.minimum_evidence_families
            )
            name_anchor[start:stop] = (
                batch_features.name_strict_exact.to_numpy(dtype=np.float32) == 1
            ) | (batch_features.name_loose_exact.to_numpy(dtype=np.float32) == 1)
            identity_anchor[start:stop] = (
                (batch_features.dob_exact.to_numpy(dtype=np.float32) == 1)
                | (batch_features.email_exact.to_numpy(dtype=np.float32) == 1)
            ) & name_anchor[start:stop]
        auto_policy_eligible = ~hard_conflict & sufficient_evidence & identity_anchor
        candidate_pairs["edge_action"] = np.select(
            [
                candidate_pairs.pair_probability.ge(auto_threshold) & auto_policy_eligible,
                candidate_pairs.pair_probability.ge(review_threshold),
            ],
            ["AUTO_EDGE", "REVIEW_EDGE"],
            default="REJECT_EDGE",
        )
        candidate_pairs["edge_policy_reason"] = np.select(
            [
                candidate_pairs.edge_action.eq("AUTO_EDGE"),
                candidate_pairs.pair_probability.ge(auto_threshold) & hard_conflict,
                candidate_pairs.pair_probability.ge(auto_threshold) & ~sufficient_evidence,
                candidate_pairs.pair_probability.ge(auto_threshold) & ~identity_anchor,
                candidate_pairs.pair_probability.ge(review_threshold),
            ],
            [
                "eligible",
                "hard_conflict",
                "insufficient_evidence",
                "identity_anchor_required",
                "review_threshold",
            ],
            default="below_review_threshold",
        )

        if hasattr(pair_model, "feature_importances_"):
            importance = (
                pd.Series(pair_model.feature_importances_, index=PAIR_FEATURES)
                .sort_values(ascending=False)
                .rename("importance")
            )
            print("Model feature importance:")
            report(importance.head(20).to_frame())
        print("Edge-action volume across the full candidate graph:")
        report(candidate_pairs.edge_action.value_counts().rename("edges").to_frame())
        assert not (candidate_pairs.edge_action.eq("AUTO_EDGE") & hard_conflict).any()

        if len(test_index):
            y_test = candidate_pairs.loc[test_index, "label"].to_numpy()
            p_test = candidate_pairs.loc[test_index, "pair_probability"].to_numpy()
            policy_safe = (
                ~hard_conflict[test_index]
                & sufficient_evidence[test_index]
                & identity_anchor[test_index]
            )
            auto_mask = (p_test >= auto_threshold) & policy_safe
            tp = int((auto_mask & (y_test == 1)).sum())
            fp = int((auto_mask & (y_test == 0)).sum())
            test_entity_ids = [tid for tid, split in split_by_truth.items() if split == "test"]
            test_sizes = truth_group_sizes.reindex(test_entity_ids).fillna(0).astype(int)
            total_test_true_pairs = int(((test_sizes * (test_sizes - 1)) // 2).sum())
            tn = int((~auto_mask & (y_test == 0)).sum())
            fn = int((~auto_mask & (y_test == 1)).sum())
            precision = tp / max(tp + fp, 1)
            recall = tp / max(tp + fn, 1)
            specificity = tn / max(tn + fp, 1)
            pair_confusion_test = pd.DataFrame(
                {
                    "metric": [
                        "true_positives",
                        "false_positives",
                        "true_negatives",
                        "false_negatives",
                        "precision",
                        "recall",
                        "specificity",
                        "f1",
                        "auc",
                        "n_test_edges",
                        "n_test_true_pairs",
                        "threshold",
                    ],
                    "value": [
                        tp,
                        fp,
                        tn,
                        fn,
                        precision,
                        recall,
                        specificity,
                        2 * precision * recall / max(precision + recall, 1e-12),
                        float(average_precision_score(y_test, p_test))
                        if len(np.unique(y_test)) > 1
                        else float("nan"),
                        len(y_test),
                        int((y_test == 1).sum()),
                        float(auto_threshold),
                    ],
                }
            )
            print("== PAIR CONFUSION MATRIX — TEST SPLIT ONLY (unseen in train/calibration) ==")
            report(pair_confusion_test.set_index("metric"))
            print("matrix layout row order: TN FP / FN TP ->")
            report(
                pd.DataFrame(
                    [[tn, fp], [fn, tp]], index=["pred=0", "pred=1"], columns=["true=0", "true=1"]
                )
            )
            pair_test_metrics = {
                "candidate_average_precision": float(average_precision_score(y_test, p_test))
                if len(np.unique(y_test)) > 1
                else float("nan"),
                "auto_edge_precision": precision,
                "auto_edge_candidate_recall": recall,
                "auto_edge_end_to_end_recall": tp / max(total_test_true_pairs, 1),
                "auto_edge_predictions": int(auto_mask.sum()),
                "auto_edge_specificity": specificity,
                "auto_edge_f1": 2 * precision * recall / max(precision + recall, 1e-12),
            }
            print("Held-out pair metrics; end-to-end recall includes candidate-generation misses:")
            report(pd.DataFrame.from_dict(pair_test_metrics, orient="index", columns=["value"]))
        else:
            pair_test_metrics = {}

    # ===== TRAINING STAGE 23 =====

    # 8. Constrained full-dataset clustering, vectorized canonicalization, and truth-based cluster audit
    class ConstrainedUnionFind:
        def __init__(self, frame: pd.DataFrame):
            n = len(frame)
            self.parent = np.arange(n, dtype=np.int32)
            self.size = np.ones(n, dtype=np.int32)
            self.types = [{value} if value else set() for value in frame.n_party_type]
            self.dobs = [{value} if value else set() for value in frame.n_dob]

        def find(self, node: int) -> int:
            while self.parent[node] != node:
                self.parent[node] = self.parent[self.parent[node]]
                node = int(self.parent[node])
            return int(node)

        def can_union(self, left: int, right: int) -> tuple[bool, str]:
            left_root, right_root = self.find(left), self.find(right)
            if left_root == right_root:
                return True, "already_connected"
            if len(self.types[left_root] | self.types[right_root]) > 1:
                return False, "cluster_party_type_conflict"
            if len(self.dobs[left_root] | self.dobs[right_root]) > 1:
                return False, "cluster_birth_date_conflict"
            return True, "consistent"

        def union(self, left: int, right: int) -> int:
            left_root, right_root = self.find(left), self.find(right)
            if left_root == right_root:
                return left_root
            if self.size[left_root] < self.size[right_root]:
                left_root, right_root = right_root, left_root
            self.parent[right_root] = left_root
            self.size[left_root] += self.size[right_root]
            self.types[left_root] |= self.types[right_root]
            self.dobs[left_root] |= self.dobs[right_root]
            return left_root

    # ===== TRAINING STAGE 24 =====

    def b3_metrics(assignments: pd.DataFrame) -> dict[str, float]:
        intersections = assignments.groupby(["nayose_id", "truth_id"]).size().rename("intersection")
        cluster_sizes = assignments.groupby("nayose_id").size()
        truth_sizes = assignments.groupby("truth_id").size()
        joined = assignments.join(intersections, on=["nayose_id", "truth_id"])
        precision = (joined.intersection / joined.nayose_id.map(cluster_sizes)).mean()
        recall = (joined.intersection / joined.truth_id.map(truth_sizes)).mean()
        return {
            "b3_precision": float(precision),
            "b3_recall": float(recall),
            "b3_f1": float(2 * precision * recall / max(precision + recall, 1e-12)),
        }

    with stage("FORM CONSTRAINED NAYOSE ENTITIES FROM EVERY AUTO EDGE"):
        union_find = ConstrainedUnionFind(reference)
        union_rows = []
        auto_edges = candidate_pairs.loc[candidate_pairs.edge_action.eq("AUTO_EDGE")].sort_values(
            "pair_probability", ascending=False
        )
        for edge in auto_edges.itertuples(index=False):
            allowed, reason = union_find.can_union(int(edge.left_index), int(edge.right_index))
            if allowed:
                union_find.union(int(edge.left_index), int(edge.right_index))
            union_rows.append(
                {
                    "left_record_id": edge.left_record_id,
                    "right_record_id": edge.right_record_id,
                    "pair_probability": float(edge.pair_probability),
                    "accepted": bool(allowed),
                    "reason": reason,
                }
            )
        union_log = pd.DataFrame(
            union_rows,
            columns=["left_record_id", "right_record_id", "pair_probability", "accepted", "reason"],
        )

        roots = np.fromiter(
            (union_find.find(i) for i in range(len(reference))),
            dtype=np.int32,
            count=len(reference),
        )
        root_to_id = {root: f"N{rank:09d}" for rank, root in enumerate(sorted(np.unique(roots)))}
        cluster_map = pd.DataFrame(
            {"record_id": reference.record_id, "nayose_id": [root_to_id[root] for root in roots]}
        )
        record_to_nayose = cluster_map.set_index("record_id")["nayose_id"]
        reference_clustered = reference_raw.merge(
            cluster_map, on="record_id", validate="one_to_one"
        )

        review_queue = candidate_pairs.loc[candidate_pairs.edge_action.eq("REVIEW_EDGE")].copy()
        review_queue = review_queue.merge(
            cluster_map.rename(
                columns={"record_id": "left_record_id", "nayose_id": "left_nayose_id"}
            ),
            on="left_record_id",
        )
        review_queue = review_queue.merge(
            cluster_map.rename(
                columns={"record_id": "right_record_id", "nayose_id": "right_nayose_id"}
            ),
            on="right_record_id",
        )
        review_queue = review_queue.loc[
            review_queue.left_nayose_id.ne(review_queue.right_nayose_id)
        ].sort_values("pair_probability", ascending=False)

        print(f"Reference records resolved: {len(reference_clustered):,}")
        print(f"Predicted entities before canonicalization: {cluster_map.nayose_id.nunique():,}")
        print(f"Accepted union edges: {int(union_log.accepted.sum()) if len(union_log) else 0:,}")
        print(
            f"Constraint-rejected edges: {int((~union_log.accepted).sum()) if len(union_log) else 0:,}"
        )
        print(f"Manual-review inter-entity edges: {len(review_queue):,}")
        if len(union_log):
            report(union_log.groupby(["accepted", "reason"]).size().rename("edges").reset_index())

    with stage("BUILD CANONICAL ENTITIES AND LONG-FORM ATTRIBUTE HISTORIES"):
        history_fields = [
            "party_type",
            "name_kanji",
            "name_kana",
            "name_romaji",
            "date_birth",
            "postal_code",
            "address",
            "phone",
            "email",
        ]
        history_parts = []
        for field_name in history_fields:
            values = reference_clustered.loc[
                reference_clustered[field_name].astype(str).ne(""),
                ["nayose_id", field_name, "observed_at", "agency_id", "record_id"],
            ].copy()
            if values.empty:
                continue
            summary = (
                values.groupby(["nayose_id", field_name], sort=False)
                .agg(
                    support_records=("record_id", "nunique"),
                    support_agencies=("agency_id", "nunique"),
                    first_seen=("observed_at", "min"),
                    last_seen=("observed_at", "max"),
                )
                .reset_index()
                .rename(columns={field_name: "value"})
            )
            summary.insert(1, "field", field_name)
            history_parts.append(summary)
        entity_attribute_history = pd.concat(history_parts, ignore_index=True)
        entity_attribute_history = entity_attribute_history.sort_values(
            ["nayose_id", "field", "support_records", "last_seen"],
            ascending=[True, True, False, False],
        ).reset_index(drop=True)

        cluster_base = (
            reference_clustered.groupby("nayose_id", sort=True)
            .agg(
                record_count=("record_id", "size"),
                policy_count=("policy_id", "nunique"),
                agency_count=("agency_id", "nunique"),
                first_seen=("observed_at", "min"),
                last_seen=("observed_at", "max"),
            )
            .reset_index()
        )
        canonical_entities = cluster_base.copy()
        best_values = entity_attribute_history.drop_duplicates(["nayose_id", "field"])
        for field_name in history_fields:
            selected = best_values.loc[
                best_values.field.eq(field_name), ["nayose_id", "value"]
            ].rename(columns={"value": field_name})
            canonical_entities = canonical_entities.merge(selected, on="nayose_id", how="left")
        canonical_entities[history_fields] = canonical_entities[history_fields].fillna("")

        internal = candidate_pairs.merge(
            cluster_map.rename(columns={"record_id": "left_record_id", "nayose_id": "left_nayose"}),
            on="left_record_id",
        )
        internal = internal.merge(
            cluster_map.rename(
                columns={"record_id": "right_record_id", "nayose_id": "right_nayose"}
            ),
            on="right_record_id",
        )
        internal = internal.loc[internal.left_nayose.eq(internal.right_nayose)]
        edge_summary = (
            internal.groupby("left_nayose")
            .pair_probability.agg(["min", "mean", "max", "count"])
            .rename_axis("nayose_id")
            .reset_index()
        )
        cluster_diagnostics = cluster_base.merge(edge_summary, on="nayose_id", how="left").fillna(
            {"min": 0, "mean": 0, "max": 0, "count": 0}
        )
        cluster_diagnostics["confidence_tier"] = np.select(
            [
                cluster_diagnostics.record_count.eq(1),
                cluster_diagnostics["min"].ge(auto_threshold),
                cluster_diagnostics["mean"].ge(review_threshold),
            ],
            ["SINGLETON", "HIGH", "MEDIUM"],
            default="LOW",
        )

        sizes = canonical_entities.record_count
        size_distribution = (
            sizes.clip(upper=10)
            .value_counts()
            .sort_index()
            .rename_axis("records_per_entity_capped_10")
            .rename("entities")
            .reset_index()
        )
        size_distribution["records"] = (
            size_distribution.records_per_entity_capped_10 * size_distribution.entities
        )
        print(f"Canonical entities: {len(canonical_entities):,}")
        print(f"Distinct historical attribute values: {len(entity_attribute_history):,}")
        print("Predicted entity-size distribution (10 means 10 or more records):")
        report(size_distribution)
        print("Predicted confidence tiers:")
        report(cluster_diagnostics.confidence_tier.value_counts().rename("entities").to_frame())
        print("Largest predicted entities:")
        report(
            canonical_entities.nlargest(10, "record_count")[
                [
                    "nayose_id",
                    "party_type",
                    "record_count",
                    "policy_count",
                    "agency_count",
                    "name_kanji",
                    "address",
                ]
            ]
        )

    with stage("EVALUATE FULL-DATASET CLUSTER QUALITY AGAINST HIDDEN TRUTH"):
        assignments = cluster_map.merge(
            record_truth[["record_id", "truth_id", "split", "hard_case"]],
            on="record_id",
            validate="one_to_one",
        )
        cluster_truth = (
            assignments.groupby(["nayose_id", "truth_id"]).size().rename("records").reset_index()
        )
        truth_counts_per_cluster = cluster_truth.groupby("nayose_id").truth_id.nunique()
        clusters_per_truth = cluster_truth.groupby("truth_id").nayose_id.nunique()
        contaminated_clusters = set(truth_counts_per_cluster[truth_counts_per_cluster > 1].index)
        fragmented_truth = set(clusters_per_truth[clusters_per_truth > 1].index)
        cluster_metrics = b3_metrics(assignments)
        cluster_metrics.update(
            {
                "predicted_entities": len(canonical_entities),
                "true_entities": assignments.truth_id.nunique(),
                "singleton_cluster_rate": float(canonical_entities.record_count.eq(1).mean()),
                "contaminated_cluster_rate": float(
                    canonical_entities.nayose_id.isin(contaminated_clusters).mean()
                ),
                "overmerge_record_rate": float(
                    assignments.nayose_id.isin(contaminated_clusters).mean()
                ),
                "undermerge_record_rate": float(assignments.truth_id.isin(fragmented_truth).mean()),
            }
        )
        cluster_purity = (
            cluster_truth.groupby("nayose_id").records.max()
            / cluster_truth.groupby("nayose_id").records.sum()
        )
        cluster_diagnostics = cluster_diagnostics.merge(
            cluster_purity.rename("truth_purity"), on="nayose_id", how="left"
        )
        cluster_diagnostics["truth_status"] = np.select(
            [cluster_diagnostics.truth_purity.eq(1), cluster_diagnostics.truth_purity.ge(0.5)],
            ["PURE", "CONTAMINATED_MAJORITY"],
            default="CONTAMINATED_NO_MAJORITY",
        )
        contaminated_audit = cluster_truth.merge(
            cluster_diagnostics[["nayose_id", "record_count", "confidence_tier", "truth_purity"]],
            on="nayose_id",
        ).sort_values(["truth_purity", "record_count"], ascending=[True, False])
        fragmented_audit = cluster_truth.merge(
            clusters_per_truth.rename("predicted_cluster_count"), on="truth_id"
        )
        fragmented_audit = fragmented_audit.query("predicted_cluster_count > 1").sort_values(
            ["predicted_cluster_count", "truth_id"], ascending=[False, True]
        )

        print(
            "Full-reference clustering metrics; B³ measures per-record overmerge and undermerge behavior:"
        )
        report(pd.DataFrame.from_dict(cluster_metrics, orient="index", columns=["value"]))
        print("Predicted confidence tier versus independent truth purity:")
        report(
            pd.crosstab(
                cluster_diagnostics.confidence_tier, cluster_diagnostics.truth_status, margins=True
            )
        )
        print("Most contaminated predicted entities:")
        report(contaminated_audit.head(20))
        print("Most fragmented latent parties:")
        report(fragmented_audit.head(20))

    # ===== TRAINING STAGE 26 =====

    # 9. One online recommendation path: validate → normalize → retrieve → score records → aggregate entities
    ENTITY_FEATURES = [
        "max_pair_probability",
        "second_pair_probability",
        "mean_top3_probability",
        "record_score_margin",
        "support_record_count",
        "support_agency_count",
        "candidate_entity_size",
        "max_name_similarity",
        "max_kana_similarity",
        "max_romaji_similarity",
        "max_address_similarity",
        "any_name_exact",
        "any_dob_exact",
        "any_phone_exact",
        "any_email_exact",
        "any_postal_exact",
        "any_identity_anchor",
        "max_evidence_family_count",
        "max_retrieval_score",
        "max_channel_count",
        "hard_conflict_count",
        "review_or_auto_record_count",
    ]

    class RerankerService:
        def __init__(self):
            self.model = None

        def score(self, left: pd.DataFrame, right: pd.DataFrame) -> np.ndarray:
            if not NEURAL_ENABLED or not MODELS[CFG.model_family]["reranker"] or not len(left):
                return np.zeros(len(left), dtype=np.float32)
            if self.model is None:
                model_name = MODELS[CFG.model_family]["reranker"]
                print(f"Lazy-loading online reranker {model_name} on {DEVICE}")
                self.model = _load_cross_encoder(model_name)
            instruction = "Determine whether the two Japanese insurance customer records refer to the same legal person or company."
            examples = [
                (
                    instruction + "\nRecord A: " + serialize_record(a),
                    "Record B: " + serialize_record(b),
                )
                for (_, a), (_, b) in zip(left.iterrows(), right.iterrows(), strict=False)
            ]
            values = _rerank_predict(self.model, examples)
            if values.min(initial=0) < 0 or values.max(initial=1) > 1:
                values = expit(values)
            return values.astype(np.float32)

    RERANKER_SERVICE = RerankerService()

    def normalize_raw_query(raw: dict[str, Any]) -> pd.Series:
        row = {column: text(raw.get(column, "")) for column in QUERY_REQUIRED}
        row["query_id"] = row["query_id"] or "MANUAL"
        row["agency_id"] = row["agency_id"] or "MANUAL"
        row["channel"] = row["channel"] or "manual_entry"
        row["observed_at"] = row["observed_at"] or pd.Timestamp.utcnow().date().isoformat()
        return normalize_frame(pd.DataFrame([row]), "query_id").iloc[0]

    def query_is_insufficient(query: pd.Series) -> tuple[bool, str]:
        strong = sum(
            bool(query[column])
            for column in [
                "n_name_loose",
                "n_kana",
                "n_romaji",
                "n_dob",
                "n_address",
                "n_phone",
                "n_email",
            ]
        )
        if strong == 0:
            return True, "No usable identity field was supplied."
        if strong == 1 and not (query.n_phone or query.n_email or query.n_dob):
            return (
                True,
                "Only one weak identity family was supplied; nearest-neighbour output would be unsafe.",
            )
        return False, ""

    def retrieve_query(query: pd.Series, index: CandidateIndex) -> pd.DataFrame:
        states: dict[int, dict[str, Any]] = {}
        if CFG.exact_mode != "fuzzy_only":
            for candidate, blocks in index.exact_candidates(query).items():
                state = states.setdefault(
                    candidate,
                    {
                        "exact_blocks": set(),
                        "sparse_score": 0.0,
                        "dense_score": 0.0,
                        "sparse_rank": 0,
                        "dense_rank": 0,
                    },
                )
                state["exact_blocks"].update(blocks)

        sparse_ids, sparse_scores = index.sparse_candidates(
            [query.retrieval_text], CFG.sparse_top_k
        )
        for rank, (candidate, score) in enumerate(
            zip(sparse_ids[0], sparse_scores[0], strict=False), start=1
        ):
            state = states.setdefault(
                int(candidate),
                {
                    "exact_blocks": set(),
                    "sparse_score": 0.0,
                    "dense_score": 0.0,
                    "sparse_rank": 0,
                    "dense_rank": 0,
                },
            )
            if float(score) > state["sparse_score"]:
                state["sparse_score"], state["sparse_rank"] = float(score), rank

        dense_ids, dense_scores = index.dense_candidates([query.retrieval_text], CFG.dense_top_k)
        for rank, (candidate, score) in enumerate(
            zip(
                dense_ids[0] if len(dense_ids) else [],
                dense_scores[0] if len(dense_scores) else [],
                strict=False,
            ),
            start=1,
        ):
            state = states.setdefault(
                int(candidate),
                {
                    "exact_blocks": set(),
                    "sparse_score": 0.0,
                    "dense_score": 0.0,
                    "sparse_rank": 0,
                    "dense_rank": 0,
                },
            )
            if float(score) > state["dense_score"]:
                state["dense_score"], state["dense_rank"] = float(score), rank

        rows = []
        for candidate, state in states.items():
            exact_blocks = sorted(state["exact_blocks"])
            exact_count = len(exact_blocks)
            retrieval_score = max(state["sparse_score"], state["dense_score"]) + 0.08 * min(
                exact_count, 3
            )
            rows.append(
                {
                    "candidate_index": candidate,
                    "exact_blocks": ",".join(exact_blocks),
                    "exact_count": exact_count,
                    "sparse_score": state["sparse_score"],
                    "dense_score": state["dense_score"],
                    "sparse_rank": state["sparse_rank"],
                    "dense_rank": state["dense_rank"],
                    "retrieval_score": retrieval_score,
                    "channel_count": int(exact_count > 0)
                    + int(state["sparse_score"] > 0)
                    + int(state["dense_score"] > 0),
                }
            )
        if not rows:
            return pd.DataFrame(
                columns=[
                    "candidate_index",
                    "exact_blocks",
                    "exact_count",
                    "sparse_score",
                    "dense_score",
                    "sparse_rank",
                    "dense_rank",
                    "retrieval_score",
                    "channel_count",
                ]
            )
        return (
            pd.DataFrame(rows)
            .sort_values(["retrieval_score", "channel_count"], ascending=False)
            .head(CFG.candidate_cap_per_record)
            .reset_index(drop=True)
        )

    # ===== TRAINING STAGE 27 =====

    def apply_pair_calibrator(
        base_probability: np.ndarray, reranker_score: np.ndarray, reranker_present: np.ndarray
    ) -> np.ndarray:
        if pair_calibrator is None:
            return base_probability
        stacked = np.column_stack([logit_clip(base_probability), reranker_score, reranker_present])
        return pair_calibrator.predict_proba(stacked)[:, 1]

    def score_query_records(
        query: pd.Series, retrieved: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if retrieved.empty:
            return pd.DataFrame(), pd.DataFrame(columns=PAIR_FEATURES)
        candidates = reference.iloc[retrieved.candidate_index.to_numpy()].reset_index(drop=True)
        left = pd.DataFrame([query.to_dict()] * len(candidates)).reset_index(drop=True)
        features = build_pair_features(left, candidates, retrieved)
        base_probability = pair_base_probability(pair_model, features)
        reranker_score = np.zeros(len(candidates), dtype=np.float32)
        reranker_present = np.zeros(len(candidates), dtype=np.float32)
        selected = np.argsort(-base_probability)[: min(CFG.rerank_top_k, len(candidates))]
        if NEURAL_ENABLED and len(selected):
            reranker_score[selected] = RERANKER_SERVICE.score(
                left.iloc[selected].reset_index(drop=True),
                candidates.iloc[selected].reset_index(drop=True),
            )
            reranker_present[selected] = 1.0
        probability = apply_pair_calibrator(base_probability, reranker_score, reranker_present)

        scored = pd.concat(
            [
                retrieved[
                    ["candidate_index", "exact_blocks", "sparse_rank", "dense_rank"]
                ].reset_index(drop=True),
                candidates.reset_index(drop=True),
                features.reset_index(drop=True).add_prefix("f_"),
            ],
            axis=1,
        )
        scored["base_probability"] = base_probability
        scored["reranker_score"] = reranker_score
        scored["pair_probability"] = probability
        scored["nayose_id"] = scored.record_id.map(cluster_map.set_index("record_id").nayose_id)
        return scored.sort_values("pair_probability", ascending=False).reset_index(
            drop=True
        ), features

    def aggregate_candidate_entities(
        scored_records: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if scored_records.empty:
            return pd.DataFrame(columns=["nayose_id", *ENTITY_FEATURES]), scored_records
        enriched = scored_records.merge(
            canonical_entities[["nayose_id", "record_count"]].rename(
                columns={"record_count": "candidate_entity_size"}
            ),
            on="nayose_id",
            how="left",
        )
        entity_rows = []
        for nayose_id, group in enriched.groupby("nayose_id", sort=False):
            group = group.sort_values("pair_probability", ascending=False)
            scores = group.pair_probability.to_numpy()
            top3 = scores[:3]
            best = group.iloc[0]
            entity_rows.append(
                {
                    "nayose_id": nayose_id,
                    "max_pair_probability": float(scores[0]),
                    "second_pair_probability": float(scores[1]) if len(scores) > 1 else 0.0,
                    "mean_top3_probability": float(top3.mean()),
                    "record_score_margin": float(scores[0] - scores[1])
                    if len(scores) > 1
                    else float(scores[0]),
                    "support_record_count": int(len(group)),
                    "support_agency_count": int(group.agency_id.nunique()),
                    "candidate_entity_size": int(best.candidate_entity_size),
                    "max_name_similarity": float(group.f_name_similarity.max()),
                    "max_kana_similarity": float(group.f_kana_similarity.max()),
                    "max_romaji_similarity": float(group.f_romaji_similarity.max()),
                    "max_address_similarity": float(group.f_address_similarity.max()),
                    "any_dob_exact": float(group.f_dob_exact.max()),
                    "any_name_exact": float(
                        (group.f_name_strict_exact.eq(1) | group.f_name_loose_exact.eq(1)).max()
                    ),
                    "any_identity_anchor": float(
                        (
                            (group.f_dob_exact.eq(1) | group.f_email_exact.eq(1))
                            & (group.f_name_strict_exact.eq(1) | group.f_name_loose_exact.eq(1))
                        ).max()
                    ),
                    "any_phone_exact": float(group.f_phone_exact.max()),
                    "any_email_exact": float(group.f_email_exact.max()),
                    "any_postal_exact": float(group.f_postal_exact.max()),
                    "max_evidence_family_count": float(group.f_evidence_family_count.max()),
                    "max_retrieval_score": float(group.f_retrieval_score.max()),
                    "max_channel_count": float(group.f_channel_count.max()),
                    "hard_conflict_count": int(
                        (group.f_type_conflict.eq(1) | group.f_dob_conflict.eq(1)).sum()
                    ),
                    "review_or_auto_record_count": int(
                        group.pair_probability.ge(review_threshold).sum()
                    ),
                    "best_record_id": best.record_id,
                    "best_record_probability": float(best.pair_probability),
                    "best_exact_blocks": best.exact_blocks,
                }
            )
        return pd.DataFrame(entity_rows).sort_values(
            "max_pair_probability", ascending=False
        ).reset_index(drop=True), enriched

    def retrieval_reasons(best_record: pd.Series) -> list[str]:
        reasons = []
        if str(best_record.get("exact_blocks", "")):
            reasons.append("exact blocks=" + str(best_record.exact_blocks))
        sparse_rank = int(best_record.get("sparse_rank", 0) or 0)
        dense_rank = int(best_record.get("dense_rank", 0) or 0)
        if sparse_rank:
            reasons.append(f"character sparse rank={sparse_rank}")
        if dense_rank:
            reasons.append(f"dense rank={dense_rank}")
        return reasons or ["candidate-union retrieval"]

    # ===== TRAINING STAGE 28 =====

    def observable_reasons(best_record: pd.Series) -> list[str]:
        positive, negative = [], []
        exact_labels = {
            "f_dob_exact": "exact birth date",
            "f_phone_exact": "exact phone",
            "f_email_exact": "exact email",
            "f_postal_exact": "exact postal code",
            "f_name_loose_exact": "exact normalized name",
            "f_kana_exact": "exact normalized kana",
        }
        for feature, label in exact_labels.items():
            if float(best_record.get(feature, 0)) >= 1:
                positive.append(label)
        similarity_labels = [
            ("f_name_similarity", "name"),
            ("f_kana_similarity", "kana"),
            ("f_address_similarity", "address"),
            ("f_romaji_similarity", "romaji"),
        ]
        for feature, label in similarity_labels:
            value = float(best_record.get(feature, 0))
            if value >= 0.75 and label not in " ".join(positive):
                positive.append(f"{label} similarity {value:.0%}")
        if best_record.get("exact_blocks", ""):
            positive.append("retrieved by " + str(best_record.exact_blocks))
        if float(best_record.get("f_type_conflict", 0)):
            negative.append("party-type conflict")
        if float(best_record.get("f_dob_conflict", 0)):
            negative.append("birth-date conflict")
        if float(best_record.get("f_missing_fraction", 0)) >= 0.6:
            negative.append("most comparable fields are missing")
        phone = str(best_record.get("n_phone", ""))
        if phone and VALUE_FREQUENCY["n_phone"].get(phone, 0) > 2:
            negative.append(f"phone is shared by {VALUE_FREQUENCY['n_phone'][phone]} records")
        return ["+ " + item for item in positive[:5]] + ["− " + item for item in negative[:4]]

    # ===== TRAINING STAGE 30 =====

    # 10. Batched incoming-query scoring, entity-level calibration, and one public engine API

    def expected_bool(value: Any) -> bool:
        return str(value).strip().lower() in {"true", "1", "yes"}

    def batch_query_retrievals(
        queries: pd.DataFrame, index: CandidateIndex
    ) -> dict[str, pd.DataFrame]:
        """Retrieve all benchmark queries in two matrix calls, then fuse exact/sparse/dense evidence."""
        print(f"Bulk-retrieving candidates for {len(queries):,} incoming queries …")
        sparse_ids, sparse_scores = index.sparse_candidates(
            queries.retrieval_text.tolist(), CFG.sparse_top_k
        )
        dense_ids, dense_scores = index.dense_candidates(
            queries.retrieval_text.tolist(), CFG.dense_top_k
        )
        output: dict[str, pd.DataFrame] = {}
        for position, query in queries.reset_index(drop=True).iterrows():
            states: dict[int, dict[str, Any]] = {}
            if CFG.exact_mode != "fuzzy_only":
                for candidate, blocks in index.exact_candidates(query).items():
                    state = states.setdefault(
                        candidate,
                        {
                            "exact_blocks": set(),
                            "sparse_score": 0.0,
                            "dense_score": 0.0,
                            "sparse_rank": 0,
                            "dense_rank": 0,
                        },
                    )
                    state["exact_blocks"].update(blocks)
            for rank, (candidate, score) in enumerate(
                zip(sparse_ids[position], sparse_scores[position], strict=False), 1
            ):
                state = states.setdefault(
                    int(candidate),
                    {
                        "exact_blocks": set(),
                        "sparse_score": 0.0,
                        "dense_score": 0.0,
                        "sparse_rank": 0,
                        "dense_rank": 0,
                    },
                )
                if float(score) > state["sparse_score"]:
                    state["sparse_score"], state["sparse_rank"] = float(score), rank
            if dense_ids.ndim == 2 and dense_ids.shape[1] > 0:
                for rank, (candidate, score) in enumerate(
                    zip(dense_ids[position], dense_scores[position], strict=False), 1
                ):
                    state = states.setdefault(
                        int(candidate),
                        {
                            "exact_blocks": set(),
                            "sparse_score": 0.0,
                            "dense_score": 0.0,
                            "sparse_rank": 0,
                            "dense_rank": 0,
                        },
                    )
                    if float(score) > state["dense_score"]:
                        state["dense_score"], state["dense_rank"] = float(score), rank
            rows = []
            for candidate, state in states.items():
                blocks = sorted(state["exact_blocks"])
                exact_count = len(blocks)
                rows.append(
                    {
                        "candidate_index": candidate,
                        "exact_blocks": ",".join(blocks),
                        "exact_count": exact_count,
                        "sparse_score": state["sparse_score"],
                        "dense_score": state["dense_score"],
                        "sparse_rank": state["sparse_rank"],
                        "dense_rank": state["dense_rank"],
                        "retrieval_score": max(state["sparse_score"], state["dense_score"])
                        + 0.08 * min(exact_count, 3),
                        "channel_count": int(exact_count > 0)
                        + int(state["sparse_score"] > 0)
                        + int(state["dense_score"] > 0),
                    }
                )
            output[str(query.query_id)] = (
                pd.DataFrame(rows)
                .sort_values(["retrieval_score", "channel_count"], ascending=False)
                .head(CFG.candidate_cap_per_record)
                .reset_index(drop=True)
                if rows
                else pd.DataFrame()
            )
        print("Candidate retrieval completed in bulk. Candidate-count distribution:")
        report(
            pd.Series({query_id: len(frame) for query_id, frame in output.items()})
            .describe()
            .to_frame("candidate_records")
        )
        return output

    def batch_score_query_records(
        queries: pd.DataFrame, retrievals: dict[str, pd.DataFrame]
    ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
        """Score every query-candidate pair in one model call; manual input reuses the same feature functions."""
        retrieval_rows = []
        for query_position, query in queries.reset_index(drop=True).iterrows():
            for item in retrievals.get(str(query.query_id), pd.DataFrame()).to_dict("records"):
                retrieval_rows.append(
                    {"query_position": query_position, "query_id": str(query.query_id), **item}
                )
        if not retrieval_rows:
            empty = pd.DataFrame()
            return empty, {str(query_id): empty.copy() for query_id in queries.query_id}

        retrieval_long = pd.DataFrame(retrieval_rows).reset_index(drop=True)
        left = (
            queries.reset_index(drop=True)
            .iloc[retrieval_long.query_position.to_numpy()]
            .reset_index(drop=True)
        )
        candidates = reference.iloc[retrieval_long.candidate_index.to_numpy()].reset_index(
            drop=True
        )
        features = build_pair_features(left, candidates, retrieval_long)
        print(
            f"Scoring {len(features):,} query→record pairs with one {pair_model_backend} prediction call …"
        )
        base_probability = pair_base_probability(pair_model, features)
        reranker_score = np.zeros(len(features), dtype=np.float32)
        reranker_present = np.zeros(len(features), dtype=np.float32)
        if NEURAL_ENABLED and len(features):
            ranks = retrieval_long[["query_id"]].assign(base_probability=base_probability)
            selected = (
                ranks.sort_values(["query_id", "base_probability"], ascending=[True, False])
                .groupby("query_id")
                .head(CFG.rerank_top_k)
                .index.to_numpy()
            )
            reranker_score[selected] = RERANKER_SERVICE.score(
                left.iloc[selected].reset_index(drop=True),
                candidates.iloc[selected].reset_index(drop=True),
            )
            reranker_present[selected] = 1.0
        probability = apply_pair_calibrator(base_probability, reranker_score, reranker_present)

        scored = pd.concat(
            [
                retrieval_long[
                    ["query_id", "candidate_index", "exact_blocks", "sparse_rank", "dense_rank"]
                ].reset_index(drop=True),
                candidates.reset_index(drop=True),
                features.reset_index(drop=True).add_prefix("f_"),
            ],
            axis=1,
        )
        scored["base_probability"] = base_probability
        scored["reranker_score"] = reranker_score
        scored["pair_probability"] = probability
        scored["nayose_id"] = scored.record_id.map(record_to_nayose)
        by_query = {
            query_id: group.sort_values("pair_probability", ascending=False).reset_index(drop=True)
            for query_id, group in scored.groupby("query_id", sort=False)
        }
        for query_id in queries.query_id.astype(str):
            by_query.setdefault(query_id, pd.DataFrame())
        print("Pair-score distribution across all incoming queries:")
        report(
            scored.pair_probability.describe(percentiles=[0.5, 0.9, 0.95, 0.99]).to_frame(
                "pair_probability"
            )
        )
        return scored, by_query

    # ===== TRAINING STAGE 31 =====

    def batch_aggregate_candidate_entities(
        scored: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
        """Compute all query→entity features in one grouped pass rather than one DataFrame loop per query."""
        if scored.empty:
            return pd.DataFrame(columns=["query_id", "nayose_id", *ENTITY_FEATURES]), scored, {}
        keys = ["query_id", "nayose_id"]
        enriched = scored.merge(
            canonical_entities[["nayose_id", "record_count"]].rename(
                columns={"record_count": "candidate_entity_size"}
            ),
            on="nayose_id",
            how="left",
            validate="many_to_one",
        )
        enriched["hard_conflict"] = enriched.f_type_conflict.eq(1) | enriched.f_dob_conflict.eq(1)
        enriched["identity_anchor"] = (
            (enriched.f_dob_exact.eq(1) | enriched.f_email_exact.eq(1))
            & (enriched.f_name_strict_exact.eq(1) | enriched.f_name_loose_exact.eq(1))
        ).astype(np.float32)
        enriched["review_or_auto"] = enriched.pair_probability.ge(review_threshold)
        ordered = enriched.sort_values(
            [*keys, "pair_probability"], ascending=[True, True, False]
        ).copy()
        ordered["entity_record_rank"] = ordered.groupby(keys, sort=False).cumcount()

        grouped = ordered.groupby(keys, sort=False)
        entity_table = grouped.agg(
            support_record_count=("record_id", "size"),
            support_agency_count=("agency_id", "nunique"),
            candidate_entity_size=("candidate_entity_size", "first"),
            max_name_similarity=("f_name_similarity", "max"),
            max_kana_similarity=("f_kana_similarity", "max"),
            max_romaji_similarity=("f_romaji_similarity", "max"),
            max_address_similarity=("f_address_similarity", "max"),
            any_name_exact=("f_name_loose_exact", "max"),
            any_identity_anchor=("identity_anchor", "max"),
            any_dob_exact=("f_dob_exact", "max"),
            any_phone_exact=("f_phone_exact", "max"),
            any_email_exact=("f_email_exact", "max"),
            any_postal_exact=("f_postal_exact", "max"),
            max_evidence_family_count=("f_evidence_family_count", "max"),
            max_retrieval_score=("f_retrieval_score", "max"),
            max_channel_count=("f_channel_count", "max"),
            hard_conflict_count=("hard_conflict", "sum"),
            review_or_auto_record_count=("review_or_auto", "sum"),
        ).reset_index()
        top1 = ordered.loc[
            ordered.entity_record_rank.eq(0),
            [*keys, "pair_probability", "record_id", "exact_blocks"],
        ].rename(
            columns={
                "pair_probability": "max_pair_probability",
                "record_id": "best_record_id",
                "exact_blocks": "best_exact_blocks",
            }
        )
        top2 = ordered.loc[ordered.entity_record_rank.eq(1), [*keys, "pair_probability"]].rename(
            columns={"pair_probability": "second_pair_probability"}
        )
        top3 = (
            ordered.loc[ordered.entity_record_rank.lt(3)]
            .groupby(keys, sort=False)
            .pair_probability.mean()
            .rename("mean_top3_probability")
            .reset_index()
        )
        entity_table = (
            entity_table.merge(top1, on=keys, validate="one_to_one")
            .merge(top2, on=keys, how="left", validate="one_to_one")
            .merge(top3, on=keys, validate="one_to_one")
        )
        entity_table["second_pair_probability"] = entity_table.second_pair_probability.fillna(0.0)
        entity_table["record_score_margin"] = np.where(
            entity_table.second_pair_probability.gt(0),
            entity_table.max_pair_probability - entity_table.second_pair_probability,
            entity_table.max_pair_probability,
        )
        entity_table["best_record_probability"] = entity_table.max_pair_probability
        entity_table = entity_table[
            [
                "query_id",
                "nayose_id",
                *ENTITY_FEATURES,
                "best_record_id",
                "best_record_probability",
                "best_exact_blocks",
            ]
        ]
        by_query = {
            query_id: group.reset_index(drop=True)
            for query_id, group in entity_table.groupby("query_id", sort=False)
        }
        print(
            f"Aggregated {len(scored):,} record candidates into {len(entity_table):,} query→entity rows in one pass."
        )
        print("Entity candidates per query:")
        report(entity_table.groupby("query_id").size().describe().to_frame("candidate_entities"))
        return entity_table, enriched, by_query

    def entity_probability(features: pd.DataFrame) -> np.ndarray:
        if features.empty:
            return np.empty(0)
        scaled = entity_scaler.transform(features[ENTITY_FEATURES])
        base = entity_model.predict_proba(scaled)[:, 1]
        return (
            base
            if entity_calibrator is None
            else entity_calibrator.predict_proba(logit_clip(base).reshape(-1, 1))[:, 1]
        )

    # ===== TRAINING STAGE 32 =====

    def entity_contributions(features: pd.DataFrame) -> list[str]:
        if features.empty:
            return []
        contributions = entity_scaler.transform(features[ENTITY_FEATURES]) * entity_model.coef_[0]
        return [
            "; ".join(f"{ENTITY_FEATURES[i]}={row[i]:+.2f}" for i in np.argsort(-np.abs(row))[:5])
            for row in contributions
        ]

    if candidate_index.sparse_backend is None:
        vectorizer = TfidfVectorizer(
            analyzer="char",
            ngram_range=(2, 4),
            min_df=1,
            max_df=0.995,
            max_features=300_000,
            sublinear_tf=True,
            dtype=np.float32,
            norm="l2",
        )
        sparse_matrix = vectorizer.fit_transform(reference.retrieval_text)
        sparse_backend = SparseCandidateBackend(
            sparse_matrix,
            vectorizer,
            batch_size=CFG.sparse_batch_size,
            query_feature_cap=DEFAULT_QUERY_FEATURE_CAP,
        )
        candidate_index.vectorizer = vectorizer
        candidate_index.sparse_matrix = sparse_matrix
        candidate_index.sparse_backend = sparse_backend

    if NEURAL_ENABLED and candidate_index.reference_embeddings is None:
        spec = MODELS[CFG.model_family]
        model_slug = Path(spec["embedding"]).name
        cache_vec = CFG.data_dir / f"reference_embeddings_{model_slug}.npy"
        reference_embeddings = np.load(cache_vec, mmap_mode=None)
        if BACKENDS["usearch"]:
            from usearch.index import Index

            dense_nn = Index(ndim=reference_embeddings.shape[1], metric="cos", dtype="f16")
            dense_nn.add(np.arange(len(reference_embeddings), dtype=np.int64), reference_embeddings)
        else:
            dense_nn = NearestNeighbors(
                metric="cosine", algorithm="brute", n_jobs=CFG.model_threads
            ).fit(reference_embeddings)
        candidate_index.reference_embeddings = reference_embeddings
        candidate_index.dense_nn = dense_nn

    evaluation_truth = read_table("evaluation_truth", hidden=True)
    query_truth = read_table("query_truth", hidden=True)

    with stage("BUILD QUERY→RECORD→ENTITY TRAINING TABLE IN BATCH"):
        benchmark_retrievals = batch_query_retrievals(incoming, candidate_index)
        benchmark_scored_records, benchmark_record_scores = batch_score_query_records(
            incoming, benchmark_retrievals
        )
        benchmark_entity_table, benchmark_enriched_records, benchmark_entity_features = (
            batch_aggregate_candidate_entities(benchmark_scored_records)
        )
        cluster_truth_sets = assignments.groupby("nayose_id").truth_id.apply(set).to_dict()
        truth_row_by_query = evaluation_truth.set_index("query_id")

        query_truth_meta = evaluation_truth[
            ["query_id", "split", "scenario", "target_truth_id", "expected_existing_customer"]
        ].copy()
        entity_examples = benchmark_entity_table.merge(
            query_truth_meta, on="query_id", how="left", validate="many_to_one"
        )
        entity_examples = entity_examples.rename(columns={"split": "query_split"})
        entity_examples["truth_set_object"] = entity_examples.nayose_id.map(
            lambda entity: cluster_truth_sets.get(entity, set())
        )
        entity_examples["contains_target"] = [
            int(expected_bool(existing) and str(target) in truth_set)
            for existing, target, truth_set in zip(
                entity_examples.expected_existing_customer,
                entity_examples.target_truth_id,
                entity_examples.truth_set_object,
                strict=False,
            )
        ]
        entity_examples["pure_correct"] = [
            int(expected_bool(existing) and truth_set == {str(target)})
            for existing, target, truth_set in zip(
                entity_examples.expected_existing_customer,
                entity_examples.target_truth_id,
                entity_examples.truth_set_object,
                strict=False,
            )
        ]
        entity_examples["truth_set"] = entity_examples.truth_set_object.map(
            lambda values: json.dumps(sorted(values))
        )
        entity_examples = entity_examples.drop(columns="truth_set_object")

        assert not entity_examples.empty
        print(
            f"Entity examples: {len(entity_examples):,}; positive pure-correct candidates={int(entity_examples.pure_correct.sum()):,}"
        )
        print("Candidate-entity truth outcomes by scenario:")
        report(
            entity_examples.groupby(["scenario", "pure_correct"])
            .size()
            .rename("rows")
            .reset_index()
        )
        print("Example training rows; truth columns are evaluation labels, never model features:")
        report(entity_examples.head(8))

    with stage("TRAIN AND CALIBRATE ENTITY RECOMMENDATION MODEL"):
        train_entities = entity_examples.query("query_split == 'train'").copy()
        calibration_entities = entity_examples.query("query_split == 'calibration'").copy()
        entity_examples.query("query_split == 'test'").copy()
        assert train_entities.pure_correct.nunique() == 2, (
            "Entity training needs positive and negative examples"
        )

        positive_train = train_entities.query("pure_correct == 1")
        negative_train = (
            train_entities.query("pure_correct == 0")
            .sort_values("max_pair_probability", ascending=False)
            .head(max(len(positive_train) * 8, 1000))
        )
        fit_entities = pd.concat([positive_train, negative_train]).sample(
            frac=1, random_state=CFG.seed
        )
        entity_scaler = StandardScaler().fit(fit_entities[ENTITY_FEATURES])
        entity_model = LogisticRegression(
            C=0.8, class_weight="balanced", max_iter=2000, random_state=CFG.seed
        )
        entity_model.fit(
            entity_scaler.transform(fit_entities[ENTITY_FEATURES]), fit_entities.pure_correct
        )

        if len(calibration_entities) and calibration_entities.pure_correct.nunique() == 2:
            raw_calibration = entity_model.predict_proba(
                entity_scaler.transform(calibration_entities[ENTITY_FEATURES])
            )[:, 1]
            entity_calibrator = LogisticRegression(C=1.0, max_iter=1000, random_state=CFG.seed).fit(
                logit_clip(raw_calibration).reshape(-1, 1), calibration_entities.pure_correct
            )
        else:
            entity_calibrator = None

        entity_examples["entity_probability"] = entity_probability(entity_examples)
        calibration_top = (
            entity_examples.query("query_split == 'calibration'")
            .sort_values(["query_id", "entity_probability"], ascending=[True, False])
            .groupby("query_id")
            .head(1)
        )
        entity_auto_threshold, entity_auto_curve, entity_threshold_status = (
            threshold_for_precision(
                calibration_top.entity_probability.to_numpy(),
                calibration_top.pure_correct.to_numpy(),
                CFG.entity_auto_precision_target,
                minimum_predictions=5,
            )
            if len(calibration_top)
            else (1.000001, pd.DataFrame(), "NO_CALIBRATION_QUERIES")
        )
        entity_review_threshold = (
            min(CFG.entity_review_threshold, entity_auto_threshold - 1e-6)
            if entity_auto_threshold <= 1
            else CFG.entity_review_threshold
        )
        entity_review_threshold = max(0.0, entity_review_threshold)

        coefficient_table = pd.DataFrame(
            {"feature": ENTITY_FEATURES, "coefficient": entity_model.coef_[0]}
        ).sort_values("coefficient", ascending=False)
        print(
            f"Entity AUTO_LINK threshold: {entity_auto_threshold:.6f} — {entity_threshold_status}"
        )
        print(f"Entity REVIEW threshold:    {entity_review_threshold:.6f}")
        print(
            "Positive coefficients increase safe-match probability; negative coefficients decrease it:"
        )
        report(coefficient_table)

    @dataclass(frozen=True, slots=True)
    class Recommendation:
        prediction_id: str
        query_id: str
        action: str
        top_nayose_id: str | None
        top_probability: float
        margin: float
        candidates: pd.DataFrame
        record_candidates: pd.DataFrame
        diagnostics: dict[str, Any]

    canonical_lookup = canonical_entities.set_index("nayose_id")

    @dataclass(slots=True)
    class NayoseEngine:
        index: CandidateIndex

        def _finalize(
            self,
            query: pd.Series,
            scored_records: pd.DataFrame,
            entities: pd.DataFrame | None,
            top_k: int,
        ) -> Recommendation:
            insufficient, message = query_is_insufficient(query)
            if insufficient:
                empty = pd.DataFrame(
                    columns=[
                        "rank",
                        "nayose_id",
                        "entity_probability",
                        "main_evidence",
                        "top_feature_contributions",
                    ]
                )
                return Recommendation(
                    "",
                    str(query.query_id),
                    "INSUFFICIENT_INPUT",
                    None,
                    0.0,
                    0.0,
                    empty,
                    scored_records,
                    {"message": message, "populated_fields": int(query.populated_fields)},
                )
            if entities is None:
                entities, scored_records = aggregate_candidate_entities(scored_records)
            if entities.empty:
                return Recommendation(
                    "",
                    str(query.query_id),
                    "NO_MATCH",
                    None,
                    0.0,
                    0.0,
                    pd.DataFrame(),
                    scored_records,
                    {"message": "No candidates were retrieved."},
                )

            entities = entities.copy()
            entities["entity_probability"] = entity_probability(entities)
            entities["top_feature_contributions"] = entity_contributions(entities)
            entities = entities.sort_values("entity_probability", ascending=False).reset_index(
                drop=True
            )
            entities["rank"] = np.arange(1, len(entities) + 1)
            top = entities.iloc[0]
            second = float(entities.entity_probability.iloc[1]) if len(entities) > 1 else 0.0
            margin = float(top.entity_probability - second)
            auto_safe = (
                float(top.entity_probability) >= entity_auto_threshold
                and margin >= CFG.entity_margin_threshold
                and float(top.max_evidence_family_count) >= CFG.minimum_evidence_families
                and int(top.hard_conflict_count) == 0
                and int(top.any_identity_anchor) == 1
            )
            action = (
                "AUTO_LINK"
                if auto_safe
                else "REVIEW"
                if float(top.entity_probability) >= entity_review_threshold
                else "NO_MATCH"
            )

            record_lookup = (
                scored_records.set_index("record_id", drop=False)
                if len(scored_records)
                else pd.DataFrame()
            )
            display_rows = []
            for entity in entities.head(top_k).itertuples(index=False):
                best = (
                    record_lookup.loc[entity.best_record_id]
                    if len(record_lookup) and entity.best_record_id in record_lookup.index
                    else pd.Series(dtype=object)
                )
                canonical = canonical_lookup.loc[entity.nayose_id]
                display_rows.append(
                    {
                        "rank": entity.rank,
                        "nayose_id": entity.nayose_id,
                        "entity_probability": entity.entity_probability,
                        "best_record_id": entity.best_record_id,
                        "support_records": entity.support_record_count,
                        "canonical_name": canonical.name_kanji
                        or canonical.name_kana
                        or canonical.name_romaji,
                        "canonical_address": canonical.address,
                        "canonical_phone": canonical.phone,
                        "canonical_email": canonical.email,
                        "main_evidence": "; ".join(observable_reasons(best)) if len(best) else "",
                        "top_feature_contributions": entity.top_feature_contributions,
                    }
                )
            candidate_table = pd.DataFrame(display_rows)
            payload = json.dumps(
                {
                    "query_id": str(query.query_id),
                    "entities": candidate_table[["nayose_id", "entity_probability"]]
                    .round(8)
                    .to_dict("records"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            prediction_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
            diagnostics = {
                "retrieved_records": len(scored_records),
                "candidate_entities": len(entities),
                "top_score": float(top.entity_probability),
                "second_score": second,
                "margin": margin,
                "top_evidence_families": int(top.max_evidence_family_count),
                "top_hard_conflicts": int(top.hard_conflict_count),
                "thresholds": {
                    "auto": entity_auto_threshold,
                    "review": entity_review_threshold,
                    "margin": CFG.entity_margin_threshold,
                },
            }
            return Recommendation(
                prediction_id,
                str(query.query_id),
                action,
                str(top.nayose_id),
                float(top.entity_probability),
                margin,
                candidate_table,
                scored_records,
                diagnostics,
            )

        def recommend(
            self,
            raw_query: dict[str, Any],
            top_k: int = 8,
            precomputed_retrieval: pd.DataFrame | None = None,
        ) -> Recommendation:
            query = normalize_raw_query(raw_query)
            insufficient, _ = query_is_insufficient(query)
            if insufficient:
                return self._finalize(query, pd.DataFrame(), pd.DataFrame(), top_k)
            retrieved = (
                precomputed_retrieval
                if precomputed_retrieval is not None
                else retrieve_query(query, self.index)
            )
            scored_records, _ = score_query_records(query, retrieved)
            return self._finalize(query, scored_records, None, top_k)

        def recommend_precomputed(
            self,
            raw_query: dict[str, Any],
            scored_records: pd.DataFrame,
            entity_features: pd.DataFrame,
            top_k: int = 8,
        ) -> Recommendation:
            return self._finalize(
                normalize_raw_query(raw_query), scored_records, entity_features, top_k
            )

    engine = NayoseEngine(candidate_index)
    print("Public APIs ready:")
    print(
        "  engine.recommend(raw_query)                                      # one new manual application"
    )
    print(
        "  engine.recommend_precomputed(raw_query, records, entity_features) # batch benchmark path"
    )

    # ===== TRAINING STAGE 34 =====

    # 11. Freeze predictions first, reveal intended answers second, and compute query-level metrics

    def truth_status_for_entity(nayose_id: str, truth_row: pd.Series) -> str:
        truth_set = cluster_truth_sets.get(nayose_id, set())
        target = str(truth_row.target_truth_id)
        related = (
            set(json.loads(truth_row.related_reference_truth_ids or "[]"))
            if "related_reference_truth_ids" in truth_row.index
            else set()
        )
        expected_existing = expected_bool(truth_row.expected_existing_customer)
        if expected_existing and truth_set == {target}:
            return "PURE_CORRECT"
        if expected_existing and target in truth_set:
            return "CORRECT_BUT_CONTAMINATED"
        if truth_set & related:
            return "RELATED_DIFFERENT"
        return "WRONG_ENTITY"

    # ===== TRAINING STAGE 35 =====

    def reveal_prediction(
        prediction: Recommendation, truth_query_id: str | None = None
    ) -> dict[str, Any]:
        lookup_id = truth_query_id or prediction.query_id
        if lookup_id not in truth_row_by_query.index:
            return {
                "truth_available": False,
                "message": "Ground truth is unavailable for manual input.",
            }
        truth_row = truth_row_by_query.loc[lookup_id]
        expected_existing = expected_bool(truth_row.expected_existing_customer)
        target = str(truth_row.target_truth_id)
        candidates = prediction.candidates.copy()
        if len(candidates):
            candidates["truth_status"] = candidates.nayose_id.map(
                lambda entity: truth_status_for_entity(entity, truth_row)
            )
            correct_rows = candidates.index[
                candidates.truth_status.isin(["PURE_CORRECT", "CORRECT_BUT_CONTAMINATED"])
            ]
            correct_rank = (
                int(candidates.loc[correct_rows[0], "rank"]) if len(correct_rows) else None
            )
            top_status = str(candidates.iloc[0].truth_status)
        else:
            candidates["truth_status"] = []
            correct_rank, top_status = None, "NO_CANDIDATE"

        if expected_existing:
            operational_result = (
                "SAFE_AUTO_LINK"
                if prediction.action == "AUTO_LINK" and top_status == "PURE_CORRECT"
                else "UNSAFE_AUTO_LINK"
                if prediction.action == "AUTO_LINK"
                else "SAFE_ABSTENTION"
                if prediction.action in {"REVIEW", "INSUFFICIENT_INPUT", "NO_MATCH"}
                else "OTHER"
            )
        else:
            operational_result = (
                "FALSE_AUTO_LINK" if prediction.action == "AUTO_LINK" else "CORRECT_NO_AUTO_LINK"
            )

        lineage = (
            field_lineage.loc[field_lineage.item_id.eq(lookup_id)].copy()
            if "item_id" in field_lineage
            else pd.DataFrame()
        )
        intended_query = query_truth.loc[query_truth.query_id.eq(lookup_id)].copy()
        return {
            "truth_available": True,
            "query_id": prediction.query_id,
            "scenario": str(truth_row.scenario),
            "expected_existing_customer": expected_existing,
            "intended_truth_id": target,
            "correct_entity_rank": correct_rank,
            "top_truth_status": top_status,
            "identity_top1_correct": top_status in {"PURE_CORRECT", "CORRECT_BUT_CONTAMINATED"},
            "pure_top1_correct": top_status == "PURE_CORRECT",
            "operational_result": operational_result,
            "candidates": candidates,
            "intended_query": intended_query,
            "field_lineage": lineage,
        }

    lineage_path = CFG.data_dir / "ground_truth" / "field_lineage.parquet"
    if lineage_path.exists():
        field_lineage = pd.read_parquet(
            lineage_path,
            filters=[("item_id", "in", incoming.query_id.tolist())],
        ).fillna("")
        print(
            f"hidden   field_lineage        rows={len(field_lineage):>9,} cols={len(field_lineage.columns):>2} source={lineage_path.name} (query-filtered)"
        )
    else:
        field_lineage = read_table("field_lineage", hidden=True)

    with stage("RUN THE PUBLIC RECOMMENDATION API OVER EVERY BENCHMARK QUERY"):
        frozen_predictions: dict[str, Recommendation] = {}
        revealed_rows = []
        for position, raw_row in incoming_raw.reset_index(drop=True).iterrows():
            query_id = raw_row.query_id
            prediction = engine.recommend_precomputed(
                raw_row.to_dict(),
                benchmark_record_scores.get(query_id, pd.DataFrame()),
                benchmark_entity_features.get(query_id, pd.DataFrame()),
                top_k=10,
            )
            frozen_predictions[query_id] = prediction
            revealed = reveal_prediction(prediction)
            revealed_rows.append(
                {
                    "query_id": query_id,
                    "scenario": revealed["scenario"],
                    "expected_existing_customer": revealed["expected_existing_customer"],
                    "action": prediction.action,
                    "top_nayose_id": prediction.top_nayose_id or "",
                    "top_probability": prediction.top_probability,
                    "margin": prediction.margin,
                    "correct_entity_rank": revealed["correct_entity_rank"] or 0,
                    "identity_top1_correct": revealed["identity_top1_correct"],
                    "pure_top1_correct": revealed["pure_top1_correct"],
                    "top_truth_status": revealed["top_truth_status"],
                    "operational_result": revealed["operational_result"],
                }
            )
            if position == 0 or (position + 1) % 100 == 0 or position + 1 == len(incoming_raw):
                print(f"  recommendation progress {position + 1:>6,}/{len(incoming_raw):,}")
        query_results = pd.DataFrame(revealed_rows)
        assert query_results.query_id.is_unique
        print(
            "Frozen prediction sample; intended truth was joined only after each recommendation returned:"
        )
        report(query_results.head(10))

    with stage("COMPUTE QUERY-LEVEL IDENTITY AND DECISION METRICS"):
        existing = query_results.expected_existing_customer
        auto = query_results.action.eq("AUTO_LINK")
        no_auto = ~auto
        correct_rank = query_results.correct_entity_rank
        query_metrics = {
            "queries": len(query_results),
            "candidate_recall_at_1_existing": float((correct_rank[existing].between(1, 1)).mean())
            if existing.any()
            else float("nan"),
            "candidate_recall_at_5_existing": float((correct_rank[existing].between(1, 5)).mean())
            if existing.any()
            else float("nan"),
            "candidate_recall_at_10_existing": float((correct_rank[existing].between(1, 10)).mean())
            if existing.any()
            else float("nan"),
            "mean_reciprocal_rank_existing": float(
                np.where(correct_rank[existing] > 0, 1 / correct_rank[existing], 0).mean()
            )
            if existing.any()
            else float("nan"),
            "top1_identity_accuracy_existing": float(
                query_results.loc[existing, "identity_top1_correct"].mean()
            )
            if existing.any()
            else float("nan"),
            "top1_pure_accuracy_existing": float(
                query_results.loc[existing, "pure_top1_correct"].mean()
            )
            if existing.any()
            else float("nan"),
            "auto_link_precision_pure": float(query_results.loc[auto, "pure_top1_correct"].mean())
            if auto.any()
            else float("nan"),
            "auto_link_end_to_end_recall_existing": float(
                (auto & query_results.pure_top1_correct)[existing].mean()
            )
            if existing.any()
            else float("nan"),
            "new_or_related_safe_no_auto_rate": float(no_auto[~existing].mean())
            if (~existing).any()
            else float("nan"),
            "false_auto_link_rate_new_or_related": float(auto[~existing].mean())
            if (~existing).any()
            else float("nan"),
            "review_rate": float(query_results.action.eq("REVIEW").mean()),
            "no_match_rate": float(query_results.action.eq("NO_MATCH").mean()),
            "insufficient_input_rate": float(query_results.action.eq("INSUFFICIENT_INPUT").mean()),
        }

        # Decision confusion: predicted action vs intended truth (expected existing customer), per scenario
        if "query_results" in locals() and len(query_results) and "action" in query_results:
            qc = query_results.reset_index(drop=True).copy()
            qc["expected_existing"] = qc.query_id.map(
                evaluation_truth.set_index("query_id")["expected_existing_customer"]
            ).fillna(False)
            qc["scenario"] = qc.query_id.map(
                evaluation_truth.set_index("query_id")["scenario"]
            ).fillna("")
            query_confusion_rows = []
            for scenario, group in qc.groupby("scenario", sort=False):
                cm = pd.crosstab(group["action"], group["expected_existing"])
                for action in ("AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"):
                    row = cm.reindex(columns=[False, True], fill_value=0)
                    query_confusion_rows.append(
                        {
                            "scenario": scenario,
                            "action": action,
                            "expected_no": int(row.loc[action, False])
                            if action in row.index
                            else 0,
                            "expected_yes": int(row.loc[action, True])
                            if action in row.index
                            else 0,
                        }
                    )
            query_confusion = pd.DataFrame(query_confusion_rows)
            print(
                "== QUERY DECISION CONFUSION (predicted action vs intended truth), per scenario =="
            )
            report(query_confusion)
        else:
            query_confusion = pd.DataFrame(
                columns=["scenario", "action", "expected_no", "expected_yes"]
            )
        print("Overall query metrics:")
        report(pd.DataFrame.from_dict(query_metrics, orient="index", columns=["value"]))

        scenario_rows = []
        for scenario, group in query_results.groupby("scenario"):
            expected = group.expected_existing_customer
            scenario_rows.append(
                {
                    "scenario": scenario,
                    "queries": len(group),
                    "candidate_recall_at_5": float(group.correct_entity_rank.between(1, 5).mean())
                    if expected.any()
                    else np.nan,
                    "top1_identity_accuracy": float(group.identity_top1_correct.mean())
                    if expected.any()
                    else np.nan,
                    "auto_link_rate": float(group.action.eq("AUTO_LINK").mean()),
                    "false_auto_link_rate": float(
                        (group.action.eq("AUTO_LINK") & ~group.pure_top1_correct).mean()
                    ),
                    "review_rate": float(group.action.eq("REVIEW").mean()),
                    "safe_no_auto_rate": float(group.action.ne("AUTO_LINK").mean())
                    if not expected.any()
                    else np.nan,
                }
            )
        scenario_metrics = pd.DataFrame(scenario_rows)
        print("Scenario slices identify exactly where the system fails:")
        report(scenario_metrics)

        print("Action × independent truth status:")
        report(pd.crosstab(query_results.action, query_results.top_truth_status, margins=True))
        print("Highest-confidence wrong or contaminated recommendations:")
        report(
            query_results.loc[~query_results.pure_top1_correct]
            .sort_values("top_probability", ascending=False)
            .head(20)
        )

    # ===== TRAINING STAGE 37 =====

    # 12. Visible full-dataset audit tables

    banner("FULL-DATASET AUDIT TABLES AND VISUALIZATIONS")

    possible_all_pairs = int(len(reference) * (len(reference) - 1) // 2)
    resolution_funnel = pd.DataFrame(
        {
            "stage": [
                "reference records",
                "possible all-pairs",
                "retrieved candidates",
                "review edges",
                "auto edges",
                "accepted unions",
                "predicted entities",
            ],
            "count": [
                len(reference),
                possible_all_pairs,
                len(candidate_pairs),
                int(candidate_pairs.edge_action.eq("REVIEW_EDGE").sum()),
                int(candidate_pairs.edge_action.eq("AUTO_EDGE").sum()),
                int(union_log.accepted.sum()) if len(union_log) else 0,
                len(canonical_entities),
            ],
        }
    )
    entity_size_distribution = (
        canonical_entities.record_count.clip(upper=10)
        .value_counts()
        .sort_index()
        .rename_axis("records_per_entity_capped_10")
        .rename("entities")
        .reset_index()
    )
    entity_size_distribution["represented_records_lower_bound"] = (
        entity_size_distribution.records_per_entity_capped_10 * entity_size_distribution.entities
    )
    edge_action_summary = (
        candidate_pairs.groupby(["edge_action", "edge_policy_reason"], sort=False)
        .size()
        .rename("edges")
        .reset_index()
    )
    full_dataset_summary = pd.DataFrame(
        [
            {
                "metric": "reference_records",
                "value": len(reference),
                "meaning": "Every loaded historical observation receives exactly one predicted Nayose ID.",
            },
            {
                "metric": "record_coverage",
                "value": len(cluster_map) / max(len(reference), 1),
                "meaning": "Share of loaded reference records present in record_to_nayose.",
            },
            {
                "metric": "possible_all_pairs",
                "value": possible_all_pairs,
                "meaning": "Quadratic comparisons that would be required without candidate generation.",
            },
            {
                "metric": "candidate_edges",
                "value": len(candidate_pairs),
                "meaning": "Unique exact/sparse/dense candidate pairs actually verified.",
            },
            {
                "metric": "candidate_reduction",
                "value": 1 - len(candidate_pairs) / max(possible_all_pairs, 1),
                "meaning": "Fraction of all-pairs comparisons avoided.",
            },
            {
                "metric": "candidate_pair_recall",
                "value": candidate_pair_recall,
                "meaning": "Hidden-truth audit: share of all true record pairs retrieved.",
            },
            {
                "metric": "multi_record_entity_candidate_recall",
                "value": entity_candidate_recall,
                "meaning": "Share of multi-record latent parties with at least one retrieved true edge.",
            },
            {
                "metric": "predicted_entities",
                "value": len(canonical_entities),
                "meaning": "Number of constrained connected components after accepted auto edges.",
            },
            {
                "metric": "singleton_entity_rate",
                "value": float(canonical_entities.record_count.eq(1).mean()),
                "meaning": "Predicted entities containing only one historical observation.",
            },
            {
                "metric": "review_queue_edges",
                "value": len(review_queue),
                "meaning": "Uncertain inter-entity links retained for human review.",
            },
            {
                "metric": "b3_precision",
                "value": cluster_metrics["b3_precision"],
                "meaning": "Per-record penalty for overmerging different customers.",
            },
            {
                "metric": "b3_recall",
                "value": cluster_metrics["b3_recall"],
                "meaning": "Per-record penalty for fragmenting one customer into multiple entities.",
            },
            {
                "metric": "b3_f1",
                "value": cluster_metrics["b3_f1"],
                "meaning": "Harmonic mean of B³ precision and recall.",
            },
            {
                "metric": "overmerge_record_rate",
                "value": cluster_metrics["overmerge_record_rate"],
                "meaning": "Records placed in a cluster containing multiple latent parties.",
            },
            {
                "metric": "undermerge_record_rate",
                "value": cluster_metrics["undermerge_record_rate"],
                "meaning": "Records whose latent party was split across predicted entities.",
            },
        ]
    )

    print("Full-dataset computation summary:")
    report(full_dataset_summary)
    print("Resolution funnel source table:")
    report(resolution_funnel)
    print("Edge-action source table:")
    report(edge_action_summary)
    print("Predicted entity-size source table (10 means 10 or more records):")
    report(entity_size_distribution)

    # ===== TRAINING STAGE 43 =====

    # 14. Atomic exports, reproducibility manifest, and end-to-end invariants

    # ===== TRAINING STAGE 44 =====

    def save_frame(frame: pd.DataFrame, stem: str) -> dict[str, Any]:
        parquet_path = OUTPUT_RUN / f"{stem}.parquet"
        csv_path = OUTPUT_RUN / f"{stem}.csv.gz"
        parquet_tmp = parquet_path.with_name(parquet_path.name + ".tmp")
        csv_tmp = csv_path.with_name(csv_path.name + ".tmp")
        try:
            frame.to_parquet(parquet_tmp, index=False)
            os.replace(parquet_tmp, parquet_path)
            path, format_name = parquet_path, "parquet"
        except Exception:
            parquet_tmp.unlink(missing_ok=True)
            frame.to_csv(csv_tmp, index=False, compression="gzip")
            os.replace(csv_tmp, csv_path)
            path, format_name = csv_path, "csv.gz"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(
            f"saved {stem:<28} rows={len(frame):>9,} format={format_name:<7} sha256={digest[:12]}…"
        )
        return {
            "name": stem,
            "path": str(path.relative_to(OUTPUT_RUN)),
            "format": format_name,
            "rows": len(frame),
            "sha256": digest,
        }

    metric_sections = {
        "candidate": {
            "pair_recall": candidate_pair_recall,
            "multi_record_entity_recall": entity_candidate_recall,
        },
        "pair": pair_test_metrics,
        "cluster": cluster_metrics,
        "incoming_query": query_metrics,
    }
    full_dataset_metrics = pd.DataFrame(
        [
            {"section": section, "metric": metric, "value": value}
            for section, values in metric_sections.items()
            for metric, value in values.items()
        ]
    )

    with stage("SAVE RESOLUTION ARTIFACTS AND RUN FINAL PREFLIGHT"):
        output_frames = {
            "record_to_nayose": cluster_map,
            "canonical_entities": canonical_entities,
            "entity_attribute_history": entity_attribute_history,
            "cluster_diagnostics": cluster_diagnostics,
            "review_queue": review_queue,
            "accepted_union_log": union_log,
            "query_results": query_results,
            "scenario_metrics": scenario_metrics,
            "stage_metrics": pd.DataFrame(STAGES),
            "full_dataset_metrics": full_dataset_metrics,
            "full_dataset_summary": full_dataset_summary,
            "resolution_funnel": resolution_funnel,
            "edge_action_summary": edge_action_summary,
            "entity_size_distribution": entity_size_distribution,
            "pair_confusion_test": pair_confusion_test
            if "pair_confusion_test" in locals()
            else pd.DataFrame(),
            "query_confusion": query_confusion if "query_confusion" in locals() else pd.DataFrame(),
        }
        if CFG.save_all_candidate_pairs:
            output_frames["all_candidate_pairs"] = candidate_pairs
        artifacts = [save_frame(frame, name) for name, frame in output_frames.items()]

        model_paths = {}
        for name, obj in {
            "pair_model": pair_model,
            "pair_calibrator": pair_calibrator,
            "entity_scaler": entity_scaler,
            "entity_model": entity_model,
            "entity_calibrator": entity_calibrator,
            "sparse_vectorizer": vectorizer,
            "dense_svd": candidate_index.svd,
        }.items():
            if obj is None:
                continue
            if name == "pair_model" and hasattr(obj, "booster_"):
                # native LightGBM booster text: portable, stable, and safe to load
                # in the same process as torch (joblib-pickle round-trips deadlock MPS)
                path = OUTPUT_RUN / f"{name}.txt"
                obj.booster_.save_model(str(path))
                model_paths[name] = str(path.relative_to(OUTPUT_RUN))
                print(f"saved model {name:<22} -> {path} (native text booster)")
                continue
            path = OUTPUT_RUN / f"{name}.joblib"
            temp = path.with_name(path.name + ".tmp")
            joblib.dump(obj, temp)
            os.replace(temp, path)
            model_paths[name] = str(path.relative_to(OUTPUT_RUN))
            print(f"saved model {name:<22} -> {path}")

        from scipy.sparse import save_npz

        sparse_path = OUTPUT_RUN / "reference_sparse_matrix.npz"
        save_npz(sparse_path, sparse_matrix)
        if reference_embeddings is not None:
            np.save(
                OUTPUT_RUN / "reference_embeddings.f16.npy", reference_embeddings.astype(np.float16)
            )

        def manifest_path(path: Path) -> str:
            try:
                return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
            except ValueError:
                return f"<external>/{path.name}"

        config_payload = {
            **asdict(CFG),
            "data_dir": manifest_path(CFG.data_dir),
            "output_dir": manifest_path(CFG.output_dir),
        }
        config_hash = hashlib.sha256(
            json.dumps(config_payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        try:
            git_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path.cwd(),
                capture_output=True,
                check=False,
                text=True,
            )
            git_commit = git_result.stdout.strip() if git_result.returncode == 0 else "unknown"
        except OSError:
            git_commit = "unknown"
        pipeline_source = Path(__file__)
        run_manifest = {
            "schema_version": "3.0",
            "created_at_utc": pd.Timestamp.utcnow().isoformat(),
            "code": {
                "git_commit": git_commit,
                "pipeline_sha256": hashlib.sha256(pipeline_source.read_bytes()).hexdigest(),
            },
            "data_fingerprint": data_fingerprint,
            "embedding_model_revision": embedding_model_revision_value,
            "embedding_preprocessing_version": (
                EMBEDDING_PREPROCESSING_VERSION if NEURAL_ENABLED else None
            ),
            "embedding_field_schema_version": (
                EMBEDDING_FIELD_SCHEMA_VERSION if NEURAL_ENABLED else None
            ),
            "config_hash": config_hash,
            "config": config_payload,
            "device": DEVICE,
            "device_diagnostics": DEVICE_DIAGNOSTICS,
            "runtime_policy": {
                "neural_device": DEVICE if NEURAL_ENABLED else None,
                "torch_dtype": F2LLM_MPS_TORCH_DTYPE if NEURAL_ENABLED else None,
                "neural_batch_size": CFG.neural_batch_size if NEURAL_ENABLED else None,
                "embedding_model_revision": embedding_model_revision_value,
                "embedding_preprocessing_version": (
                    EMBEDDING_PREPROCESSING_VERSION if NEURAL_ENABLED else None
                ),
                "embedding_field_schema_version": (
                    EMBEDDING_FIELD_SCHEMA_VERSION if NEURAL_ENABLED else None
                ),
                "dense_profile": CFG.dense_profile,
                "process_workers": process_worker_count(),
                "pytorch_mps_fallback": os.getenv("PYTORCH_ENABLE_MPS_FALLBACK", "0"),
                "macos_openmp_aligned": os.getenv("NAYOSE_MACOS_RUNTIME_ALIGNED") == "1",
            },
            "backends": {
                **BACKENDS,
                "pair_model": pair_model_backend,
                "pair_calibration": calibrator_backend,
                "dense_index": candidate_index.dense_backend,
                "neural_enabled": NEURAL_ENABLED,
                "degraded_run": candidate_index.dense_backend.endswith("degraded")
                or (CFG.model_family != "off" and not NEURAL_ENABLED),
            },
            "thresholds": {
                "pair_auto": auto_threshold,
                "pair_review": review_threshold,
                "entity_auto": entity_auto_threshold,
                "entity_review": entity_review_threshold,
                "entity_margin": CFG.entity_margin_threshold,
                "pair_auto_status": auto_threshold_status,
                "pair_review_status": review_threshold_status,
                "entity_auto_status": entity_threshold_status,
            },
            "identity_policy": {
                "auto_link_requires_exact_normalized_name": True,
                "auto_link_requires_exact_date_birth_or_email": True,
                "phone_only_is_reviewable": True,
            },
            "counts": {
                "reference_records": len(reference),
                "candidate_pairs": len(candidate_pairs),
                "possible_all_pairs": int(len(reference) * (len(reference) - 1) // 2),
                "auto_edges": int(candidate_pairs.edge_action.eq("AUTO_EDGE").sum()),
                "review_edges": int(candidate_pairs.edge_action.eq("REVIEW_EDGE").sum()),
                "predicted_entities": len(canonical_entities),
                "incoming_queries": len(incoming),
            },
            "candidate_metrics": {
                "pair_recall": candidate_pair_recall,
                "multi_record_entity_recall": entity_candidate_recall,
            },
            "pair_metrics": pair_test_metrics,
            "cluster_metrics": cluster_metrics,
            "query_metrics": query_metrics,
            "artifacts": artifacts,
            "models": model_paths,
            "embedding_model": MODELS[CFG.model_family]["embedding"] if NEURAL_ENABLED else None,
            "stage_metrics": STAGES,
        }
        manifest_path = OUTPUT_RUN / "run_manifest.json"
        manifest_tmp = manifest_path.with_name(manifest_path.name + ".tmp")
        manifest_tmp.write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(manifest_tmp, manifest_path)
        print(f"saved run manifest -> {manifest_path}")

    if os.getenv("NAYOSE_PROMOTE", "1") == "0":
        print("  promotion disabled; nayose model train will promote after verification")
    else:
        # Point models/current at this run and append to the run registry (Decision B).
        current_link = CFG.output_dir / "current"
        tmp_link = CFG.output_dir / f".current.tmp.{os.getpid()}"
        source_manifest = CFG.data_dir / "generation_manifest.json"
        if source_manifest.exists():
            source_fingerprint = json.loads(source_manifest.read_text(encoding="utf-8")).get(
                "data_fingerprint"
            )
            if source_fingerprint != data_fingerprint:
                raise RuntimeError("refusing to promote model: source data fingerprint changed")
        try:
            tmp_link.unlink(missing_ok=True)
            tmp_link.symlink_to(os.path.relpath(OUTPUT_RUN, CFG.output_dir))
            os.replace(tmp_link, current_link)
            registry_file = CFG.output_dir / "registry.json"
            registry = (
                json.loads(registry_file.read_text(encoding="utf-8"))
                if registry_file.exists()
                else []
            )
            registry.append(
                {
                    "run_id": RUN_ID,
                    "created_at": pd.Timestamp.utcnow().isoformat(),
                    "config_hash": RUN_CONFIG_HASH,
                    "data_fingerprint": data_fingerprint,
                    "model_family": CFG.model_family,
                    "device": DEVICE,
                    "pair_model_backend": pair_model_backend,
                    "dense_backend": candidate_index.dense_backend,
                    "stage_metrics": json.loads(pd.DataFrame(STAGES).to_json(orient="records")),
                }
            )
            registry_tmp = registry_file.with_name(f".{registry_file.name}.tmp.{os.getpid()}")
            registry_tmp.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(registry_tmp, registry_file)
            print(
                f"  current -> {OUTPUT_RUN.relative_to(CFG.output_dir)}; registry rows={len(registry)}"
            )
        except OSError:
            print("  WARN: symlink unavailable; skip current/ alias + registry")

    # End-to-end invariants. These test architecture, not unrealistic perfection.
    assert not FORBIDDEN_VISIBLE.intersection(reference_raw.columns)
    assert not FORBIDDEN_VISIBLE.intersection(incoming_raw.columns)
    assert len(cluster_map) == len(reference) and cluster_map.record_id.is_unique
    assert (
        float(
            full_dataset_summary.loc[
                full_dataset_summary.metric.eq("record_coverage"), "value"
            ].iloc[0]
        )
        == 1.0
    )
    assert set(cluster_map.record_id) == set(reference.record_id)
    assert canonical_entities.nayose_id.is_unique
    assert set(cluster_map.nayose_id) == set(canonical_entities.nayose_id)
    assert set(entity_attribute_history.nayose_id) <= set(canonical_entities.nayose_id)
    assert not candidate_pairs.duplicated(["left_index", "right_index"]).any()
    assert candidate_pairs.pair_probability.between(0, 1).all()
    assert review_threshold <= auto_threshold or auto_threshold > 1
    assert entity_review_threshold <= entity_auto_threshold or entity_auto_threshold > 1
    assert entity_examples.entity_probability.between(0, 1).all()
    assert query_results.query_id.is_unique and set(query_results.query_id) == set(
        incoming.query_id
    )
    empty_prediction = engine.recommend({field: "" for field in QUERY_REQUIRED})
    assert empty_prediction.action == "INSUFFICIENT_INPUT"
    assert set(evaluation_truth.scenario) >= {
        "existing_stable",
        "existing_changed",
        "existing_damaged",
        "new_customer",
        "related_only",
        "insufficient",
    }
    sample_id = incoming.query_id.iloc[0]
    before_id = frozen_predictions[sample_id].prediction_id
    _ = reveal_prediction(frozen_predictions[sample_id])
    assert frozen_predictions[sample_id].prediction_id == before_id
    assert all((OUTPUT_RUN / item["path"]).exists() for item in artifacts)
    assert manifest_path.exists()

    banner("NAYOSE PIPELINE PREFLIGHT PASSED")
    print(f"Full reference records resolved: {len(reference):,}")
    print(f"Candidate graph edges:           {len(candidate_pairs):,}")
    print(f"Predicted Nayose entities:       {len(canonical_entities):,}")
    print(f"Incoming queries evaluated:      {len(query_results):,}")
    print(f"Candidate pair recall:           {candidate_pair_recall:.3%}")
    print(f"B³ cluster F1:                   {cluster_metrics['b3_f1']:.3%}")
    print(
        f"Top-1 query identity accuracy:   {query_metrics['top1_identity_accuracy_existing']:.3%}"
    )
    print(f"Output directory:                {CFG.output_dir.resolve()}")
    if run_manifest["backends"]["degraded_run"]:
        print(
            "WARNING: DEGRADED RUN — configured neural models were unavailable; metrics are not directly comparable with an MPS neural run."
        )

    # ===== TRAINING STAGE 45 =====

    # 14b. Materialize per-entity lineage summary (Phase 2: fast dashboard lineage)
    try:
        _rn = pd.read_parquet(
            OUTPUT_RUN / "record_to_nayose.parquet", columns=["record_id", "nayose_id"]
        )
        _fl = pd.read_parquet(
            CFG.data_dir / "ground_truth" / "field_lineage.parquet",
            columns=["item_id", "field", "transformation"],
        )
        _sum = _fl.merge(_rn, left_on="item_id", right_on="record_id", how="inner")
        _agg = (
            _sum.groupby(["nayose_id", "field", "transformation"])
            .size()
            .rename("count")
            .reset_index()
        )
        _agg["entity_id"] = _agg.nayose_id
        _agg.drop(columns="nayose_id").to_parquet(
            OUTPUT_RUN / "lineage_summary.parquet", index=False
        )
        print(
            f"lineage_summary materialized: {len(_agg):,} rows -> {OUTPUT_RUN / 'lineage_summary.parquet'}"
        )
    except Exception as exc:
        print(
            f"WARN: lineage_summary not materialized ({type(exc).__name__}); dashboard falls back to on-the-fly"
        )

    workflow_ledger.counter("reference_records", len(reference))
    workflow_ledger.counter("incoming_queries", len(incoming))
    workflow_ledger.counter("candidate_pairs", len(candidate_pairs))
    workflow_ledger.counter("artifact_tables", len(artifacts))
    workflow_ledger.metadata("data_fingerprint", data_fingerprint)
    workflow_ledger.write(OUTPUT_RUN / "workflow_metrics.json")


if __name__ == "__main__":
    run()
