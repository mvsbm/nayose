from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from api.domain.features import (
    ENTITY_FEATURE_COLS,
    PAIR_FEATURE_COLS,
    compute_pair_features_frame,
    logit_clip,
)
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
from nayose.config import TrainingConfig

ENTITY_FEATURES = ENTITY_FEATURE_COLS


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
        retrieval_long = _retrieve_many(self.index, queries)
        query_ids = queries.query_id.astype(str).tolist()
        entities = {
            query_id: pd.DataFrame(columns=["nayose_id", *ENTITY_FEATURES])
            for query_id in query_ids
        }
        if retrieval_long.empty:
            return entities
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
        aggregated = _aggregate_entities_many(
            scored, self.clusters.canonical_entities, self.pair.review_threshold
        )
        for query_id, group in aggregated.groupby("query_id", sort=False):
            entities[str(query_id)] = group.drop(columns="query_id").reset_index(drop=True)
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


def _retrieve_many(index: TrainingCandidateIndex, queries: pd.DataFrame) -> pd.DataFrame:
    values = queries.reset_index(drop=True)
    row_count = len(values)
    empty_ids: np.ndarray = np.empty((row_count, 0), dtype=np.int32)
    empty_scores: np.ndarray = np.empty((row_count, 0), dtype=np.float32)
    sparse_ids, sparse_scores = empty_ids, empty_scores
    dense_ids, dense_scores = empty_ids, empty_scores
    if index.config.exact_mode != "exact_only":
        texts = values.retrieval_text.astype(str).tolist()
        if index.reference_embeddings is not None:
            with ThreadPoolExecutor(max_workers=1) as executor:
                sparse_future = executor.submit(
                    index.sparse_candidates, texts, index.config.sparse_top_k
                )
                dense_ids, dense_scores = index.dense_candidates(texts, index.config.dense_top_k)
                sparse_ids, sparse_scores = sparse_future.result()
        else:
            sparse_ids, sparse_scores = index.sparse_candidates(texts, index.config.sparse_top_k)

    parts = [
        _retrieval_channel_frame(sparse_ids, sparse_scores, "sparse_score", "sparse_rank"),
        _retrieval_channel_frame(dense_ids, dense_scores, "dense_score", "dense_rank"),
    ]
    if index.config.exact_mode != "fuzzy_only":
        exact_rows: list[dict[str, object]] = []
        for position, (_, query) in enumerate(values.iterrows()):
            for candidate, blocks in index.exact_candidates(query).items():
                ordered_blocks = sorted(blocks)
                exact_rows.append(
                    {
                        "query_position": position,
                        "candidate_index": candidate,
                        "exact_blocks": ",".join(ordered_blocks),
                        "exact_count": len(ordered_blocks),
                    }
                )
        if exact_rows:
            parts.append(pd.DataFrame(exact_rows).set_index(["query_position", "candidate_index"]))
    parts = [part for part in parts if not part.empty]
    if not parts:
        return pd.DataFrame()
    combined = parts[0]
    for part in parts[1:]:
        combined = combined.join(part, how="outer")
    for column in ("sparse_score", "dense_score", "sparse_rank", "dense_rank", "exact_count"):
        if column not in combined:
            combined[column] = 0
        else:
            combined[column] = combined[column].fillna(0)
    if "exact_blocks" not in combined:
        combined["exact_blocks"] = ""
    else:
        combined["exact_blocks"] = combined["exact_blocks"].fillna("")
    combined["retrieval_score"] = combined[["sparse_score", "dense_score"]].max(axis=1) + (
        0.08 * combined.exact_count.clip(upper=3)
    )
    combined["channel_count"] = (
        combined.exact_count.gt(0).astype(np.int8)
        + combined.sparse_score.gt(0).astype(np.int8)
        + combined.dense_score.gt(0).astype(np.int8)
    )
    result = combined.reset_index()
    result["query_id"] = values.query_id.astype(str).to_numpy()[
        result.query_position.to_numpy(dtype=np.int64)
    ]
    result = result.sort_values(
        ["query_position", "retrieval_score", "channel_count"],
        ascending=[True, False, False],
        kind="stable",
    )
    return (
        result.groupby("query_position", sort=False, group_keys=False)
        .head(index.config.candidate_cap_per_record)
        .reset_index(drop=True)
    )


def _retrieval_channel_frame(
    ids: np.ndarray, scores: np.ndarray, score_column: str, rank_column: str
) -> pd.DataFrame:
    if ids.shape != scores.shape or ids.ndim != 2 or ids.shape[1] == 0:
        return pd.DataFrame()
    width = ids.shape[1]
    positions: np.ndarray = np.repeat(np.arange(ids.shape[0], dtype=np.int64), width)
    values: np.ndarray = ids.reshape(-1).astype(np.int64, copy=False)
    valid = values >= 0
    if not valid.any():
        return pd.DataFrame()
    ranks = np.tile(np.arange(1, width + 1, dtype=np.int16), ids.shape[0])
    frame = pd.DataFrame(
        {
            "query_position": positions[valid],
            "candidate_index": values[valid],
            score_column: scores.reshape(-1)[valid],
            rank_column: ranks[valid],
        }
    )
    return frame.groupby(["query_position", "candidate_index"], sort=False).agg(
        {score_column: "max", rank_column: "min"}
    )


