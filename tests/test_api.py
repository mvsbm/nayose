"""API tests (self-contained fake bundle; TestClient)."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient


def _write_bundle(root) -> tuple[str, str]:
    run = root / "runs" / "run_fake" / "runs" / "run_fake"
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
            "record_count": [1],
        }
    ).to_parquet(run / "canonical_entities.parquet", index=False)
    pd.DataFrame({"metric": ["candidate_pair_recall"], "value": [0.994]}).to_parquet(
        run / "full_dataset_summary.parquet", index=False
    )
    pd.DataFrame({"record_id": ["R0000000000"], "nayose_id": ["N000000001"]}).to_parquet(
        run / "record_to_nayose.parquet", index=False
    )
    pd.DataFrame({"query_id": ["Q0000000000"]}).to_parquet(
        run / "query_results.parquet", index=False
    )

    pd.DataFrame(
        {
            "record_id": ["R0000000000"],
            "name_kanji": ["佐藤 太郎"],
            "name_kana": ["サトウ タロウ"],
            "phone": ["090-1234-5678"],
            "postal_code": ["1000001"],
        }
    ).to_parquet(data / "reference_records.parquet", index=False)
    pd.DataFrame(
        {"item_id": ["R0000000000"], "field": ["name_kanji"], "transformation": ["homophone_kanji"]}
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


def test_index_and_static(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_runs_lists_bundle(client):
    runs = client.get("/api/runs").json()
    assert len(runs) == 1
    assert runs[0]["id"] == "run_fake"


def test_query_runs_sql_over_parquet(client):
    res = client.post(
        "/api/query",
        json={
            "run_id": "run_fake",
            "sql": "SELECT count(*) AS n FROM 'bundle/canonical_entities.parquet'",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows"][0][0] == 1
    # hidden ground truth is queryable via 'data/' root (audit tool)
    res2 = client.post(
        "/api/query",
        json={
            "run_id": "run_fake",
            "sql": "SELECT transformation FROM 'data/ground_truth/field_lineage.parquet'",
        },
    )
    assert res2.status_code == 200
    assert res2.json()["rows"][0][0] == "homophone_kanji"


def test_query_rejects_non_select(client):
    res = client.post("/api/query", json={"run_id": "run_fake", "sql": "DROP TABLE t"})
    assert res.status_code == 400


def test_recommend_rejects_unsupported_bundle(client):
    res = client.post(
        "/api/recommend", json={"run_id": "run_fake", "record": {"name_kanji": "佐藤 太郎"}}
    )
    assert res.status_code == 409
