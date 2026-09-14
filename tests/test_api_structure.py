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
