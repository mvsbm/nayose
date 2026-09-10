"""Release gates for comparing a candidate model with its retrieval baseline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GateDecision:
    passed: bool
    status: str
    message: str


def _query_slice_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    values = list(rows)
    core_scenarios = {"existing_stable", "existing_changed", "existing_damaged"}
    hard_scenarios = {"existing_changed", "existing_damaged"}

    def metric(rows_for_slice: list[Mapping[str, Any]], key: str) -> float:
        return sum(float(bool(row.get(key))) for row in rows_for_slice) / max(
            len(rows_for_slice), 1
        )

    def recall5(rows_for_slice: list[Mapping[str, Any]]) -> float:
        return sum(
            1 <= int(row.get("correct_entity_rank", 0)) <= 5 for row in rows_for_slice
        ) / max(len(rows_for_slice), 1)

    def auto_recall(rows_for_slice: list[Mapping[str, Any]]) -> float:
        return sum(
            row.get("action") == "AUTO_LINK" and bool(row.get("pure_top1_correct"))
            for row in rows_for_slice
        ) / max(len(rows_for_slice), 1)

    core = [row for row in values if row.get("scenario") in core_scenarios]
    hard = [row for row in values if row.get("scenario") in hard_scenarios]
    return {
        "core_top1": metric(core, "identity_top1_correct"),
        "core_recall5": recall5(core),
        "core_auto_recall": auto_recall(core),
        "hard_recall5": recall5(hard),
    }


def evaluate_f2llm_candidate(
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    candidate_results: Iterable[Mapping[str, Any]] | None = None,
    baseline_results: Iterable[Mapping[str, Any]] | None = None,
    min_top1_gain: float = 0.01,
    min_recall5_gain: float = 0.01,
    min_hard_recall5_gain: float = 0.01,
) -> GateDecision:
    """Require a measurable retrieval gain before releasing the 330M profile."""
    candidate_family = str(candidate.get("config", {}).get("model_family", ""))
    if candidate_family != "f2llm":
        return GateDecision(True, "NOT_APPLICABLE", "candidate is not the F2LLM-330M profile")
    if baseline is None:
        return GateDecision(True, "SKIPPED", "no matching baseline bundle was supplied")
    if candidate_results is None or baseline_results is None:
        return GateDecision(False, "FAILED", "query result tables are required for the F2LLM gate")
    if candidate.get("data_fingerprint") != baseline.get("data_fingerprint"):
        return GateDecision(True, "SKIPPED", "baseline fingerprint does not match candidate data")
    baseline_family = str(baseline.get("config", {}).get("model_family", ""))
    if baseline_family != "off":
        return GateDecision(True, "SKIPPED", "baseline is not the deterministic SVD profile")

    candidate_metrics = candidate.get("query_metrics", {})
    baseline_metrics = baseline.get("query_metrics", {})
    metric_names = (
        "candidate_recall_at_1_existing",
        "candidate_recall_at_5_existing",
        "auto_link_precision_pure",
        "false_auto_link_rate_new_or_related",
    )
    if any(name not in candidate_metrics or name not in baseline_metrics for name in metric_names):
        return GateDecision(True, "SKIPPED", "baseline or candidate lacks comparable query metrics")

    top1_gain = float(candidate_metrics[metric_names[0]]) - float(baseline_metrics[metric_names[0]])
    recall5_gain = float(candidate_metrics[metric_names[1]]) - float(
        baseline_metrics[metric_names[1]]
    )
    precision_delta = float(candidate_metrics[metric_names[2]]) - float(
        baseline_metrics[metric_names[2]]
    )
    false_auto_delta = float(candidate_metrics[metric_names[3]]) - float(
        baseline_metrics[metric_names[3]]
    )
    candidate_slices = _query_slice_metrics(candidate_results)
    baseline_slices = _query_slice_metrics(baseline_results)
    core_top1_delta = candidate_slices["core_top1"] - baseline_slices["core_top1"]
    core_auto_delta = candidate_slices["core_auto_recall"] - baseline_slices["core_auto_recall"]
    hard_recall5_gain = candidate_slices["hard_recall5"] - baseline_slices["hard_recall5"]
    failures = []
    if top1_gain < min_top1_gain:
        failures.append(f"Top-1 gain {top1_gain:+.3%} < {min_top1_gain:.3%}")
    if recall5_gain < min_recall5_gain:
        failures.append(f"Recall@5 gain {recall5_gain:+.3%} < {min_recall5_gain:.3%}")
    if core_top1_delta < 0:
        failures.append(f"core identity Top-1 changed {core_top1_delta:+.3%}")
    if core_auto_delta < 0:
        failures.append(f"core auto-recall changed {core_auto_delta:+.3%}")
    if hard_recall5_gain < min_hard_recall5_gain:
        failures.append(
            f"hard-query Recall@5 gain {hard_recall5_gain:+.3%} < {min_hard_recall5_gain:.3%}"
        )
    if precision_delta < 0:
        failures.append(f"pure auto precision changed {precision_delta:+.3%}")
    if false_auto_delta > 0:
        failures.append(f"false-auto rate changed {false_auto_delta:+.3%}")
    if failures:
        return GateDecision(False, "FAILED", "; ".join(failures))
    return GateDecision(
        True,
        "PASSED",
        f"Top-1 {top1_gain:+.3%}, Recall@5 {recall5_gain:+.3%}, "
        f"core Top-1 {core_top1_delta:+.3%}, hard Recall@5 {hard_recall5_gain:+.3%}, "
        f"core auto-recall {core_auto_delta:+.3%}, pure precision {precision_delta:+.3%}, "
        f"false-auto {false_auto_delta:+.3%}",
    )
