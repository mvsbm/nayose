import sqlite3

import pytest

from api.infrastructure.observability.telemetry import Stats
from api.infrastructure.persistence.audit import AuditLog
from api.infrastructure.persistence.operational_store import OperationalStore


def test_operational_store_uses_one_transactional_schema(tmp_path):
    store = OperationalStore(tmp_path / "operational.sqlite3")
    assert store.schema_version == 4

    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO manual_records(record_id, payload_json, created_at) VALUES (?, ?, ?)",
            ("M1", "{}", 1.0),
        )

    with store.read_connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "manual_records" in tables
        assert connection.execute("SELECT count(*) FROM manual_records").fetchone()[0] == 1


def test_operational_store_records_sqlite_boundary_metrics(tmp_path):
    stats = Stats()
    store = OperationalStore(tmp_path / "operational.sqlite3", stats=stats)

    _ = store.schema_version
    with store.transaction() as connection:
        connection.execute("SELECT 1")

    rows = {row["endpoint"]: row for row in stats.snapshot()}
    assert rows["sqlite.read"]["hits"] >= 1
    assert rows["sqlite.transaction"]["hits"] == 1


def test_operational_store_rolls_back_failed_transactions(tmp_path):
    store = OperationalStore(tmp_path / "operational.sqlite3")

    with pytest.raises(sqlite3.IntegrityError), store.transaction() as connection:
        connection.execute(
            "INSERT INTO manual_records(record_id, payload_json, created_at) VALUES (?, ?, ?)",
            ("M1", "{}", 1.0),
        )
        connection.execute(
            "INSERT INTO manual_records(record_id, payload_json, created_at) VALUES (?, ?, ?)",
            ("M1", "{}", 2.0),
        )

    with store.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM manual_records").fetchone()[0] == 0


def test_operational_store_migrates_schema_versions(tmp_path):
    path = tmp_path / "operational.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '2')")
        connection.execute(
            """
            CREATE TABLE decision_sets (
                run_id TEXT PRIMARY KEY,
                data_fingerprint TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    store = OperationalStore(path)

    assert store.schema_version == 4
    with store.read_connection() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'release_history'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT 1 FROM pragma_table_info('job_snapshots') WHERE name = 'version'"
        ).fetchone() == (1,)


def test_operational_store_couples_audit_with_the_mutation_transaction(tmp_path):
    store = OperationalStore(tmp_path / "operational.sqlite3")
    audit = AuditLog(store)

    with pytest.raises(RuntimeError), store.transaction() as connection:
        connection.execute(
            "INSERT INTO manual_records(record_id, payload_json, created_at) VALUES (?, ?, ?)",
            ("M1", "{}", 1.0),
        )
        audit.record("tester", "register", {"record_id": "M1"}, connection)
        raise RuntimeError("rollback")

    with store.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM manual_records").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0
