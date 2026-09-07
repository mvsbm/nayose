from pathlib import Path

from synthetic.config import SyntheticConfig
from synthetic.pipeline import build_manifest


def test_generation_manifest_uses_portable_paths_and_code_identity(tmp_path):
    config = SyntheticConfig(profile="smoke", output_dir=tmp_path / "data", raw_dir=tmp_path / "raw")

    manifest = build_manifest(config, {"parties": 1, "observations": 2, "queries": 3})

    assert not Path(manifest["config"]["output_dir"]).is_absolute()
    assert not Path(manifest["config"]["raw_dir"]).is_absolute()
    assert set(manifest["code"]) == {"git_commit", "pipeline_sha256"}
    assert len(manifest["code"]["pipeline_sha256"]) == 64
