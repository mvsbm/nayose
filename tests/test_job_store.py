from api.admin_jobs import AdminJobRunner
from api.job_store import SQLiteJobStore


class Audit:
    def record(self, actor, action, detail):
        return {"actor": actor, "action": action, **detail}


def test_sqlite_job_store_round_trips_and_filters_namespace(tmp_path):
    path = tmp_path / "operational.sqlite3"
    admin = SQLiteJobStore(path, "admin")
    matching = SQLiteJobStore(path, "matching")
    job = {"id": "job-1", "stage": "complete", "result": {"ok": True}}

    admin.save(job, action="generate", idempotency_key="request-1")

    assert admin.list() == [job]
    assert admin.find_by_idempotency("request-1") == job
    assert matching.list() == []


def test_admin_jobs_mark_unfinished_work_as_lost_after_restart(tmp_path):
    path = tmp_path / "operational.sqlite3"
    store = SQLiteJobStore(path, "admin")
    store.save(
        {
            "id": "job-1",
            "run_id": "admin",
            "stage": "generating",
            "timeline": [{"stage": "generating", "elapsed_ms": 12}],
            "progress": None,
            "error": None,
            "result": None,
            "_elapsed_ms": 12,
        },
        action="generate",
    )

    runner = AdminJobRunner(tmp_path / "data", tmp_path / "models", Audit(), path)
    try:
        status = runner.status("job-1")
    finally:
        runner.close()

    assert status["stage"] == "failed"
    assert status["error"] == "worker_lost"
    assert status["timeline"][-1]["stage"] == "failed"


def test_admin_job_idempotency_returns_existing_job(tmp_path, monkeypatch):
    runner = AdminJobRunner(tmp_path / "data", tmp_path / "models", Audit())
    monkeypatch.setattr(runner, "_execute", lambda *args: None)
    try:
        first = runner.start("generate", {"idempotency_key": "same-request"}, "tester")
        second = runner.start("generate", {"idempotency_key": "same-request"}, "tester")
    finally:
        runner.close()

    assert second["id"] == first["id"]
