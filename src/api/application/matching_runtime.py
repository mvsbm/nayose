"""Runtime state and bounded job execution for matching."""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import duckdb
import numpy as np
from fastapi import HTTPException

from api.domain.evidence import sufficient
from api.infrastructure.bundles.release import ReleaseError, SnapshotResolver
from api.infrastructure.bundles.request_context import bind_snapshot
from api.infrastructure.bundles.request_context import snapshot_for as request_snapshot_for
from api.infrastructure.matching.scoring import empty_result
from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.job_store import SQLiteJobStore
from api.infrastructure.persistence.operational_store import OperationalStore
from api.infrastructure.retrieval.entity_search import EntitySearch
from nayose.config import EXACT_MODES

LOGGER = logging.getLogger(__name__)
_JOB_DEADLINE_SECONDS = 300


class JobCancelled(RuntimeError):
    """Raised when a matching request is cancelled or exceeds its deadline."""


class JobDeadlineExceeded(JobCancelled):
    """Raised when cooperative matching execution exceeds its deadline."""


@lru_cache(maxsize=2)
def _review_scores(run_dir: str) -> tuple[np.ndarray, int]:
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            "SELECT pair_probability FROM read_parquet(?) WHERE pair_probability IS NOT NULL",
            [str(Path(run_dir) / "review_queue.parquet")],
        ).fetchall()
    finally:
        connection.close()
    scores: np.ndarray = np.fromiter((float(row[0]) for row in rows), dtype=np.float64)
    scores.sort()
    return scores, int(len(scores))


