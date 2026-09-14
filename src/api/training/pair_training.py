from __future__ import annotations

import gc
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

from api.domain.features import PAIR_FEATURE_COLS, compute_pair_features_frame, logit_clip
from api.domain.hard_negatives import select_hard_negatives
from api.training.contracts import PairTrainingResult, RuntimeInfo, TruthSnapshot
from nayose.config import MODEL_SPECS, TrainingConfig


@dataclass(frozen=True, slots=True)
class PairTrainer:
    config: TrainingConfig
    runtime: RuntimeInfo
    reference: pd.DataFrame
    frequency_maps: dict[str, dict[str, int]]

    def fit(self, graph: pd.DataFrame, truth: TruthSnapshot) -> PairTrainingResult:
        pairs, truth_groups, split_by_truth = _label_graph(graph, truth, self.reference)
        rng = np.random.default_rng(self.config.seed)
        train_rows = self._sample_rows(pairs, "train", rng)
        calibration_rows = self._sample_rows(pairs, "calibration", rng)
        test_rows = pairs.index[pairs["pair_split"].eq("test")].to_numpy()
        train_features = self._feature_frame(pairs, train_rows)
        model, backend = _fit_model(
            train_features,
            pairs.loc[train_rows, "label"].to_numpy(),
            self.config,
            self.runtime,
        )
        probabilities: np.ndarray = np.empty(len(pairs), dtype=np.float64)
        for start in range(0, len(pairs), self.config.pair_feature_batch):
            stop = min(start + self.config.pair_feature_batch, len(pairs))
            features = self._feature_frame(pairs, np.arange(start, stop, dtype=np.int64))
            probabilities[start:stop] = _predict(model, features)
        pairs["base_probability"] = probabilities
        reranker_scores, reranker_present = self._rerank(pairs, model)
        pairs["reranker_score"] = reranker_scores
        pairs["reranker_present"] = reranker_present
        calibrator = _fit_calibrator(pairs, calibration_rows, self.config.seed)
        pairs["pair_probability"] = _calibrated_probabilities(pairs, calibrator)
        calibration_labels = pairs.loc[calibration_rows, "label"].to_numpy()
        auto_threshold, _, auto_status = threshold_for_precision(
            pairs.loc[calibration_rows, "pair_probability"].to_numpy(),
            calibration_labels,
            self.config.pair_auto_precision_target,
            decision_profile=self.config.decision_profile,
        )
        review_threshold, _, review_status = threshold_for_precision(
            pairs.loc[calibration_rows, "pair_probability"].to_numpy(),
            calibration_labels,
            self.config.pair_review_precision_target,
            decision_profile=self.config.decision_profile,
        )
        review_threshold = _ordered_review_threshold(review_threshold, auto_threshold)
        pairs = self._apply_policy(pairs, auto_threshold, review_threshold)
        metrics, confusion_test = _test_metrics(
            pairs,
            test_rows,
            auto_threshold,
            self.reference,
            truth_groups,
            split_by_truth,
            self.frequency_maps,
            self.config,
        )
        return PairTrainingResult(
            model,
            calibrator,
            backend,
            pairs,
            auto_threshold,
            review_threshold,
            auto_status,
            review_status,
            metrics,
            confusion_test,
        )

    def _sample_rows(self, pairs: pd.DataFrame, split: str, rng: np.random.Generator) -> np.ndarray:
        rows = np.flatnonzero(pairs["pair_split"].to_numpy() == split)
        labels = pairs["label"].to_numpy(dtype=np.int8, copy=False)
        positive = rows[labels[rows] == 1]
        negative = rows[labels[rows] == 0]
        limit = min(
            len(negative),
            max(len(positive) * self.config.training_negatives_per_positive, 500),
        )
        if len(negative) > limit:
            negative = select_hard_negatives(
                pairs,
                self.reference,
                negative,
                limit,
                frequency_maps=self.frequency_maps,
            )
        selected = np.unique(np.concatenate([positive, negative]))
        rng.shuffle(selected)
        return selected

    def _feature_frame(self, pairs: pd.DataFrame, rows: np.ndarray) -> pd.DataFrame:
        selected = pairs.iloc[rows]
        left = self.reference.iloc[selected.left_index.to_numpy()].reset_index(drop=True)
        right = self.reference.iloc[selected.right_index.to_numpy()].reset_index(drop=True)
        values = compute_pair_features_frame(
            left,
            right,
            selected,
            frequency_maps=self.frequency_maps,
        )
        if not np.isfinite(values.to_numpy(dtype=np.float32, copy=False)).all():
            raise ValueError("pair feature computation produced non-finite values")
        return values

    def _rerank(self, pairs: pd.DataFrame, model: Any) -> tuple[np.ndarray, np.ndarray]:
        scores: np.ndarray = np.zeros(len(pairs), dtype=np.float32)
        present: np.ndarray = np.zeros(len(pairs), dtype=np.float32)
        model_name = MODEL_SPECS[self.config.model_family]["reranker"]
        if not self.runtime.neural_enabled or not model_name or not len(pairs):
            return scores, present
        selected = pairs.nlargest(
            min(self.config.rerank_pair_limit, len(pairs)),
            ["base_probability", "retrieval_score"],
        )
        left = self.reference.iloc[selected.left_index.to_numpy()].reset_index(drop=True)
        right = self.reference.iloc[selected.right_index.to_numpy()].reset_index(drop=True)
        values = CrossEncoderReranker(
            model_name, self.runtime.device, self.config.neural_batch_size
        ).score(left, right)
        scores[selected.index.to_numpy()] = values
        present[selected.index.to_numpy()] = 1.0
        return scores, present

    def _apply_policy(
        self, pairs: pd.DataFrame, auto_threshold: float, review_threshold: float
    ) -> pd.DataFrame:
        values = pairs.copy()
        features = self._feature_frame(values, np.arange(len(values), dtype=np.int64))
        hard_conflict = features.type_conflict.eq(1) | features.dob_conflict.eq(1)
        sufficient = features.evidence_family_count.ge(self.config.minimum_evidence_families)
        name_anchor = features.name_strict_exact.eq(1) | features.name_loose_exact.eq(1)
        identity_anchor = (features.dob_exact.eq(1) | features.email_exact.eq(1)) & name_anchor
        auto_eligible = ~hard_conflict & sufficient & identity_anchor
        values["edge_action"] = np.select(
            [
                values.pair_probability.ge(auto_threshold) & auto_eligible,
                values.pair_probability.ge(review_threshold),
            ],
            ["AUTO_EDGE", "REVIEW_EDGE"],
            default="REJECT_EDGE",
        )
        values["edge_policy_reason"] = np.select(
            [
                values.edge_action.eq("AUTO_EDGE"),
                values.pair_probability.ge(auto_threshold) & hard_conflict,
                values.pair_probability.ge(auto_threshold) & ~sufficient,
                values.pair_probability.ge(auto_threshold) & ~identity_anchor,
                values.pair_probability.ge(review_threshold),
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
        return values


class CrossEncoderReranker:
    def __init__(self, model_name: str, device: str, batch_size: int):
        import torch
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(
            model_name,
            device=device,
            model_kwargs={"torch_dtype": torch.float16} if device in {"mps", "cuda"} else {},
        )
        self._batch_size = max(1, batch_size // 2)

    def score(self, left: pd.DataFrame, right: pd.DataFrame) -> np.ndarray:
        instruction = (
            "Determine whether the two Japanese insurance customer records refer to the "
            "same legal person or company."
        )
        examples = [
            (
                instruction + "\nRecord A: " + _serialize_record(a),
                "Record B: " + _serialize_record(b),
            )
            for (_, a), (_, b) in zip(left.iterrows(), right.iterrows(), strict=True)
        ]
        lengths = np.asarray([len(item[0]) + len(item[1]) for item in examples])
        order = np.argsort(lengths)
        values: np.ndarray = np.empty(len(examples), dtype=np.float32)
        for start in range(0, len(examples), self._batch_size):
            selected = order[start : start + self._batch_size]
            predicted = np.asarray(
                self._model.predict(
                    [examples[index] for index in selected], batch_size=self._batch_size
                )
            ).reshape(-1)
            values[selected] = predicted
        if values.min(initial=0) < 0 or values.max(initial=1) > 1:
            values = expit(values)
        del self._model
        gc.collect()
        return values


def _serialize_record(row: pd.Series) -> str:
    return " | ".join(
        f"{label}={row.get(column, '') or '[MISSING]'}"
        for label, column in (
            ("種別", "party_type"),
            ("氏名漢字", "name_kanji"),
            ("氏名カナ", "name_kana"),
            ("生年月日", "date_birth"),
            ("郵便番号", "postal_code"),
            ("住所", "address"),
            ("電話", "phone"),
            ("メール", "email"),
        )
    )


def _label_graph(
    graph: pd.DataFrame, truth: TruthSnapshot, reference: pd.DataFrame
) -> tuple[pd.DataFrame, pd.Series, dict[str, str]]:
    values = graph.copy()
    record_to_truth = truth.record_truth.set_index("record_id")["truth_id"]
    values["left_truth_id"] = values.left_record_id.map(record_to_truth)
    values["right_truth_id"] = values.right_record_id.map(record_to_truth)
    values["label"] = values.left_truth_id.eq(values.right_truth_id).astype(np.int8)
    party_splits = (
        truth.party_truth.set_index("truth_id")["split"].to_dict()
        if "split" in truth.party_truth
        else truth.record_truth.groupby("truth_id").split.first().to_dict()
    )
    values["pair_split"] = [
        party_splits.get(left, "")
        if party_splits.get(left, "") == party_splits.get(right, "")
        else "cross"
        for left, right in zip(values.left_truth_id, values.right_truth_id, strict=True)
    ]
    groups = truth.record_truth.groupby("truth_id").size()
    values.drop(columns=["left_truth_id", "right_truth_id"], inplace=True)
    return values, groups, {str(key): str(value) for key, value in party_splits.items()}


def _fit_model(
    features: pd.DataFrame, labels: np.ndarray, config: TrainingConfig, runtime: RuntimeInfo
) -> tuple[Any, str]:
    if len(np.unique(labels)) < 2:
        raise ValueError("pair training requires both classes")
    if config.pair_model_backend in {"auto", "lightgbm"} and runtime.backends.get(
        "lightgbm", False
    ):
        import lightgbm as lgb

        model = lgb.LGBMClassifier(
            objective="binary",
            n_estimators=260,
            learning_rate=0.035,
            num_leaves=31,
            min_child_samples=25,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_alpha=0.2,
            reg_lambda=0.8,
            random_state=config.seed,
            verbosity=-1,
            n_jobs=1,
            force_col_wise=True,
        )
        model.fit(features, labels)
        return model, "lightgbm"
    if config.pair_model_backend == "lightgbm":
        raise RuntimeError("LightGBM was explicitly requested but is unavailable")
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(
        max_iter=220,
        learning_rate=0.045,
        max_leaf_nodes=31,
        l2_regularization=0.8,
        random_state=config.seed,
    )
    model.fit(features, labels)
    return model, "sklearn_hist_gradient_boosting"


def _predict(model: Any, features: pd.DataFrame) -> np.ndarray:
    return np.asarray(model.predict_proba(features[PAIR_FEATURE_COLS])[:, 1], dtype=np.float64)


def _fit_calibrator(pairs: pd.DataFrame, rows: np.ndarray, seed: int) -> Any | None:
    labels = pairs.loc[rows, "label"].to_numpy()
    if len(np.unique(labels)) < 2:
        return None
    features = np.column_stack(
        [
            logit_clip(pairs.loc[rows, "base_probability"].to_numpy()),
            pairs.loc[rows, "reranker_score"].to_numpy(),
            pairs.loc[rows, "reranker_present"].to_numpy(),
        ]
    )
    return LogisticRegression(C=1.0, max_iter=1000, random_state=seed).fit(features, labels)


def _calibrated_probabilities(pairs: pd.DataFrame, calibrator: Any | None) -> np.ndarray:
    base = pairs.base_probability.to_numpy()
    if calibrator is None:
        return base
    features = np.column_stack(
        [logit_clip(base), pairs.reranker_score.to_numpy(), pairs.reranker_present.to_numpy()]
    )
    return np.asarray(calibrator.predict_proba(features)[:, 1], dtype=np.float64)


def wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return 0.0
    p = successes / total
    denominator = 1 + z * z / total
    center = p + z * z / (2 * total)
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (center - radius) / denominator


def threshold_for_precision(
    scores: np.ndarray,
    labels: np.ndarray,
    target: float,
    minimum_predictions: int = 8,
    decision_profile: str = "audit",
) -> tuple[float, pd.DataFrame, str]:
    order = np.argsort(-scores)
    ordered_scores, ordered_labels = scores[order], labels[order].astype(int)
    count = np.arange(1, len(labels) + 1)
    true_positives: np.ndarray = np.cumsum(ordered_labels)
    precision = true_positives / count
    recall = true_positives / max(int(ordered_labels.sum()), 1)
    lower = np.asarray(
        [wilson_lower(int(a), int(b)) for a, b in zip(true_positives, count, strict=True)]
    )
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-12)
    curve = pd.DataFrame(
        {
            "threshold": ordered_scores,
            "predictions": count,
            "true_positives": true_positives,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "wilson_lower_95": lower,
        }
    )
    eligible = np.flatnonzero((lower >= target) & (count >= minimum_predictions))
    if len(eligible):
        position, status = int(eligible[-1]), "TARGET_MET"
    elif decision_profile == "audit" and len(curve):
        valid = np.flatnonzero(count >= min(minimum_predictions, len(count)))
        position = int(valid[np.argmax(f1[valid])]) if len(valid) else int(np.argmax(f1))
        status = "AUDIT_F1_FALLBACK_TARGET_NOT_PROVEN"
    else:
        return 1.000001, curve, "TARGET_NOT_MET_ABSTAIN"
    return float(ordered_scores[position]), curve, status


def _ordered_review_threshold(review: float, auto: float) -> float:
    return max(0.0, min(review, auto - 1e-6) if auto <= 1 else review)


def _test_metrics(
    pairs: pd.DataFrame,
    test_rows: np.ndarray,
    threshold: float,
    reference: pd.DataFrame,
    truth_groups: pd.Series,
    split_by_truth: dict[str, str],
    frequency_maps: dict[str, dict[str, int]],
    config: TrainingConfig,
) -> tuple[dict[str, float], pd.DataFrame]:
    if not len(test_rows):
        return {}, pd.DataFrame(columns=["metric", "value"])
    labels = pairs.loc[test_rows, "label"].to_numpy()
    probabilities = pairs.loc[test_rows, "pair_probability"].to_numpy()
    left = reference.iloc[pairs.loc[test_rows, "left_index"].to_numpy()].reset_index(drop=True)
    right = reference.iloc[pairs.loc[test_rows, "right_index"].to_numpy()].reset_index(drop=True)
    features = compute_pair_features_frame(
        left,
        right,
        pairs.loc[test_rows],
        frequency_maps=frequency_maps,
    )
    hard_conflict = features.type_conflict.eq(1) | features.dob_conflict.eq(1)
    sufficient = features.evidence_family_count.ge(config.minimum_evidence_families)
    name_anchor = features.name_strict_exact.eq(1) | features.name_loose_exact.eq(1)
    identity_anchor = (features.dob_exact.eq(1) | features.email_exact.eq(1)) & name_anchor
    auto = (
        (probabilities >= threshold)
        & ~hard_conflict.to_numpy()
        & sufficient.to_numpy()
        & identity_anchor.to_numpy()
    )
    tp = int((auto & (labels == 1)).sum())
    fp = int((auto & (labels == 0)).sum())
    tn = int((~auto & (labels == 0)).sum())
    fn = int((~auto & (labels == 1)).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    test_ids = [key for key, split in split_by_truth.items() if split == "test"]
    test_sizes = truth_groups.reindex(test_ids).fillna(0).astype(int)
    true_pairs = int(((test_sizes * (test_sizes - 1)) // 2).sum())
    average_precision = (
        float(average_precision_score(labels, probabilities))
        if len(np.unique(labels)) > 1
        else float("nan")
    )
    specificity = tn / max(tn + fp, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    metrics = {
        "candidate_average_precision": average_precision,
        "auto_edge_precision": precision,
        "auto_edge_candidate_recall": recall,
        "auto_edge_end_to_end_recall": tp / max(true_pairs, 1),
        "auto_edge_predictions": float(auto.sum()),
        "auto_edge_specificity": specificity,
        "auto_edge_f1": f1,
    }
    confusion = pd.DataFrame(
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
                "average_precision",
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
                f1,
                average_precision,
                len(labels),
                int((labels == 1).sum()),
                threshold,
            ],
        }
    )
    return metrics, confusion
