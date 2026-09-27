"""Optional, data-independent evaluation contracts for entity resolution."""

from api.benchmarking.contracts import BenchmarkDataset, BenchmarkEntity, BenchmarkQuery
from api.benchmarking.csv_adapter import CsvBenchmarkAdapter
from api.benchmarking.evaluation import (
    BenchmarkReport,
    NormalizedFieldCandidateProvider,
    NormalizedFieldScorer,
    evaluate_ranking,
)
from api.benchmarking.nayose_adapter import NayoseBenchmarkAdapter
from api.benchmarking.resolution import (
    DecisionMetrics,
    NayoseResolutionAdapter,
    ResolutionReport,
    ResolutionResult,
    evaluate_resolution,
)
from api.benchmarking.stress import StressConfig, stress_dataset

__all__ = [
    "BenchmarkDataset",
    "BenchmarkEntity",
    "BenchmarkQuery",
    "BenchmarkReport",
    "CsvBenchmarkAdapter",
    "DecisionMetrics",
    "NayoseBenchmarkAdapter",
    "NayoseResolutionAdapter",
    "NormalizedFieldCandidateProvider",
    "NormalizedFieldScorer",
    "ResolutionReport",
    "ResolutionResult",
    "StressConfig",
    "evaluate_ranking",
    "evaluate_resolution",
    "stress_dataset",
]
