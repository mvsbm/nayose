"""Repositories for immutable model bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from api.infrastructure.bundles.release import ReleaseCatalog, ReleaseError


class RunRepository:
    def __init__(self, root: Path, release_catalog: ReleaseCatalog | None = None):
        self.root = root
        self.release_catalog = release_catalog

    def list(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        current = self._current_model_dir()
        runs_root = self.root / "runs"
        if runs_root.is_dir():
            for run in sorted(runs_root.iterdir(), key=lambda p: p.name, reverse=True):
                if not run.is_dir():
                    continue
                entry: dict[str, Any] = {
                    "id": run.name,
                    "current": bool(current and run.resolve() == current),
                }
                manifest = run / "run_manifest.json"
                if manifest.exists():
                    meta = json.loads(manifest.read_text(encoding="utf-8"))
                    entry["created"] = meta.get("created_at_utc", "")
                    entry["fingerprint"] = str(meta.get("data_fingerprint", ""))[:12]
                runs.append(entry)
        return runs

    def resolve(self, run_id: str) -> Path:
        if not run_id:
            current = self._current_model_dir()
            if current is not None and current.is_dir():
                return current
            raise HTTPException(404, "no current run (train first: nayose model train)")
        candidate = (self.root / "runs" / run_id).resolve()
        base = self.root.resolve()
        if base not in candidate.parents or not candidate.is_dir():
            raise HTTPException(404, f"unknown run {run_id!r}")
        return candidate

    def _current_model_dir(self) -> Path | None:
        if self.release_catalog is not None:
            try:
                release = self.release_catalog.current()
            except ReleaseError as exc:
                raise HTTPException(503, "release_unavailable") from exc
            if release is None:
                raise HTTPException(503, "release_unavailable")
            return release.model_dir
        current = self.root / "current"
        return current.resolve() if current.exists() else None
