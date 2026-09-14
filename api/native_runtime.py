"""Process-launch safeguards for native ML runtimes."""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Sequence
from pathlib import Path

_ALIGNMENT_MARKER = "NAYOSE_MACOS_RUNTIME_ALIGNED"


def _torch_library_directory() -> Path | None:
    spec = importlib.util.find_spec("torch")
    if spec is None or spec.submodule_search_locations is None:
        return None
    package = Path(next(iter(spec.submodule_search_locations)))
    library = package / "lib"
    return library if library.is_dir() else None


def reexec_with_aligned_macos_runtime(argv: Sequence[str], *, module: str = "api.cli") -> bool:
    """Re-exec a native-ML command with Torch's OpenMP runtime first.

    Returns ``False`` when no re-exec is needed. A successful ``execve`` does not
    return, and the marker prevents a re-exec loop in the child process.
    """
    if sys.platform != "darwin" or os.getenv(_ALIGNMENT_MARKER) == "1":
        return False
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"
    torch_library = _torch_library_directory()
    if torch_library is None:
        return False
    existing = os.getenv("DYLD_LIBRARY_PATH", "")
    paths = [str(torch_library)]
    if existing:
        paths.append(existing)
    environment = os.environ.copy()
    environment["DYLD_LIBRARY_PATH"] = os.pathsep.join(paths)
    environment["PYTHONUNBUFFERED"] = "1"
    environment[_ALIGNMENT_MARKER] = "1"
    os.execve(sys.executable, [sys.executable, "-m", module, *argv], environment)
    return True
