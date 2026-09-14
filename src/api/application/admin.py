"""Application service for admin jobs, serving controls, audit events, and
manual data-entry mutations.

Design notes
------------
- Jobs reuse the matching state machine (queued -> stages -> complete/failed,
  real elapsed, timeline) so the UI has ONE mental model for every long op.
- Generate/train run as SUBPROCESSES of this same interpreter: the API process
  stays responsive, resource churn is isolated, and a crash can't take the API
  down.
- Every mutation is append-only audited in the operational SQLite store.
- Manual entries (data entry) live OUTSIDE the immutable generation corpus
  so the fingerprint guard is never broken; they become part of
  a trained corpus only after the next generation.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from api.domain.evidence import VISIBLE_FIELDS
from api.infrastructure.bundles.release import (
    data_generation_for_fingerprint,
    safe_run_id,
    validate_model_for_data,
)
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.audit import AuditLog
from api.infrastructure.persistence.manual_store import ManualStore
from api.infrastructure.persistence.operational_store import OperationalStore
from api.infrastructure.process.admin_jobs import AdminJobRunner
from api.infrastructure.process.progress import ProgressUpdate
from api.infrastructure.process.progress import parse as parse_progress
from api.infrastructure.runtime.performance import model_thread_budget
from data_generation.preview import catalog as generation_catalog
from data_generation.preview import preview as generation_preview
from nayose.config import DIFFICULTY as GENERATION_DIFFICULTIES
from nayose.config import (
    EXACT_MODES,
    SUPPORTED_MODEL_FAMILIES,
    ConfigSnapshot,
    configuration_summary,
    load_config,
)
from nayose.config import PROFILES as GENERATION_PROFILES

LOGGER = logging.getLogger(__name__)

ADMIN_ACTIONS = ("reload_engine", "settings", "promote", "prune", "reset_decisions")
MODEL_FAMILIES = SUPPORTED_MODEL_FAMILIES
DEVICES = ("mps",)
PROFILES = tuple(GENERATION_PROFILES)
DIFFICULTIES = tuple(GENERATION_DIFFICULTIES)


class AdminService:
    """One worker executes long ops (generate/train) serially; everything else
    is quick, synchronous and audited."""

    def __init__(
        self,
        data_root: Path,
        models_dir: Path,
        engine_reset: Callable[[], None] | None = None,
        exact_mode_set: Callable[[str], None] | None = None,
        exact_mode: str = "hybrid",
        warm_embed: bool = False,
        release_publish: Callable[[str, str], Any] | None = None,
        decisions_clear: Callable[[str, sqlite3.Connection | None, str], int] | None = None,
        operational_store: OperationalStore | None = None,
        audit: AuditLog | None = None,
        stats: Stats | None = None,
        config_snapshot: ConfigSnapshot | None = None,
    ):
        if exact_mode not in EXACT_MODES:
            raise ValueError(f"exact_mode must be one of {EXACT_MODES}")
        self.data_root = data_root.resolve()
        self.models_dir = models_dir.resolve()
        self.operational_store = operational_store or OperationalStore(
            self.models_dir / "operational.sqlite3"
        )
        self.manual_store = ManualStore(
            self.operational_store,
            self.data_root,
            tuple(
                field
                for field in VISIBLE_FIELDS
                if field not in ("record_id", "observed_at", "policy_id", "agency_id", "channel")
            ),
        )
        self.audit = audit or AuditLog(self.operational_store)
        self.jobs = AdminJobRunner(
            self.data_root,
            self.models_dir,
            self.audit,
            operational_store=self.operational_store,
            stats=stats,
        )
        self._engine_reset = engine_reset
        self._exact_mode_set = exact_mode_set
        self._exact_mode = exact_mode
        self._warm_embed = warm_embed
        self._release_publish = release_publish
        self._decisions_clear = decisions_clear
        self._config_snapshot = config_snapshot or load_config()

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
                "threads_default": model_thread_budget(),
            },
            "settings": {
                "exact_mode": self._exact_mode,
                "warm_embed": self._warm_embed,
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
    def configuration(self) -> dict[str, Any]:
        return configuration_summary(self._config_snapshot)

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

    def cancel(self, job_id: str, actor: str) -> dict[str, Any]:
        return self.jobs.cancel(job_id, actor)

    def wait(self, job_id: str, timeout: float) -> None:
        self.jobs.wait(job_id, timeout)

    # ── synchronised actions (audited) ───────────────────────────────────────
    def action(self, action: str, params: dict[str, Any], actor: str) -> dict[str, Any]:
        if action == "settings":
            exact = params.get("exact_mode")
            if exact not in EXACT_MODES:
                raise HTTPException(400, f"invalid exact_mode {exact!r}")
            self._exact_mode = exact
            if self._exact_mode_set is not None:
                self._exact_mode_set(exact)
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
                generation = data_generation_for_fingerprint(self.data_root, fingerprint)
                if generation is None or self._release_publish is None:
                    raise ValueError("release publication is unavailable")
                release = self._release_publish(generation.name, run_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            self.audit.record(
                actor,
                "promote",
                {
                    "run": run_id,
                    "generation": generation.name,
                    "release_id": release.release_id,
                    "fingerprint": fingerprint[:12],
                },
            )
            return {"ok": True, "run": run_id, "release_id": release.release_id}
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
            if self._decisions_clear is None:
                raise HTTPException(503, "decision_store_unavailable")
            try:
                with self.operational_store.transaction() as connection:
                    self._decisions_clear(run_id, connection, actor)
                    self.audit.record(actor, "reset_decisions", {"run": run_id}, connection)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"ok": True}
        raise HTTPException(400, f"unknown action {action!r}")

    # ── manual corpus (D, data entry) ─────────────────────────────────────────
    def manual_count(self) -> int:
        return self.manual_store.count()

    def manual_list(self) -> list[dict[str, Any]]:
        return self.manual_store.rows()

    def manual_register(self, record: dict[str, Any], actor: str) -> dict[str, Any]:
        with self.operational_store.transaction() as connection:
            record_id = self.manual_store.register(record, connection)
            self.audit.record(actor, "data_entry_register", {"record_id": record_id}, connection)
        return {"ok": True, "record_id": record_id}

    def manual_import(
        self, rows: list[dict[str, Any]], dry_run: bool, actor: str
    ) -> dict[str, Any]:
        if dry_run:
            return self.manual_store.import_rows(rows, dry_run=True)
        with self.operational_store.transaction() as connection:
            result = self.manual_store.import_rows(rows, dry_run=False, connection=connection)
            self.audit.record(
                actor,
                "data_entry_import",
                {"rows": result["imported"], "duplicates": len(result["duplicates"])},
                connection,
            )
        return result

    def manual_undo(self, record_ids: list[str], actor: str) -> dict[str, Any]:
        with self.operational_store.transaction() as connection:
            removed = self.manual_store.undo(record_ids, connection)
            if removed:
                self.audit.record(actor, "data_entry_undo", {"removed": removed}, connection)
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
