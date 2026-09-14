#!/usr/bin/env python3
"""Run one command and measure its complete descendant process tree."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from api.performance import available_cpu_count, cpu_thread_budget, model_thread_budget

try:
    import psutil  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal environments
    psutil = None


@dataclass(slots=True)
class ProcessUsage:
    user_cpu_seconds: float
    system_cpu_seconds: float
    rss_bytes: int
    read_bytes: int
    write_bytes: int
    threads: int


class ProcessTreeMeter:
    """Poll a process tree and retain cumulative usage for exited descendants."""

    def __init__(self, root_pid: int):
        self.root_pid = root_pid
        self._seen: dict[tuple[int, float], ProcessUsage] = {}
        self._peak_rss_bytes = 0
        self._peak_threads = 0
        self._io_supported = True
        self._lock = Lock()

    def sample(self) -> None:
        if psutil is None:
            return
        try:
            root = psutil.Process(self.root_pid)
            processes = [root, *root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            processes = []
        current_rss = 0
        current_threads = 0
        with self._lock:
            for process in processes:
                try:
                    with process.oneshot():
                        identity = (process.pid, process.create_time())
                        cpu = process.cpu_times()
                        memory = process.memory_info()
                        io_reader = getattr(process, "io_counters", None)
                        try:
                            io = io_reader() if io_reader is not None else None
                        except (AttributeError, NotImplementedError, OSError, psutil.Error):
                            io = None
                            self._io_supported = False
                        usage = ProcessUsage(
                            user_cpu_seconds=float(cpu.user),
                            system_cpu_seconds=float(cpu.system),
                            rss_bytes=int(memory.rss),
                            read_bytes=int(io.read_bytes) if io is not None else 0,
                            write_bytes=int(io.write_bytes) if io is not None else 0,
                            threads=int(process.num_threads()),
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                self._seen[identity] = usage
                current_rss += usage.rss_bytes
                current_threads += usage.threads
            self._peak_rss_bytes = max(self._peak_rss_bytes, current_rss)
            self._peak_threads = max(self._peak_threads, current_threads)

    def summary(self) -> dict[str, int | float]:
        with self._lock:
            usages = list(self._seen.values())
        return {
            "process_count": len(usages),
            "peak_rss_bytes": self._peak_rss_bytes,
            "peak_threads": self._peak_threads,
            "user_cpu_ms": round(sum(item.user_cpu_seconds for item in usages) * 1000, 3),
            "system_cpu_ms": round(sum(item.system_cpu_seconds for item in usages) * 1000, 3),
            "read_bytes": sum(item.read_bytes for item in usages),
            "write_bytes": sum(item.write_bytes for item in usages),
            "io_supported": self._io_supported,
            "process_metrics_supported": psutil is not None and bool(usages),
        }


def cgroup_snapshot() -> dict[str, int] | None:
    """Read Linux cgroup v2 counters when the runner exposes them."""
    if platform.system() != "Linux":
        return None
    try:
        relative = next(
            line.split(":", 2)[2].strip()
            for line in Path("/proc/self/cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
        root = Path("/sys/fs/cgroup") / relative.lstrip("/")
        values: dict[str, int] = {}
        for filename, key in (("memory.peak", "memory_peak_bytes"), ("pids.peak", "pids_peak")):
            path = root / filename
            if path.exists():
                values[key] = int(path.read_text().strip())
        cpu_stat = root / "cpu.stat"
        if cpu_stat.exists():
            for line in cpu_stat.read_text().splitlines():
                name, _, value = line.partition(" ")
                if name == "usage_usec":
                    values["cpu_usage_usec"] = int(value)
        return values or None
    except (OSError, StopIteration, ValueError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="command")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def normalize_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("a command is required after --")
    if command[0] in {"python", "python3"}:
        return [sys.executable, *command[1:]]
    return command


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    command = normalize_command(args.command)
    cgroup_before = cgroup_snapshot()
    started = time.perf_counter()
    process = subprocess.Popen(command)
    meter = ProcessTreeMeter(process.pid)
    meter.sample()
    while process.poll() is None:
        meter.sample()
        time.sleep(0.05)
    returncode = process.wait()
    meter.sample()
    cgroup_after = cgroup_snapshot()
    cgroup_metrics = None
    if cgroup_before is not None and cgroup_after is not None:
        cgroup_metrics = {
            "memory_peak_bytes": cgroup_after.get("memory_peak_bytes", 0),
            "pids_peak": cgroup_after.get("pids_peak", 0),
            "cpu_usage_usec": max(
                0, cgroup_after.get("cpu_usage_usec", 0) - cgroup_before.get("cpu_usage_usec", 0)
            ),
        }
    payload = {
        "schema_version": 1,
        "kind": "command_resource",
        "label": args.label,
        "command": command,
        "cwd": str(Path.cwd()),
        "returncode": returncode,
        "wall_ms": round((time.perf_counter() - started) * 1000, 3),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "available_cpus": os.cpu_count() or 1,
        },
        "resource": meter.summary(),
        "cpu_policy": {
            "available_cpus": available_cpu_count(),
            "cpu_threads_per_process": cpu_thread_budget(),
            "model_threads_per_process": model_thread_budget(),
        },
        "cgroup": cgroup_metrics,
    }
    write_json(args.output, payload)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
