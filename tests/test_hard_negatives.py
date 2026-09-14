import numpy as np
import pandas as pd

from api.hard_negatives import select_hard_negatives


def test_hard_negative_selection_reserves_same_name_and_missing_contact_cases() -> None:
    reference = pd.DataFrame(
        {
            "n_name_loose": ["sato", "sato", "suzuki", "tanaka"],
            "n_address": ["tokyo1", "tokyo2", "osaka", "tokyo1"],
            "n_postal": ["100", "100", "200", "300"],
            "n_phone": ["0901", "", "0902", "0903"],
            "n_email": ["a@example", "", "b@example", "c@example"],
            "channel": ["web", "paper_ocr", "web", "legacy_migration"],
        }
    )
    pairs = pd.DataFrame(
        {
            "left_index": [0, 0, 1, 2],
            "right_index": [1, 2, 3, 3],
            "retrieval_score": [0.9, 0.8, 0.7, 0.6],
        }
    )
    negative_rows: np.ndarray = np.arange(len(pairs), dtype=np.int64)

    selected = select_hard_negatives(
        pairs,
        reference,
        negative_rows,
        limit=2,
        frequency_maps={"n_name_loose": {"sato": 2}},
    )

    assert len(selected) == 2
    assert 0 in selected
    assert set(selected).issubset(set(negative_rows.tolist()))


def test_hard_negative_selection_returns_all_rows_when_limit_is_large() -> None:
    pairs = pd.DataFrame(
        {
            "left_index": [0],
            "right_index": [1],
            "retrieval_score": [0.5],
        }
    )
    reference = pd.DataFrame(index=[0, 1])

    selected = select_hard_negatives(pairs, reference, np.asarray([0]), limit=3)

    np.testing.assert_array_equal(selected, np.asarray([0]))
