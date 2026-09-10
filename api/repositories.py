"""Repositories for immutable model bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException


class RunRepository:
    def __init__(self, root: Path):
        self.root = root

    def list(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        current = (self.root / "current").resolve() if (self.root / "current").exists() else None
        runs_root = self.root / "runs"
        if runs_root.is_dir():
            for run in sorted(runs_root.iterdir(), key=lambda p: p.name, reverse=True):
                if not run.is_dir():
                    continue
                entry: dict[str, Any] = {
                    "id": run.name,
                    "path": str(run),
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
            current = (
                (self.root / "current").resolve() if (self.root / "current").exists() else None
            )
            if current is not None and current.is_dir():
                return current
            raise HTTPException(404, "no current run (train first: scripts/train.py)")
        candidate = (self.root / "runs" / run_id).resolve()
        base = self.root.resolve()
        if base not in candidate.parents or not candidate.is_dir():
            raise HTTPException(404, f"unknown run {run_id!r}")
        return candidate
