import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from api.cli import main


def test_cli_routes_nested_quality_commands_without_subprocess() -> None:
    with patch("api.cli.invoke", return_value=0) as invoke:
        assert main(["audit", "repo", "--strict"]) == 0

    invoke.assert_called_once_with("api.commands.quality", ["repo", "--strict"])


def test_cli_routes_lifecycle_commands_to_their_owner() -> None:
    with patch("api.cli.invoke", return_value=0) as invoke:
        assert main(["model", "train", "--seed", "7"]) == 0

    invoke.assert_called_once_with("api.commands.lifecycle", ["--seed", "7"])


def test_locked_f2llm_profile_freezes_mps_runtime_settings(tmp_path: Path) -> None:
    from api.commands.lifecycle import _env_for

    args = Namespace(
        data_dir=str(tmp_path / "data"),
        models_dir=str(tmp_path / "models"),
        seed=42,
        device="mps",
        pair_model="lightgbm",
        model_family="f2llm_330m",
        embedding_model=None,
        exact_mode="hybrid",
    )

    environment = _env_for(args)

    assert environment["NAYOSE_DEVICE"] == "mps"
    assert environment["NAYOSE_PAIR_MODEL"] == "lightgbm"
    assert environment["NAYOSE_EMBEDDING_MODEL"] == "codefuse-ai/F2LLM-v2-330M"
    assert environment["NAYOSE_NEURAL_BATCH"] == "32"
    assert environment["NAYOSE_PROCESS_WORKERS"] == "1"
    assert environment["NAYOSE_ALLOW_SVD_FALLBACK"] == "0"
    assert environment["PYTORCH_ENABLE_MPS_FALLBACK"] == "0"
    assert environment["NAYOSE_USE_DENSE"] == "1"


def test_locked_f2llm_profile_rejects_non_mps() -> None:
    from api.commands.lifecycle import _validate_locked_profile

    args = Namespace(model_family="f2llm_330m", device="cpu", embedding_model=None)

    with pytest.raises(SystemExit, match="locked to Apple MPS"):
        _validate_locked_profile(args)


def test_release_validate_uses_the_fingerprint_matching_generation(tmp_path, capsys) -> None:
    data_root = tmp_path / "data"
    generation = data_root / "generations" / "gen_a"
    generation.mkdir(parents=True)
    (generation / "reference_records.parquet").touch()
    (generation / "generation_manifest.json").write_text(
        json.dumps({"data_fingerprint": "fp-a"}), encoding="utf-8"
    )
    models_root = tmp_path / "models"
    run = models_root / "runs" / "run_a"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps({"data_fingerprint": "fp-a"}), encoding="utf-8"
    )

    assert (
        main(
            [
                "release",
                "validate",
                "--data-dir",
                str(data_root),
                "--models-dir",
                str(models_root),
                "--run-id",
                "run_a",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["valid"] is True
