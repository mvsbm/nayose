"""Admin service (SRP): one owner for training/generation/serving-control jobs,
the audit log, the manual corpus (D: data entry) and destructive ops (C).

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
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import HTTPException

from api.evidence import VISIBLE_FIELDS
from api.progress import ProgressUpdate
from api.progress import parse as parse_progress
from api.release import atomic_directory_alias, validate_model_for_data
from synthetic.config import DIFFICULTY as GENERATION_DIFFICULTIES
from synthetic.config import PROFILES as GENERATION_PROFILES
from synthetic.generation_preview import catalog as generation_catalog
from synthetic.generation_preview import preview as generation_preview

LOGGER = logging.getLogger(__name__)

ADMIN_ACTIONS = ("reload_engine", "settings", "promote", "prune", "reset_decisions")
EXACT_MODES = ("hybrid", "exact_only", "fuzzy_only")
MODEL_FAMILIES = ("off", "ruri", "f2llm", "qwen")
DEVICES = ("auto", "cpu", "mps", "cuda")
PROFILES = tuple(GENERATION_PROFILES)
DIFFICULTIES = tuple(GENERATION_DIFFICULTIES)
MANUAL_FIELDS = tuple(
    f
    for f in VISIBLE_FIELDS
    if f not in ("record_id", "observed_at", "policy_id", "agency_id", "channel")
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AuditLog:
    def __init__(self, path: Path):
        self.path = path

    def record(self, actor: str, action: str, detail: dict[str, Any]) -> dict[str, Any]:
        row = {"ts": _now(), "actor": actor, "action": action, **detail}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
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


def _newest(root: Path, prefix: str) -> Path | None:
    if not root.is_dir():
        return None
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith(prefix)),
        key=lambda p: p.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


class AdminService:
    """One worker executes long ops (generate/train) serially; everything else
    is quick, synchronous and audited."""

    def __init__(self, data_root: Path, models_dir: Path, manual_dir: Path):
        self.data_root = data_root.resolve()
        self.models_dir = models_dir.resolve()
        self.manual_dir = manual_dir.resolve()
        self.audit = AuditLog(self.models_dir / "admin_audit.jsonl")
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="admin")
        self._jobs_lock = Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}
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
            "manual_count": self._manual_count(),
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
        """Run a subprocess, OPTIONALLY streaming parsed ETA/progress lines into
        the job (Observer: the job record is the single source the UI polls)."""
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
        assert stream is not None
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
        generation = _newest(target / "generations", "gen_")
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
        run = _newest(models / "runs", "run_")
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
            target = (self.models_dir / "runs" / run_id).resolve()
            base = self.models_dir.resolve()
            if (
                not run_id
                or base not in target.parents
                or not (target / "run_manifest.json").exists()
            ):
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
            path = self.models_dir / "decisions" / f"{run_id}.json"
            if path.exists():
                path.unlink()
            self.audit.record(actor, "reset_decisions", {"run": run_id})
            return {"ok": True}
        raise HTTPException(400, f"unknown action {action!r}")

    # ── manual corpus (D, data entry) ─────────────────────────────────────────
    def _manual_path(self) -> Path:
        return self.manual_dir / "records.parquet"

    def _manual_count(self) -> int:
        path = self._manual_path()
        if not path.exists():
            return 0
        return int(pd.read_parquet(path).shape[0])

    def manual_list(self) -> list[dict[str, Any]]:
        path = self._manual_path()
        if not path.exists():
            return []
        return pd.read_parquet(path).to_dict("records")

    def _validate_manual(self, record: dict[str, Any]) -> dict[str, str]:
        cleaned = {k: str(v).strip() for k, v in record.items() if k in MANUAL_FIELDS}
        if not cleaned.get("name_kanji"):
            raise HTTPException(422, "name_kanji is required")
        for key in ("phone", "name_kanji", "name_kana", "postal_code"):
            cleaned.setdefault(key, "")
        cleaned.setdefault("email", "")
        cleaned.setdefault("date_birth", "")
        cleaned.setdefault("address", "")
        cleaned.setdefault("party_type", "person")
        cleaned.setdefault("name_romaji", "")
        return cleaned

    def _append_manual(self, rows: list[dict[str, str]]) -> None:
        self.manual_dir.mkdir(parents=True, exist_ok=True)
        path = self._manual_path()
        existing = (
            pd.read_parquet(path)
            if path.exists()
            else pd.DataFrame(columns=["record_id", *MANUAL_FIELDS])
        )
        stamp = int(time.time() * 1000)
        rows_with_id = [
            {
                **row,
                "record_id": f"M{stamp + i:010d}",
                "observed_at": _now()[:10],
                "channel": "manual",
            }
            for i, row in enumerate(rows)
        ]
        frame = pd.concat([existing, pd.DataFrame(rows_with_id)], ignore_index=True)
        self._write_atomic(frame)

    def _write_atomic(self, frame: pd.DataFrame) -> None:
        """Transactional write: temp file + os.replace, so a concurrent reader
        never sees a half-written parquet (mirrors DecisionsStore)."""
        self.manual_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._manual_path().with_suffix(".parquet.tmp")
        frame.to_parquet(tmp, index=False)
        tmp.replace(self._manual_path())

    def manual_register(self, record: dict[str, Any], actor: str) -> dict[str, Any]:
        row = self._validate_manual(record)
        self._append_manual([row])
        self.audit.record(
            actor,
            "data_entry_register",
            {"record_id": (self.manual_list() or [{}])[-1]["record_id"]},
        )
        return {"ok": True, "record_id": (self.manual_list() or [{}])[-1]["record_id"]}

    def manual_import(
        self, rows: list[dict[str, Any]], dry_run: bool, actor: str
    ) -> dict[str, Any]:
        if not rows or len(rows) > 500:
            raise HTTPException(422, "import must have 1..500 rows")
        cleaned: list[dict[str, str]] = []
        errors: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            try:
                cleaned.append(self._validate_manual(row))
            except HTTPException as exc:
                errors.append({"index": i, "error": str(exc.detail)})
        # duplicate hint against the visible reference corpus (server-owned)
        corpus: set[str] = set()
        try:
            import duckdb

            con = duckdb.connect()
            try:
                ref_path = self.data_root / "current" / "reference_records.parquet"
                query = f"select distinct phone from read_parquet('{ref_path}') where phone != ''"
                corpus = {
                    str(p[0]).replace("-", "").replace(" ", "")
                    for p in con.execute(query).fetchall()
                }
            finally:
                con.close()
        except Exception:
            corpus = set()
        dup_rows = [
            i
            for i, r in enumerate(cleaned)
            if r["phone"] and r["phone"].replace("-", "").replace(" ", "") in corpus
        ]
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "candidates": len(rows),
                "valid": len(cleaned),
                "errors": errors,
                "duplicates": dup_rows,
            }
        if errors:
            raise HTTPException(422, f"import rows invalid: {errors[:5]}")
        self._append_manual(cleaned)
        self.audit.record(
            actor, "data_entry_import", {"rows": len(cleaned), "duplicates": len(dup_rows)}
        )
        return {"ok": True, "imported": len(cleaned), "duplicates": len(dup_rows)}

    def manual_undo(self, record_ids: list[str], actor: str) -> dict[str, Any]:
        path = self._manual_path()
        if not path.exists():
            return {"ok": True, "removed": 0}
        frame = pd.read_parquet(path)
        keep = frame[~frame["record_id"].isin(record_ids)]
        removed = int(frame.shape[0] - keep.shape[0])
        if removed:
            self._write_atomic(keep)
        self.audit.record(actor, "data_entry_undo", {"removed": removed})
        return {"ok": True, "removed": removed}

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

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
