"""Release-boundary helpers for immutable data/model artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from api.operational_lock import advisory_file_lock
from api.operational_store import OperationalStore

_RUN_IDENTIFIER = re.compile(r"^run_[A-Za-z0-9][A-Za-z0-9._-]*$")
_GENERATION_IDENTIFIER = re.compile(r"^gen_[A-Za-z0-9][A-Za-z0-9._-]*$")
_RELEASE_IDENTIFIER = re.compile(r"^rel_[A-Za-z0-9][A-Za-z0-9._-]*$")


class ReleaseError(ValueError):
    """Raised when the current release pointer is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ReleaseSnapshot:
    """Immutable model/data selection validated as one serving unit."""

    release_id: str
    data_generation_id: str
    model_run_id: str
    data_fingerprint: str
    data_dir: Path
    model_dir: Path
    data_manifest_sha256: str
    model_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseStatus:
    data_fingerprint: str | None
    model_fingerprint: str | None
    data_target: str | None
    model_target: str | None
    release_id: str | None = None

    @property
    def compatible(self) -> bool:
        return bool(
            self.data_fingerprint
            and self.model_fingerprint
            and self.data_fingerprint == self.model_fingerprint
        )

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "compatible": self.compatible,
            "data_fingerprint": self.data_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "data_target": self.data_target,
            "model_target": self.model_target,
            "release_id": self.release_id,
        }


def safe_run_id(run_id: str) -> str:
    value = run_id.strip()
    if not _RUN_IDENTIFIER.fullmatch(value):
        raise ValueError("invalid run identifier")
    return value


def manifest_fingerprint(path: Path) -> str | None:
    manifest = path / "generation_manifest.json"
    if not manifest.exists():
        manifest = path / "run_manifest.json"
    if not manifest.exists():
        return None
    try:
        value = json.loads(manifest.read_text(encoding="utf-8")).get("data_fingerprint")
    except (OSError, json.JSONDecodeError):
        return None
    return str(value) if value else None


