"""Release-boundary helpers for immutable data/model aliases."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_IDENTIFIER = re.compile(r"^run_[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class ReleaseStatus:
    data_fingerprint: str | None
    model_fingerprint: str | None
    data_target: str | None
    model_target: str | None

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
        }


def safe_run_id(run_id: str) -> str:
    value = run_id.strip()
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid run identifier")
    return value


def _manifest_fingerprint(path: Path) -> str | None:
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


def release_status(data_root: Path, models_root: Path) -> ReleaseStatus:
    data_link = Path(data_root) / "current"
    model_link = Path(models_root) / "current"
    data_target = data_link.resolve() if data_link.exists() else None
    model_target = model_link.resolve() if model_link.exists() else None
    return ReleaseStatus(
        data_fingerprint=_manifest_fingerprint(data_target) if data_target else None,
        model_fingerprint=_manifest_fingerprint(model_target) if model_target else None,
        data_target=data_target.name if data_target else None,
        model_target=model_target.name if model_target else None,
    )


def atomic_directory_alias(target: Path, alias: Path) -> None:
    """Replace a directory symlink without exposing a missing alias."""
    alias.parent.mkdir(parents=True, exist_ok=True)
    temporary = alias.with_name(f".{alias.name}.tmp.{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(os.path.relpath(target, alias.parent), target_is_directory=True)
    os.replace(temporary, alias)


def data_generation_for_fingerprint(data_root: Path, fingerprint: str) -> Path | None:
    configured_root = Path(data_root)
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
        if _manifest_fingerprint(candidate) == fingerprint:
            return candidate
    return None


def validate_model_for_data(run_dir: Path, data_root: Path) -> str:
    model_fingerprint = _manifest_fingerprint(run_dir)
    data_link = data_root / "current"
    data_target = data_link.resolve() if data_link.exists() else None
    data_fingerprint = _manifest_fingerprint(data_target) if data_target else None
    if not model_fingerprint:
        raise ValueError("model manifest has no data fingerprint")
    if not data_fingerprint:
        raise ValueError("current data manifest has no data fingerprint")
    if model_fingerprint != data_fingerprint:
        raise ValueError("model/data fingerprint mismatch")
    return model_fingerprint
