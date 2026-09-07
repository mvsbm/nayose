"""API tests: typed domain endpoints against a hermetic fake bundle."""

import os
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient


def _write_bundle(root) -> tuple[str, str]:
    run = root / "runs" / "run_fake"
    data = root / "data"
    run.mkdir(parents=True, exist_ok=True)
    (data / "ground_truth").mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        {
            "nayose_id": ["N000000001"],
            "name_kanji": ["佐藤 太郎"],
            "name_kana": ["サトウ タロウ"],
            "name_romaji": ["Sato Taro"],
            "date_birth": ["1960-05-04"],
            "party_type": ["person"],
            "email": ["sato@example.jp"],
            "postal_code": ["1000001"],
            "address": ["東京都千代田区"],
            "phone": ["090-1234-5678"],
            "record_count": [2],
        }
    ).to_parquet(run / "canonical_entities.parquet", index=False)
    pd.DataFrame(
        {
            "metric": [
                "candidate_pair_recall",
                "b3_precision",
                "b3_recall",
                "predicted_entities",
                "candidate_edges",
                "singleton_entity_rate",
            ],
            "value": [0.994, 0.999, 0.926, 1, 3, 0.2],
        }
    ).to_parquet(run / "full_dataset_summary.parquet", index=False)
    pd.DataFrame(
        {
            "section": [
                "candidate",
                "incoming_query",
                "incoming_query",
                "incoming_query",
                "incoming_query",
            ],
            "metric": [
                "pair_recall",
                "top1_identity_accuracy_existing",
                "mean_reciprocal_rank_existing",
                "review_rate",
                "no_match_rate",
            ],
            "value": [0.994, 0.935, 0.952, 0.267, 0.155],
        }
    ).to_parquet(run / "full_dataset_metrics.parquet", index=False)
    pd.DataFrame(
        {
            "metric": [
                "true_positives",
                "false_positives",
                "true_negatives",
                "false_negatives",
                "precision",
                "recall",
                "specificity",
                "f1",
                "auc",
            ],
            "value": [3553, 41, 13751, 2, 0.989, 0.999, 0.997, 0.994, 0.9999],
        }
    ).to_parquet(run / "pair_confusion_test.parquet", index=False)
    pd.DataFrame(
        {
            "scenario": ["existing_stable", "new_customer"],
            "action": ["AUTO_LINK", "NO_MATCH"],
            "expected_no": [0, 137],
            "expected_yes": [19, 0],
        }
    ).to_parquet(run / "query_confusion.parquet", index=False)
    pd.DataFrame(
        {
            "left_record_id": ["R0000000000"],
            "right_record_id": ["R0000000001"],
            "pair_probability": [0.81],
            "exact_count": [1],
        }
    ).to_parquet(run / "review_queue.parquet", index=False)
    pd.DataFrame(
        {"record_id": ["R0000000000", "R0000000001"], "nayose_id": ["N000000001", "N000000001"]}
    ).to_parquet(run / "record_to_nayose.parquet", index=False)
    pd.DataFrame(
        {
            "query_id": ["Q0000000000"],
            "scenario": ["existing_stable"],
            "action": ["AUTO_LINK"],
            "top_nayose_id": ["N000000001"],
            "top_probability": [0.9],
            "identity_top1_correct": [1],
        }
    ).to_parquet(run / "query_results.parquet", index=False)

    pd.DataFrame(
        {
            "record_id": ["R0000000000", "R0000000001"],
            "name_kanji": ["佐藤 太郎", "佐藤 太郎"],
            "name_kana": ["サトウ タロウ", "サトウ タロウ"],
            "phone": ["090-1234-5678", "090-1234-5678"],
            "postal_code": ["1000001", "1000001"],
            "name_romaji": ["Sato Taro", "Sato Taro"],
            "date_birth": ["1960-05-04", "1960-05-04"],
            "address": ["東京都千代田区", "大阪府大阪市"],
            "email": ["", "taro@example.test"],
            "party_type": ["person", "person"],
            "channel": ["paper_ocr", "web"],
            "observed_at": ["2020-01-01", "2021-01-01"],
            "policy_id": ["P1", "P2"],
            "agency_id": ["A1", "A2"],
        }
    ).to_parquet(data / "reference_records.parquet", index=False)
    pd.DataFrame(
        {
            "item_id": ["R0000000000", "R0000000000"],
            "field": ["name_kanji", "phone"],
            "transformation": ["homophone_kanji", "current"],
        }
    ).to_parquet(data / "ground_truth" / "field_lineage.parquet", index=False)
    (run / "run_manifest.json").write_text(
        '{"created_at_utc":"2026-01-01T00:00:00","data_fingerprint":"abc123def4567890",'
        '"thresholds":{"entity_auto":0.9,"entity_review":0.6}}',
        encoding="utf-8",
    )
    generation = data / "generations" / "gen_fake"
    generation.mkdir(parents=True, exist_ok=True)
    pd.read_parquet(data / "reference_records.parquet").to_parquet(
        generation / "reference_records.parquet", index=False
    )
    pd.DataFrame(
        {
            "query_id": ["Q0000000000"],
            "agency_id": ["A1"],
            "channel": ["web"],
            "observed_at": ["2026-08-25"],
            "party_type": ["person"],
            "name_kanji": ["佐藤 太郎"],
            "name_kana": ["サトウ タロウ"],
            "name_romaji": ["Sato Taro"],
            "date_birth": ["1960-05-04"],
            "postal_code": ["1000001"],
            "address": ["東京都千代田区"],
            "phone": ["090-1234-5678"],
            "email": [""],
        }
    ).to_parquet(generation / "incoming_queries.parquet", index=False)
    pd.DataFrame(
        {
            "agency_id": ["A1"],
            "channel": ["web"],
            "missing_factor": [1.0],
            "ocr_factor": [0.2],
            "format_factor": [1.0],
            "shared_phone": ["03-1"],
            "shared_email": ["office@example.test"],
        }
    ).to_parquet(generation / "agencies.parquet", index=False)
    (generation / "generation_manifest.json").write_text(
        '{"created_at_utc":"2026-01-01T00:00:00","data_fingerprint":"abc123def4567890",'
        '"config":{"snapshot_date":"2025-12-31","query_date":"2026-08-25"},'
        '"counts":{"parties":1,"observations":2,"queries":1}}',
        encoding="utf-8",
    )
    if not (root / "current").exists():
        (root / "current").symlink_to(run, target_is_directory=True)
    if not (data / "current").exists():
        (data / "current").symlink_to(generation, target_is_directory=True)
    return str(run), str(data)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    run, data = _write_bundle(tmp_path)
    import api.main as m

    monkeypatch.setattr(m, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(m, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(m, "DATA_ROOT", data)
    m.RUNS = m.RunRepository(m.MODELS_DIR)
    m.QUERIES = m.QueryService(m.RUNS, m.DATA_DIR)
    m.RECOMMEND = m.RecommendService(m.RUNS, m.DATA_DIR)
    m.ADMIN.close()
    m.ADMIN = m.AdminService(tmp_path, m.MODELS_DIR, tmp_path / "manual")
    m.STATS.reset()  # module-global shared state => explicit per-test isolation
    yield TestClient(m.app)
    m.RECOMMEND.close()


Q = "?run_id=run_fake"


def test_index_and_static(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200


def test_runs_and_stats(client):
    runs = client.get("/api/runs").json()
    assert [r["id"] for r in runs] == ["run_fake"]
    client.get("/api/metrics" + Q)
    stats = client.get("/api/stats").json()
    assert any(s["endpoint"] == "metrics" and s["hits"] == 1 for s in stats)


def test_entities_search_and_list(client):
    hit = client.get("/api/entities" + Q + "&q=佐藤").json()["rows"]
    assert len(hit) == 1 and hit[0]["nayose_id"] == "N000000001"
    # whitespace/case tolerance: "佐藤太郎" matches the stored "佐藤 太郎"
    tolerant = client.get("/api/entities" + Q + "&q=佐藤太郎").json()["rows"]
    assert tolerant[0]["nayose_id"] == "N000000001"
    # regexp chars are escaped, not interpreted
    assert client.get("/api/entities" + Q + "&q=.*").json()["rows"] == []
    all_rows = client.get("/api/entities" + Q).json()["rows"]
    assert len(all_rows) == 1


def test_entities_prioritize_name_matches_over_address_matches(tmp_path):
    from api.main import QueryService, RunRepository

    _write_bundle(tmp_path)
    canonical_path = tmp_path / "runs" / "run_fake" / "canonical_entities.parquet"
    canonical = pd.read_parquet(canonical_path)
    address_match = canonical.iloc[0].copy()
    address_match["nayose_id"] = "N000000002"
    address_match["name_kanji"] = "AAAA"
    address_match["name_kana"] = "エーエーエーエー"
    address_match["address"] = "佐藤"
    pd.concat([canonical, pd.DataFrame([address_match])], ignore_index=True).to_parquet(
        canonical_path, index=False
    )

    service = QueryService(RunRepository(tmp_path), tmp_path / "data")
    rows = service.entities("run_fake", "佐藤")

    assert [row["nayose_id"] for row in rows] == ["N000000001", "N000000002"]
    assert "name_kanji" in rows[0]["signals"]


def test_entities_filters_are_server_owned(client):
    body = client.get("/api/entities" + Q + "&q=佐藤&party_type=person&min_record_count=2").json()

    assert [row["nayose_id"] for row in body["rows"]] == ["N000000001"]
    assert body["query_plan"]["filters"] == {"party_type": "person", "min_record_count": 2}
    assert client.get("/api/entities" + Q + "&q=佐藤&party_type=company").json()["rows"] == []


def test_entities_fall_back_when_engine_not_loaded(client):
    res = client.get("/api/entities" + Q + "&q=佐藤&semantic=1")
    body = res.json()
    assert body["semantic"] is False, "engine never loads in this fixture"
    assert body["rows"][0]["nayose_id"] == "N000000001"


def test_dataset_explorer_lists_summarizes_and_pages_visible_columns(client):
    generations = client.get("/api/admin/generations")
    assert generations.status_code == 200
    assert generations.json()[0]["id"] == "gen_fake"
    summary = client.get("/api/admin/generations/gen_fake/summary")
    assert summary.status_code == 200
    tables = {table["id"]: table for table in summary.json()["tables"]}
    assert tables["reference"]["rows"] == 2
    assert "truth_id" not in tables["reference"]["columns"]
    rows = client.get(
        "/api/admin/generations/gen_fake/records", params={"table": "reference", "q": "佐藤"}
    )
    assert rows.status_code == 200
    body = rows.json()
    assert body["total"] == 2
    assert body["rows"][0]["name_kanji"] == "佐藤 太郎"
    assert "truth_id" not in body["columns"]


def test_entities_semantic_merge_orders_and_deduplicates(client, monkeypatch):
    from api import main as m

    def fake_search(run_id, text, limit=20):
        return [
            {
                "nayose_id": "N000000002",
                "name_kanji": "鈴木",
                "name_kana": "スズキ",
                "phone": "",
                "postal_code": "",
                "record_count": 1,
                "score": 0.91,
            },
            {
                "nayose_id": "N000000001",
                "name_kanji": "佐藤 太郎",
                "name_kana": "サトウ タロウ",
                "phone": "",
                "postal_code": "",
                "record_count": 2,
                "score": 0.4,
            },
        ]

    monkeypatch.setattr(m.RECOMMEND, "search_entities", fake_search)
    res = client.get("/api/entities" + Q + "&q=佐藤&semantic=1")
    body = res.json()
    assert body["semantic"] is True
    ids = [row["nayose_id"] for row in body["rows"]]
    # exact LIKE hit (N000000001) FIRST — a phone/postal match must never be
    # buried under embedding noise; then the semantic-only entity
    assert ids == ["N000000001", "N000000002"]
    assert "score" not in body["rows"][0] and body["rows"][1]["score"] == 0.91


def test_entity_detail_excludes_hidden_lineage(client):
    detail = client.get("/api/entity/N000000001" + Q).json()
    assert detail["entity"]["name_kanji"] == "佐藤 太郎"
    assert len(detail["members"]) == 2
    assert detail["merge_evidence_available"] is False
    assert "lineage" not in detail, "always-empty contract field removed"
    assert "homophone_kanji" not in str(detail)
    assert client.get("/api/entity/NOPE" + Q).status_code == 404


def test_entity_members_are_complete_paginated_and_compared(client):
    first = client.get("/api/entity/N000000001" + Q + "&offset=0&limit=1").json()
    second = client.get("/api/entity/N000000001" + Q + "&offset=1&limit=1").json()
    assert first["total"] == second["total"] == 2
    assert len(first["members"]) == len(second["members"]) == 1
    assert first["members"][0]["record_id"] == "R0000000000"
    assert second["members"][0]["channel"] == "web"
    assert second["members"][0]["policy_id"] == "P2"
    assert second["comparisons"]["R0000000001"]["address"]["status"] == "different"
    assert first["comparisons"]["R0000000000"]["email"]["status"] == "missing"
    assert client.get("/api/entity/N000000001" + Q + "&offset=-1").status_code == 422
    assert client.get("/api/entity/N000000001" + Q + "&limit=0").status_code == 422


def test_match_sample_uses_visible_active_data(client):
    result = client.get("/api/match-sample" + Q)
    assert result.status_code == 200
    assert result.json()["record"]["name_kanji"] == "佐藤 太郎"
    assert "truth_id" not in result.json()["record"]


def test_empty_matching_input_does_not_load_engine(client, monkeypatch):
    from api import main as m

    def fail_load(*args):
        pytest.fail("empty input must not initialize a model")

    monkeypatch.setattr(m.RECOMMEND, "loader", fail_load)
    response = client.post("/api/recommend", json={"run_id": "run_fake", "record": {}})
    assert response.status_code == 200
    assert response.json()["action"] == "INSUFFICIENT_INPUT"


def test_members_beyond_eight_are_accessible(client):
    from api import main as m

    path = m.DATA_DIR / "reference_records.parquet"
    seed = pd.read_parquet(path).iloc[0].to_dict()
    records = [dict(seed, record_id=f"R{i:010d}") for i in range(37)]
    pd.DataFrame(records).to_parquet(path, index=False)
    pd.DataFrame(
        {"record_id": [r["record_id"] for r in records], "nayose_id": ["N000000001"] * 37}
    ).to_parquet(m.RUNS.resolve("run_fake") / "record_to_nayose.parquet", index=False)
    seen: list[str] = []
    for offset in (0, 10, 20, 30):
        page = client.get("/api/entity/N000000001" + Q + f"&offset={offset}&limit=10").json()
        assert page["total"] == 37
        seen.extend(row["record_id"] for row in page["members"])
    assert seen == [r["record_id"] for r in records]


def test_matching_job_endpoints_and_validation(client):
    from api import main as m

    response = client.post("/api/recommendations", json={"run_id": "run_fake", "record": {}})
    assert response.status_code == 202
    job_id = response.json()["id"]
    m.RECOMMEND.wait(job_id, timeout=5)
    status = client.get(f"/api/recommendations/{job_id}").json()
    assert status["stage"] == "complete"
    assert status["result"]["action"] == "INSUFFICIENT_INPUT"
    assert client.get("/api/recommendations/not-a-job").status_code == 404
    assert (
        client.post(
            "/api/recommendations", json={"run_id": "run_fake", "record": {"truth_id": "hidden"}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/recommendations", json={"run_id": "run_fake", "record": {"phone": "1" * 1001}}
        ).status_code
        == 422
    )


def test_failed_job_is_not_no_match(client, monkeypatch):
    from api import main as m

    def fail_load(*args):
        raise RuntimeError("fingerprint mismatch")

    monkeypatch.setattr(m.RECOMMEND, "loader", fail_load)
    job = client.post(
        "/api/recommendations", json={"run_id": "run_fake", "record": {"name_kanji": "佐藤"}}
    ).json()
    m.RECOMMEND.wait(job["id"], timeout=5)
    status = client.get(f"/api/recommendations/{job['id']}").json()
    assert status["stage"] == "failed"
    assert status["error"] == "model_unavailable"
    assert status["result"] is None


def test_review_filter_and_pagination(client):
    page = client.get("/api/review" + Q + "&min_p=0.0&offset=0&limit=10").json()
    assert page["total"] == 1 and page["rows"][0]["pair_probability"] == 0.81
    empty = client.get("/api/review" + Q + "&min_p=0.9").json()
    assert empty["total"] == 0


def test_review_carries_score_histogram_and_manifest_marker(client):
    body = client.get("/api/review" + Q + "&min_p=0.0&limit=10").json()
    assert body["histogram"], "histogram must be present (review filter visualization)"
    assert sum(count for _, count in body["histogram"]) == body["total"]
    lows = [lo for lo, _ in body["histogram"]]
    assert lows == sorted(lows)
    assert body["pair_auto_threshold"] is None, "fixture manifest has no pair_auto"


def test_recommend_accepts_validated_threshold_overrides(client):
    # session-only threshold preview must not be rejected, but garbage must be
    ok = client.post(
        "/api/recommendations",
        json={"run_id": "run_fake", "record": {}, "thresholds": {"auto": 0.5, "review": 0.2}},
    )
    assert ok.status_code == 202
    for bad in (
        {"auto": 0.2, "review": 0.5},
        {"auto": 1.5},
        {"auto": -0.1, "review": 0.2},
        {"auto": 0.5, "review": 0.2, "margin": 0.1},
    ):
        res = client.post(
            "/api/recommendations", json={"run_id": "run_fake", "record": {}, "thresholds": bad}
        )
        assert res.status_code == 422, f"{bad} must be rejected"


def test_metrics_and_confusion(client):
    met = client.get("/api/metrics" + Q).json()
    assert met["kpis"][0][0] == "candidate recall" and met["kpis"][0][1] == pytest.approx(0.994)
    conf = client.get("/api/confusion" + Q).json()
    assert conf["pairs"]["auc"] == pytest.approx(0.9999)
    assert conf["scenarios"][0]["scenario"] == "existing_stable"
    assert conf["decisions"]["existing_stable"][0]["query_id"] == "Q0000000000"


def test_compare(client):
    rows = client.get("/api/compare" + Q + "&left=R0000000000&right=R0000000001").json()["rows"]
    assert len(rows) == 2


def test_recommend_rejects_unsupported_bundle(client):
    res = client.post(
        "/api/recommend", json={"run_id": "run_fake", "record": {"name_kanji": "佐藤 太郎"}}
    )
    assert res.status_code == 409


def test_decisions_roundtrip(client):
    assert client.get("/api/decisions?run_id=run_fake").json()["items"] == []
    put = client.put(
        "/api/decisions",
        json={
            "run_id": "run_fake",
            "items": [{"left": "R0000000000", "right": "R0000000001", "ts": "now"}],
        },
    )
    assert put.status_code == 200 and put.json()["saved"] == 1
    items = client.get("/api/decisions?run_id=run_fake").json()["items"]
    assert items[0]["left"] == "R0000000000" and items[0]["right"] == "R0000000001"
    assert items[0]["reviewer"] == "web", "default reviewer identity stamped"


def test_decisions_stamp_reviewer_header(client):
    client.put(
        "/api/decisions",
        headers={"X-Reviewer": "alice@agency"},
        json={"run_id": "run_fake", "items": [{"left": "L", "right": "R", "ts": ""}]},
    )
    items = client.get("/api/decisions?run_id=run_fake").json()["items"]
    assert items[0]["reviewer"] == "alice@agency"
    assert items[0]["ts"], "missing ts backfilled"


def test_health_reports_current_run(client):
    h = client.get("/api/health")
    body = h.json()
    assert h.status_code == 200
    assert body["status"] == "ok"
    assert body["run"] == "run_fake"
    assert body["fingerprint"] == "abc123def456"
    assert body["engine_warm"] is False, "fixture never loads the engine"
    assert "stats" in body


def test_empty_run_id_resolves_to_current(client):
    res = client.get("/api/review")
    assert res.status_code == 200, "default run_id serves the current bundle"
    assert res.json()["rows"]


def test_bearer_auth_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("NAYOSE_API_TOKEN", "sekret")
    from api import main as m

    client = TestClient(m.app)
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/health").status_code == 200, "health probe exempt from auth"
    ok = client.get("/api/runs", headers={"Authorization": "Bearer sekret"})
    assert ok.status_code == 200 and ok.json()
    page = client.get("/")
    assert page.status_code == 200, "static shell stays readable"


def test_raw_sql_is_disabled_by_default(client):
    res = client.post(
        "/api/query",
        json={
            "run_id": "run_fake",
            "sql": "SELECT count(*) AS n FROM 'bundle/canonical_entities.parquet'",
        },
    )
    assert res.status_code == 403


def test_admin_is_always_open_without_separate_token(client):
    capability = client.get("/api/admin/capable")
    assert capability.json() == {"capable": True}
    assert client.get("/api/admin/state").status_code == 200
    body = client.get("/api/admin/state").json()
    assert body["profile"]["profiles"]
    assert {"id", "manifest", "current"} <= set(body["runs"][0])


def test_generation_config_and_preview_validate_every_control(client):
    catalog = client.get("/api/admin/generation-config").json()
    assert len(catalog["events"]) == 17
    assert {event["id"] for event in catalog["events"]} == set(catalog["defaults"]["event_rates"])
    controls = catalog["defaults"]
    controls["event_rates"]["paper_ocr"] = 0.72
    controls["n_parties"] = 300
    preview = client.post("/api/admin/generation-preview", json={"controls": controls})
    assert preview.status_code == 200
    body = preview.json()
    assert body["counts"]["parties"] == 300
    assert body["controls"]["event_rates"]["paper_ocr"] == 0.72
    assert next(event for event in body["events"] if event["id"] == "paper_ocr")["rate"] == 0.72
    assert body["time_window"]["snapshot_date"] <= body["time_window"]["query_date"]
    assert body["time_window"]["gap_days"] > 0
    impact = body["dataset_impact"]
    assert {row["id"] for row in impact["quality"]} == {"average", "poor", "catastrophic"}
    assert sum(row["records"] for row in impact["quality"]) == body["counts"]["records"]
    assert {row["id"] for row in impact["channels"]} == {
        "web",
        "agent_pc",
        "paper_ocr",
        "legacy_migration",
    }
    assert len(impact["fields"]) == 8
    assert len(body["examples"]) == 6
    assert {example["channel"] for example in body["examples"]} >= {
        "web",
        "paper_ocr",
        "agent_pc",
        "legacy_migration",
    }
    sample = body["samples"]["average"]
    assert set(sample) == {"quality", "channel", "canonical", "observed", "changes"}
    assert sample["changes"], "preview sample should explain at least one expected change"
    unknown = {**controls, "event_rates": {**controls["event_rates"], "not_registered": 0.5}}
    assert (
        client.post("/api/admin/generation-preview", json={"controls": unknown}).status_code == 422
    )
    invalid_mix = {**controls, "query_mix": {**controls["query_mix"], "new_customer": 0.01}}
    assert (
        client.post("/api/admin/generation-preview", json={"controls": invalid_mix}).status_code
        == 422
    )


def test_admin_settings_and_audit(client):
    h = {"X-Actor": "pytest"}
    res = client.post(
        "/api/admin/actions",
        json={"action": "settings", "params": {"exact_mode": "exact_only"}},
        headers=h,
    )
    assert res.status_code == 200 and res.json()["exact_mode"] == "exact_only"
    bad = client.post(
        "/api/admin/actions",
        json={"action": "settings", "params": {"exact_mode": "wat"}},
        headers=h,
    )
    assert bad.status_code == 400
    audit = client.get("/api/admin/state", headers=h).json()["audit"]
    assert any(row["action"] == "settings" for row in audit)
    os.environ.pop("NAYOSE_EXACT_MODE", None)


def test_manual_data_entry_register_dryrun_undo(client):
    base = client.get("/api/data-entry").json()
    assert base["count"] == 0
    res = client.post(
        "/api/data-entry/register",
        json={"record": {"name_kanji": "手入力 太郎", "phone": "090-9999-9999"}},
    )
    assert res.status_code == 200
    rid = res.json()["record_id"]
    assert rid.startswith("M")
    assert client.get("/api/data-entry").json()["count"] == 1
    dry = client.post(
        "/api/data-entry/import",
        json={"dry_run": True, "rows": [{"name_kanji": "導入 花子"}, {"phone": "x"}]},
    )
    assert dry.status_code == 200 and dry.json()["dry_run"] is True
    assert dry.json()["valid"] == 1 and any(e["index"] == 1 for e in dry.json()["errors"])
    imp = client.post(
        "/api/data-entry/import",
        json={"dry_run": False, "rows": [{"name_kanji": "導入 花子"}]},
    )
    assert imp.json()["imported"] == 1
    undo = client.post("/api/data-entry/undo", json={"record_ids": [rid]})
    assert undo.json()["removed"] == 1


def test_entities_search_by_policy_agency_and_exposes_signals(client):
    hit = client.get("/api/entities" + Q + "&q=P1").json()
    assert hit["rows"][0]["nayose_id"] == "N000000001", "policy-number lookup"
    detail = client.get("/api/entities" + Q + "&q=佐藤").json()
    row = detail["rows"][0]
    assert "name_kanji" in row["signals"], "hit explains which field matched"
    assert row["kind"] == "exact"
    assert row["prefecture"] == "東京都"
    assert "twin_count" in row
    email = client.get("/api/entities" + Q + "&q=sato@example.jp").json()
    assert email["exact"][0]["nayose_id"] == "N000000001"
    assert "email" in email["exact"][0]["signals"]
    party = client.get("/api/entities" + Q + "&q=person").json()
    assert party["exact"][0]["nayose_id"] == "N000000001"
    assert "party_type" in party["exact"][0]["signals"]
    natural = client.get("/api/entities" + Q + "&q=person 東京都").json()
    assert natural["exact"][0]["nayose_id"] == "N000000001"
    assert {"party_type", "address"}.issubset(natural["exact"][0]["signals"])
    nearest = client.get("/api/entities" + Q + "&q=佐藤 朗").json()
    assert nearest["nearest"][0]["kind"] == "nearest"
    assert nearest["nearest"][0]["match_method"] == "fuzzy_nearest"
    assert "normalized_query" in nearest
    assert "suggestion" in client.get("/api/entities" + Q + "&q=xzzz").json()


def test_nearest_search_ignores_single_character_false_matches():
    from types import SimpleNamespace
    from typing import cast

    from api.main import QueryService, RunRepository

    service = QueryService(
        cast(RunRepository, SimpleNamespace(resolve=lambda _: Path("."))), Path(".")
    )
    service._nearest_choices = lambda _run_dir, _fields: [  # type: ignore[assignment]
        {
            "text": "本",
            "field": "name_kanji",
            "entity": {"nayose_id": "N1", "name_kanji": "本", "record_count": 1},
        },
        {
            "text": "松本彬",
            "field": "name_kanji",
            "entity": {"nayose_id": "N2", "name_kanji": "松本彬", "record_count": 1},
        },
    ]

    hits = service.nearest_entities("run_fake", "松本哲彬", limit=2)

    assert hits and hits[0]["nayose_id"] == "N2"


def test_normalize_preview_endpoint(client):
    body = client.get("/api/normalize", params={"text": "０９０−１２３４−５６７８"}).json()
    assert body["phone"] == "09012345678"
    assert set(("name_kanji", "name_kana", "postal_code", "date_birth")) <= set(body)


def test_review_can_be_filtered_by_entity(client):
    body = client.get("/api/review" + Q + "&nayose=N000000001").json()
    assert body["total"] == 1
    empty = client.get("/api/review" + Q + "&nayose=NOPE").json()
    assert empty["total"] == 0


def test_manual_validation_rejects_missing_name(client):
    res = client.post("/api/data-entry/register", json={"record": {"phone": "090-0000-0000"}})
    assert res.status_code == 422
