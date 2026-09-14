from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, replace

import pandas as pd

from api.training.artifacts import RunBundleWriter
from api.training.clustering import build_clusters
from api.training.config import TrainingConfig
from api.training.contracts import (
    CandidateGraphResult,
    RunIdentity,
    TrainingRunResult,
    TruthSnapshot,
)
from api.training.data import load_truth, load_visible_snapshot, normalize_corpus
from api.training.entity_training import EntityTrainer
from api.training.evaluation import Evaluator
from api.training.indexing import build_graph, build_index
from api.training.pair_training import PairTrainer
from api.training.preflight import Preflight
from api.training.stages import StageReporter
from api.work_ledger import WorkflowLedger


@dataclass(slots=True)
class TrainingApplication:
    config: TrainingConfig

    @classmethod
    def from_env(cls) -> TrainingApplication:
        return cls(TrainingConfig.from_env())

    def run(self) -> TrainingRunResult:
        snapshot = load_visible_snapshot(self.config)
        identity = self._identity(snapshot.data_fingerprint)
        ledger = WorkflowLedger(
            "training",
            workflow_id=identity.run_id,
            metadata={"model_family": self.config.model_family},
        )
        reporter = StageReporter(ledger)
        runtime = reporter.run("RUNTIME PREFLIGHT", lambda: Preflight(self.config).run())
        visible = reporter.run("LOAD MATCHER-VISIBLE SNAPSHOTS", lambda: snapshot)
        corpus = reporter.run("NORMALIZE MODEL-VISIBLE FIELDS", lambda: normalize_corpus(visible))
        index, embedding_revision = reporter.run(
            "BUILD HYBRID CANDIDATE INDEX",
            lambda: build_index(corpus, self.config, runtime, visible.data_fingerprint),
        )
        runtime = replace(runtime, embedding_model_revision=embedding_revision)
        graph = reporter.run(
            "BUILD REFERENCE CANDIDATE GRAPH", lambda: build_graph(index, self.config)
        )
        truth = reporter.run(
            "LOAD HIDDEN TRUTH AND AUDIT CANDIDATE RECALL",
            lambda: load_truth(self.config.data_dir.resolve(), corpus.incoming, corpus.reference),
        )
        graph_result = self._graph_result(graph, truth, corpus.reference)
        pair = reporter.run(
            "TRAIN ENTITY-DISJOINT PAIR VERIFIER",
            lambda: PairTrainer(self.config, runtime, corpus.reference, corpus.value_frequency).fit(
                graph, truth
            ),
        )
        pair = replace(
            pair,
            metrics={
                **pair.metrics,
                "candidate_pair_recall": graph_result.candidate_pair_recall,
                "multi_record_entity_recall": graph_result.entity_candidate_recall,
            },
        )
        clusters = reporter.run(
            "FORM CONSTRAINED NAYOSE ENTITIES",
            lambda: build_clusters(
                visible.reference,
                corpus.reference,
                pair.candidate_pairs,
                truth,
                pair.review_threshold,
                pair.auto_threshold,
            ),
        )
        entity = reporter.run(
            "TRAIN ENTITY RECOMMENDATION MODEL",
            lambda: EntityTrainer(self.config, runtime, corpus, index, pair, clusters, truth).fit(),
        )
        evaluation = reporter.run(
            "EVALUATE FROZEN RECOMMENDATIONS",
            lambda: Evaluator(self.config, corpus, truth, clusters, pair, entity).run(),
        )
        stages = reporter.records
        output = reporter.run(
            "WRITE FINGERPRINT-BOUND TRAINING BUNDLE",
            lambda: RunBundleWriter(identity, self.config, runtime).write(
                index,
                pair,
                clusters,
                entity,
                evaluation,
                stages,
                visible.data_fingerprint,
                truth,
            ),
        )
        index.release_query_model()
        ledger.counter("reference_records", len(corpus.reference))
        ledger.counter("incoming_queries", len(corpus.incoming))
        ledger.counter("candidate_pairs", len(pair.candidate_pairs))
        ledger.counter("artifact_tables", 0)
        ledger.metadata("data_fingerprint", visible.data_fingerprint)
        ledger.metadata("embedding_model_revision", embedding_revision or "")
        ledger.write(output / "workflow_metrics.json")
        return TrainingRunResult(identity, output / "run_manifest.json", visible.data_fingerprint)

    def _identity(self, fingerprint: str) -> RunIdentity:
        timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
        return self.config.run_identity(timestamp, source=fingerprint)

    @staticmethod
    def _graph_result(
        graph: pd.DataFrame, truth: TruthSnapshot, reference: pd.DataFrame
    ) -> CandidateGraphResult:
        truth_frame = truth.record_truth
        truth_by_record = truth_frame.set_index("record_id")["truth_id"]
        left_truth = graph.left_record_id.map(truth_by_record)
        right_truth = graph.right_record_id.map(truth_by_record)
        true_groups = truth_frame.groupby("truth_id").size()
        total_pairs = int(((true_groups * (true_groups - 1)) // 2).sum())
        retrieved = int(left_truth.eq(right_truth).sum())
        entities = set(true_groups[true_groups > 1].index)
        retrieved_entities = set(left_truth[left_truth.eq(right_truth)])
        entity_recall = len(entities & retrieved_entities) / max(len(entities), 1)
        possible = len(reference) * (len(reference) - 1) // 2
        hardness = 1.0 - len(graph) / max(possible, 1)
        return CandidateGraphResult(graph, retrieved / max(total_pairs, 1), entity_recall, hardness)


def run_from_environment() -> TrainingRunResult:
    return TrainingApplication.from_env().run()


def main() -> int:
    result = run_from_environment()
    print(json.dumps({"run_id": result.identity.run_id, "manifest": str(result.manifest_path)}))
    return 0
