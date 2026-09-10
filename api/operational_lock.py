"""Small local-filesystem primitives for operational writes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_fcntl: Any
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows fallback
    _fcntl = None


@contextmanager
def advisory_file_lock(path: Path, *, shared: bool = False) -> Iterator[None]:
    """Hold a cross-process advisory lock when the platform supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        if _fcntl is not None:
            mode = _fcntl.LOCK_SH if shared else _fcntl.LOCK_EX
            _fcntl.flock(stream.fileno(), mode)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(stream.fileno(), _fcntl.LOCK_UN)
