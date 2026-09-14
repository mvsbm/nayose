import hashlib
import json

from api.audit import AuditLog
from api.operational_store import OperationalStore


def test_audit_records_form_a_hash_chain(tmp_path):
    store = OperationalStore(tmp_path / "operational.sqlite3")
    log = AuditLog(store)

    first = log.record("tester", "start", {"job": "one"})
    second = log.record("tester", "done", {"job": "one"})

    unsigned = {key: value for key, value in first.items() if key != "event_hash"}
    expected = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert first["event_id"]
    assert first["prev_hash"] == ""
    assert first["event_hash"] == expected
    assert second["prev_hash"] == first["event_hash"]
    assert log.verify() is True
    assert log.tail(2) == [first, second]

    with store.transaction() as connection:
        connection.execute(
            "UPDATE audit_events SET action = ? WHERE event_id = ?",
            ("tampered", second["event_id"]),
        )
    assert log.verify() is False
