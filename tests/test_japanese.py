"""Unit tests for Japanese utilities (no external data)."""

import numpy as np
import pytest

from data_generation.japanese import (
    OCR_CONFUSIONS,
    ascii_romaji,
    date_to_era,
    mixed_width,
    ocr_mutate,
    to_katakana,
    to_romaji,
)


def test_romaji_and_kana_agree_with_kanji():
    kanji = "佐藤 太郎"
    kana = to_katakana(kanji)
    romaji = ascii_romaji(to_romaji(kanji))
    assert kana == "サトウ タロウ"
    assert romaji == "satoatarou" or romaji.startswith("sato")  # kakasi hepburn


def test_romaji_deterministic_and_cached():
    assert to_romaji("田中") == to_romaji("田中")
    assert to_katakana("田中") == "タナカ"


def test_mixed_width_converts_kana_digits_ascii():
    assert mixed_width("ワタナベ ソウタ") == "ﾜﾀﾅﾍﾞ ｿｳﾀ"
    assert mixed_width("０９０") == "090"
    assert mixed_width("ＡＢＣ") == "ABC"


def test_era_boundaries():
    assert date_to_era(1989, 1, 7) == "昭和64.1.7"
    assert date_to_era(1989, 1, 8) == "平成1.1.8"
    assert date_to_era(2019, 4, 30) == "平成31.4.30"
    assert date_to_era(2019, 5, 1) == "令和1.5.1"
    assert date_to_era(1990, 4, 1) == "平成2.4.1"


def test_ocr_confusion_map_is_consistent():
    for source, target in OCR_CONFUSIONS.items():
        assert source != target  # no self-mapping (chains like 已->己->巳 are intentional)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_ocr_mutate_shapes(seed):
    rng = np.random.default_rng(seed)
    value = "佐藤 太郎"
    for strength in (0.1, 0.9):
        mutated, label = ocr_mutate(value, strength, rng)
        assert label in {
            "unchanged",
            "ocr_substitute",
            "ocr_delete",
            "ocr_transpose",
            "ocr_duplicate",
        }
        if label == "ocr_delete":
            assert len(mutated) == len(value) - 1
        elif label in {"ocr_duplicate"}:
            assert len(mutated) == len(value) + 1
        elif label == "ocr_transpose":
            assert len(mutated) == len(value)
        elif label == "ocr_substitute":
            assert mutated != value


def test_ascii_romaji_strips_non_ascii():
    assert ascii_romaji("Yamada Taro") == "yamadataro"
    assert ascii_romaji("Ōta") == "ta"  # macron-form ō is stripped, not transliterated
