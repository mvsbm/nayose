"""Exclusive compute-session ownership and process-tree recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread
from typing import Any, Protocol
from uuid import uuid4

from api.infrastructure.runtime.storage_resources import WorkspaceLease

_STALE_SESSION_SECONDS = 120.0
_HEARTBEAT_SECONDS = 5.0


class ComputePlanView(Protocol):
    @property
    def workload(self) -> str: ...


class ProcessController(Protocol):
    def start_time(self, pid: int) -> float | None: ...

    def is_alive(self, pid: int) -> bool: ...

    def terminate_tree(self, pid: int) -> None: ...


class LocalProcessController:
    """Control only processes whose identity was recorded by a Nayose lease."""

    def __init__(self) -> None:
        try:
            import psutil  # type: ignore[import-untyped]
        except (ImportError, OSError):
            psutil = None
        self._psutil = psutil

    def start_time(self, pid: int) -> float | None:
        if self._psutil is not None:
            try:
                return float(self._psutil.Process(pid).create_time())
            except (self._psutil.Error, OSError, ValueError):
                return None
        return _proc_start_time(pid)

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def terminate_tree(self, pid: int) -> None:
        if self._psutil is None:
            for child_pid in [*_proc_children(pid), pid]:
                with suppress(OSError, ProcessLookupError):
                    os.kill(child_pid, 15)
            return
        try:
            process = self._psutil.Process(pid)
            children = process.children(recursive=True)
            for child in [*children, process]:
                with suppress(self._psutil.Error, OSError):
                    child.terminate()
            _, alive = self._psutil.wait_procs([*children, process], timeout=3)
            for child in alive:
                with suppress(self._psutil.Error, OSError):
                    child.kill()
        except (self._psutil.Error, OSError):
            return


class ComputeLease:
    def __init__(
        self, state_dir: Path, plan: ComputePlanView, process_controller: ProcessController
    ) -> None:
        self.state_dir = state_dir
        self.plan = plan
        self.process_controller = process_controller
        self.token = uuid4().hex
        self.path = state_dir / f"session-{self.token}.json"
        self.lock_path = state_dir / "compute.lock"
        self._lock_stream: Any = None
        self._lock_backend: str | None = None
        self._stop = Event()
        self._heartbeat: Thread | None = None
        self._processes: dict[int, float | None] = {}

    def __enter__(self) -> ComputeLease:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        try:
            self._acquire_lock()
        except RuntimeError:
            self._reap_stale()
            self._acquire_lock()
        self._reap_stale()
        self._processes[os.getpid()] = self.process_controller.start_time(os.getpid())
        self._write()
        self._heartbeat = Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is not None:
            self._terminate_registered_processes()
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=2.0)
        with suppress(FileNotFoundError):
            self.path.unlink()
        self._release_lock()

    def register_process(self, pid: int) -> None:
        self._processes[pid] = self.process_controller.start_time(pid)
        self._write()

    def _terminate_registered_processes(self) -> None:
        for pid, expected in self._processes.items():
            if pid == os.getpid() or expected is None or not self.process_controller.is_alive(pid):
                continue
            if self.process_controller.start_time(pid) == expected:
                self.process_controller.terminate_tree(pid)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_HEARTBEAT_SECONDS):
            self._write()

    def _write(self) -> None:
        payload = {
            "token": self.token,
            "pid": os.getpid(),
            "uid": getattr(os, "getuid", lambda: -1)(),
            "workload": self.plan.workload,
            "heartbeat": time.time(),
            "processes": [
                {"pid": pid, "start_time": start_time}
                for pid, start_time in self._processes.items()
            ],
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.path)

    def _reap_stale(self) -> None:
        now = time.time()
        for path in self.state_dir.glob("session-*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if now - float(payload.get("heartbeat", now)) < _STALE_SESSION_SECONDS:
                    continue
                if int(payload.get("uid", -1)) != getattr(os, "getuid", lambda: -2)():
                    continue
                for item in payload.get("processes", []):
                    pid = int(item.get("pid", -1))
                    expected = item.get("start_time")
                    actual = self.process_controller.start_time(pid)
                    owned_process = (
                        pid > 0
                        and actual is not None
                        and expected is not None
                        and actual == expected
                    )
                    if owned_process:
                        self.process_controller.terminate_tree(pid)
                path.unlink(missing_ok=True)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue

    def _acquire_lock(self) -> None:
        if os.name == "nt":
            msvcrt_module: Any = None
            with suppress(ImportError):
                msvcrt_module = __import__("msvcrt")
            if msvcrt_module is not None:
                self._lock_stream = self.lock_path.open("a+", encoding="utf-8")
                self._lock_stream.seek(0)
                self._lock_stream.write(" ")
                self._lock_stream.flush()
                try:
                    msvcrt_module.locking(self._lock_stream.fileno(), msvcrt_module.LK_NBLCK, 1)
                except OSError as exc:
                    self._lock_stream.close()
                    raise RuntimeError("another Nayose compute session is active") from exc
                self._lock_backend = "msvcrt"
                return
        try:
            import fcntl
        except ImportError:
            try:
                self._lock_stream = self.lock_path.open("x", encoding="utf-8")
            except FileExistsError as exc:
                raise RuntimeError("another Nayose compute session is active") from exc
            self._lock_backend = "file"
            return
        self._lock_stream = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_stream.close()
            raise RuntimeError("another Nayose compute session is active") from exc
        self._lock_backend = "fcntl"

    def _release_lock(self) -> None:
        if self._lock_stream is None:
            return
        if self._lock_backend == "msvcrt":
            msvcrt_module: Any = __import__("msvcrt")
            self._lock_stream.seek(0)
            msvcrt_module.locking(self._lock_stream.fileno(), msvcrt_module.LK_UNLCK, 1)
        elif self._lock_backend == "fcntl":
            import fcntl

            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_UN)
        self._lock_stream.close()
        if self._lock_backend in {"file", "msvcrt"}:
            with suppress(FileNotFoundError):
                self.lock_path.unlink()
        self._lock_stream = None
        self._lock_backend = None


class ResourceLease:
    """Compose compute admission with an owned ephemeral workspace."""

    def __init__(self, compute: ComputeLease, workspace: WorkspaceLease) -> None:
        self.compute = compute
        self._workspace = workspace

    @property
    def workspace(self) -> Path:
        return self._workspace.path

    def __enter__(self) -> ResourceLease:
        self.compute.__enter__()
        try:
            self._workspace.__enter__()
        except BaseException:
            self.compute.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self._workspace.__exit__(exc_type, exc, traceback)
        finally:
            self.compute.__exit__(exc_type, exc, traceback)

    def register_process(self, pid: int) -> None:
        self.compute.register_process(pid)


def _proc_stat(pid: int) -> list[str] | None:
    if sys.platform != "linux":
        return None
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return text.rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None


def _proc_start_time(pid: int) -> float | None:
    fields = _proc_stat(pid)
    if fields is not None and len(fields) > 19:
        try:
            return float(fields[19])
        except ValueError:
            return None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart="],
                capture_output=True,
                check=True,
                text=True,
                timeout=1.0,
            )
            return time.mktime(time.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y"))
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    return None


def _proc_children(pid: int) -> list[int]:
    if sys.platform != "linux":
        return []
    children: list[int] = []
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            child_pid = int(path.parent.name)
        except ValueError:
            continue
        fields = _proc_stat(child_pid)
        if fields is None or len(fields) <= 1:
            continue
        try:
            if int(fields[1]) == pid:
                children.append(child_pid)
        except ValueError:
            continue
    descendants = list(children)
    for child_pid in children:
        descendants.extend(_proc_children(child_pid))
    return descendants
