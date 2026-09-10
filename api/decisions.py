"""Persistent review decisions bound to immutable model manifests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from api.release import safe_run_id


class DecisionsStore:
    """Store review decisions separately from immutable model bundles."""

    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self._lock = Lock()

    def _path(self, run_id: str) -> Path:
        root = (self.models_dir / "decisions").resolve()
        root.mkdir(parents=True, exist_ok=True)
        current_link = self.models_dir / "current"
        current = current_link.resolve() if current_link.exists() else None
        value = run_id or (current.name if current else "")
        safe = safe_run_id(value)
        path = (root / f"{safe}.json").resolve()
        if root not in path.parents:
            raise ValueError("invalid decision path")
        return path

    def _manifest(self, run_id: str) -> tuple[Path, dict[str, Any]]:
        safe = safe_run_id(run_id)
        candidates = [self.models_dir / "runs" / safe, self.models_dir / safe]
        current = self.models_dir / "current"
        if current.exists() and current.resolve().name == safe:
            candidates.append(current.resolve())
        for run_dir in candidates:
            path = run_dir / "run_manifest.json"
            if path.exists():
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if not manifest.get("data_fingerprint"):
                    break
                return path, manifest
        raise ValueError("run manifest with data fingerprint is required")

    @staticmethod
    def _binding_path(path: Path) -> Path:
        return path.with_suffix(".meta.json")

    def load(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            path = self._path(run_id)
            if not path.exists():
                return []
            manifest_path, manifest = self._manifest(run_id or path.stem)
            binding_path = self._binding_path(path)
            if not binding_path.exists():
                raise ValueError("decision file is not bound to a model manifest")
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if binding.get("data_fingerprint") != manifest.get("data_fingerprint"):
                raise ValueError("decision data fingerprint does not match the model")
            if binding.get("manifest_sha256") != manifest_sha256:
                raise ValueError("decision model manifest has changed")
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, run_id: str, items: list[dict[str, Any]]) -> None:
        with self._lock:
            path = self._path(run_id)
            manifest_path, manifest = self._manifest(run_id or path.stem)
            binding = {
                "run_id": path.stem,
                "data_fingerprint": manifest["data_fingerprint"],
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            }
            tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
            meta = self._binding_path(path)
            meta_tmp = meta.with_name(f".{meta.name}.tmp.{os.getpid()}")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            meta_tmp.write_text(json.dumps(binding, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            os.replace(meta_tmp, meta)
