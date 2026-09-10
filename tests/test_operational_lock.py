import hashlib
import json

from api.admin import AuditLog


def test_audit_records_form_a_hash_chain(tmp_path):
    log = AuditLog(tmp_path / "admin_audit.jsonl")

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
    assert log.tail(2) == [first, second]
