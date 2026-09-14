from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from api.training.contracts import ClusterResult, TruthSnapshot


@dataclass(slots=True)
class ConstrainedUnionFind:
    parent: np.ndarray
    size: np.ndarray
    types: list[set[str]]
    dobs: list[set[str]]

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> ConstrainedUnionFind:
        return cls(
            np.arange(len(frame), dtype=np.int32),
            np.ones(len(frame), dtype=np.int32),
            [{str(value)} if value else set() for value in frame.n_party_type],
            [{str(value)} if value else set() for value in frame.n_dob],
        )

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


def build_clusters(
    reference_raw: pd.DataFrame,
    reference: pd.DataFrame,
    pairs: pd.DataFrame,
    truth: TruthSnapshot,
    review_threshold: float,
    auto_threshold: float,
) -> ClusterResult:
    union_find = ConstrainedUnionFind.from_frame(reference)
    union_rows: list[dict[str, object]] = []
    auto_edges = pairs.loc[pairs.edge_action.eq("AUTO_EDGE")].sort_values(
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
    roots: np.ndarray = np.fromiter(
        (union_find.find(position) for position in range(len(reference))),
        dtype=np.int32,
        count=len(reference),
    )
    root_ids = {root: f"N{rank:09d}" for rank, root in enumerate(sorted(np.unique(roots)))}
    mapping = pd.DataFrame(
        {"record_id": reference.record_id, "nayose_id": [root_ids[root] for root in roots]}
    )
    clustered = reference_raw.merge(mapping, on="record_id", validate="one_to_one")
    history = build_attribute_history(clustered)
    canonical = build_canonical_entities(clustered, history)
    diagnostics = build_cluster_diagnostics(
        clustered, pairs, mapping, canonical, auto_threshold, review_threshold
    )
    review_queue = _review_queue(pairs, mapping, review_threshold)
    truth_sets = _truth_sets(mapping, truth.record_truth)
    metrics = cluster_metrics(mapping, truth.record_truth, canonical, truth_sets)
    return ClusterResult(
        mapping, canonical, history, diagnostics, review_queue, union_log, truth_sets, metrics
    )


def build_attribute_history(clustered: pd.DataFrame) -> pd.DataFrame:
    fields = (
        "party_type",
        "name_kanji",
        "name_kana",
        "name_romaji",
        "date_birth",
        "postal_code",
        "address",
        "phone",
        "email",
    )
    parts: list[pd.DataFrame] = []
    for field in fields:
        values = clustered.loc[
            clustered[field].astype(str).ne(""),
            ["nayose_id", field, "observed_at", "agency_id", "record_id"],
        ]
        if values.empty:
            continue
        summary = (
            values.groupby(["nayose_id", field], sort=False)
            .agg(
                support_records=("record_id", "nunique"),
                support_agencies=("agency_id", "nunique"),
                first_seen=("observed_at", "min"),
                last_seen=("observed_at", "max"),
            )
            .reset_index()
            .rename(columns={field: "value"})
        )
        summary.insert(1, "field", field)
        parts.append(summary)
    if not parts:
        return pd.DataFrame(
            columns=[
                "nayose_id",
                "field",
                "value",
                "support_records",
                "support_agencies",
                "first_seen",
                "last_seen",
            ]
        )
    return (
        pd.concat(parts, ignore_index=True)
        .sort_values(
            ["nayose_id", "field", "support_records", "last_seen"],
            ascending=[True, True, False, False],
        )
        .reset_index(drop=True)
    )


def build_canonical_entities(clustered: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    fields = [
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
    result = (
        clustered.groupby("nayose_id", sort=True)
        .agg(
            record_count=("record_id", "size"),
            policy_count=("policy_id", "nunique"),
            agency_count=("agency_id", "nunique"),
            first_seen=("observed_at", "min"),
            last_seen=("observed_at", "max"),
        )
        .reset_index()
    )
    best = history.drop_duplicates(["nayose_id", "field"])
    for field in fields:
        selected = best.loc[best.field.eq(field), ["nayose_id", "value"]].rename(
            columns={"value": field}
        )
        result = result.merge(selected, on="nayose_id", how="left")
    result[fields] = result[fields].fillna("")
    return result


def build_cluster_diagnostics(
    clustered: pd.DataFrame,
    pairs: pd.DataFrame,
    mapping: pd.DataFrame,
    canonical: pd.DataFrame,
    auto_threshold: float,
    review_threshold: float,
) -> pd.DataFrame:
    internal = pairs.merge(
        mapping.rename(columns={"record_id": "left_record_id", "nayose_id": "left_nayose"}),
        on="left_record_id",
    )
    internal = internal.merge(
        mapping.rename(columns={"record_id": "right_record_id", "nayose_id": "right_nayose"}),
        on="right_record_id",
    )
    internal = internal.loc[internal.left_nayose.eq(internal.right_nayose)]
    edge_summary = (
        internal.groupby("left_nayose")
        .pair_probability.agg(["min", "mean", "max", "count"])
        .rename_axis("nayose_id")
        .reset_index()
    )
    result = (
        canonical[["nayose_id", "record_count"]]
        .merge(edge_summary, on="nayose_id", how="left")
        .fillna({"min": 0, "mean": 0, "max": 0, "count": 0})
    )
    result["confidence_tier"] = np.select(
        [
            result.record_count.eq(1),
            result["min"].ge(auto_threshold),
            result["mean"].ge(review_threshold),
        ],
        ["SINGLETON", "HIGH", "MEDIUM"],
        default="LOW",
    )
    return result


def _review_queue(pairs: pd.DataFrame, mapping: pd.DataFrame, threshold: float) -> pd.DataFrame:
    queue = pairs.loc[pairs.edge_action.eq("REVIEW_EDGE")].copy()
    queue = queue.merge(
        mapping.rename(columns={"record_id": "left_record_id", "nayose_id": "left_nayose_id"}),
        on="left_record_id",
    )
    queue = queue.merge(
        mapping.rename(columns={"record_id": "right_record_id", "nayose_id": "right_nayose_id"}),
        on="right_record_id",
    )
    return queue.loc[queue.left_nayose_id.ne(queue.right_nayose_id)].sort_values(
        "pair_probability", ascending=False
    )


def _truth_sets(mapping: pd.DataFrame, record_truth: pd.DataFrame) -> dict[str, set[str]]:
    joined = mapping.merge(
        record_truth[["record_id", "truth_id"]], on="record_id", validate="one_to_one"
    )
    return (
        joined.groupby("nayose_id")
        .truth_id.apply(lambda values: {str(value) for value in values})
        .to_dict()
    )


def cluster_metrics(
    mapping: pd.DataFrame,
    record_truth: pd.DataFrame,
    canonical: pd.DataFrame,
    truth_sets: dict[str, set[str]],
) -> dict[str, float]:
    assignments = mapping.merge(
        record_truth[["record_id", "truth_id"]], on="record_id", validate="one_to_one"
    )
    intersections = assignments.groupby(["nayose_id", "truth_id"]).size().rename("intersection")
    cluster_sizes = assignments.groupby("nayose_id").size()
    truth_sizes = assignments.groupby("truth_id").size()
    joined = assignments.join(intersections, on=["nayose_id", "truth_id"])
    precision = (joined.intersection / joined.nayose_id.map(cluster_sizes)).mean()
    recall = (joined.intersection / joined.truth_id.map(truth_sizes)).mean()
    contaminated = {entity for entity, values in truth_sets.items() if len(values) > 1}
    fragmented = set(
        assignments.groupby("truth_id").nayose_id.nunique().loc[lambda values: values > 1].index
    )
    return {
        "b3_precision": float(precision),
        "b3_recall": float(recall),
        "b3_f1": float(2 * precision * recall / max(precision + recall, 1e-12)),
        "predicted_entities": float(len(canonical)),
        "true_entities": float(assignments.truth_id.nunique()),
        "singleton_cluster_rate": float(canonical.record_count.eq(1).mean()),
        "contaminated_cluster_rate": float(canonical.nayose_id.isin(contaminated).mean()),
        "overmerge_record_rate": float(assignments.nayose_id.isin(contaminated).mean()),
        "undermerge_record_rate": float(assignments.truth_id.isin(fragmented).mean()),
    }
