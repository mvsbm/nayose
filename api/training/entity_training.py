from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from api.features import (
    ENTITY_FEATURE_COLS,
    PAIR_FEATURE_COLS,
    compute_pair_features_frame,
    logit_clip,
)
from api.training.config import TrainingConfig
from api.training.contracts import (
    ClusterResult,
    EntityTrainingResult,
    NormalizedCorpus,
    PairTrainingResult,
    RuntimeInfo,
    TruthSnapshot,
)
from api.training.indexing import TrainingCandidateIndex
from api.training.pair_training import CrossEncoderReranker, threshold_for_precision

ENTITY_FEATURES = ENTITY_FEATURE_COLS


@dataclass(slots=True)
class RetrievalState:
    exact_blocks: set[str] = field(default_factory=set)
    sparse_score: float = 0.0
    dense_score: float = 0.0
    sparse_rank: int = 0
    dense_rank: int = 0


@dataclass(frozen=True, slots=True)
class EntityTrainer:
    config: TrainingConfig
    runtime: RuntimeInfo
    corpus: NormalizedCorpus
    index: TrainingCandidateIndex
    pair: PairTrainingResult
    clusters: ClusterResult
    truth: TruthSnapshot

    def fit(self) -> EntityTrainingResult:
        entity_features = self._score_queries()
        examples = self._build_examples(entity_features)
        train = examples.loc[examples.query_split.eq("train")]
        calibration = examples.loc[examples.query_split.eq("calibration")]
        if train.pure_correct.nunique() < 2:
            raise ValueError("entity training requires positive and negative examples")
        positive = train.loc[train.pure_correct.eq(1)]
        negative = (
            train.loc[train.pure_correct.eq(0)]
            .sort_values("max_pair_probability", ascending=False)
            .head(max(len(positive) * 8, 1000))
        )
        fit = pd.concat([positive, negative]).sample(frac=1, random_state=self.config.seed)
        scaler = StandardScaler().fit(fit[ENTITY_FEATURES])
        model = LogisticRegression(
            C=0.8, class_weight="balanced", max_iter=2000, random_state=self.config.seed
        )
        model.fit(scaler.transform(fit[ENTITY_FEATURES]), fit.pure_correct)
        calibrator = self._fit_calibrator(model, scaler, calibration, self.config.seed)
        probabilities = self._probability(model, scaler, calibrator, examples[ENTITY_FEATURES])
        examples = examples.copy()
        examples["entity_probability"] = probabilities
        top = (
            examples.loc[examples.query_split.eq("calibration")]
            .sort_values(["query_id", "entity_probability"], ascending=[True, False])
            .groupby("query_id", sort=False)
            .head(1)
        )
        threshold, _, status = (
            threshold_for_precision(
                top.entity_probability.to_numpy(),
                top.pure_correct.to_numpy(),
                self.config.entity_auto_precision_target,
                minimum_predictions=5,
                decision_profile=self.config.decision_profile,
            )
            if len(top)
            else (1.000001, pd.DataFrame(), "NO_CALIBRATION_QUERIES")
        )
        review = (
            min(self.config.entity_review_threshold, threshold - 1e-6)
            if threshold <= 1
            else self.config.entity_review_threshold
        )
        return EntityTrainingResult(
            scaler,
            model,
            calibrator,
            entity_features,
            threshold,
            max(0.0, review),
            status,
            {},
        )

    def _score_queries(self) -> dict[str, pd.DataFrame]:
        queries = self.corpus.incoming.reset_index(drop=True)
        retrievals = _retrieve_many(self.index, queries)
        retrieval_rows: list[dict[str, object]] = []
        for position, query in enumerate(queries.to_dict("records")):
            query_id = str(query["query_id"])
            for item in retrievals[query_id].to_dict("records"):
                retrieval_rows.append({"query_position": position, "query_id": query_id, **item})
        entities = {
            str(query_id): pd.DataFrame(columns=["nayose_id", *ENTITY_FEATURES])
            for query_id in queries.query_id
        }
        if not retrieval_rows:
            return entities
        retrieval_long = pd.DataFrame(retrieval_rows).reset_index(drop=True)
        left = queries.iloc[retrieval_long.query_position.to_numpy()].reset_index(drop=True)
        candidates = self.corpus.reference.iloc[
            retrieval_long.candidate_index.to_numpy()
        ].reset_index(drop=True)
        features = compute_pair_features_frame(
            left, candidates, retrieval_long, frequency_maps=self.corpus.value_frequency
        )
        base = np.asarray(
            self.pair.model.predict_proba(features[PAIR_FEATURE_COLS])[:, 1], dtype=np.float64
        )
        reranker_score: np.ndarray = np.zeros(len(base), dtype=np.float32)
        reranker_present: np.ndarray = np.zeros(len(base), dtype=np.float32)
        model_name = self._reranker_name()
        if model_name and self.runtime.neural_enabled:
            selected = (
                retrieval_long[["query_id"]]
                .assign(base_probability=base)
                .sort_values(["query_id", "base_probability"], ascending=[True, False])
                .groupby("query_id", sort=False)
                .head(self.config.rerank_top_k)
                .index.to_numpy()
            )
            reranker = CrossEncoderReranker(
                model_name, self.runtime.device, self.config.neural_batch_size
            )
            reranker_score[selected] = reranker.score(
                left.iloc[selected].reset_index(drop=True),
                candidates.iloc[selected].reset_index(drop=True),
            )
            reranker_present[selected] = 1.0
        probability = _calibrate(base, reranker_score, reranker_present, self.pair.calibrator)
        scored = pd.concat(
            [
                retrieval_long[
                    [
                        "query_id",
                        *[
                            column
                            for column in retrieval_long.columns
                            if column not in {"query_id", "query_position"}
                        ],
                    ]
                ].reset_index(drop=True),
                candidates,
                features.add_prefix("f_"),
            ],
            axis=1,
        )
        scored["base_probability"] = base
        scored["reranker_score"] = reranker_score
        scored["pair_probability"] = probability
        mapping = self.clusters.mapping.set_index("record_id")["nayose_id"]
        scored["nayose_id"] = scored.record_id.map(mapping)
        for query_id, group in scored.groupby("query_id", sort=False):
            ordered = group.sort_values("pair_probability", ascending=False).reset_index(drop=True)
            entities[str(query_id)] = _aggregate_entities(
                ordered, self.clusters.canonical_entities, self.pair.review_threshold
            )
        return entities

    def _reranker_name(self) -> str | None:
        return self.config.model_spec()["reranker"]

    def _build_examples(self, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
        evaluation = self.truth.evaluation_truth
        rows: list[pd.DataFrame] = []
        for query_id, table in tables.items():
            if table.empty:
                continue
            meta = evaluation.loc[evaluation.query_id.astype(str).eq(query_id)].iloc[0]
            frame = table.copy()
            frame.insert(0, "query_id", query_id)
            frame["query_split"] = str(meta.get("split", ""))
            frame["scenario"] = str(meta.get("scenario", ""))
            frame["target_truth_id"] = str(meta.get("target_truth_id", ""))
            frame["expected_existing_customer"] = meta.get("expected_existing_customer", False)
            frame["truth_set"] = frame.nayose_id.map(
                lambda entity: json.dumps(sorted(self.clusters.truth_sets.get(str(entity), set())))
            )
            frame["contains_target"] = [
                int(
                    _bool(existing)
                    and str(target) in self.clusters.truth_sets.get(str(entity), set())
                )
                for existing, target, entity in zip(
                    frame.expected_existing_customer,
                    frame.target_truth_id,
                    frame.nayose_id,
                    strict=True,
                )
            ]
            frame["pure_correct"] = [
                int(
                    _bool(existing)
                    and self.clusters.truth_sets.get(str(entity), set()) == {str(target)}
                )
                for existing, target, entity in zip(
                    frame.expected_existing_customer,
                    frame.target_truth_id,
                    frame.nayose_id,
                    strict=True,
                )
            ]
            rows.append(frame)
        if not rows:
            raise ValueError("query benchmark produced no entity candidates")
        return pd.concat(rows, ignore_index=True)

    @staticmethod
    def _fit_calibrator(
        model: Any, scaler: Any, calibration: pd.DataFrame, seed: int
    ) -> Any | None:
        if len(calibration) == 0 or calibration.pure_correct.nunique() < 2:
            return None
        raw = model.predict_proba(scaler.transform(calibration[ENTITY_FEATURES]))[:, 1]
        return LogisticRegression(C=1.0, max_iter=1000, random_state=seed).fit(
            logit_clip(raw).reshape(-1, 1), calibration.pure_correct
        )

    @staticmethod
    def _probability(
        model: Any, scaler: Any, calibrator: Any | None, features: pd.DataFrame
    ) -> np.ndarray:
        raw = np.asarray(
            model.predict_proba(scaler.transform(features[ENTITY_FEATURES]))[:, 1], dtype=np.float64
        )
        if calibrator is None:
            return raw
        return np.asarray(
            calibrator.predict_proba(logit_clip(raw).reshape(-1, 1))[:, 1], dtype=np.float64
        )


def _retrieve_many(index: TrainingCandidateIndex, queries: pd.DataFrame) -> dict[str, pd.DataFrame]:
    values = queries.reset_index(drop=True)
    empty_ids: np.ndarray = np.empty((len(values), 0), dtype=np.int32)
    empty_scores: np.ndarray = np.empty((len(values), 0), dtype=np.float32)
    sparse_ids, sparse_scores = empty_ids, empty_scores
    dense_ids, dense_scores = empty_ids, empty_scores
    if index.config.exact_mode != "exact_only":
        sparse_ids, sparse_scores = index.sparse_candidates(
            values.retrieval_text.astype(str).tolist(), index.config.sparse_top_k
        )
        dense_ids, dense_scores = index.dense_candidates(
            values.retrieval_text.astype(str).tolist(), index.config.dense_top_k
        )
    output: dict[str, pd.DataFrame] = {}
    for position, (_, query) in enumerate(values.iterrows()):
        states: dict[int, RetrievalState] = {}
        if index.config.exact_mode != "fuzzy_only":
            for candidate, blocks in index.exact_candidates(query).items():
                states.setdefault(candidate, RetrievalState()).exact_blocks.update(blocks)
        if index.config.exact_mode != "exact_only":
            _merge_retrieval(states, sparse_ids[position], sparse_scores[position], "sparse")
            if dense_ids.shape[1]:
                _merge_retrieval(states, dense_ids[position], dense_scores[position], "dense")
        output[str(query.query_id)] = _retrieval_frame(index, states)
    return output


def _retrieval_frame(
    index: TrainingCandidateIndex, states: dict[int, RetrievalState]
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate, state in states.items():
        exact_blocks = sorted(state.exact_blocks)
        rows.append(
            {
                "candidate_index": candidate,
                "exact_blocks": ",".join(exact_blocks),
                "exact_count": len(exact_blocks),
                "sparse_score": state.sparse_score,
                "dense_score": state.dense_score,
                "sparse_rank": state.sparse_rank,
                "dense_rank": state.dense_rank,
                "retrieval_score": max(state.sparse_score, state.dense_score)
                + 0.08 * min(len(exact_blocks), 3),
                "channel_count": int(bool(exact_blocks))
                + int(state.sparse_score > 0)
                + int(state.dense_score > 0),
            }
        )
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .sort_values(["retrieval_score", "channel_count"], ascending=False)
        .head(index.config.candidate_cap_per_record)
        .reset_index(drop=True)
    )


def _merge_retrieval(
    states: dict[int, RetrievalState], ids: np.ndarray, scores: np.ndarray, channel: str
) -> None:
    for rank, (candidate, score) in enumerate(zip(ids, scores, strict=True), start=1):
        if int(candidate) < 0:
            continue
        state = states.setdefault(int(candidate), RetrievalState())
        if channel == "sparse" and float(score) > state.sparse_score:
            state.sparse_score, state.sparse_rank = float(score), rank
        if channel == "dense" and float(score) > state.dense_score:
            state.dense_score, state.dense_rank = float(score), rank


def _calibrate(
    base: np.ndarray, reranker: np.ndarray, present: np.ndarray, calibrator: Any | None
) -> np.ndarray:
    if calibrator is None:
        return base
    stacked = np.column_stack([logit_clip(base), reranker, present])
    return np.asarray(calibrator.predict_proba(stacked)[:, 1], dtype=np.float64)


def _aggregate_entities(
    scored: pd.DataFrame, canonical: pd.DataFrame, review_threshold: float
) -> pd.DataFrame:
    if scored.empty:
        return pd.DataFrame(columns=["nayose_id", *ENTITY_FEATURES])
    enriched = scored.merge(
        canonical[["nayose_id", "record_count"]].rename(
            columns={"record_count": "candidate_entity_size"}
        ),
        on="nayose_id",
        how="left",
    )
    rows: list[dict[str, Any]] = []
    for entity, group in enriched.groupby("nayose_id", sort=False):
        group = group.sort_values("pair_probability", ascending=False)
        scores = group.pair_probability.to_numpy()
        top3 = scores[:3]
        rows.append(
            {
                "nayose_id": entity,
                "max_pair_probability": float(scores[0]),
                "second_pair_probability": float(scores[1]) if len(scores) > 1 else 0.0,
                "mean_top3_probability": float(top3.mean()),
                "record_score_margin": float(scores[0] - scores[1])
                if len(scores) > 1
                else float(scores[0]),
                "support_record_count": int(len(group)),
                "support_agency_count": int(group.agency_id.nunique()),
                "candidate_entity_size": int(group.candidate_entity_size.iloc[0]),
                "max_name_similarity": float(group.f_name_similarity.max()),
                "max_kana_similarity": float(group.f_kana_similarity.max()),
                "max_romaji_similarity": float(group.f_romaji_similarity.max()),
                "max_address_similarity": float(group.f_address_similarity.max()),
                "any_name_exact": float(
                    (group.f_name_strict_exact.eq(1) | group.f_name_loose_exact.eq(1)).max()
                ),
                "any_dob_exact": float(group.f_dob_exact.max()),
                "any_phone_exact": float(group.f_phone_exact.max()),
                "any_email_exact": float(group.f_email_exact.max()),
                "any_postal_exact": float(group.f_postal_exact.max()),
                "any_identity_anchor": float(
                    (
                        (group.f_dob_exact.eq(1) | group.f_email_exact.eq(1))
                        & (group.f_name_strict_exact.eq(1) | group.f_name_loose_exact.eq(1))
                    ).max()
                ),
                "max_evidence_family_count": float(group.f_evidence_family_count.max()),
                "max_retrieval_score": float(group.f_retrieval_score.max()),
                "max_channel_count": float(group.f_channel_count.max()),
                "hard_conflict_count": int(
                    (group.f_type_conflict.eq(1) | group.f_dob_conflict.eq(1)).sum()
                ),
                "review_or_auto_record_count": int(
                    group.pair_probability.ge(review_threshold).sum()
                ),
                "best_record_id": str(group.record_id.iloc[0]),
                "best_record_probability": float(scores[0]),
                "best_exact_blocks": str(group.exact_blocks.iloc[0]),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("max_pair_probability", ascending=False)
        .reset_index(drop=True)
    )


def _bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}
