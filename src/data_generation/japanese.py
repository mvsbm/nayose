"""Japanese text utilities: romanization, kana conversion, width flipping,
Japanese-era dates, and a real OCR confusion lexicon (data, not code)."""

from __future__ import annotations

import re
from functools import lru_cache

import jaconv
from pykakasi import kakasi

# --------------------------------------------------------------------------
# converters (singleton instances; pykakasi is not thread-safe)
# --------------------------------------------------------------------------
_romaji_kakasi = kakasi()
_romaji_kakasi.setMode("J", "a")
_romaji_kakasi.setMode("H", "a")
_romaji_kakasi.setMode("K", "a")
ROMAJI_CONVERTER = _romaji_kakasi.getConverter()

_kana_kakasi = kakasi()
_kana_kakasi.setMode("J", "K")
_kana_kakasi.setMode("H", "K")
_kana_kakasi.setMode("K", "K")
KANA_CONVERTER = _kana_kakasi.getConverter()


@lru_cache(maxsize=200_000)
def to_romaji(japanese_text: str) -> str:
    """Convert Japanese text to ASCII romaji (accent-free, capitalized words)."""
    if not japanese_text:
        return ""
    result = ROMAJI_CONVERTER.do(japanese_text)
    return " ".join(word.capitalize() for word in result.split())


@lru_cache(maxsize=200_000)
def to_katakana(japanese_text: str) -> str:
    """Convert Japanese text to katakana (no furigana ambiguity)."""
    if not japanese_text:
        return ""
    return re.sub(r"\s+", " ", KANA_CONVERTER.do(japanese_text)).strip()


def ascii_romaji(value: str) -> str:
    """Romaji with all non a-z removed (email local parts etc.)."""
    return re.sub(r"[^a-z]", "", value.lower())


# --------------------------------------------------------------------------
# width handling (half/full width) - realistic data-entry noise
# --------------------------------------------------------------------------
def mixed_width(value: str) -> str:
    """Half-width katakana + half-width digits/ascii (legacy-migration look)."""
    return jaconv.z2h(value, kana=True, ascii=True, digit=True)


# --------------------------------------------------------------------------
# Japanese-era dates (realistic for legacy_migration records)
# --------------------------------------------------------------------------
_ERA_BOUNDARIES = (  # (era, start year CE, start month, start day)
    ("令和", 2019, 5, 1),
    ("平成", 1989, 1, 8),
    ("昭和", 1926, 12, 25),
    ("大正", 1912, 7, 30),
)


def date_to_era(year: int, month: int, day: int) -> str:
    """Convert a CE date to Japanese era format, e.g. 1990-04-01 -> 平成2.4.1."""
    for era, era_year, era_month, era_day in _ERA_BOUNDARIES:
        if (year, month, day) >= (era_year, era_month, era_day):
            n = year - era_year + 1
            return f"{era}{n}.{month}.{day}"
    return f"{year}.{month}.{day}"  # pre-Taisho / out of range: gregorian


# --------------------------------------------------------------------------
# OCR confusion lexicon: real similar-glyph pairs (kanji + kana), used by
# the paper_ocr corruption family and single-field OCR noise.
# --------------------------------------------------------------------------
OCR_CONFUSIONS: dict[str, str] = {
    # kanji lookalikes (stroke-level confusion)
    "人": "入",
    "入": "人",
    "大": "太",
    "太": "大",
    "王": "玉",
    "玉": "王",
    "土": "士",
    "士": "土",
    "千": "干",
    "干": "千",
    "未": "末",
    "末": "未",
    "已": "己",
    "己": "巳",
    "巳": "己",
    "日": "目",
    "目": "日",
    "月": "用",
    "口": "ロ",
    "ロ": "口",
    "田": "由",
    "由": "田",
    "甲": "申",
    "申": "甲",
    "内": "円",
    "円": "内",
    "外": "処",
    "処": "外",
    "名": "各",
    "各": "名",
    "線": "緑",
    "緑": "線",
    "鳥": "島",
    "島": "鳥",
    "判": "別",
    "別": "判",
    "舌": "吉",
    "吉": "舌",
    "束": "東",
    "東": "束",
    "零": "霊",
    "霊": "零",
    # kana lookalikes (shape confusion in print/scan)
    "シ": "ツ",
    "ツ": "シ",
    "ソ": "ン",
    "ン": "ソ",
    "ウ": "ワ",
    "ワ": "ウ",
    "ク": "ケ",
    "ケ": "ク",
    "ハ": "八",
    "八": "ハ",
    "エ": "工",
    "工": "エ",
    "コ": "二",
    "二": "コ",
    "ス": "ヌ",
    "ヌ": "ス",
    "セ": "七",
    "七": "セ",
    "ホ": "木",
    "木": "ホ",
    "ヤ": "ゃ",
    "レ": "し",
    "ア": "ァ",
    "ァ": "ア",
    "オ": "ォ",
    "ォ": "オ",
}
OCR_OPERATIONS = ("substitute", "delete", "transpose", "duplicate")


def ocr_mutate(value: str, strength: float, rng) -> tuple[str, str]:
    """Apply OCR-like damage. ``strength`` in [0,1] controls the chance the
    mutation fires; returns (mutated_value, transformation_label)."""
    rng = rng
    if not value:
        return value, "unchanged"
    chars = list(value)
    operation = rng.choice(list(OCR_OPERATIONS), p=[0.5, 0.2, 0.15, 0.15])
    if operation == "substitute":
        index = int(rng.integers(len(chars)))
        original = chars[index]
        chars[index] = OCR_CONFUSIONS.get(original, original)
        if chars[index] == original:
            return value, "unchanged"
        return "".join(chars), "ocr_substitute"
    if operation == "delete":
        index = int(rng.integers(len(chars)))
        chars.pop(index)
        return "".join(chars), "ocr_delete"
    if operation == "transpose" and len(chars) > 1:
        index = int(rng.integers(len(chars) - 1))
        chars[index], chars[index + 1] = chars[index + 1], chars[index]
        return "".join(chars), "ocr_transpose"
    if operation == "duplicate":
        index = int(rng.integers(len(chars)))
        chars.insert(index, chars[index])
        return "".join(chars), "ocr_duplicate"
    return value, "unchanged"
