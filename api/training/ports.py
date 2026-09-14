from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, TypeAlias

import numpy as np
import pandas as pd


class EmbeddingModel(Protocol):
    def encode(self, values: Sequence[str], **kwargs: Any) -> np.ndarray: ...

    def get_embedding_dimension(self) -> int: ...


class ProbabilityModel(Protocol):
    def fit(self, features: Any, labels: Sequence[int]) -> Any: ...

    def predict_proba(self, features: Any) -> np.ndarray: ...


class Reranker(Protocol):
    def score(self, left: pd.DataFrame, right: pd.DataFrame) -> np.ndarray: ...


class TableRepository(Protocol):
    def read(self, stem: str, *, hidden: bool = False) -> pd.DataFrame: ...


class ArtifactWriter(Protocol):
    def write_frame(self, frame: pd.DataFrame, name: str) -> dict[str, object]: ...

    def write_model(self, model: object, name: str) -> dict[str, object]: ...


class StageObserver(Protocol):
    def begin(self, name: str) -> None: ...

    def complete(self, name: str, elapsed_seconds: float) -> None: ...

    def fail(self, name: str, elapsed_seconds: float, error_type: str) -> None: ...


DenseSearchResult: TypeAlias = tuple[np.ndarray, np.ndarray]
PathLike: TypeAlias = str | Path
