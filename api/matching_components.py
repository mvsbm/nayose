"""Independent matching stages used by the serving orchestrator."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from api.evidence import compare_fields, visible_record
from api.features import PAIR_FEATURE_COLS, has_safe_identity_anchor, logit_clip


def empty_result(action: str, reason: str) -> dict[str, Any]:
    return {
        "action": action,
        "reason": reason,
        "top_nayose_id": None,
        "top_probability": 0.0,
        "margin": 0.0,
        "candidates": [],
        "thresholds": None,
        "diagnostics": {},
        "scoring_mode": "not_scored",
    }


class PairScorer:
    """Apply the bundle's pair model and calibrator."""

    def __init__(self, engine: Any):
        self.engine = engine

    def score(self, features: pd.DataFrame) -> np.ndarray:
        engine = self.engine
        base = np.asarray(engine._pair_model.predict_proba(features[PAIR_FEATURE_COLS])[:, 1])
        inputs = getattr(engine._pair_calibrator, "n_features_in_", 1)
        if inputs == 3:
            calibration = np.column_stack(
                [logit_clip(base), np.zeros(len(base)), np.zeros(len(base))]
            )
        elif inputs == 1:
            calibration = base.reshape(-1, 1)
        else:
            raise ValueError("unsupported calibrator input schema")
        scores = np.asarray(engine._pair_calibrator.predict_proba(calibration)[:, 1])
        if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
            raise ValueError("invalid calibrated scores")
        return scores


