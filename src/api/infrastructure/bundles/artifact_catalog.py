"""Protected-reference discovery and conservative artifact collection."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from api.infrastructure.persistence.audit import AuditLog
from api.infrastructure.persistence.operational_store import OperationalStore


@dataclass(frozen=True, slots=True)
class Artifact:
    kind: str
    identifier: str
    path: Path
    size_bytes: int
    protected_by: tuple[str, ...]
    tombstoned: bool

    @property
    def eligible(self) -> bool:
        return not self.protected_by and self.tombstoned

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "protected_by": list(self.protected_by),
            "tombstoned": self.tombstoned,
            "eligible": self.eligible,
        }


def inspect_artifacts(
    data_root: Path,
    models_root: Path,
    *,
    operational_db: Path | None = None,
    older_than_days: int = 30,
) -> tuple[Artifact, ...]:
    if older_than_days < 0:
        raise ValueError("older_than_days must not be negative")
    protected = _protected_references(data_root, models_root, operational_db)
    cutoff = time.time() - older_than_days * 86400
    artifacts: list[Artifact] = []
    for kind, root, manifest in (
        ("generation", data_root / "generations", "generation_manifest.json"),
        ("model", models_root / "runs", "run_manifest.json"),
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_dir() or not (path / manifest).is_file():
                continue
            if path.stat().st_mtime > cutoff:
                continue
            identifier = path.name
            reasons = tuple(sorted(protected.get((kind, identifier), ())))
            artifacts.append(
                Artifact(
                    kind,
                    identifier,
                    path,
                    _directory_size(path),
                    reasons,
                    (path / ".tombstone.json").is_file(),
                )
            )
    return tuple(artifacts)


def collect_artifacts(
    artifacts: tuple[Artifact, ...],
    *,
    operational_db: Path | None = None,
    actor: str = "storage-gc",
) -> tuple[Artifact, ...]:
    candidates = tuple(item for item in artifacts if not item.protected_by)
    for artifact in candidates:
        tombstone = artifact.path / ".tombstone.json"
        if not tombstone.exists():
            tombstone.write_text(
                json.dumps(
                    {
                        "kind": artifact.kind,
                        "identifier": artifact.identifier,
                        "tombstoned_at": time.time(),
                        "size_bytes": artifact.size_bytes,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            continue
        shutil.rmtree(artifact.path)
    if operational_db is not None and candidates:
        audit = AuditLog(OperationalStore(operational_db))
        audit.record(actor, "artifact_gc", {"artifacts": [item.as_dict() for item in candidates]})
    return candidates


def _protected_references(
    data_root: Path, models_root: Path, operational_db: Path | None
) -> dict[tuple[str, str], set[str]]:
    protected: dict[tuple[str, str], set[str]] = {}
    _protect_target(protected, "generation", data_root / "current", "current")
    _protect_target(protected, "model", models_root / "current", "current")
    pointer = data_root / "release.json"
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        _protect(protected, "generation", payload.get("data_generation_id"), "release")
        _protect(protected, "model", payload.get("model_run_id"), "release")
    except (OSError, TypeError, json.JSONDecodeError):
        pass
    registry = models_root / "registry.json"
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    _protect(protected, "model", entry.get("run_id"), "registry")
    except (OSError, TypeError, json.JSONDecodeError):
        pass
    if operational_db is not None and operational_db.is_file():
        try:
            import sqlite3

            with sqlite3.connect(operational_db) as connection:
                rows = connection.execute(
                    "SELECT data_generation_id, model_run_id FROM release_history"
                ).fetchall()
            for data_id, model_id in rows:
                _protect(protected, "generation", data_id, "release-history")
                _protect(protected, "model", model_id, "release-history")
        except sqlite3.Error:
            pass
    return protected


def _protect_target(
    protected: dict[tuple[str, str], set[str]], kind: str, target: Path, reason: str
) -> None:
    try:
        _protect(protected, kind, target.resolve().name, reason)
    except OSError:
        return


def _protect(
    protected: dict[tuple[str, str], set[str]], kind: str, identifier: object, reason: str
) -> None:
    if isinstance(identifier, str) and identifier:
        protected.setdefault((kind, identifier), set()).add(reason)


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
