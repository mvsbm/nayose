from api.retrieval_ablation import _metrics


def test_ablation_metrics_include_operational_slices() -> None:
    rows = [
        {
            "query_id": "Q1",
            "scenario": "existing_changed",
            "channel": "paper_ocr",
            "missingness": "2-3_missing",
            "name_frequency": "rare",
            "entity_size": "large",
            "candidate_recall": True,
            "action": "REVIEW",
            "auto_link": False,
            "auto_correct": False,
            "candidates": [{"rank": 1, "entity_id": "N1"}],
        }
    ]

    metrics = _metrics(rows, {"Q1": {"N1"}})

    assert metrics["slices"]["channel"]["paper_ocr"]["candidate_recall"] == 1.0
    assert metrics["slices"]["missingness"]["2-3_missing"]["review_rate"] == 1.0
    assert metrics["slices"]["name_frequency"]["rare"]["recall_at_1"] == 1.0
    assert metrics["slices"]["entity_size"]["large"]["recall_at_5"] == 1.0
