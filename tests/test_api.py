"""API tests: typed domain endpoints against a hermetic fake bundle."""

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
        }
    ).to_parquet(data / "reference_records.parquet", index=False)
    pd.DataFrame(
        {
            "item_id": ["R0000000000", "R0000000000"],
            "field": ["name_kanji", "phone"],
            "transformation": ["homophone_kanji", "current"],
        }
    ).to_parquet(data / "ground_truth" / "field_lineage.parquet", index=False)
    return str(run), str(data)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    run, data = _write_bundle(tmp_path)
    import api.main as m

    monkeypatch.setattr(m, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(m, "DATA_DIR", tmp_path / "data")
    m.RUNS = m.RunRepository(m.MODELS_DIR)
    m.QUERIES = m.QueryService(m.RUNS, m.DATA_DIR)
    m.RECOMMEND = m.RecommendService(m.RUNS, m.DATA_DIR)
    return TestClient(m.app)


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
    all_rows = client.get("/api/entities" + Q).json()["rows"]
    assert len(all_rows) == 1


def test_entity_detail_with_lineage(client):
    detail = client.get("/api/entity/N000000001" + Q).json()
    assert detail["entity"]["name_kanji"] == "佐藤 太郎"
    assert len(detail["members"]) == 2
    assert detail["lineage"]["name_kanji"] == ["homophone_kanji"]
    assert client.get("/api/entity/NOPE" + Q).status_code == 404


def test_review_filter_and_pagination(client):
    page = client.get("/api/review" + Q + "&min_p=0.0&offset=0&limit=10").json()
    assert page["total"] == 1 and page["rows"][0]["pair_probability"] == 0.81
    empty = client.get("/api/review" + Q + "&min_p=0.9").json()
    assert empty["total"] == 0


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


def test_raw_sql_is_disabled_by_default(client):
    res = client.post(
        "/api/query",
        json={
            "run_id": "run_fake",
            "sql": "SELECT count(*) AS n FROM 'bundle/canonical_entities.parquet'",
        },
    )
    assert res.status_code == 403
