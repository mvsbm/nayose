import sqlite3

import pytest

from api.admin_jobs import AdminJobRunner
from api.job_store import JobConflict, SQLiteJobStore


class Audit:
    def record(self, actor, action, detail, connection=None):
        return {"actor": actor, "action": action, **detail}


def test_sqlite_job_store_round_trips_and_filters_namespace(tmp_path):
    path = tmp_path / "operational.sqlite3"
    admin = SQLiteJobStore(path, "admin")
    matching = SQLiteJobStore(path, "matching")
    job = {"id": "job-1", "stage": "complete", "result": {"ok": True}}

    admin.save(job, action="generate", idempotency_key="request-1")

    assert admin.list() == [job]
    assert admin.version("job-1") == 1
    with pytest.raises(JobConflict):
        admin.save(job, action="generate", expected_version=0)
    assert admin.find_by_idempotency("request-1") == job
    assert matching.list() == []
    with pytest.raises(sqlite3.IntegrityError):
        admin.save(
            {"id": "job-2", "stage": "queued"}, action="generate", idempotency_key="request-1"
        )


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


def test_admin_job_cancellation_is_persisted(tmp_path, monkeypatch):
    from threading import Event

    started, release = Event(), Event()
    runner = AdminJobRunner(tmp_path / "data", tmp_path / "models", Audit())

    def run_generate(_params, _job_id):
        started.set()
        assert release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(runner, "_run_generate", run_generate)
    try:
        job = runner.start("generate", {}, "tester")
        assert started.wait(5)
        status = runner.cancel(job["id"], "tester")
        assert status["stage"] == "cancelled"
        release.set()
        runner.wait(job["id"], timeout=5)
        assert runner.status(job["id"])["stage"] == "cancelled"
        assert runner.status(job["id"])["error"] == "request_cancelled"
    finally:
        release.set()
        runner.close()


def test_admin_job_idempotency_returns_existing_job(tmp_path, monkeypatch):
    runner = AdminJobRunner(tmp_path / "data", tmp_path / "models", Audit())
    monkeypatch.setattr(runner, "_execute", lambda *args: None)
    try:
        first = runner.start("generate", {"idempotency_key": "same-request"}, "tester")
        second = runner.start("generate", {"idempotency_key": "same-request"}, "tester")
    finally:
        runner.close()

    assert second["id"] == first["id"]
