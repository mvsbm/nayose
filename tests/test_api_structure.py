from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_main_is_a_composition_root() -> None:
    source = (REPO / "api" / "main.py").read_text(encoding="utf-8")

    assert "create_app(APP_SERVICES, api_router)" in source
    assert "@router." not in source
    assert "def entities(" not in source
    assert "def recommend(" not in source


def test_cli_shared_artifact_work_is_not_script_to_script() -> None:
    materialize = (REPO / "api" / "commands" / "materialize.py").read_text(encoding="utf-8")
    train = (REPO / "api" / "commands" / "lifecycle.py").read_text(encoding="utf-8")

    assert "from api.commands.lifecycle" not in materialize
    assert "def materialize_runtime_artifacts" not in train
    assert "from api.bundle_materialization import materialize_runtime_artifacts" in train


def test_training_pipeline_is_a_compatibility_shim() -> None:
    source = (REPO / "api" / "training_pipeline.py").read_text(encoding="utf-8")

    assert len(source.splitlines()) <= 40
    assert "runpy" not in source
    assert "NAYOSE_PROMOTE" not in source


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
        "ports.py",
        "preflight.py",
        "stages.py",
        "worker.py",
    }

    assert expected <= {path.name for path in training.glob("*.py")}


def test_api_routes_are_split_by_capability() -> None:
    routes = REPO / "api" / "routers"
    expected = {
        "system.py",
        "search.py",
        "entities.py",
        "evaluation.py",
        "admin.py",
        "matching.py",
        "decisions.py",
        "debug.py",
    }

    assert expected <= {path.name for path in routes.glob("*.py")}
