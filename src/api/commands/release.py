"""Fingerprint-bound release validation and publication commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from api.infrastructure.bundles.release import (
    ReleaseCatalog,
    ReleaseError,
    ReleasePublisher,
    data_generation_for_fingerprint,
    manifest_fingerprint,
    release_status,
    safe_run_id,
    validate_model_for_data,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "validate", "promote"))
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--models-dir", default="models")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--data-generation", default="")
    parser.add_argument("--release-id", default="")
    return parser


def _root(path: str) -> Path:
    candidate = Path(path)
    if (candidate / "generation_manifest.json").is_file() or (
        candidate / "run_manifest.json"
    ).is_file():
        return candidate.parent.parent
    if candidate.name == "current":
        return candidate.parent
    return candidate


def _selected_ids(
    data_root: Path, models_root: Path, run_id: str, generation_id: str
) -> tuple[str, str]:
    model_path = (
        models_root / "runs" / safe_run_id(run_id)
        if run_id
        else (models_root / "current").resolve()
    )
    if not model_path.is_dir():
        raise ReleaseError("model run is unavailable")
    selected_model = model_path.name
    fingerprint = manifest_fingerprint(model_path)
    if not fingerprint:
        raise ReleaseError("model manifest has no data fingerprint")
    if generation_id:
        selected_generation = Path(generation_id).name
    else:
        generation = data_generation_for_fingerprint(data_root, fingerprint)
        if generation is None:
            raise ReleaseError(f"no data generation matches model fingerprint {fingerprint[:12]}")
        selected_generation = generation.name
    return selected_generation, selected_model


def main() -> int:
    args = _parser().parse_args()
    data_root = _root(args.data_dir)
    models_root = _root(args.models_dir)
    catalog = ReleaseCatalog(data_root, models_root)

    if args.action == "status":
        print(
            json.dumps(release_status(data_root, models_root).as_dict(), indent=2, sort_keys=True)
        )
        return 0

    if args.action == "validate":
        generation_id, model_id = _selected_ids(
            data_root, models_root, args.run_id, args.data_generation
        )
        model_dir = models_root / "runs" / model_id
        generation_dir = data_root / "generations" / generation_id
        fingerprint = validate_model_for_data(model_dir, generation_dir)
        snapshot = catalog.snapshot_for(generation_id, model_id, args.release_id or "rel_validate")
        print(
            json.dumps(
                {
                    "valid": True,
                    "release_id": snapshot.release_id,
                    "data_generation_id": snapshot.data_generation_id,
                    "model_run_id": snapshot.model_run_id,
                    "data_fingerprint": fingerprint,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    generation_id, model_id = _selected_ids(
        data_root, models_root, args.run_id, args.data_generation
    )
    snapshot = ReleasePublisher(catalog).publish(generation_id, model_id, args.release_id or None)
    print(
        json.dumps(
            {
                "published": True,
                "release_id": snapshot.release_id,
                "data_generation_id": snapshot.data_generation_id,
                "model_run_id": snapshot.model_run_id,
                "data_fingerprint": snapshot.data_fingerprint,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