class ReleaseCatalog:
    """Read and validate the single authoritative current release pointer."""

    def __init__(self, data_root: Path, models_root: Path, pointer: Path | None = None):
        self.data_root = Path(data_root).resolve()
        self.models_root = Path(models_root).resolve()
        self.pointer = (pointer or self.data_root / "release.json").resolve()

    def current(self) -> ReleaseSnapshot | None:
        if not self.pointer.exists():
            return None
        try:
            payload = json.loads(self.pointer.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ReleaseError("release pointer must be an object")
            release_id = self._identifier(payload.get("release_id"), _RELEASE_IDENTIFIER)
            data_id = self._identifier(payload.get("data_generation_id"), _GENERATION_IDENTIFIER)
            model_id = self._identifier(payload.get("model_run_id"), _RUN_IDENTIFIER)
            snapshot = self.snapshot_for(data_id, model_id, release_id)
            if payload.get("data_fingerprint") != snapshot.data_fingerprint:
                raise ReleaseError("release pointer fingerprint does not match its artifacts")
            if payload.get("data_manifest_sha256") != snapshot.data_manifest_sha256:
                raise ReleaseError("release pointer data manifest hash is stale")
            if payload.get("model_manifest_sha256") != snapshot.model_manifest_sha256:
                raise ReleaseError("release pointer model manifest hash is stale")
            return snapshot
        except ReleaseError:
            raise
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ReleaseError("release pointer cannot be read") from exc

    def snapshot_for(
        self, data_generation_id: str, model_run_id: str, release_id: str
    ) -> ReleaseSnapshot:
        data_id = self._identifier(data_generation_id, _GENERATION_IDENTIFIER)
        model_id = self._identifier(model_run_id, _RUN_IDENTIFIER)
        selected_release_id = self._identifier(release_id, _RELEASE_IDENTIFIER)
        return self._snapshot(selected_release_id, data_id, model_id)

    def _snapshot(self, release_id: str, data_id: str, model_id: str) -> ReleaseSnapshot:
        data_dir = self._artifact(self.data_root / "generations", data_id)
        model_dir = self._artifact(self.models_root / "runs", model_id)
        data_manifest = data_dir / "generation_manifest.json"
        model_manifest = model_dir / "run_manifest.json"
        if not data_manifest.is_file() or not model_manifest.is_file():
            raise ReleaseError("release artifacts must contain their manifests")
        if not (data_dir / "reference_records.parquet").is_file():
            raise ReleaseError("release data generation has no reference records")
        data_fingerprint = manifest_fingerprint(data_dir)
        model_fingerprint = manifest_fingerprint(model_dir)
        if not data_fingerprint or not model_fingerprint:
            raise ReleaseError("release artifacts have no data fingerprint")
        if data_fingerprint != model_fingerprint:
            raise ReleaseError("release artifacts have different data fingerprints")
        return ReleaseSnapshot(
            release_id=release_id,
            data_generation_id=data_id,
            model_run_id=model_id,
            data_fingerprint=data_fingerprint,
            data_dir=data_dir,
            model_dir=model_dir,
            data_manifest_sha256=_sha256(data_manifest),
            model_manifest_sha256=_sha256(model_manifest),
        )

    @staticmethod
    def _identifier(value: object, pattern: re.Pattern[str]) -> str:
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ReleaseError("release pointer contains an invalid identifier")
        return value

    @staticmethod
    def _artifact(root: Path, identifier: str) -> Path:
        path = (root / identifier).resolve()
        if root.resolve() not in path.parents or not path.is_dir():
            raise ReleaseError(f"release artifact is unavailable: {identifier}")
        return path


class SnapshotResolver:
    """Resolve current or historical requests to one validated snapshot."""

    def __init__(self, catalog: ReleaseCatalog, operational_store: OperationalStore | None = None):
        self.catalog = catalog
        self.operational_store = operational_store

    def resolve(self, run_id: str = "") -> ReleaseSnapshot:
        requested = run_id.strip()
        if not requested:
            snapshot = self.catalog.current()
            if snapshot is None:
                raise ReleaseError("current release is unavailable")
            return snapshot
        safe_id = safe_run_id(requested)
        current = self.catalog.current()
        if current is not None and current.model_run_id == safe_id:
            return current
        model_dir = self.catalog._artifact(self.catalog.models_root / "runs", safe_id)
        fingerprint = manifest_fingerprint(model_dir)
        if not fingerprint:
            raise ReleaseError("historical model manifest has no data fingerprint")
        generation_id = self._historical_generation_id(safe_id, fingerprint)
        generation = (
            self.catalog.data_root / "generations" / generation_id
            if generation_id is not None
            else data_generation_for_fingerprint(self.catalog.data_root, fingerprint)
        )
        if generation is None:
            raise ReleaseError(f"no data generation matches model fingerprint {fingerprint[:12]}")
        return self.catalog.snapshot_for(
            generation.name,
            safe_id,
            f"rel_historical_{safe_id}",
        )

    def _historical_generation_id(self, run_id: str, fingerprint: str) -> str | None:
        if self.operational_store is None:
            return None
        with self.operational_store.read_connection() as connection:
            row = connection.execute(
                """
                SELECT data_generation_id
                FROM release_history
                WHERE model_run_id = ? AND data_fingerprint = ?
                ORDER BY published_at DESC
                LIMIT 1
                """,
                (run_id, fingerprint),
            ).fetchone()
        return str(row[0]) if row else None


class ReleasePublisher:
    """Validate and atomically publish one release selection."""

    def __init__(self, catalog: ReleaseCatalog, operational_store: OperationalStore | None = None):
        self.catalog = catalog
        self.operational_store = operational_store

    def publish(
        self,
        data_generation_id: str,
        model_run_id: str,
        release_id: str | None = None,
    ) -> ReleaseSnapshot:
        selected_release_id = release_id or self._new_release_id()
        snapshot = self.catalog.snapshot_for(data_generation_id, model_run_id, selected_release_id)
        payload = {
            "release_id": snapshot.release_id,
            "data_generation_id": snapshot.data_generation_id,
            "model_run_id": snapshot.model_run_id,
            "data_fingerprint": snapshot.data_fingerprint,
            "data_manifest_sha256": snapshot.data_manifest_sha256,
            "model_manifest_sha256": snapshot.model_manifest_sha256,
            "published_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with advisory_file_lock(self.catalog.pointer.with_suffix(".lock")):
            atomic_directory_alias(snapshot.data_dir, self.catalog.data_root / "current")
            atomic_directory_alias(snapshot.model_dir, self.catalog.models_root / "current")
            _atomic_json_write(self.catalog.pointer, payload)
            if self.operational_store is not None:
                self._record_history(snapshot, payload)
        return snapshot

    def _record_history(self, snapshot: ReleaseSnapshot, payload: dict[str, str]) -> None:
        assert self.operational_store is not None
        pointer_hash = _sha256(self.catalog.pointer)
        with self.operational_store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO release_history
                    (release_id, data_generation_id, model_run_id, data_fingerprint,
                     pointer_sha256, published_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(release_id) DO UPDATE SET
                    pointer_sha256=excluded.pointer_sha256,
                    published_at=excluded.published_at
                """,
                (
                    snapshot.release_id,
                    snapshot.data_generation_id,
                    snapshot.model_run_id,
                    snapshot.data_fingerprint,
                    pointer_hash,
                    payload["published_at_utc"],
                ),
            )

    @staticmethod
    def _new_release_id() -> str:
        return f"rel_{time.strftime('%Y%m%d%H%M%S', time.gmtime())}_{uuid4().hex[:12]}"


def release_status(data_root: Path, models_root: Path) -> ReleaseStatus:
    catalog = ReleaseCatalog(data_root, models_root)
    try:
        snapshot = catalog.current()
    except ReleaseError:
        return ReleaseStatus(None, None, None, None)
    if snapshot is None:
        return ReleaseStatus(None, None, None, None)
    return ReleaseStatus(
        data_fingerprint=snapshot.data_fingerprint,
        model_fingerprint=snapshot.data_fingerprint,
        data_target=snapshot.data_generation_id,
        model_target=snapshot.model_run_id,
        release_id=snapshot.release_id,
    )


def atomic_directory_alias(target: Path, alias: Path) -> None:
    """Replace a directory symlink without exposing a missing alias."""
    alias.parent.mkdir(parents=True, exist_ok=True)
    temporary = alias.with_name(f".{alias.name}.tmp.{os.getpid()}.{uuid4().hex}")
    try:
        temporary.symlink_to(os.path.relpath(target, alias.parent), target_is_directory=True)
        os.replace(temporary, alias)
        _fsync_directory(alias.parent)
    finally:
        temporary.unlink(missing_ok=True)


def data_generation_for_fingerprint(data_root: Path, fingerprint: str) -> Path | None:
    configured_root = Path(data_root)
    if (
        configured_root.name != "current"
        and (configured_root / "generation_manifest.json").is_file()
    ):
        direct = configured_root.resolve()
        return direct if manifest_fingerprint(direct) == fingerprint else None
    root = (
        configured_root.parent.resolve()
        if configured_root.name == "current"
        else configured_root.resolve()
    )
    candidates: list[Path] = []
    current = root / "current"
    if current.exists():
        candidates.append(current.resolve())
    generations = root / "generations"
    if generations.is_dir():
        candidates.extend(path for path in generations.iterdir() if path.is_dir())
    for candidate in candidates:
        if manifest_fingerprint(candidate) == fingerprint:
            return candidate
    return None


def validate_model_for_data(run_dir: Path, data_root: Path) -> str:
    model_fingerprint = manifest_fingerprint(run_dir)
    configured_root = Path(data_root)
    data_link = configured_root / "current"
    data_target = (
        configured_root.resolve()
        if (configured_root / "generation_manifest.json").is_file()
        else configured_root.resolve()
        if configured_root.name == "current"
        else data_link.resolve()
        if data_link.exists()
        else None
    )
    data_fingerprint = manifest_fingerprint(data_target) if data_target else None
    if not model_fingerprint:
        raise ValueError("model manifest has no data fingerprint")
    if not data_fingerprint:
        raise ValueError("current data manifest has no data fingerprint")
    if model_fingerprint != data_fingerprint:
        raise ValueError("model/data fingerprint mismatch")
    return model_fingerprint


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
