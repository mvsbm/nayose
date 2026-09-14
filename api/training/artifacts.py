from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from api.training.config import TrainingConfig
from api.training.contracts import (
    ArtifactRecord,
    ClusterResult,
    EntityTrainingResult,
    EvaluationResult,
    PairTrainingResult,
    RunIdentity,
    RuntimeInfo,
    StageRecord,
    TruthSnapshot,
)
from api.training.indexing import TrainingCandidateIndex


@dataclass(slots=True)
class RunBundleWriter:
    identity: RunIdentity
    config: TrainingConfig
    runtime: RuntimeInfo

    def write(
        self,
        index: TrainingCandidateIndex,
        pair: PairTrainingResult,
        clusters: ClusterResult,
        entity: EntityTrainingResult,
        evaluation: EvaluationResult,
        stages: list[StageRecord],
        data_fingerprint: str,
        truth: TruthSnapshot,
    ) -> Path:
        staging = self.config.output_dir / ".staging" / self.identity.run_id
        final = self.config.output_dir / "runs" / self.identity.run_id
        if staging.exists() or final.exists():
            raise FileExistsError(f"training run already exists: {self.identity.run_id}")
        staging.mkdir(parents=True, exist_ok=False)
        try:
            return self._write_staged(
                staging,
                index,
                pair,
                clusters,
                entity,
                evaluation,
                stages,
                data_fingerprint,
                truth,
            )
        except BaseException:
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _write_staged(
        self,
        staging: Path,
        index: TrainingCandidateIndex,
        pair: PairTrainingResult,
        clusters: ClusterResult,
        entity: EntityTrainingResult,
        evaluation: EvaluationResult,
        stages: list[StageRecord],
        data_fingerprint: str,
        truth: TruthSnapshot,
    ) -> Path:
        records: list[ArtifactRecord] = []
        frames = self._frames(pair, clusters, evaluation, stages, truth)
        if self.config.save_all_candidate_pairs:
            frames["all_candidate_pairs"] = pair.candidate_pairs
        for name, frame in frames.items():
            records.append(self._write_frame(staging, name, frame))
        models = {
            "pair_model": pair.model,
            "pair_calibrator": pair.calibrator,
            "entity_scaler": entity.scaler,
            "entity_model": entity.model,
            "entity_calibrator": entity.calibrator,
            "sparse_vectorizer": index.vectorizer,
            "dense_svd": index.svd,
        }
        for name, model in models.items():
            if model is not None:
                records.append(self._write_model(staging, name, model))
        from scipy.sparse import save_npz

        sparse_path = staging / "reference_sparse_matrix.npz"
        save_npz(sparse_path, index.sparse_matrix)
        records.append(self._record(sparse_path, "reference_sparse_matrix", 0))
        if index.reference_embeddings is not None:
            embedding_path = staging / "reference_embeddings.f16.npy"
            np.save(embedding_path, index.reference_embeddings.astype(np.float16))
            records.append(
                self._record(
                    embedding_path, "reference_embeddings", len(index.reference_embeddings)
                )
            )
        manifest = self._manifest(
            records, index, pair, clusters, entity, evaluation, stages, data_fingerprint
        )
        manifest_path = staging / "run_manifest.json"
        _atomic_json_write(manifest_path, manifest)
        self._fsync_tree(staging)
        final = self.config.output_dir / "runs" / self.identity.run_id
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise FileExistsError(f"training run already exists: {self.identity.run_id}")
        os.replace(staging, final)
        _fsync_directory(final.parent)
        return final

    @staticmethod
    def _frames(
        pair: PairTrainingResult,
        clusters: ClusterResult,
        evaluation: EvaluationResult,
        stages: list[StageRecord],
        truth: TruthSnapshot,
    ) -> dict[str, pd.DataFrame]:
        size_distribution = (
            clusters.canonical_entities.record_count.clip(upper=10)
            .value_counts()
            .sort_index()
            .rename_axis("records_per_entity_capped_10")
            .rename("entities")
            .reset_index()
        )
        size_distribution["represented_records_lower_bound"] = (
            size_distribution.records_per_entity_capped_10 * size_distribution.entities
        )
        edge_summary = (
            pair.candidate_pairs.groupby(["edge_action", "edge_policy_reason"], sort=False)
            .size()
            .rename("edges")
            .reset_index()
        )
        possible = len(clusters.mapping) * (len(clusters.mapping) - 1) // 2
        summary = pd.DataFrame(
            [
                {"metric": "reference_records", "value": len(clusters.mapping)},
                {"metric": "record_coverage", "value": 1.0},
                {"metric": "possible_all_pairs", "value": possible},
                {"metric": "candidate_edges", "value": len(pair.candidate_pairs)},
                {
                    "metric": "candidate_reduction",
                    "value": 1 - len(pair.candidate_pairs) / max(possible, 1),
                },
                {
                    "metric": "candidate_pair_recall",
                    "value": pair.metrics.get("candidate_pair_recall", float("nan")),
                },
                {
                    "metric": "multi_record_entity_candidate_recall",
                    "value": pair.metrics.get("multi_record_entity_recall", float("nan")),
                },
                {"metric": "predicted_entities", "value": len(clusters.canonical_entities)},
                {
                    "metric": "singleton_entity_rate",
                    "value": clusters.metrics.get("singleton_cluster_rate", float("nan")),
                },
                {"metric": "review_queue_edges", "value": len(clusters.review_queue)},
                {
                    "metric": "b3_precision",
                    "value": clusters.metrics.get("b3_precision", float("nan")),
                },
                {"metric": "b3_recall", "value": clusters.metrics.get("b3_recall", float("nan"))},
                {"metric": "b3_f1", "value": clusters.metrics.get("b3_f1", float("nan"))},
                {
                    "metric": "overmerge_record_rate",
                    "value": clusters.metrics.get("overmerge_record_rate", float("nan")),
                },
                {
                    "metric": "undermerge_record_rate",
                    "value": clusters.metrics.get("undermerge_record_rate", float("nan")),
                },
            ]
        )
        funnel = pd.DataFrame(
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
                    len(clusters.mapping),
                    possible,
                    len(pair.candidate_pairs),
                    int(pair.candidate_pairs.edge_action.eq("REVIEW_EDGE").sum()),
                    int(pair.candidate_pairs.edge_action.eq("AUTO_EDGE").sum()),
                    int(clusters.union_log.accepted.sum()) if len(clusters.union_log) else 0,
                    len(clusters.canonical_entities),
                ],
            }
        )
        return {
            "record_to_nayose": clusters.mapping,
            "canonical_entities": clusters.canonical_entities,
            "entity_attribute_history": clusters.attribute_history,
            "lineage_summary": _lineage_summary(truth.field_lineage, clusters.mapping),
            "cluster_diagnostics": clusters.diagnostics,
            "review_queue": clusters.review_queue,
            "accepted_union_log": clusters.union_log,
            "query_results": evaluation.query_results,
            "scenario_metrics": evaluation.scenario_metrics,
            "query_confusion": evaluation.query_confusion,
            "stage_metrics": pd.DataFrame([asdict(record) for record in stages]),
            "full_dataset_metrics": pd.DataFrame(
                [
                    {"section": "cluster", "metric": key, "value": value}
                    for key, value in clusters.metrics.items()
                ]
                + [
                    {"section": "pair", "metric": key, "value": value}
                    for key, value in pair.metrics.items()
                ]
                + [
                    {"section": "incoming_query", "metric": key, "value": value}
                    for key, value in evaluation.metrics.items()
                ]
            ),
            "full_dataset_summary": summary,
            "resolution_funnel": funnel,
            "edge_action_summary": edge_summary,
            "entity_size_distribution": size_distribution,
            "pair_confusion_test": pair.confusion_test,
        }

    @staticmethod
    def _write_frame(root: Path, name: str, frame: pd.DataFrame) -> ArtifactRecord:
        path = root / f"{name}.parquet"
        temporary = root / f".{name}.parquet.tmp.{os.getpid()}"
        try:
            frame.to_parquet(temporary, index=False)
            os.replace(temporary, path)
            format_name = "parquet"
        except (ImportError, OSError, ValueError):
            temporary.unlink(missing_ok=True)
            path = root / f"{name}.csv.gz"
            temporary = root / f".{name}.csv.gz.tmp.{os.getpid()}"
            frame.to_csv(temporary, index=False, compression="gzip")
            os.replace(temporary, path)
            format_name = "csv.gz"
        return RunBundleWriter._record(path, name, len(frame), format_name)

    @staticmethod
    def _write_model(root: Path, name: str, model: object) -> ArtifactRecord:
        if name == "pair_model" and hasattr(model, "booster_"):
            path = root / "pair_model.txt"
            model.booster_.save_model(str(path))
            return RunBundleWriter._record(path, name, 0, "lightgbm_text")
        path = root / f"{name}.joblib"
        temporary = root / f".{name}.joblib.tmp.{os.getpid()}"
        joblib.dump(model, temporary)
        os.replace(temporary, path)
        return RunBundleWriter._record(path, name, 0, "joblib")

    @staticmethod
    def _record(path: Path, name: str, rows: int, format_name: str | None = None) -> ArtifactRecord:
        return {
            "name": name,
            "path": path.name,
            "format": format_name or path.suffix.lstrip("."),
            "rows": rows,
            "sha256": _sha256(path),
        }

    def _manifest(
        self,
        records: list[ArtifactRecord],
        index: TrainingCandidateIndex,
        pair: PairTrainingResult,
        clusters: ClusterResult,
        entity: EntityTrainingResult,
        evaluation: EvaluationResult,
        stages: list[StageRecord],
        fingerprint: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "4.0",
            "created_at_utc": dt.datetime.now(dt.UTC).isoformat(),
            "training_algorithm_version": "modular-v1",
            "code": {
                "git_commit": _git_commit(),
                "pipeline_sha256": _pipeline_sha256(),
            },
            "data_fingerprint": fingerprint,
            "config_hash": self.identity.config_hash,
            "config": self.config.payload(),
            "device": self.runtime.device,
            "device_diagnostics": self.runtime.diagnostics,
            "embedding_model": self.config.model_spec()["embedding"]
            if self.runtime.neural_enabled
            else None,
            "embedding_model_revision": self.runtime.embedding_model_revision,
            "embedding_preprocessing_version": (
                "normalize-retrieval-v1" if self.runtime.neural_enabled else None
            ),
            "embedding_field_schema_version": (
                "retrieval-text-v1" if self.runtime.neural_enabled else None
            ),
            "identity_policy": {
                "auto_link_requires_exact_normalized_name": True,
                "auto_link_requires_exact_date_birth_or_email": True,
                "phone_only_is_reviewable": True,
            },
            "backends": {
                **self.runtime.backends,
                "pair_model": pair.backend,
                "pair_calibration": "platt_logistic_with_optional_reranker"
                if pair.calibrator is not None
                else "identity_fallback",
                "dense_index": index.dense_backend,
                "neural_enabled": self.runtime.neural_enabled,
                "degraded_run": self.config.model_family != "off"
                and not self.runtime.neural_enabled,
            },
            "runtime_policy": {
                "neural_device": self.runtime.device if self.runtime.neural_enabled else None,
                "torch_dtype": self.config.torch_dtype if self.runtime.neural_enabled else None,
                "neural_batch_size": self.config.neural_batch_size
                if self.runtime.neural_enabled
                else None,
                "process_workers": self.runtime.diagnostics.get("process_workers", 1),
                "embedding_model_revision": self.runtime.embedding_model_revision,
                "embedding_preprocessing_version": (
                    "normalize-retrieval-v1" if self.runtime.neural_enabled else None
                ),
                "embedding_field_schema_version": (
                    "retrieval-text-v1" if self.runtime.neural_enabled else None
                ),
                "dense_profile": self.config.dense_profile,
                "pytorch_mps_fallback": "0",
            },
            "thresholds": {
                "pair_auto": pair.auto_threshold,
                "pair_review": pair.review_threshold,
                "entity_auto": entity.auto_threshold,
                "entity_review": entity.review_threshold,
                "entity_margin": self.config.entity_margin_threshold,
                "pair_auto_status": pair.auto_status,
                "pair_review_status": pair.review_status,
                "entity_auto_status": entity.threshold_status,
            },
            "entity_scoring_mode": self.config.entity_scoring_mode,
            "counts": {
                "reference_records": len(clusters.mapping),
                "candidate_pairs": len(pair.candidate_pairs),
                "possible_all_pairs": len(clusters.mapping) * (len(clusters.mapping) - 1) // 2,
                "auto_edges": int(pair.candidate_pairs.edge_action.eq("AUTO_EDGE").sum()),
                "review_edges": int(pair.candidate_pairs.edge_action.eq("REVIEW_EDGE").sum()),
                "predicted_entities": len(clusters.canonical_entities),
                "incoming_queries": len(evaluation.query_results),
            },
            "candidate_metrics": {
                key: value for key, value in pair.metrics.items() if "recall" in key
            },
            "pair_metrics": pair.metrics,
            "cluster_metrics": clusters.metrics,
            "query_metrics": evaluation.metrics,
            "artifacts": records,
            "stage_metrics": [asdict(record) for record in stages],
        }

    @staticmethod
    def _fsync_tree(root: Path) -> None:
        for path in root.iterdir():
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        _fsync_directory(root)


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _pipeline_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _lineage_summary(lineage: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    columns = ["field", "transformation", "count", "entity_id"]
    if lineage.empty or not {"item_id", "field", "transformation"} <= set(lineage.columns):
        return pd.DataFrame(columns=columns)
    joined = lineage[["item_id", "field", "transformation"]].merge(
        mapping.rename(columns={"record_id": "item_id", "nayose_id": "entity_id"}),
        on="item_id",
        how="inner",
    )
    return (
        joined.groupby(["entity_id", "field", "transformation"], sort=False)
        .size()
        .rename("count")
        .reset_index()[columns]
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json_write(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)
