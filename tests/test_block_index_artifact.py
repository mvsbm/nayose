from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def test_polars_block_index_backend_matches_pandas():
    pytest.importorskip("polars")
    from api.block_index import build_block_indexes

    reference = pd.DataFrame(
        [
            {
                "name_kanji": "髙橋 太郎",
                "name_kana": "タカハシ タロウ",
                "date_birth": "1990-01-02",
                "postal_code": "100-0001",
                "address": "東京都千代田区1-2-3",
                "phone": "090-1234-5678",
                "email": "taro@example.jp",
            },
            {
                "name_kanji": "鈴木 花子",
                "name_kana": "スズキ ハナコ",
                "date_birth": "1985-04-05",
                "postal_code": "150-0001",
                "address": "東京都渋谷区4-5-6",
                "phone": "080-9876-5432",
                "email": "hanako@example.jp",
            },
        ]
    )
    pandas_index = build_block_indexes(reference, backend="pandas")
    polars_index = build_block_indexes(reference, backend="polars")

    assert pandas_index.keys() == polars_index.keys()
    for block_name in pandas_index:
        assert pandas_index[block_name].keys() == polars_index[block_name].keys()
        for key in pandas_index[block_name]:
            np.testing.assert_array_equal(
                pandas_index[block_name][key], polars_index[block_name][key]
            )


def test_block_index_artifact_round_trip_preserves_candidates(tmp_path: Path):
    from api.block_index import build_block_indexes, load_block_indexes, save_block_indexes
    from api.engine import _data_fingerprint

    reference = pd.DataFrame(
        [
            {
                "name_kanji": "髙橋 太郎",
                "name_kana": "タカハシ タロウ",
                "date_birth": "1990-01-02",
                "postal_code": "100-0001",
                "address": "東京都千代田区1-2-3",
                "phone": "090-1234-5678",
                "email": "taro@example.jp",
            },
            {
                "name_kanji": "鈴木 花子",
                "name_kana": "スズキ ハナコ",
                "date_birth": "1985-04-05",
                "postal_code": "150-0001",
                "address": "東京都渋谷区4-5-6",
                "phone": "080-9876-5432",
                "email": "hanako@example.jp",
            },
        ]
    )
    fingerprint = _data_fingerprint(reference)
    expected = build_block_indexes(reference)
    path = tmp_path / "block_indexes.npz"

    save_block_indexes(path, expected, fingerprint=fingerprint, row_count=len(reference))
    actual = load_block_indexes(
        path, expected_fingerprint=fingerprint, expected_row_count=len(reference)
    )

    assert actual.keys() == expected.keys()
    for block_name in expected:
        assert actual[block_name].keys() == expected[block_name].keys()
        for key in expected[block_name]:
            np.testing.assert_array_equal(actual[block_name][key], expected[block_name][key])


def test_block_index_artifact_rejects_wrong_reference(tmp_path: Path):
    from api.block_index import build_block_indexes, load_block_indexes, save_block_indexes

    path = tmp_path / "block_indexes.npz"
    save_block_indexes(
        path,
        build_block_indexes(pd.DataFrame([{"name_kanji": "佐藤"}])),
        fingerprint="a" * 64,
        row_count=1,
    )

    with pytest.raises(RuntimeError, match="fingerprint"):
        load_block_indexes(path, expected_fingerprint="b" * 64, expected_row_count=1)


def test_dense_index_artifact_round_trip_is_optional(tmp_path: Path):
    pytest.importorskip("usearch")
    from api.dense_index import build_dense_index, load_dense_index, save_dense_index

    embeddings: np.ndarray = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    path = tmp_path / "dense_index.usearch"
    save_dense_index(
        path,
        build_dense_index(embeddings),
    )
    restored = load_dense_index(path, expected_rows=2, expected_dimensions=2)

    assert restored.size == 2
    assert restored.ndim == 2


def test_block_index_artifact_rejects_invalid_row_count(tmp_path: Path):
    from api.block_index import build_block_indexes, load_block_indexes, save_block_indexes

    path = tmp_path / "block_indexes.npz"
    save_block_indexes(
        path,
        build_block_indexes(pd.DataFrame([{"name_kanji": "佐藤"}])),
        fingerprint="a" * 64,
        row_count=1,
    )

    with pytest.raises(RuntimeError, match="row count"):
        load_block_indexes(path, expected_fingerprint="a" * 64, expected_row_count=2)
