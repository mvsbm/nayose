"""Cancellable subprocess adapter for administrative jobs."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.process.job_control import JobCancelled
from api.infrastructure.process.progress import parse as parse_progress
from api.infrastructure.runtime.resource_manager import ResourceManager
from nayose.config import load_config

ProgressCallback = Callable[[str, float, int, int, int], None]


class AdminProcessRunner:
    """Run one admin subprocess while forwarding progress and cancellation."""

    def __init__(self, cwd: Path, logger: logging.Logger, stats: Stats | None = None):
        self.cwd = cwd
        self.logger = logger
        self._stats = stats

    def run(
        self,
        argv: list[str],
        stage_label: str,
        job_id: str,
        env_overrides: Mapping[str, str],
        cancel: threading.Event,
        on_stage: Callable[[str], None],
        on_progress: ProgressCallback,
        register: Callable[[subprocess.Popen[str]], None],
        unregister: Callable[[], None],
        stream_progress: bool = False,
    ) -> None:
        started = time.perf_counter()
        try:
            self._run(
                argv,
                stage_label,
                job_id,
                env_overrides,
                cancel,
                on_stage,
                on_progress,
                register,
                unregister,
                stream_progress,
            )
        except BaseException:
            self._record("admin.subprocess", started, error=True)
            raise
        else:
            self._record("admin.subprocess", started)

    def _run(
        self,
        argv: list[str],
        stage_label: str,
        job_id: str,
        env_overrides: Mapping[str, str],
        cancel: threading.Event,
        on_stage: Callable[[str], None],
        on_progress: ProgressCallback,
        register: Callable[[subprocess.Popen[str]], None],
        unregister: Callable[[], None],
        stream_progress: bool,
    ) -> None:
        on_stage(stage_label)
        settings = load_config().performance
        manager = ResourceManager(self.cwd / ".nayose-runtime")
        plan = manager.plan(
            workload="admin-subprocess",
            requested_device="cpu",
            settings=settings,
        )
        requested_model_threads = env_overrides.get("NAYOSE_MODEL_THREADS")
        model_threads = plan.model_threads
        if requested_model_threads is not None:
            try:
                model_threads = min(model_threads, int(requested_model_threads))
            except ValueError as exc:
                raise ValueError("NAYOSE_MODEL_THREADS must be a positive integer") from exc
            if model_threads < 1:
                raise ValueError("NAYOSE_MODEL_THREADS must be positive")
        environment = {
            **os.environ,
            **plan.env(),
            "PYTHONUNBUFFERED": "1",
            **env_overrides,
            "NAYOSE_CPU_THREADS": str(plan.cpu_threads),
            "NAYOSE_MODEL_THREADS": str(model_threads),
        }
        process = subprocess.Popen(
            argv,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
        register(process)
        stream = process.stdout
        if stream is None:
            unregister()
            raise RuntimeError("admin subprocess did not expose stdout")
        thread = threading.Thread(
            target=self._pump,
            args=(stream, stream_progress, on_progress),
            name=f"admin-output-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        try:
            while thread.is_alive():
                thread.join(timeout=0.1)
                if cancel.is_set() and process.poll() is None:
                    process.terminate()
            try:
                process.wait(timeout=0.5 if cancel.is_set() else None)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if cancel.is_set():
                raise JobCancelled
            if process.returncode != 0:
                raise RuntimeError(f"{argv[0]} failed ({process.returncode})")
        finally:
            unregister()

    def _record(self, name: str, started: float, *, error: bool = False) -> None:
        if self._stats is not None:
            self._stats.record(name, time.perf_counter() - started, error=error)

    def _pump(
        self,
        stream: TextIO,
        stream_progress: bool,
        on_progress: ProgressCallback,
    ) -> None:
        last_pct: dict[str, float] = {}
        last_ts = 0.0
        for raw in stream:
            line = raw.rstrip("\n")
            if not stream_progress:
                self.logger.info("admin subprocess %s", line[:200])
                continue
            update = parse_progress(line)
            if update is None:
                continue
            now = time.monotonic()
            if last_pct.get(update.phase) == update.pct and now - last_ts <= 0.8:
                continue
            last_pct[update.phase] = update.pct
            last_ts = now
            on_progress(
                update.phase,
                update.pct,
                update.eta_seconds,
                update.done,
                update.total,
            )
