"""Serial generation and training job execution."""

from __future__ import annotations

import json
import logging
import os
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

from api.progress import parse as parse_progress

LOGGER = logging.getLogger(__name__)


class AuditSink(Protocol):
    def record(self, actor: str, action: str, detail: dict[str, Any]) -> dict[str, Any]: ...


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

    def __init__(self, data_root: Path, models_dir: Path, audit: AuditSink):
        self.data_root = data_root.resolve()
        self.models_dir = models_dir.resolve()
        self.audit = audit
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="admin")
        self._jobs_lock = Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}

    def start(self, action: str, params: dict[str, Any], actor: str) -> dict[str, Any]:
        if action not in ("generate", "train"):
            raise HTTPException(400, f"unsupported job action {action!r}")
        with self._jobs_lock:
            running = [j for j in self._jobs.values() if j["stage"] not in ("complete", "failed")]
            if running:
                raise HTTPException(409, "admin_busy")
            if len(self._jobs) >= 16:
                oldest = min(
                    (j for j in self._jobs.values() if j["stage"] in ("complete", "failed")),
                    key=lambda j: j["updated"],
                    default=None,
                )
                if oldest:
                    self._jobs.pop(oldest["id"])
                    self._futures.pop(oldest["id"], None)
            job_id = uuid4().hex
            started = time.monotonic()
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
            }
            self._futures[job_id] = self._pool.submit(self._execute, action, params, actor, job_id)
        self.audit.record(actor, action + "_start", {"job": job_id, **params})
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
        LOGGER.info("admin job=%s stage=%s", job_id, stage)

    def _execute(self, action: str, params: dict[str, Any], actor: str, job_id: str) -> None:
        try:
            self._update(job_id, "validating")
            if action == "generate":
                result = self._run_generate(params, job_id)
            else:
                result = self._run_train(params, job_id)
            self.audit.record(actor, action + "_done", {"job": job_id, **result})
            self._update(job_id, "complete", result=result)
        except Exception as exc:
            LOGGER.warning("admin job=%s %s failed: %s", job_id, action, exc)
            self.audit.record(actor, action + "_failed", {"job": job_id, "error": str(exc)[:200]})
            self._update(job_id, "failed", error="admin_failed")

    def _spawn(
        self,
        argv: list[str],
        stage_label: str,
        job_id: str,
        env_overrides: dict[str, str],
        stream_progress: bool = False,
    ) -> None:
        """Run a subprocess and optionally stream parsed progress into the job."""
        self._update(job_id, stage_label)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", **env_overrides}
        proc = subprocess.Popen(
            argv,
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        if proc.stdout is None:
            raise RuntimeError("admin subprocess did not expose stdout")

        def pump() -> None:
            last_pct: dict[str, float] = {}
            last_ts = 0.0
            for raw in stream:
                line = raw.rstrip("\n")
                if stream_progress:
                    update = parse_progress(line)
                    if update is not None:
                        # throttle: only update the job when the % actually moves
                        # (or >0.8s has passed) — avoids lock churn every line
                        now = time.monotonic()
                        if last_pct.get(update.phase) != update.pct or now - last_ts > 0.8:
                            last_pct[update.phase] = update.pct
                            last_ts = now
                            self._update_progress(
                                job_id,
                                update.phase,
                                update.pct,
                                update.eta_seconds,
                                update.done,
                                update.total,
                            )
                else:
                    logger_tail(line)

        def logger_tail(text: str) -> None:
            LOGGER.info("subprocess %s", text[:200])

        stream = proc.stdout
        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        thread.join()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"{argv[0]} failed ({proc.returncode})")
        return None

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
        LOGGER.info("admin job=%s progress phase=%s pct=%s", job_id, phase, round(pct, 1))

    def _run_generate(self, params: dict[str, Any], job_id: str) -> dict[str, Any]:
        target = Path(params.get("data_root") or self.data_root)
        controls = {key: value for key, value in params.items() if key != "data_root"}
        argv = [
            sys.executable,
            "scripts/generate_data.py",
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
            "scripts/train.py",
            "--data-dir",
            str(data_dir),
            "--models-dir",
            str(models),
            "--seed",
            str(int(params.get("seed", 42))),
            "--device",
            str(params.get("device", "cpu")),
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

    def status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if job_id not in self._jobs:
                raise HTTPException(404, "admin_job_expired")
            job = json.loads(json.dumps(self._jobs[job_id]))
        stop = job["updated"] if job["stage"] in ("complete", "failed") else time.monotonic()
        job["elapsed_ms"] = round((stop - job.pop("started")) * 1000)
        job.pop("updated")
        return job

    def wait(self, job_id: str, timeout: float) -> None:
        self._futures[job_id].result(timeout=timeout)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