def _calibrate(
    base: np.ndarray, reranker: np.ndarray, present: np.ndarray, calibrator: Any | None
) -> np.ndarray:
    if calibrator is None:
        return base
    stacked = np.column_stack([logit_clip(base), reranker, present])
    return np.asarray(calibrator.predict_proba(stacked)[:, 1], dtype=np.float64)


def _aggregate_entities_many(
    scored: pd.DataFrame, canonical: pd.DataFrame, review_threshold: float
) -> pd.DataFrame:
    if scored.empty:
        return pd.DataFrame(columns=["query_id", "nayose_id", *ENTITY_FEATURES])
    enriched = scored.merge(
        canonical[["nayose_id", "record_count"]].rename(
            columns={"record_count": "candidate_entity_size"}
        ),
        on="nayose_id",
        how="left",
    )
    keys = ["query_id", "nayose_id"]
    ordered = enriched.sort_values(
        [*keys, "pair_probability"], ascending=[True, True, False], kind="stable"
    ).reset_index(drop=True)
    ordered["_entity_rank"] = ordered.groupby(keys, sort=False, observed=True).cumcount()
    grouped = ordered.groupby(keys, sort=False, observed=True)
    result = grouped["pair_probability"].agg(
        max_pair_probability="max", support_record_count="size"
    )
    second = (
        ordered.loc[ordered["_entity_rank"].eq(1)]
        .set_index(keys)["pair_probability"]
        .reindex(result.index)
        .fillna(0.0)
    )
    top3 = (
        ordered.loc[ordered["_entity_rank"].lt(3)]
        .groupby(keys, sort=False, observed=True)["pair_probability"]
        .mean()
        .reindex(result.index)
    )
    result["second_pair_probability"] = second
    result["mean_top3_probability"] = top3
    result["record_score_margin"] = result.max_pair_probability - second
    result["support_agency_count"] = grouped["agency_id"].nunique()
    result["candidate_entity_size"] = grouped["candidate_entity_size"].first()
    for output, column in (
        ("max_name_similarity", "f_name_similarity"),
        ("max_kana_similarity", "f_kana_similarity"),
        ("max_romaji_similarity", "f_romaji_similarity"),
        ("max_address_similarity", "f_address_similarity"),
        ("max_evidence_family_count", "f_evidence_family_count"),
        ("max_retrieval_score", "f_retrieval_score"),
        ("max_channel_count", "f_channel_count"),
    ):
        result[output] = grouped[column].max()
    for output, column in (
        ("any_name_exact", "f_name_strict_exact"),
        ("any_dob_exact", "f_dob_exact"),
        ("any_phone_exact", "f_phone_exact"),
        ("any_email_exact", "f_email_exact"),
        ("any_postal_exact", "f_postal_exact"),
    ):
        result[output] = grouped[column].max()
    result["any_name_exact"] = np.maximum(
        result["any_name_exact"], grouped["f_name_loose_exact"].max()
    )
    result["any_identity_anchor"] = (
        (
            (ordered["f_dob_exact"].eq(1) | ordered["f_email_exact"].eq(1))
            & (ordered["f_name_strict_exact"].eq(1) | ordered["f_name_loose_exact"].eq(1))
        )
        .groupby([ordered[key] for key in keys], sort=False, observed=True)
        .max()
        .reindex(result.index)
    )
    result["hard_conflict_count"] = (
        (ordered["f_type_conflict"].eq(1) | ordered["f_dob_conflict"].eq(1))
        .groupby([ordered[key] for key in keys], sort=False, observed=True)
        .sum()
        .reindex(result.index)
    )
    result["review_or_auto_record_count"] = (
        ordered["pair_probability"]
        .ge(review_threshold)
        .groupby([ordered[key] for key in keys], sort=False, observed=True)
        .sum()
        .reindex(result.index)
    )
    first = ordered.loc[ordered["_entity_rank"].eq(0)].set_index(keys).reindex(result.index)
    result["best_record_id"] = first["record_id"]
    result["best_record_probability"] = result.max_pair_probability
    result["best_exact_blocks"] = first["exact_blocks"]
    result = result.reset_index()
    result["any_identity_anchor"] = result["any_identity_anchor"].astype(float)
    result["hard_conflict_count"] = result["hard_conflict_count"].astype(int)
    result["review_or_auto_record_count"] = result["review_or_auto_record_count"].astype(int)
    return result.sort_values(
        ["query_id", "max_pair_probability"], ascending=[True, False], kind="stable"
    ).reset_index(drop=True)


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
