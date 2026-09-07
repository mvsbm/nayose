import json
from argparse import Namespace
from pathlib import Path

from scripts.train import REQUIRED_BUNDLE_TABLES, promote_bundle, verify_bundle


def _args(data_root: Path, models_root: Path) -> Namespace:
    return Namespace(
        data_dir=str(data_root),
        models_dir=str(models_root),
        prune_aborted=True,
    )


def test_verify_ignores_aborted_newer_run_and_promotes_verified_bundle(tmp_path):
    data_root = tmp_path / "data"
    generation = data_root / "generations" / "gen_a"
    generation.mkdir(parents=True)
    fingerprint = "a" * 64
    (generation / "generation_manifest.json").write_text(
        json.dumps({"data_fingerprint": fingerprint}), encoding="utf-8"
    )
    (data_root / "current").symlink_to(Path("generations/gen_a"), target_is_directory=True)

    models_root = tmp_path / "models"
    run = models_root / "runs" / "run_a"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "data_fingerprint": fingerprint,
                "created_at_utc": "2026-01-01T00:00:00Z",
                "config_hash": "config",
                "config": {"model_family": "off"},
                "device": "cpu",
                "backends": {},
            }
        ),
        encoding="utf-8",
    )
    for table in REQUIRED_BUNDLE_TABLES:
        (run / f"{table}.parquet").touch()
    aborted = models_root / "runs" / "run_aborted"
    aborted.mkdir(parents=True)

    args = _args(data_root, models_root)
    verified = verify_bundle(args)
    assert verified == run
    promote_bundle(args, verified)

    assert (models_root / "current").resolve() == run
    registry = json.loads((models_root / "registry.json").read_text(encoding="utf-8"))
    assert registry[-1]["run_id"] == "run_a"
    assert registry[-1]["data_fingerprint"] == fingerprint
    assert not aborted.exists()
