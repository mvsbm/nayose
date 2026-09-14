"""Serial generation and training job execution."""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

import pandas as pd
from fastapi import HTTPException

from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.job_store import SQLiteJobStore
from api.infrastructure.persistence.operational_store import OperationalStore
from api.infrastructure.process.admin_process import AdminProcessRunner
from api.infrastructure.process.job_control import JobCancelled
from nayose.config import repository_root

LOGGER = logging.getLogger(__name__)


class AuditSink(Protocol):
    def record(
        self,
        actor: str,
        action: str,
        detail: dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]: ...


def newest(root: Path, prefix: str) -> Path | None:
    if not root.is_dir():
        return None
    candidates = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith(prefix)),
        key=lambda path: path.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


class AdminJobRunner:
    """Own the bounded worker and lifecycle of generation/training jobs."""

    def __init__(
        self,
        data_root: Path,
        models_dir: Path,
        audit: AuditSink,
        job_store_path: Path | None = None,
        operational_store: OperationalStore | None = None,
        stats: Stats | None = None,
    ):
        self.data_root = data_root.resolve()
        self.models_dir = models_dir.resolve()
        self.audit = audit
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="admin")
        self._jobs_lock = Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._heartbeat_events: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._process_runner = AdminProcessRunner(repository_root(), LOGGER, stats=stats)
        if operational_store is not None and job_store_path is not None:
            raise ValueError("provide operational_store or job_store_path, not both")
        self._job_store = SQLiteJobStore(
            operational_store or job_store_path or self.models_dir / "operational.sqlite3",
            "admin",
        )
        self._restore_jobs()

    def _restore_jobs(self) -> None:
        now = time.monotonic()
        for job in self._job_store.list():
            elapsed_ms = int(job.pop("_elapsed_ms", 0))
            job.setdefault("_version", self._job_store.version(str(job["id"])))
            job["started"] = now - elapsed_ms / 1000
            job["updated"] = now
            if job.get("stage") not in ("complete", "failed", "cancelled"):
                job["stage"] = "failed"
                job["error"] = "worker_lost"
                job["timeline"] = [
                    *job.get("timeline", []),
                    {"stage": "failed", "elapsed_ms": elapsed_ms},
                ]
            job_id = str(job["id"])
            self._jobs[job_id] = job
            self._cancel_events.setdefault(job_id, threading.Event())
            self._heartbeat_events.setdefault(job_id, threading.Event())
            self._persist(job)

    def _persist(self, job: dict[str, Any], connection: sqlite3.Connection | None = None) -> None:
        version = int(job.get("_version", 0))
        persisted = dict(job)
        persisted["_version"] = version + 1
        persisted["_elapsed_ms"] = round((time.monotonic() - job["started"]) * 1000)
        new_version = self._job_store.save(
            persisted,
            action=str(job.get("_action", "")),
            idempotency_key=job.get("_idempotency_key"),
            connection=connection,
            expected_version=version,
        )
        job["_version"] = new_version

    def start(self, action: str, params: dict[str, Any], actor: str) -> dict[str, Any]:
        if action not in ("generate", "train"):
            raise HTTPException(400, f"unsupported job action {action!r}")
        params = dict(params)
        idempotency_key = str(params.pop("idempotency_key", "")).strip() or None
        if idempotency_key and len(idempotency_key) > 128:
            raise HTTPException(422, "idempotency_key_too_long")
        existing_id: str | None = None
        with self._jobs_lock:
            if idempotency_key:
                existing = self._job_store.find_by_idempotency(idempotency_key)
                if existing is not None:
                    existing_id = str(existing["id"])
            if existing_id is None:
                running = [
                    j
                    for j in self._jobs.values()
                    if j["stage"] not in ("complete", "failed", "cancelled")
                ]
                if running:
                    raise HTTPException(409, "admin_busy")
                if len(self._jobs) >= 16:
                    oldest = min(
                        (
                            j
                            for j in self._jobs.values()
                            if j["stage"] in ("complete", "failed", "cancelled")
                        ),
                        key=lambda j: j["updated"],
                        default=None,
                    )
                    if oldest:
                        self._jobs.pop(oldest["id"])
                        self._futures.pop(oldest["id"], None)
                        self._cancel_events.pop(oldest["id"], None)
                        self._heartbeat_events.pop(oldest["id"], None)
                        self._job_store.delete(oldest["id"])
                job_id = uuid4().hex
                started = time.monotonic()
                self._cancel_events[job_id] = threading.Event()
                self._heartbeat_events[job_id] = threading.Event()
                self._jobs[job_id] = {
                    "id": job_id,
                    "run_id": "admin",
                    "stage": "queued",
                    "started": started,
                    "updated": started,
                    "timeline": [{"stage": "queued", "elapsed_ms": 0}],
                    "progress": None,
                    "error": None,
                    "result": None,
                    "_action": action,
                    "_idempotency_key": idempotency_key,
                }
                try:
                    with self._job_store.store.transaction() as connection:
                        self._persist(self._jobs[job_id], connection)
                        self.audit.record(
                            actor,
                            action + "_start",
                            {"job": job_id, **params},
                            connection,
                        )
                except sqlite3.IntegrityError:
                    self._jobs.pop(job_id)
                    self._cancel_events.pop(job_id, None)
                    self._heartbeat_events.pop(job_id, None)
                    if idempotency_key is None:
                        raise
                    persisted = self._job_store.find_by_idempotency(idempotency_key)
                    if persisted is None:
                        raise
                    existing_id = str(persisted["id"])
                except Exception:
                    self._jobs.pop(job_id)
                    self._cancel_events.pop(job_id, None)
                    self._heartbeat_events.pop(job_id, None)
                    raise
                else:
                    self._futures[job_id] = self._pool.submit(
                        self._execute, action, params, actor, job_id
                    )
                    threading.Thread(
                        target=self._heartbeat,
                        args=(job_id,),
                        name=f"admin-heartbeat-{job_id[:8]}",
                        daemon=True,
                    ).start()
        if existing_id is not None:
            return self.status(existing_id)
        return self.status(job_id)

    def _update(self, job_id: str, stage: str, **values: Any) -> None:
        with self._jobs_lock:
            job = self._jobs[job_id]
            now = time.monotonic()
            elapsed = round((now - job["started"]) * 1000)
            job.update(stage=stage, updated=now, **values)
            if job["timeline"][-1]["stage"] == stage:
                job["timeline"][-1]["elapsed_ms"] = elapsed
            else:
                job["timeline"].append({"stage": stage, "elapsed_ms": elapsed})
            self._persist(job)
        LOGGER.info("admin job=%s stage=%s", job_id, stage)

    def _heartbeat(self, job_id: str) -> None:
        stop = self._heartbeat_events[job_id]
        while not stop.wait(5.0):
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if job is None or job["stage"] in ("complete", "failed", "cancelled"):
                    return
                job["updated"] = time.monotonic()
                self._persist(job)

    def _execute(self, action: str, params: dict[str, Any], actor: str, job_id: str) -> None:
        try:
            self._check_cancelled(job_id)
            self._update(job_id, "validating")
            if action == "generate":
                result = self._run_generate(params, job_id)
            else:
                result = self._run_train(params, job_id)
            self.audit.record(actor, action + "_done", {"job": job_id, **result})
            self._check_cancelled(job_id)
            self._update(job_id, "complete", result=result)
        except JobCancelled:
            LOGGER.info("admin job=%s %s cancelled", job_id, action)
            self.audit.record(actor, action + "_cancelled", {"job": job_id})
            self._update(job_id, "cancelled", error="request_cancelled")
        except Exception as exc:
            LOGGER.warning("admin job=%s %s failed: %s", job_id, action, exc)
            self.audit.record(actor, action + "_failed", {"job": job_id, "error": str(exc)[:200]})
            self._update(job_id, "failed", error="admin_failed")
        finally:
            self._heartbeat_events[job_id].set()

    def _check_cancelled(self, job_id: str) -> None:
        if self._cancel_events[job_id].is_set():
            raise JobCancelled

    def _spawn(
        self,
        argv: list[str],
        stage_label: str,
        job_id: str,
        env_overrides: dict[str, str],
        stream_progress: bool = False,
    ) -> None:
        self._process_runner.run(
            argv,
            stage_label,
            job_id,
            env_overrides,
            self._cancel_events[job_id],
            lambda stage: self._update(job_id, stage),
            lambda phase, pct, eta, done, total: self._update_progress(
                job_id, phase, pct, eta, done, total
            ),
            lambda process: self._register_process(job_id, process),
            lambda: self._unregister_process(job_id),
            stream_progress,
        )

    def _register_process(self, job_id: str, process: subprocess.Popen[str]) -> None:
        with self._jobs_lock:
            self._processes[job_id] = process

    def _unregister_process(self, job_id: str) -> None:
        with self._jobs_lock:
            self._processes.pop(job_id, None)

    def _update_progress(
        self, job_id: str, phase: str, pct: float, eta_seconds: int, done: int, total: int
    ) -> None:
        with self._jobs_lock:
            job = self._jobs[job_id]
            job["progress"] = {
                "phase": phase,
                "pct": round(pct, 1),
                "eta_seconds": eta_seconds,
                "done": done,
                "total": total,
            }
            job["updated"] = time.monotonic()
            self._persist(job)
        LOGGER.info("admin job=%s progress phase=%s pct=%s", job_id, phase, round(pct, 1))

    def _run_generate(self, params: dict[str, Any], job_id: str) -> dict[str, Any]:
        target = Path(params.get("data_root") or self.data_root)
        controls = {key: value for key, value in params.items() if key != "data_root"}
        argv = [
            sys.executable,
            "-m",
            "api.cli",
            "data",
            "generate",
            "--controls-json",
            json.dumps(controls, ensure_ascii=False, separators=(",", ":")),
            "--data-dir",
            str(target),
        ]
        self._spawn(argv, "generating", job_id, {}, stream_progress=True)
        generation = newest(target / "generations", "gen_")
        if generation is None:
            raise RuntimeError("no generation produced")
        manifest = json.loads((generation / "generation_manifest.json").read_text(encoding="utf-8"))
        records = int(manifest.get("counts", {}).get("reference_records", 0))
        if not records:  # generation manifests store counts elsewhere: fall back to the parquet
            try:
                path = target / "generations" / generation.name / "reference_records.parquet"
                records = int(pd.read_parquet(path).shape[0])
            except Exception:
                records = 0
        return {
            "generation": generation.name,
            "fingerprint": str(manifest.get("data_fingerprint", ""))[:12],
            "records": records,
        }

    def _run_train(self, params: dict[str, Any], job_id: str) -> dict[str, Any]:
        models = Path(params.get("models_dir") or self.models_dir)
        data_dir = Path(params.get("data_dir") or (self.data_root / "current"))
        argv = [
            sys.executable,
            "-m",
            "api.cli",
            "model",
            "train",
            "--data-dir",
            str(data_dir),
            "--models-dir",
            str(models),
            "--seed",
            str(int(params.get("seed", 42))),
            "--device",
            str(params.get("device", "mps")),
            "--model-family",
            str(params.get("model_family", "off")),
            "--prune-aborted",
        ]
        env = {
            "NAYOSE_MODEL_THREADS": str(int(params.get("threads", 2))),
            "NAYOSE_MAX_FULL_RECORDS": str(int(params.get("max_records", 700_000))),
        }
        if params.get("candidate_cap"):
            env["NAYOSE_CANDIDATE_CAP"] = str(int(params["candidate_cap"]))
        self._spawn(argv, "training", job_id, env)
        run = newest(models / "runs", "run_")
        if run is None or not (run / "run_manifest.json").exists():
            raise RuntimeError("no trained bundle produced")
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        return {
            "run": run.name,
            "fingerprint": str(manifest.get("data_fingerprint", ""))[:12],
            "metrics": manifest.get("query_metrics", {}),
        }

    def cancel(self, job_id: str, actor: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "admin_job_expired")
            if job["stage"] not in ("complete", "failed", "cancelled"):
                self._cancel_events[job_id].set()
                now = time.monotonic()
                elapsed = round((now - job["started"]) * 1000)
                job.update(stage="cancelled", updated=now, error="request_cancelled")
                job["timeline"].append({"stage": "cancelled", "elapsed_ms": elapsed})
                with self._job_store.store.transaction() as connection:
                    self._persist(job, connection)
                    self.audit.record(actor, "admin_job_cancel", {"job": job_id}, connection)
        return self.status(job_id)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if job_id not in self._jobs:
                raise HTTPException(404, "admin_job_expired")
            job = json.loads(json.dumps(self._jobs[job_id]))
        stop = (
            job["updated"]
            if job["stage"] in ("complete", "failed", "cancelled")
            else time.monotonic()
        )
        job["elapsed_ms"] = round((stop - job.pop("started")) * 1000)
        job.pop("updated")
        job.pop("_action", None)
        job.pop("_idempotency_key", None)
        job.pop("_version", None)
        return job

    def wait(self, job_id: str, timeout: float) -> None:
        future = self._futures.get(job_id)
        if future is None:
            if self.status(job_id)["stage"] in ("complete", "failed", "cancelled"):
                return
            raise HTTPException(503, "admin_worker_unavailable")
        future.result(timeout=timeout)

    def close(self) -> None:
        for event in self._cancel_events.values():
            event.set()
        for event in self._heartbeat_events.values():
            event.set()
        for process in list(self._processes.values()):
            if process.poll() is None:
                process.terminate()
        self._pool.shutdown(wait=False, cancel_futures=True)
