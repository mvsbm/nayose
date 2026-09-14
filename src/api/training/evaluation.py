from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from api.training.contracts import (
    ClusterResult,
    EntityTrainingResult,
    EvaluationResult,
    NormalizedCorpus,
    PairTrainingResult,
    TruthSnapshot,
)
from api.training.entity_training import ENTITY_FEATURES
from nayose.config import TrainingConfig


@dataclass(frozen=True, slots=True)
class Evaluator:
    config: TrainingConfig
    corpus: NormalizedCorpus
    truth: TruthSnapshot
    clusters: ClusterResult
    pair: PairTrainingResult
    entity: EntityTrainingResult

    def run(self) -> EvaluationResult:
        rows: list[dict[str, object]] = []
        evaluation = self.truth.evaluation_truth.copy()
        evaluation["query_id"] = evaluation["query_id"].astype(str)
        evaluation = evaluation.set_index("query_id")
        for query_id in self.corpus.incoming.query_id.astype(str):
            if query_id not in evaluation.index:
                continue
            entities = self.entity.entity_features.get(query_id, pd.DataFrame())
            truth_row = evaluation.loc[query_id]
            scored = self._score_entities(entities)
            action, top_id, top_score, margin = self._decision(scored)
            statuses = {
                str(entity): self._truth_status(str(entity), truth_row)
                for entity in scored.nayose_id.tolist()
            }
            top_status = statuses.get(top_id or "", "NO_CANDIDATE")
            correct = [
                index + 1
                for index, entity in enumerate(scored.nayose_id)
                if statuses[entity] in {"PURE_CORRECT", "CORRECT_BUT_CONTAMINATED"}
            ]
            rows.append(
                {
                    "query_id": query_id,
                    "scenario": str(truth_row.get("scenario", "")),
                    "split": str(truth_row.get("split", "")),
                    "expected_existing_customer": _bool(
                        truth_row.get("expected_existing_customer", False)
                    ),
                    "action": action,
                    "top_nayose_id": top_id or "",
                    "top_probability": top_score,
                    "margin": margin,
                    "correct_entity_rank": correct[0] if correct else 0,
                    "identity_top1_correct": top_status
                    in {"PURE_CORRECT", "CORRECT_BUT_CONTAMINATED"},
                    "pure_top1_correct": top_status == "PURE_CORRECT",
                    "top_truth_status": top_status,
                    "operational_result": self._operational_result(action, top_status, truth_row),
                }
            )
        query_results = pd.DataFrame(rows)
        if query_results.empty:
            raise ValueError("evaluation produced no query results")
        scenario_metrics = self._scenario_metrics(query_results)
        query_confusion = self._confusion(query_results)
        return EvaluationResult(
            query_results, scenario_metrics, query_confusion, self._metrics(query_results)
        )

    def _score_entities(self, entities: pd.DataFrame) -> pd.DataFrame:
        if entities.empty:
            return entities.copy()
        values = entities.copy()
        if self.config.entity_scoring_mode == "legacy_mean_top3":
            values["entity_probability"] = values.mean_top3_probability
        else:
            values["entity_probability"] = self.entity_probability(values)
        return values.sort_values(
            ["entity_probability", "nayose_id"], ascending=[False, True]
        ).reset_index(drop=True)

    def entity_probability(self, features: pd.DataFrame) -> np.ndarray:
        if features.empty:
            return np.empty(0)
        raw = np.asarray(
            self.entity.model.predict_proba(
                self.entity.scaler.transform(features[ENTITY_FEATURES])
            )[:, 1],
            dtype=np.float64,
        )
        if self.entity.calibrator is None:
            return raw
        return np.asarray(
            self.entity.calibrator.predict_proba(
                np.log(np.clip(raw, 1e-6, 1 - 1e-6) / np.clip(1 - raw, 1e-6, 1)).reshape(-1, 1)
            )[:, 1],
            dtype=np.float64,
        )

    def _decision(self, entities: pd.DataFrame) -> tuple[str, str | None, float, float]:
        if entities.empty:
            return "NO_MATCH", None, 0.0, 0.0
        top = entities.iloc[0]
        second = float(entities.entity_probability.iloc[1]) if len(entities) > 1 else 0.0
        probability = float(top.entity_probability)
        margin = probability - second
        auto = (
            probability >= self.entity.auto_threshold
            and margin >= self.config.entity_margin_threshold
            and float(top.max_evidence_family_count) >= self.config.minimum_evidence_families
            and int(top.hard_conflict_count) == 0
            and int(top.any_identity_anchor) == 1
        )
        action = (
            "AUTO_LINK"
            if auto
            else "REVIEW"
            if probability >= self.entity.review_threshold
            else "NO_MATCH"
        )
        return action, str(top.nayose_id), probability, margin

    def _truth_status(self, entity: str, row: pd.Series) -> str:
        values = self.clusters.truth_sets.get(entity, set())
        target = str(row.get("target_truth_id", ""))
        related_value = row.get("related_reference_truth_ids", "[]")
        related = set(__import__("json").loads(str(related_value or "[]")))
        existing = _bool(row.get("expected_existing_customer", False))
        if existing and values == {target}:
            return "PURE_CORRECT"
        if existing and target in values:
            return "CORRECT_BUT_CONTAMINATED"
        if values & related:
            return "RELATED_DIFFERENT"
        return "WRONG_ENTITY"

    @staticmethod
    def _operational_result(action: str, status: str, row: pd.Series) -> str:
        if _bool(row.get("expected_existing_customer", False)):
            if action == "AUTO_LINK" and status == "PURE_CORRECT":
                return "SAFE_AUTO_LINK"
            if action == "AUTO_LINK":
                return "UNSAFE_AUTO_LINK"
            return "SAFE_ABSTENTION"
        return "FALSE_AUTO_LINK" if action == "AUTO_LINK" else "CORRECT_NO_AUTO_LINK"

    @staticmethod
    def _metrics(results: pd.DataFrame) -> dict[str, float]:
        existing = results.expected_existing_customer.astype(bool)
        auto = results.action.eq("AUTO_LINK")
        ranks = results.correct_entity_rank
        return {
            "queries": float(len(results)),
            "candidate_recall_at_1_existing": float(ranks[existing].between(1, 1).mean())
            if existing.any()
            else float("nan"),
            "candidate_recall_at_5_existing": float(ranks[existing].between(1, 5).mean())
            if existing.any()
            else float("nan"),
            "candidate_recall_at_10_existing": float(ranks[existing].between(1, 10).mean())
            if existing.any()
            else float("nan"),
            "mean_reciprocal_rank_existing": float(
                np.where(ranks[existing] > 0, 1 / ranks[existing], 0).mean()
            )
            if existing.any()
            else float("nan"),
            "top1_identity_accuracy_existing": float(
                results.loc[existing, "identity_top1_correct"].mean()
            )
            if existing.any()
            else float("nan"),
            "top1_pure_accuracy_existing": float(results.loc[existing, "pure_top1_correct"].mean())
            if existing.any()
            else float("nan"),
            "auto_link_precision_pure": float(results.loc[auto, "pure_top1_correct"].mean())
            if auto.any()
            else float("nan"),
            "auto_link_end_to_end_recall_existing": float(
                (auto & results.pure_top1_correct)[existing].mean()
            )
            if existing.any()
            else float("nan"),
            "new_or_related_safe_no_auto_rate": float((~auto)[~existing].mean())
            if (~existing).any()
            else float("nan"),
            "false_auto_link_rate_new_or_related": float(auto[~existing].mean())
            if (~existing).any()
            else float("nan"),
            "review_rate": float(results.action.eq("REVIEW").mean()),
            "no_match_rate": float(results.action.eq("NO_MATCH").mean()),
            "insufficient_input_rate": 0.0,
        }

    @staticmethod
    def _scenario_metrics(results: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for scenario, group in results.groupby("scenario", sort=True):
            expected = group.expected_existing_customer.astype(bool)
            rows.append(
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
        return pd.DataFrame(rows)

    @staticmethod
    def _confusion(results: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for scenario, group in results.groupby("scenario", sort=False):
            table = pd.crosstab(group.action, group.expected_existing_customer).reindex(
                columns=[False, True], fill_value=0
            )
            for action in ("AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"):
                rows.append(
                    {
                        "scenario": scenario,
                        "action": action,
                        "expected_no": int(table.loc[action, False])
                        if action in table.index
                        else 0,
                        "expected_yes": int(table.loc[action, True])
                        if action in table.index
                        else 0,
                    }
                )
        return pd.DataFrame(rows)


def _bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}
