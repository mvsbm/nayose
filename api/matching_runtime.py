"""Runtime state and bounded job execution for matching."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import duckdb
from fastapi import HTTPException

from api.evidence import sufficient
from api.matching_components import empty_result
from api.search import EntitySearch

LOGGER = logging.getLogger(__name__)


def _score_context(run_dir: Path, score: float) -> dict[str, Any]:
    connection = duckdb.connect()
    try:
        row = connection.execute(
            f"SELECT count(*), count(*) FILTER (WHERE pair_probability >= {float(score)}) "
            f"FROM read_parquet('{run_dir}/review_queue.parquet')"
        ).fetchone()
        if row is None:
            return {"at_or_above": 0, "total": 0, "pct": 0.0}
        total, above = int(row[0]), int(row[1])
    except (duckdb.Error, OSError, ValueError):
        return {"at_or_above": 0, "total": 0, "pct": 0.0}
    finally:
        connection.close()
    pct = round(100.0 * above / total, 1) if total else 0.0
    return {"at_or_above": above, "total": total, "pct": pct}


class EngineSession:
    def __init__(self, repo: Any, data_dir: Path, loader: Callable):
        self.repo, self.data_dir, self.loader = repo, data_dir, loader
        self._engine_key: tuple[Path, Path] | None = None
        self._engine: Any | None = None
        self._engine_lock = Lock()
        self._search_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="entity-search")
        self._searchers: dict[tuple[Path, Path], EntitySearch] = {}
        self._search_futures: dict[tuple[Path, Path], Future] = {}

    def engine_ready(self, run_id: str) -> bool:
        key = self.repo.resolve(run_id).resolve(), self.data_dir.resolve()
        return self._engine_key == key and self._engine is not None

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
        key = self.repo.resolve(run_id).resolve(), self.data_dir.resolve()
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
                except (RuntimeError, FileNotFoundError) as exc:
                    raise HTTPException(409, "model_unavailable") from exc
                self._engine, self._engine_key = engine, key
                stage("loading")  # re-stamp with the REAL load time (replaces the pre-load row)
            assert self._engine is not None
            result = self._engine.recommend(record, stage, overrides=overrides)
            if result.get("top_probability", 0) > 0 and result.get("candidates"):
                result["score_context"] = _score_context(key[0], result["top_probability"])
            return result

    def recommend(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        key = self.repo.resolve(run_id).resolve(), self.data_dir.resolve()
        return self._recommend(key, record, lambda _: None, overrides=overrides)


class MatchingJobRunner:
    def __init__(self, session: EngineSession):
        self.session = session
        self._jobs_lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="matching")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}

    def start(
        self, run_id: str, record: dict[str, str], overrides: dict[str, float] | None = None
    ) -> dict[str, Any]:
        key = self.session.repo.resolve(run_id).resolve(), self.session.data_dir.resolve()
        now = time.monotonic()
        with self._jobs_lock:
            expired = [
                k
                for k, j in self._jobs.items()
                if now - j["updated"] > 1800 and j["stage"] in ("complete", "failed")
            ]
            for job_id in expired:
                self._jobs.pop(job_id)
                self._futures.pop(job_id, None)
            if len(self._jobs) >= 32:
                finished = next(
                    (k for k, j in self._jobs.items() if j["stage"] in ("complete", "failed")), None
                )
                if finished is None:
                    raise HTTPException(429, "matching_busy")
                self._jobs.pop(finished)
                self._futures.pop(finished, None)
            job_id = uuid4().hex
            self._jobs[job_id] = {
                "id": job_id,
                "run_id": key[0].name,
                "stage": "queued",
                "started": now,
                "updated": now,
                "timeline": [{"stage": "queued", "elapsed_ms": 0}],
                "result": None,
                "error": None,
            }
            if sufficient(record):
                self._futures[job_id] = self._pool.submit(
                    self._execute, job_id, key, dict(record), dict(overrides) if overrides else None
                )
            else:
                self._jobs[job_id].update(
                    stage="complete",
                    result=empty_result("INSUFFICIENT_INPUT", "insufficient_input"),
                    timeline=[
                        {"stage": "validating", "elapsed_ms": 0},
                        {"stage": "complete", "elapsed_ms": 0},
                    ],
                )
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
        LOGGER.info("matching job=%s stage=%s elapsed_ms=%s", job_id, stage, elapsed)

    def _execute(
        self,
        job_id: str,
        key: tuple[Path, Path],
        record: dict[str, str],
        overrides: dict[str, float] | None = None,
    ) -> None:
        try:
            self._update(job_id, "validating")
            result = self.session._recommend(
                key, record, lambda stage: self._update(job_id, stage), overrides=overrides
            )
            self._update(job_id, "complete", result=result)
        except Exception as exc:
            code = (
                "model_unavailable"
                if isinstance(exc, HTTPException) and exc.status_code == 409
                else "matching_failed"
            )
            LOGGER.warning("matching job=%s error_type=%s", job_id, type(exc).__name__)
            self._update(job_id, "failed", error=code)

    @staticmethod
    def _score_context(run_dir: Path, score: float) -> dict[str, Any]:
        return _score_context(run_dir, score)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if job_id not in self._jobs:
                raise HTTPException(404, "matching_job_expired")
            job = copy.deepcopy(self._jobs[job_id])
        stop = job["updated"] if job["stage"] in ("complete", "failed") else time.monotonic()
        job["elapsed_ms"] = round((stop - job.pop("started")) * 1000)
        job.pop("updated")
        return job

    def wait(self, job_id: str, timeout: float) -> None:
        self._futures[job_id].result(timeout=timeout)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.session.close()
