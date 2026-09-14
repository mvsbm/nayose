"""Evaluation and review queries over an immutable model bundle."""

from __future__ import annotations

import json
from typing import Any

from api.infrastructure.read_model.bundle_reader import BundleReader


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
            metrics = {
                r["metric"]: float(r["value"])
                for _, r in fetch("full_dataset_metrics", "section,metric,value").iterrows()
                if r["section"] == "incoming_query"
            }
            p, r = summary.get("b3_precision", 0), summary.get("b3_recall", 0)
            b3 = 2 * p * r / (p + r) if p + r > 0 else 0
            return {
                "kpis": [
                    ["candidate recall", summary.get("candidate_pair_recall", 0)],
                    ["B3 F1", b3],
                    ["top-1 identity", metrics.get("top1_identity_accuracy_existing", 0)],
                    ["MRR", metrics.get("mean_reciprocal_rank_existing", 0)],
                    ["review", metrics.get("review_rate", 0)],
                    ["no-match", metrics.get("no_match_rate", 0)],
                ],
                "extras": {
                    "entities": summary.get("predicted_entities", 0),
                    "edges": summary.get("candidate_edges", 0),
                    "singleton": summary.get("singleton_entity_rate", 0),
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
            decisions: dict[str, list[dict[str, Any]]] = {}
            qr = fetch("query_results", "WHERE scenario IS NOT NULL LIMIT 20000")
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
            return {"pairs": pairs, "scenarios": scenarios, "decisions": decisions}
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
