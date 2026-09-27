"""Evaluation and review queries over an immutable model bundle."""

from __future__ import annotations

import json
from typing import Any

from api.infrastructure.read_model.bundle_reader import BundleReader


def _rate(rows: list[dict[str, Any]], action: str) -> float:
    total = sum(int(row["count"]) for row in rows)
    count = next((int(row["count"]) for row in rows if row["id"] == action), 0)
    return count / total if total else 0.0


def _query_outcomes(con: Any, run_dir: Any, query_count: int) -> list[dict[str, Any]]:
    path = run_dir / "query_results.parquet"
    if not path.exists():
        return []
    rows = con.execute(
        f"SELECT action, count(*) AS count FROM read_parquet('{path}'::VARCHAR) "
        "GROUP BY action ORDER BY action"
    ).fetchall()
    total = query_count or sum(int(row[1]) for row in rows)
    return [
        {"id": str(action), "count": int(count), "rate": int(count) / total if total else 0.0}
        for action, count in rows
    ]


def _metric_items(
    summary: dict[str, float], query: dict[str, float], candidates: tuple[tuple[str, ...], ...]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        value = None
        for key in candidate[1:]:
            if key in query:
                value = query[key]
                break
            if key in summary:
                value = summary[key]
                break
        if value is not None:
            unit = "count" if candidate[0] in {"predicted_entities", "candidate_edges"} else "ratio"
            items.append({"id": candidate[0], "value": value, "unit": unit})
    return items


def _assessment_reasons(
    summary: dict[str, float], review_rate: float, no_match_rate: float
) -> list[str]:
    reasons: list[str] = []
    if summary.get("undermerge_record_rate", 0) >= 0.25:
        reasons.append("fragmentation")
    if review_rate >= 0.40:
        reasons.append("review_load")
    if no_match_rate >= 0.15:
        reasons.append("unresolved_queries")
    return reasons


class EvaluationQueries:
    def __init__(self, context: BundleReader):
        self.context = context

    def review(
        self, run_id: str, min_p: float, offset: int, limit: int, nayose: str = ""
    ) -> dict[str, Any]:
        con, snapshot = self.context.open(run_id)
        run_dir = snapshot.run_dir
        try:
            scope = ""
            if nayose:
                mapping = str(run_dir / "record_to_nayose.parquet")
                esc_n = nayose.replace("'", "''")
                scope = (
                    " AND (left_record_id IN (SELECT record_id "
                    f"FROM read_parquet('{mapping}') WHERE nayose_id='{esc_n}') "
                    "OR right_record_id IN (SELECT record_id "
                    f"FROM read_parquet('{mapping}') WHERE nayose_id='{esc_n}'))"
                )
            queue = f"read_parquet('{run_dir}/review_queue.parquet'::VARCHAR)"
            total = int(
                con.execute(
                    f"SELECT count(*) AS c FROM {queue} "
                    f"WHERE pair_probability >= {float(min_p)}{scope}"
                ).fetchone()[0]
            )
            rows = (
                con.execute(
                    f"SELECT left_record_id, right_record_id, pair_probability, exact_count "
                    f"FROM {queue} WHERE pair_probability >= {float(min_p)}{scope} "
                    f"ORDER BY pair_probability DESC LIMIT {int(limit)} OFFSET {int(offset)}"
                )
                .fetchdf()
                .to_dict("records")
            )
            # score-band histogram over the WHOLE queue: shows how much work is
            # hidden below the current filter and where the risk concentrates
            buckets = [
                [float(lo), int(count)]
                for lo, count in con.execute(
                    f"SELECT floor(pair_probability * 20) / 20 AS lo, count(*) AS c "
                    f"FROM {queue} GROUP BY 1 ORDER BY 1"
                ).fetchall()
            ]
            pair_auto = None
            manifest = run_dir / "run_manifest.json"
            if manifest.exists():
                thresholds = json.loads(manifest.read_text(encoding="utf-8")).get("thresholds", {})
                pair_auto = thresholds.get("pair_auto")
            return {
                "total": total,
                "rows": rows,
                "histogram": buckets,
                "pair_auto_threshold": round(float(pair_auto), 4) if pair_auto else None,
            }
        finally:
            con.close()

    def metrics(self, run_id: str) -> dict[str, Any]:
        con, snapshot = self.context.open(run_id)
        run_dir = snapshot.run_dir

        def fetch(stem: str, cols: str = "metric,value"):
            return con.execute(
                f"SELECT {cols} FROM read_parquet('{run_dir}/{stem}.parquet'::VARCHAR)"
            ).fetchdf()

        try:
            summary = {
                r["metric"]: float(r["value"]) for _, r in fetch("full_dataset_summary").iterrows()
            }
            all_metrics = {
                (str(r["section"]), str(r["metric"])): float(r["value"])
                for _, r in fetch("full_dataset_metrics", "section,metric,value").iterrows()
            }
            query_metrics = {
                metric: value
                for (section, metric), value in all_metrics.items()
                if section == "incoming_query"
            }
            p, r = summary.get("b3_precision", 0), summary.get("b3_recall", 0)
            b3 = summary.get("b3_f1", 2 * p * r / (p + r) if p + r > 0 else 0)
            query_count = int(query_metrics.get("queries", 0))
            outcomes = _query_outcomes(con, run_dir, query_count)
            review_rate = query_metrics.get("review_rate", _rate(outcomes, "REVIEW"))
            no_match_rate = query_metrics.get("no_match_rate", _rate(outcomes, "NO_MATCH"))
            reasons = _assessment_reasons(summary, review_rate, no_match_rate)
            return {
                # Kept for clients that consume the original compact contract.
                "kpis": [
                    ["candidate recall", summary.get("candidate_pair_recall", 0)],
                    ["B3 F1", b3],
                    [
                        "top-1 identity",
                        query_metrics.get(
                            "top1_identity_accuracy_existing",
                            query_metrics.get("candidate_recall_at_1_existing", 0),
                        ),
                    ],
                    ["MRR", query_metrics.get("mean_reciprocal_rank_existing", 0)],
                    ["review", review_rate],
                    ["no-match", no_match_rate],
                ],
                "extras": {
                    "entities": summary.get("predicted_entities", 0),
                    "edges": summary.get("candidate_edges", 0),
                    "singleton": summary.get("singleton_entity_rate", 0),
                },
                "run_id": run_id,
                "assessment": {"status": "attention" if reasons else "ready", "reasons": reasons},
                "outcomes": outcomes,
                "metric_groups": {
                    "quality": _metric_items(
                        summary,
                        query_metrics,
                        (
                            ("candidate_recall", "candidate_pair_recall"),
                            ("cluster_f1", "b3_f1"),
                            (
                                "top1_identity",
                                "top1_identity_accuracy_existing",
                                "candidate_recall_at_1_existing",
                            ),
                            ("mrr", "mean_reciprocal_rank_existing"),
                        ),
                    ),
                    "workload": _metric_items(
                        summary,
                        query_metrics,
                        (
                            ("review_rate", "review_rate"),
                            ("no_match_rate", "no_match_rate"),
                            ("undermerge_rate", "undermerge_record_rate"),
                        ),
                    ),
                    "structure": _metric_items(
                        summary,
                        query_metrics,
                        (
                            ("predicted_entities", "predicted_entities"),
                            ("candidate_edges", "candidate_edges"),
                            ("singleton_rate", "singleton_entity_rate"),
                        ),
                    ),
                },
            }
        finally:
            con.close()

    def confusion(self, run_id: str) -> dict[str, Any]:
        con, snapshot = self.context.open(run_id)
        run_dir = snapshot.run_dir

        def fetch(stem: str, extra: str = ""):
            return con.execute(
                f"SELECT * FROM read_parquet('{run_dir}/{stem}.parquet'::VARCHAR) {extra}"
            ).fetchdf()

        try:
            pairs = {
                r["metric"]: float(r["value"]) for _, r in fetch("pair_confusion_test").iterrows()
            }
            qc = fetch("query_confusion")
            scenarios: list[dict[str, Any]] = []
            for scenario, g in qc.groupby("scenario"):
                scenarios.append(
                    {
                        "scenario": str(scenario),
                        "actions": {str(r.action): int(r.expected_yes) for _, r in g.iterrows()},
                    }
                )
            matrix = [
                {"id": metric, "count": int(pairs.get(metric, 0))}
                for metric in (
                    "true_positives",
                    "false_negatives",
                    "false_positives",
                    "true_negatives",
                )
                if metric in pairs
            ]
            decisions: dict[str, list[dict[str, Any]]] = {}
            all_qr = fetch("query_results", "WHERE scenario IS NOT NULL")
            qr = all_qr.head(20000)
            for scenario, g in qr.groupby("scenario"):
                decisions[str(scenario)] = g[
                    [
                        "query_id",
                        "action",
                        "top_nayose_id",
                        "top_probability",
                        "identity_top1_correct",
                    ]
                ].to_dict("records")
                full_group = all_qr[all_qr["scenario"] == scenario]
                actual = full_group["action"].value_counts().to_dict()
                for row in scenarios:
                    if row["scenario"] == str(scenario):
                        row["actual"] = {
                            str(action): int(count) for action, count in actual.items()
                        }
                        row["total"] = int(len(full_group))
            error_cohorts = (
                [
                    {"id": str(status), "count": int(count)}
                    for status, count in all_qr["top_truth_status"].value_counts().items()
                ]
                if "top_truth_status" in all_qr
                else []
            )
            query_outcomes = (
                [
                    {"id": str(result), "count": int(count)}
                    for result, count in all_qr["operational_result"].value_counts().items()
                ]
                if "operational_result" in all_qr
                else []
            )
            return {
                "pairs": pairs,
                "matrix": matrix,
                "scenarios": scenarios,
                "decisions": decisions,
                "query_outcomes": query_outcomes,
                "error_cohorts": error_cohorts,
            }
        finally:
            con.close()

    def compare(self, run_id: str, left: str, right: str) -> list[dict[str, Any]]:
        con, snapshot = self.context.open(run_id)
        try:
            df = con.execute(
                "SELECT * FROM read_parquet(?) WHERE record_id IN (?, ?)",
                [str(snapshot.data_dir / "reference_records.parquet"), left, right],
            ).fetchdf()
            return df.to_dict("records")
        finally:
            con.close()
