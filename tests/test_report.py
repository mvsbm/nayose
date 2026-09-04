"""Unit tests for the audit dashboard renderer (self-contained fake bundle)."""

import pandas as pd
import pytest

from synthetic.report import render


def _write_frames(root) -> tuple[str, str]:
    run = root / "run"
    data = root / "data"
    run.mkdir(parents=True, exist_ok=True)
    (data / "ground_truth").mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        {
            "metric": ["candidate_pair_recall", "b3_precision", "b3_recall"],
            "value": [0.994, 0.999, 0.926],
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
                "n_test_edges",
            ],
            "value": [3553, 41, 13751, 2, 0.989, 0.999, 0.997, 0.994, 0.9999, 17347],
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
            "pair_probability": [0.31],
        }
    ).to_parquet(run / "review_queue.parquet", index=False)
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
        {"record_id": ["R0000000000", "R0000000001"], "nayose_id": ["N000000001", "N000000001"]}
    ).to_parquet(run / "record_to_nayose.parquet", index=False)

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
        {"item_id": ["R0000000000"], "field": ["name_kanji"], "transformation": ["homophone_kanji"]}
    ).to_parquet(data / "ground_truth" / "field_lineage.parquet", index=False)
    return str(run), str(data)


def test_render_produces_artifact(tmp_path):
    run, data = _write_frames(tmp_path)
    out = render(run, data)
    assert out.exists() and out.suffix == ".html"
    page = out.read_text(encoding="utf-8")
    for marker in (
        "<!doctype html",
        'lang="ja"',
        'role="tablist"',
        'role="search"',
        "data-theme",
        'id="app-data"',
        'aria-live="polite"',
        "Noto Sans JP",
    ):
        assert marker in page, marker


def test_render_contains_all_views(tmp_path):
    run, data = _write_frames(tmp_path)
    page = render(run, data).read_text(encoding="utf-8")
    for marker in (
        "panel-kpi",
        "panel-confusion",
        "panel-review",
        "panel-entities",
        "panel-search",
        "true_positives",
        "佐藤 太郎",
    ):
        assert marker in page, marker


def test_render_rejects_incomplete_bundle(tmp_path):
    run, data = _write_frames(tmp_path)
    pd.read_parquet(run + "/canonical_entities.parquet").drop(columns=["record_count"]).to_parquet(
        run + "/canonical_entities.parquet", index=False
    )
    with pytest.raises(ValueError, match="record_count"):
        render(run, data)