def _score_context(run_dir: Path, score: float, stats: Stats | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    error = False
    try:
        scores, total = _review_scores(str(run_dir.resolve()))
        above = total - int(np.searchsorted(scores, float(score), side="left"))
    except (duckdb.Error, OSError, ValueError):
        error = True
        return {"at_or_above": 0, "total": 0, "pct": 0.0}
    finally:
        if stats is not None:
            stats.record("duckdb.score_context", time.perf_counter() - started, error=error)
    pct = round(100.0 * above / total, 1) if total else 0.0
    return {"at_or_above": above, "total": total, "pct": pct}


class EngineSession:
    def __init__(
        self,
        repo: Any,
        data_dir: Path,
        loader: Callable,
        exact_mode: str = "hybrid",
        operational_store: OperationalStore | None = None,
        stats: Stats | None = None,
    ):
        if exact_mode not in EXACT_MODES:
            raise ValueError(f"exact_mode must be one of {EXACT_MODES}")
        self.repo, self.data_dir, self.loader = repo, data_dir, loader
        self._stats = stats
        self._resolver = (
            SnapshotResolver(repo.release_catalog, operational_store)
            if getattr(repo, "release_catalog", None) is not None
            else None
        )
        self._exact_mode = exact_mode
        self._engine_key: tuple[Path, Path] | None = None
        self._engine: Any | None = None
        self._engine_lock = Lock()
        self._search_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="entity-search")
        self._searchers: dict[tuple[Path, Path], EntitySearch] = {}
        self._search_futures: dict[tuple[Path, Path], Future] = {}

    def _snapshot(self, run_id: str):
        requested = run_id.strip()
        cached = request_snapshot_for(requested)
        if cached is not None:
            return cached
        if self._resolver is None:
            return None
        try:
            snapshot = self._resolver.resolve(requested)
        except ReleaseError as exc:
            raise HTTPException(409, "release_unavailable") from exc
        bind_snapshot(requested, snapshot)
        return snapshot

    def _key(self, run_id: str) -> tuple[Path, Path]:
        snapshot = self._snapshot(run_id)
        if snapshot is not None:
            return snapshot.model_dir.resolve(), snapshot.data_dir.resolve()
        return self.repo.resolve(run_id).resolve(), self.data_dir.resolve()

    def engine_ready(self, run_id: str) -> bool:
        key = self._key(run_id)
        return self._engine_key == key and self._engine is not None

    def set_exact_mode(self, mode: str) -> None:
        if mode not in EXACT_MODES:
            raise ValueError(f"exact_mode must be one of {EXACT_MODES}")
        with self._engine_lock:
            self._exact_mode = mode
            if self._engine is not None:
                setter = getattr(self._engine, "set_exact_mode", None)
                if setter is not None:
                    setter(mode)

    def reset_engine(self) -> None:
        """Drop the loaded engine + search caches (reload on next request)."""
        with self._engine_lock:
            engine = self._engine
            self._engine = None
            self._engine_key = None
            if engine is not None:
                close = getattr(engine, "close", None)
                if close is not None:
                    close()
            self._searchers.clear()
            for future in self._search_futures.values():
                future.cancel()
            self._search_futures.clear()

    def close(self) -> None:
        self.reset_engine()
        self._search_pool.shutdown(wait=False, cancel_futures=True)

    def search_entities(
        self, run_id: str, text: str, limit: int = 20
    ) -> list[dict[str, Any]] | None:
        """Semantic entity search over the loaded engine. Returns None when the
        engine is not ready (caller falls back to exact LIKE search) — a search
        must NEVER trigger a multi-minute model load."""
        key = self._key(run_id)
        with self._engine_lock:
            if self._engine_key != key or self._engine is None:
                return None
            searcher = self._searchers.get(key)
            if searcher is None:
                searcher = EntitySearch(self._engine)
                self._searchers[key] = searcher
            future = self._search_futures.get(key)
            if future is None:
                self._search_futures[key] = self._search_pool.submit(searcher.prepare)
                return None
            if not future.done():
                return None
            try:
                future.result()
            except (OSError, RuntimeError, ValueError):
                self._search_futures.pop(key, None)
                return None
            return searcher.search(text, top_k=limit)

    def _recommend(
        self,
        key: tuple[Path, Path],
        record: dict[str, str],
        stage: Callable,
        overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if not sufficient(record):
            return empty_result("INSUFFICIENT_INPUT", "insufficient_input")
        with self._engine_lock:
            if self._engine_key != key:
                stage("loading")
                old_engine = self._engine
                if old_engine is not None:
                    close = getattr(old_engine, "close", None)
                    if close is not None:
                        close()
                self._engine, self._engine_key = None, None
                try:
                    engine = self.loader(*key)
                    setter = getattr(engine, "set_exact_mode", None)
                    if setter is not None:
                        setter(self._exact_mode)
                except (RuntimeError, FileNotFoundError) as exc:
                    raise HTTPException(409, "model_unavailable") from exc
                self._engine, self._engine_key = engine, key
                stage("loading")  # re-stamp with the REAL load time (replaces the pre-load row)
            assert self._engine is not None
            result = self._engine.recommend(record, stage, overrides=overrides)
            if result.get("top_probability", 0) > 0 and result.get("candidates"):
                result["score_context"] = _score_context(
                    key[0], result["top_probability"], self._stats
                )
            return result

    def recommend(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        key = self._key(run_id)
        return self._recommend(key, record, lambda _: None, overrides=overrides)


class MatchingJobRunner:
    def __init__(
        self,
        session: EngineSession,
        job_store_path: Path | None = None,
        operational_store: OperationalStore | None = None,
    ):
        self.session = session
        self._jobs_lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="matching")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._heartbeat_events: dict[str, threading.Event] = {}
        repo_root = getattr(session.repo, "root", session.data_dir.parent)
        if operational_store is not None and job_store_path is not None:
            raise ValueError("provide operational_store or job_store_path, not both")
        self._job_store = SQLiteJobStore(
            operational_store
            or job_store_path
            or Path(repo_root).resolve() / "operational.sqlite3",
            "matching",
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

    def _persist(self, job: dict[str, Any]) -> None:
        version = int(job.get("_version", 0))
        persisted = dict(job)
        persisted["_version"] = version + 1
        persisted["_elapsed_ms"] = round((time.monotonic() - job["started"]) * 1000)
        new_version = self._job_store.save(persisted, action="recommend", expected_version=version)
        job["_version"] = new_version

    def start(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        key = self.session._key(run_id)
        now = time.monotonic()
        with self._jobs_lock:
            expired = [
                k
                for k, j in self._jobs.items()
                if now - j["updated"] > 1800 and j["stage"] in ("complete", "failed", "cancelled")
            ]
            for job_id in expired:
                self._jobs.pop(job_id)
                self._futures.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
                self._heartbeat_events.pop(job_id, None)
                self._job_store.delete(job_id)
            if len(self._jobs) >= 32:
                finished = next(
                    (
                        k
                        for k, j in self._jobs.items()
                        if j["stage"] in ("complete", "failed", "cancelled")
                    ),
                    None,
                )
                if finished is None:
                    raise HTTPException(429, "matching_busy")
                self._jobs.pop(finished)
                self._futures.pop(finished, None)
                self._cancel_events.pop(finished, None)
                self._heartbeat_events.pop(finished, None)
                self._job_store.delete(finished)
            job_id = uuid4().hex
            self._cancel_events[job_id] = threading.Event()
            self._heartbeat_events[job_id] = threading.Event()
            self._jobs[job_id] = {
                "id": job_id,
                "run_id": key[0].name,
                "stage": "queued",
                "started": now,
                "updated": now,
                "timeline": [{"stage": "queued", "elapsed_ms": 0}],
                "result": None,
                "error": None,
                "_action": "recommend",
            }
            self._persist(self._jobs[job_id])
            if sufficient(record):
                self._futures[job_id] = self._pool.submit(
                    self._execute, job_id, key, dict(record), dict(overrides) if overrides else None
                )
                threading.Thread(
                    target=self._heartbeat,
                    args=(job_id,),
                    name=f"matching-heartbeat-{job_id[:8]}",
                    daemon=True,
                ).start()
            else:
                self._jobs[job_id].update(
                    stage="complete",
                    result=empty_result("INSUFFICIENT_INPUT", "insufficient_input"),
                    timeline=[
                        {"stage": "validating", "elapsed_ms": 0},
                        {"stage": "complete", "elapsed_ms": 0},
                    ],
                )
                self._persist(self._jobs[job_id])
                future: Future = Future()
                future.set_result(None)
                self._futures[job_id] = future
        return self.status(job_id)

    def _update(self, job_id: str, stage: str, **values: Any) -> None:
        with self._jobs_lock:
            job = self._jobs[job_id]
            now = time.monotonic()
            elapsed = round((now - job["started"]) * 1000)
            job.update(stage=stage, updated=now, **values)
            # a stage reported again (e.g. loading stamped before a slow model
            # load) REPLACES its own previous timeline row, so the load time is
            # attributed to loading, not to the following stage
            if job["timeline"][-1]["stage"] == stage:
                job["timeline"][-1]["elapsed_ms"] = elapsed
            else:
                job["timeline"].append({"stage": stage, "elapsed_ms": elapsed})
            self._persist(job)
        LOGGER.info("matching job=%s stage=%s elapsed_ms=%s", job_id, stage, elapsed)

    def _heartbeat(self, job_id: str) -> None:
        stop = self._heartbeat_events[job_id]
        while not stop.wait(5.0):
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if job is None or job["stage"] in ("complete", "failed", "cancelled"):
                    return
                job["updated"] = time.monotonic()
                self._persist(job)

    def _check_cancelled(self, job_id: str) -> None:
        event = self._cancel_events[job_id]
        if event.is_set():
            raise JobCancelled
        with self._jobs_lock:
            started = float(self._jobs[job_id]["started"])
        if time.monotonic() - started > _JOB_DEADLINE_SECONDS:
            event.set()
            raise JobDeadlineExceeded

    def _execute(
        self,
        job_id: str,
        key: tuple[Path, Path],
        record: dict[str, str],
        overrides: dict[str, float] | None = None,
    ) -> None:
        try:
            self._check_cancelled(job_id)
            self._update(job_id, "validating")

            def stage(stage_name: str) -> None:
                self._check_cancelled(job_id)
                self._update(job_id, stage_name)

            result = self.session._recommend(key, record, stage, overrides=overrides)
            self._check_cancelled(job_id)
            self._update(job_id, "complete", result=result)
        except JobCancelled as exc:
            error = (
                "request_timeout" if isinstance(exc, JobDeadlineExceeded) else "request_cancelled"
            )
            LOGGER.info("matching job=%s terminal=%s", job_id, error)
            self._update(job_id, "cancelled", error=error)
        except Exception as exc:
            code = (
                "model_unavailable"
                if isinstance(exc, HTTPException) and exc.status_code == 409
                else "matching_failed"
            )
            LOGGER.warning("matching job=%s error_type=%s", job_id, type(exc).__name__)
            self._update(job_id, "failed", error=code)
        finally:
            self._heartbeat_events[job_id].set()

    @staticmethod
    def _score_context(run_dir: Path, score: float) -> dict[str, Any]:
        return _score_context(run_dir, score)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "matching_job_expired")
            if job["stage"] not in ("complete", "failed", "cancelled"):
                self._cancel_events[job_id].set()
                now = time.monotonic()
                elapsed = round((now - job["started"]) * 1000)
                job.update(stage="cancelled", updated=now, error="request_cancelled")
                job["timeline"].append({"stage": "cancelled", "elapsed_ms": elapsed})
                self._persist(job)
        return self.status(job_id)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if job_id not in self._jobs:
                raise HTTPException(404, "matching_job_expired")
            job = copy.deepcopy(self._jobs[job_id])
        stop = (
            job["updated"]
            if job["stage"] in ("complete", "failed", "cancelled")
            else time.monotonic()
        )
        job["elapsed_ms"] = round((stop - job.pop("started")) * 1000)
        job.pop("updated")
        job.pop("_action", None)
        job.pop("_elapsed_ms", None)
        job.pop("_version", None)
        return job

    def wait(self, job_id: str, timeout: float) -> None:
        future = self._futures.get(job_id)
        if future is None:
            if self.status(job_id)["stage"] in ("complete", "failed", "cancelled"):
                return
            raise HTTPException(503, "matching_worker_unavailable")
        future.result(timeout=timeout)

    def close(self) -> None:
        for event in self._cancel_events.values():
            event.set()
        for event in self._heartbeat_events.values():
            event.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.session.close()