class CandidateAggregator:
    """Turn scored records into ranked entity evidence."""

    def __init__(self, engine: Any):
        self.engine = engine

    def build(
        self,
        record: dict[str, str],
        raw: pd.DataFrame,
        features: pd.DataFrame,
        scores: np.ndarray,
        retrieval: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        engine = self.engine
        groups: dict[str, list[dict[str, Any]]] = {}
        for i, row in enumerate(raw.to_dict("records")):
            entity_id = engine._record_to_nayose.get(str(row["record_id"]))
            if entity_id:
                retrieval_row = retrieval.iloc[i].to_dict()
                dense_view_scores = retrieval_row.get("dense_view_scores", {})
                dense_view_ranks = retrieval_row.get("dense_view_ranks", {})
                retrieval_methods = retrieval_row.get("retrieval_methods", [])
                groups.setdefault(entity_id, []).append(
                    {
                        "record": visible_record(row),
                        "score": float(scores[i]),
                        "comparisons": compare_fields(record, row),
                        "features": {k: float(v) for k, v in features.iloc[i].items()},
                        "retrieval": {
                            "exact_blocks": [
                                str(block) for block in retrieval_row.get("exact_blocks", [])
                            ],
                            "exact_count": int(retrieval_row.get("exact_count", 0)),
                            "sparse_score": float(retrieval_row.get("sparse_score", 0.0)),
                            "dense_score": float(retrieval_row.get("dense_score", 0.0)),
                            "dense_view_scores": {
                                str(key): float(value) for key, value in dense_view_scores.items()
                            }
                            if isinstance(dense_view_scores, dict)
                            else {},
                            "dense_view_ranks": {
                                str(key): int(value) for key, value in dense_view_ranks.items()
                            }
                            if isinstance(dense_view_ranks, dict)
                            else {},
                            "retrieval_score": float(retrieval_row.get("retrieval_score", 0.0)),
                            "retrieval_fusion_score": float(
                                retrieval_row.get("retrieval_fusion_score", 0.0)
                            ),
                            "retrieval_methods": [str(method) for method in retrieval_methods]
                            if isinstance(retrieval_methods, list)
                            else [],
                        },
                    }
                )
        candidates: list[dict[str, Any]] = []
        for entity_id, evidence in groups.items():
            evidence.sort(key=lambda e: (-e["score"], e["record"]["record_id"]))
            canonical = engine._canonical[engine._canonical.nayose_id == entity_id]
            if canonical.empty:
                raise ValueError("entity mapping has no canonical record")
            top_evidence = evidence[:3]
            max_evidence_families = max(
                (int(item["features"].get("evidence_family_count", 0)) for item in top_evidence),
                default=0,
            )
            hard_conflict_count = sum(
                int(
                    item["features"].get("type_conflict", 0)
                    + item["features"].get("dob_conflict", 0)
                )
                for item in top_evidence
            )
            identity_anchor_count = max(
                (
                    int(
                        item["features"].get("dob_exact", 0)
                        + item["features"].get("email_exact", 0)
                    )
                    for item in top_evidence
                ),
                default=0,
            )
            identity_anchor_safe = max(
                (int(has_safe_identity_anchor(item["features"])) for item in top_evidence),
                default=0,
            )
            candidates.append(
                {
                    "nayose_id": entity_id,
                    "name": str(canonical.iloc[0].get("name_kanji", "")),
                    "probability": float(np.mean([e["score"] for e in top_evidence])),
                    "support": len(evidence),
                    "max_evidence_family_count": max_evidence_families,
                    "hard_conflict_count": hard_conflict_count,
                    "identity_anchor_count": identity_anchor_count,
                    "identity_anchor_safe": identity_anchor_safe,
                    "evidence": top_evidence,
                }
            )
        candidates.sort(key=lambda c: (-c["probability"], c["nayose_id"]))
        return candidates


class PipelineTraceBuilder:
    """Build the evidence graph returned with a recommendation."""

    @staticmethod
    def normalized_fields(query: dict[str, str]) -> dict[str, str]:
        return {
            field: query.get(normalized, "")
            for field, normalized in {
                "name_kanji": "n_name_loose",
                "name_kana": "n_kana",
                "phone": "n_phone",
                "email": "n_email",
                "postal_code": "n_postal",
                "date_birth": "n_dob",
                "address": "n_address",
            }.items()
        }

    def _nodes(
        self,
        record: dict[str, str],
        fields: dict[str, str],
        retrieval: pd.DataFrame,
        result: dict[str, Any],
        candidates: list[dict[str, Any]],
        top: dict[str, Any] | None,
        first: dict[str, Any] | None,
        retrieval_info: dict[str, Any],
        comparisons: dict[str, Any],
        statuses: list[str],
    ) -> list[dict[str, Any]]:
        evidence = top.get("evidence", []) if top else []
        branches = {
            "exact": bool(retrieval_info.get("exact_blocks")),
            "sparse": float(retrieval_info.get("sparse_score", 0)) > 0,
            "dense": float(retrieval_info.get("dense_score", 0)) > 0,
        }
        return [
            {
                "id": "input",
                "kind": "input",
                "status": "complete",
                "metrics": {"populated_fields": sum(bool(value) for value in record.values())},
                "details": {"fields": visible_record(record)},
            },
            {
                "id": "normalize",
                "kind": "normalize",
                "status": "complete",
                "metrics": {"fields": len([value for value in fields.values() if value])},
                "details": {"fields": fields},
            },
            {
                "id": "retrieve",
                "kind": "retrieve",
                "status": "complete" if len(retrieval) else "warning",
                "metrics": {
                    "records": len(retrieval),
                    "entities": len(candidates),
                    "backend": str(
                        result.get("diagnostics", {}).get("retrieval_backend", "unknown")
                    ),
                    "mode": str(result.get("diagnostics", {}).get("exact_mode", "unknown")),
                },
                "details": {"candidate": top.get("nayose_id") if top else None},
            },
            *[
                {
                    "id": kind,
                    "kind": kind,
                    "status": "complete" if active else "skipped",
                    "metrics": {"active": active},
                    "details": {
                        "candidate": top.get("nayose_id") if top else None,
                        "exact_blocks": retrieval_info.get("exact_blocks", []),
                        "score": retrieval_info.get(f"{kind}_score", 0.0),
                    },
                }
                for kind, active in branches.items()
            ],
            {
                "id": "candidate",
                "kind": "candidate",
                "status": "complete" if top else "warning",
                "metrics": {"rank": top.get("rank", 0) if top else 0},
                "details": {
                    "id": top.get("nayose_id") if top else None,
                    "name": top.get("name") if top else None,
                },
            },
            {
                "id": "compare",
                "kind": "compare",
                "status": "complete" if first else "skipped",
                "metrics": {
                    "exact": statuses.count("exact"),
                    "normalized": statuses.count("normalized"),
                    "different": statuses.count("different"),
                    "missing": statuses.count("missing"),
                },
                "details": {"fields": comparisons},
            },
            {
                "id": "score",
                "kind": "score",
                "status": "complete" if first else "skipped",
                "metrics": {"records": len(evidence)},
                "details": {"scores": [float(item.get("score", 0)) for item in evidence]},
            },
            {
                "id": "aggregate",
                "kind": "aggregate",
                "status": "complete" if top else "skipped",
                "metrics": {"probability": float(top.get("probability", 0)) if top else 0.0},
                "details": {
                    "method": "mean_top_3",
                    "scores": [float(item.get("score", 0)) for item in evidence],
                },
            },
            {
                "id": "decision",
                "kind": "decision",
                "status": "complete" if result.get("thresholds") else "warning",
                "metrics": {"action": result.get("action", "NO_MATCH")},
                "details": {
                    "reason": result.get("reason", ""),
                    "thresholds": result.get("thresholds"),
                    "margin": result.get("margin", 0.0),
                },
            },
        ]

    def build(
        self,
        record: dict[str, str],
        query: dict[str, str],
        retrieval: pd.DataFrame,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        candidates = result.get("candidates", [])
        top = candidates[0] if candidates else None
        evidence = top.get("evidence", []) if top else []
        first = evidence[0] if evidence else None
        retrieval_info = first.get("retrieval", {}) if first else {}
        comparisons = first.get("comparisons", {}) if first else {}
        statuses = [str(value.get("status", "")) for value in comparisons.values()]
        fields = self.normalized_fields(query)
        nodes = self._nodes(
            record,
            fields,
            retrieval,
            result,
            candidates,
            top,
            first,
            retrieval_info,
            comparisons,
            statuses,
        )
        return {
            "selected_candidate": top.get("nayose_id") if top else None,
            "nodes": nodes,
            "edges": [
                ["input", "normalize"],
                ["normalize", "retrieve"],
                ["retrieve", "exact"],
                ["retrieve", "sparse"],
                ["retrieve", "dense"],
                ["exact", "candidate"],
                ["sparse", "candidate"],
                ["dense", "candidate"],
                ["candidate", "compare"],
                ["compare", "score"],
                ["score", "aggregate"],
                ["aggregate", "decision"],
            ],
        }


class DecisionPolicy:
    """Apply manifest or session thresholds without changing scores."""

    def __init__(self, engine: Any, retriever: Any):
        self.engine = engine
        self.retriever = retriever

    def decide(
        self,
        candidates: list[dict[str, Any]],
        retrieved: int,
        overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        engine = self.engine
        if not candidates:
            return empty_result("NO_MATCH", "no_entities")
        top = candidates[0]
        auto = float(engine.config.entity_auto_threshold)
        review = float(engine.config.entity_review_threshold)
        source = "manifest"
        # session-only threshold preview (A/B): scores are unchanged, only the
        # decision layer moves; invalid overrides fall back to the manifest
        if overrides:
            cand_auto, cand_review = overrides.get("auto"), overrides.get("review")
            if (
                cand_auto is not None
                and cand_review is not None
                and 0.0 <= cand_review <= cand_auto <= 1.0
            ):
                auto, review = cand_auto, cand_review
                source = "session"
        margin = top["probability"] - (candidates[1]["probability"] if len(candidates) > 1 else 0.0)
        margin_threshold = float(getattr(engine.config, "entity_margin_threshold", 0.0))
        minimum_families = int(getattr(engine.config, "minimum_evidence_families", 1))
        safe_for_auto = (
            top["probability"] >= auto
            and margin >= margin_threshold
            and int(top.get("max_evidence_family_count", 0)) >= minimum_families
            and int(top.get("hard_conflict_count", 0)) == 0
            and int(top.get("identity_anchor_safe", 0)) >= 1
        )
        action, reason = (
            ("AUTO_LINK", "above_auto_threshold")
            if safe_for_auto
            else ("REVIEW", "evidence_safety_check")
            if top["probability"] >= review
            else ("NO_MATCH", "below_review_threshold")
        )
        nearest = (
            [dict(c, rank=i) for i, c in enumerate(candidates[:3], 1)]
            if action == "NO_MATCH"
            else None
        )
        return {
            "action": action,
            "reason": reason,
            "top_nayose_id": top["nayose_id"],
            "top_probability": top["probability"],
            "margin": top["probability"]
            - (candidates[1]["probability"] if len(candidates) > 1 else 0),
            "thresholds": {"auto": auto, "review": review},
            "threshold_source": source,
            "nearest": nearest,
            "scoring_mode": "calibrated_pair_mean_top3",
            "diagnostics": {
                "retrieved": retrieved,
                "entities": len(candidates),
                "reranker": False,
                "retrieval_backend": getattr(self.retriever, "active_backend", "unknown"),
                "exact_mode": getattr(self.retriever, "exact_mode", "unknown"),
                "auto_eligibility": {
                    "identity_anchor": bool(top.get("identity_anchor_safe", 0)),
                    "evidence_families": int(top.get("max_evidence_family_count", 0)),
                    "hard_conflicts": int(top.get("hard_conflict_count", 0)),
                },
            },
            "candidates": [
                dict(candidate, rank=i) for i, candidate in enumerate(candidates[:8], 1)
            ],
        }
