from __future__ import annotations

import importlib.metadata as metadata
import importlib.util as util
import platform
import sys
from dataclasses import dataclass
from typing import Any

from api.infrastructure.runtime.performance import (
    configure_mps_runtime,
    configure_torch_threads,
    model_thread_budget,
    process_worker_count,
    require_mps_device,
    resolve_device,
)
from api.training.contracts import RuntimeInfo
from nayose.config import TrainingConfig

CORE_MODULES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "scikit-learn": "sklearn",
    "rapidfuzz": "rapidfuzz",
    "joblib": "joblib",
}
OPTIONAL_MODULES = {
    "lightgbm": "lightgbm",
    "sentence_transformers": "sentence_transformers",
    "mlx": "mlx",
    "mlx_lm": "mlx_lm",
    "usearch": "usearch",
    "pyarrow": "pyarrow",
    "gradio": "gradio",
}


@dataclass(frozen=True, slots=True)
class Preflight:
    config: TrainingConfig

    def run(self) -> RuntimeInfo:
        missing = [
            package for package, module in CORE_MODULES.items() if util.find_spec(module) is None
        ]
        if missing:
            raise RuntimeError(f"missing required training packages: {', '.join(missing)}")
        backends = {
            name: util.find_spec(module) is not None for name, module in OPTIONAL_MODULES.items()
        }
        if self.config.embedding_backend == "pytorch_mps":
            configure_mps_runtime()
            device = (
                require_mps_device(self.config.device)
                if self.config.model_family != "off"
                else resolve_device(self.config.device)
            )
            configure_torch_threads(model_thread_budget())
            neural_available = backends["sentence_transformers"]
        else:
            device = self.config.device
            neural_available = backends["mlx"] and backends["mlx_lm"]
        diagnostics = self._diagnostics(device)
        neural_enabled = self.config.model_family != "off" and neural_available
        if self.config.model_family != "off" and not neural_enabled:
            required = (
                "sentence-transformers"
                if self.config.embedding_backend == "pytorch_mps"
                else "mlx and mlx-lm"
            )
            raise RuntimeError(f"neural model requested but {required} is unavailable")
        return RuntimeInfo(
            device, diagnostics, backends, neural_enabled, self.config.embedding_backend
        )

    @staticmethod
    def _diagnostics(device: str) -> dict[str, Any]:
        values: dict[str, Any] = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "device": device,
            "process_workers": process_worker_count(),
            "model_threads": model_thread_budget(),
        }
        for package in (
            "numpy",
            "pandas",
            "scikit-learn",
            "lightgbm",
            "torch",
            "sentence-transformers",
            "mlx",
            "mlx-lm",
            "usearch",
        ):
            try:
                values[package] = metadata.version(package)
            except metadata.PackageNotFoundError:
                values[package] = "not installed"
        return values
