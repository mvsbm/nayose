from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_main_is_a_thin_process_entrypoint() -> None:
    source = (REPO / "api" / "main.py").read_text(encoding="utf-8")

    assert "from api.composition import build_application" in source
    assert "create_app(APP_SERVICES, api_router)" in source
    assert "def build_application(" not in source
    assert "@router." not in source
    assert "def entities(" not in source
    assert "def recommend(" not in source


def test_api_root_contains_only_entrypoint_modules() -> None:
    actual = {path.name for path in (REPO / "api").glob("*.py")}
    assert actual == {
        "__init__.py",
        "__main__.py",
        "cli.py",
        "composition.py",
        "main.py",
        "runtime_config.py",
    }


def test_api_architecture_packages_are_explicit() -> None:
    api = REPO / "api"
    expected = {
        "application",
        "commands",
        "domain",
        "http",
        "infrastructure",
        "training",
    }
    assert expected <= {path.name for path in api.iterdir() if path.is_dir()}


def test_cli_shared_artifact_work_is_not_script_to_script() -> None:
    materialize = (REPO / "api" / "commands" / "materialize.py").read_text(encoding="utf-8")
    train = (REPO / "api" / "commands" / "lifecycle.py").read_text(encoding="utf-8")

    assert "from api.commands.lifecycle" not in materialize
    assert "def materialize_runtime_artifacts" not in train
    assert (
        "from api.infrastructure.bundles.materialization import materialize_runtime_artifacts"
        in train
    )


def test_training_has_one_canonical_worker_entrypoint() -> None:
    assert not (REPO / "api" / "training_pipeline.py").exists()
    assert not (REPO / "api" / "training" / "ports.py").exists()
    assert (REPO / "api" / "training" / "worker.py").is_file()


def test_training_application_is_split_into_stages() -> None:
    training = REPO / "api" / "training"
    expected = {
        "application.py",
        "artifacts.py",
        "clustering.py",
        "config.py",
        "contracts.py",
        "data.py",
        "entity_training.py",
        "evaluation.py",
        "indexing.py",
        "pair_training.py",
        "preflight.py",
        "stages.py",
        "worker.py",
    }

    assert expected <= {path.name for path in training.glob("*.py")}


def test_api_routes_are_split_by_capability() -> None:
    routes = REPO / "api" / "http" / "routers"
    expected = {
        "system.py",
        "entities.py",
        "evaluation.py",
        "admin.py",
        "data_entry.py",
        "matching.py",
        "decisions.py",
        "debug.py",
    }

    actual = {path.name for path in routes.glob("*.py")}
    assert expected <= actual
    assert "search.py" not in actual
