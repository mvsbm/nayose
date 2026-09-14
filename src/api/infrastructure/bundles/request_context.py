"""Request-scoped immutable release selection."""

from __future__ import annotations

from contextvars import ContextVar

from api.infrastructure.bundles.release import ReleaseSnapshot

_SNAPSHOTS: ContextVar[dict[str, ReleaseSnapshot] | None] = ContextVar(
    "nayose_release_snapshots", default=None
)


def snapshot_for(run_id: str) -> ReleaseSnapshot | None:
    snapshots = _SNAPSHOTS.get()
    return snapshots.get(run_id.strip()) if snapshots is not None else None


def bind_snapshot(run_id: str, snapshot: ReleaseSnapshot) -> None:
    snapshots = dict(_SNAPSHOTS.get() or {})
    snapshots[run_id.strip()] = snapshot
    _SNAPSHOTS.set(snapshots)


def clear_snapshot_context() -> None:
    _SNAPSHOTS.set(None)
