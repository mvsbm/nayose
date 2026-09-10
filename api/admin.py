"""Application service for admin jobs, serving controls, audit events, and
manual data-entry mutations.

Design notes
------------
- Jobs reuse the matching state machine (queued -> stages -> complete/failed,
  real elapsed, timeline) so the UI has ONE mental model for every long op.
- Generate/train run as SUBPROCESSES of this same interpreter: the API process
  stays responsive, resource churn is isolated, and a crash can't take the API
  down.
- Every mutation is append-only audited (models/admin_audit.jsonl).
- Manual entries (data entry) live OUTSIDE the immutable generation corpus
  (data/manual) so the fingerprint guard is never broken; they become part of
  a trained corpus only after the next generation.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import HTTPException

from api.admin_jobs import AdminJobRunner
from api.embedding_config import SUPPORTED_MODEL_FAMILIES
from api.evidence import VISIBLE_FIELDS
from api.manual_store import ManualStore
from api.progress import ProgressUpdate
from api.progress import parse as parse_progress
from api.release import atomic_directory_alias, safe_run_id, validate_model_for_data
from synthetic.config import DIFFICULTY as GENERATION_DIFFICULTIES
from synthetic.config import PROFILES as GENERATION_PROFILES
from synthetic.generation_preview import catalog as generation_catalog
from synthetic.generation_preview import preview as generation_preview

LOGGER = logging.getLogger(__name__)

ADMIN_ACTIONS = ("reload_engine", "settings", "promote", "prune", "reset_decisions")
EXACT_MODES = ("hybrid", "exact_only", "fuzzy_only")
MODEL_FAMILIES = SUPPORTED_MODEL_FAMILIES
DEVICES = ("auto", "cpu", "mps", "cuda")
PROFILES = tuple(GENERATION_PROFILES)
DIFFICULTIES = tuple(GENERATION_DIFFICULTIES)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()

    def record(self, actor: str, action: str, detail: dict[str, Any]) -> dict[str, Any]:
        row = {"ts": _now(), "actor": actor, "action": action, **detail}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows


class AdminService:
    """One worker executes long ops (generate/train) serially; everything else
    is quick, synchronous and audited."""

    def __init__(self, data_root: Path, models_dir: Path, manual_dir: Path):
        self.data_root = data_root.resolve()
        self.models_dir = models_dir.resolve()
        self.manual_store = ManualStore(
            manual_dir,
            self.data_root,
            tuple(
                field
                for field in VISIBLE_FIELDS
                if field not in ("record_id", "observed_at", "policy_id", "agency_id", "channel")
            ),
        )
        self.audit = AuditLog(self.models_dir / "admin_audit.jsonl")
        self.jobs = AdminJobRunner(self.data_root, self.models_dir, self.audit)
        self._engine_reset: Callable[[], None] | None = None

    # ── capability / state ────────────────────────────────────────────────────
    def capable(self) -> bool:
        """The local-first admin surface is always available."""
        return True

    def state(self, ready: Callable[[str], bool]) -> dict[str, Any]:
        current_run = (
            (self.models_dir / "current").resolve().name
            if (self.models_dir / "current").exists()
            else None
        )
        return {
            "profile": {
                "profiles": PROFILES,
                "difficulties": DIFFICULTIES,
                "model_families": MODEL_FAMILIES,
                "devices": DEVICES,
                "exact_modes": EXACT_MODES,
                "threads_default": int(os.getenv("NAYOSE_MODEL_THREADS", "2")),
            },
            "settings": {
                "exact_mode": os.getenv("NAYOSE_EXACT_MODE", "hybrid"),
                "warm_embed": os.getenv("NAYOSE_WARM_EMBED", "0") == "1",
                "engine_warm": ready(current_run or ""),
                "engine_backend": "unknown",
            },
            "runs": [
                {
                    "id": run.name,
                    "manifest": (run / "run_manifest.json").exists(),
                    "current": run.resolve() == (self.models_dir / "current").resolve()
                    if (self.models_dir / "current").exists()
                    else False,
                }
                for run in sorted(
                    (self.models_dir / "runs").iterdir(), key=lambda p: p.name, reverse=True
                )
                if run.is_dir()
            ]
            if (self.models_dir / "runs").is_dir()
            else [],
            "manual_count": self.manual_store.count(),
            "audit": self.audit.tail(30),
        }

    # ── generation controls / dry preview ────────────────────────────────────
    def generation_config(self) -> dict[str, Any]:
        return generation_catalog()

    def generation_preview(self, controls: dict[str, Any]) -> dict[str, Any]:
        try:
            return generation_preview(controls)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    # ── long-running jobs (generate / train) ─────────────────────────────────
    def start(self, action: str, params: dict[str, Any], actor: str) -> dict[str, Any]:
        return self.jobs.start(action, params, actor)

    def status(self, job_id: str) -> dict[str, Any]:
        return self.jobs.status(job_id)

    def wait(self, job_id: str, timeout: float) -> None:
        self.jobs.wait(job_id, timeout)

    # ── synchronised actions (audited) ───────────────────────────────────────
    def action(self, action: str, params: dict[str, Any], actor: str) -> dict[str, Any]:
        if action == "settings":
            exact = params.get("exact_mode")
            if exact not in EXACT_MODES:
                raise HTTPException(400, f"invalid exact_mode {exact!r}")
            os.environ["NAYOSE_EXACT_MODE"] = exact  # honored by BundleRetriever per call
            self.audit.record(actor, "settings", {"exact_mode": exact})
            return {"ok": True, "exact_mode": exact}
        if action == "reload_engine":
            if self._engine_reset:
                self._engine_reset()
            self.audit.record(actor, "reload_engine", {})
            return {"ok": True}
        if action == "promote":
            run_id = str(params.get("run_id", ""))
            try:
                run_id = safe_run_id(run_id)
            except ValueError as exc:
                raise HTTPException(400, "invalid run") from exc
            target = (self.models_dir / "runs" / run_id).resolve()
            base = self.models_dir.resolve()
            if base not in target.parents or not (target / "run_manifest.json").exists():
                raise HTTPException(400, "invalid run")
            try:
                fingerprint = validate_model_for_data(target, self.data_root)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            atomic_directory_alias(target, self.models_dir / "current")
            self.audit.record(actor, "promote", {"run": run_id, "fingerprint": fingerprint[:12]})
            return {"ok": True, "run": run_id}
        if action == "prune":
            removed = []
            for run in (self.models_dir / "runs").iterdir():
                if (
                    run.is_dir()
                    and not (run / "run_manifest.json").exists()
                    and run.resolve() != (self.models_dir / "current").resolve()
                ):
                    shutil_rmtree(run)
                    removed.append(run.name)
            self.audit.record(actor, "prune", {"removed": removed})
            return {"ok": True, "removed": removed}
        if action == "reset_decisions":
            run_id = str(params.get("run_id", "")) or (
                (self.models_dir / "current").resolve().name
                if (self.models_dir / "current").exists()
                else ""
            )
            try:
                run_id = safe_run_id(run_id)
            except ValueError as exc:
                raise HTTPException(400, "invalid run") from exc
            path = (self.models_dir / "decisions" / f"{run_id}.json").resolve()
            decisions_root = (self.models_dir / "decisions").resolve()
            if decisions_root not in path.parents:
                raise HTTPException(400, "invalid run")
            for decision_path in (path, path.with_suffix(".meta.json")):
                if decision_path.exists():
                    decision_path.unlink()
            self.audit.record(actor, "reset_decisions", {"run": run_id})
            return {"ok": True}
        raise HTTPException(400, f"unknown action {action!r}")

    # ── manual corpus (D, data entry) ─────────────────────────────────────────
    def _manual_count(self) -> int:
        return self.manual_store.count()

    def manual_list(self) -> list[dict[str, Any]]:
        return self.manual_store.rows()

    def manual_register(self, record: dict[str, Any], actor: str) -> dict[str, Any]:
        record_id = self.manual_store.register(record)
        self.audit.record(actor, "data_entry_register", {"record_id": record_id})
        return {"ok": True, "record_id": record_id}

    def manual_import(
        self, rows: list[dict[str, Any]], dry_run: bool, actor: str
    ) -> dict[str, Any]:
        result = self.manual_store.import_rows(rows, dry_run)
        if not dry_run:
            self.audit.record(
                actor,
                "data_entry_import",
                {"rows": result["imported"], "duplicates": len(result["duplicates"])},
            )
        return result

    def manual_undo(self, record_ids: list[str], actor: str) -> dict[str, Any]:
        removed = self.manual_store.undo(record_ids)
        if removed:
            self.audit.record(actor, "data_entry_undo", {"removed": removed})
        return {"ok": True, "removed": removed}

    def close(self) -> None:
        self.jobs.close()

    @staticmethod
    def stream_parse(lines: list[str], on_update: Callable[[ProgressUpdate], None]) -> None:
        """Deterministic unit-test seam: feed lines, receive parsed updates."""
        for line in lines:
            update = parse_progress(line)
            if update is not None:
                on_update(update)


def shutil_rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)
