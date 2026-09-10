"""Real-data providers behind small source interfaces.

- PersonaSource: NVIDIA Nemotron-Personas-Japan (1M rows). Real family/given
  name frequencies, sex, age, prefecture, occupation, etc. Kana/romaji are
  derived from the kanji so all three spellings always agree.
- CompanySource: EDINET company names (real legal names) + cached romaji.
- AddressSource: Japan Post ken_all, split into per-prefecture pools so a
  persona's address matches its (real, demographically skewed) prefecture.
- BuildingComposer: real building-name lexicon, available for every
  prefecture (no per-prefecture hardcoded catalogues).
"""

from __future__ import annotations

import csv
import re
import ssl
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DataSources
from .jp import to_katakana, to_romaji

PERSONA_NAME_RE = re.compile(r"^([\u4e00-\u9fff々〆〇]+(?:\s+[\u4e00-\u9fff々〆〇]+)?)\s*は")
PLACEHOLDER_TOWNS = {"以下に掲載がない場合"}


# --------------------------------------------------------------------------
# Personas (real names + demographics)
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Person:
    name_kanji: str
    name_kana: str
    name_romaji: str
    sex: str
    age: int
    prefecture: str
    region: str
    occupation: str
    education_level: str
    marital_status: str


class PersonaCatalog:
    """Compact indexed view over persona columns; materializes only samples."""

    def __init__(self, frame: pd.DataFrame):
        self._columns = tuple(frame[column].to_numpy(copy=False) for column in frame.columns)

    def __len__(self) -> int:
        return len(self._columns[0]) if self._columns else 0

    def __getitem__(self, index: int) -> Person:
        values = [column[index] for column in self._columns]
        return Person(
            str(values[0]),
            str(values[1]),
            str(values[2]),
            str(values[3]),
            int(values[4]),
            str(values[5]),
            str(values[6]),
            str(values[7]),
            str(values[8]),
            str(values[9]),
        )

    def __iter__(self) -> Iterator[Person]:
        return (self[index] for index in range(len(self)))


class PersonaSource:
    """Real Japanese identities sampled from the Nemotron personas corpus.

    One-time extraction is cached to ``{data_dir}/{cache_name}.parquet``;
    subsequent sessions load the cache (fast, offline, deterministic).
    """

    def __init__(self, data_dir: Path, sources: DataSources = DataSources()):
        cache_path = data_dir / sources.persona_cache
        if not cache_path.exists():
            self._build_cache(cache_path, sources)
        frame = pd.read_parquet(cache_path)
        # Defense-in-depth (the alpha_in_kana bug once leaked Latin into kanji):
        # identity names must start with a Han character — anything else is an
        # extraction artifact, never a customer identity. (Python re, not the
        # pyarrow regex engine: \u escapes are invalid there.)
        han_start = re.compile(r"^[\u4e00-\u9fff々〆〇]")
        valid: np.ndarray = np.fromiter(
            (bool(han_start.match(str(value))) for value in frame["name_kanji"].to_numpy()),
            dtype=bool,
            count=len(frame),
        )
        frame = frame.loc[valid].reset_index(drop=True)
        self.persons = PersonaCatalog(frame)
        self.prefectures: tuple[str, ...] = tuple(np.unique(frame["prefecture"].to_numpy()))
        print(
            f"Persona source: {len(self.persons):,} identities "
            f"({len(self.prefectures)} prefectures) loaded from {cache_path.name}"
        )

    def _build_cache(self, cache_path: Path, sources: DataSources) -> None:
        try:
            from datasets import load_dataset
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError("datasets package required for persona cache build") from exc
        print(f"Building persona cache from {sources.persona_dataset} (one-time)...")
        ds = load_dataset(sources.persona_dataset, split=sources.persona_split)
        rows: list[tuple[object, ...]] = []
        for row in ds:
            match = PERSONA_NAME_RE.match(row["persona"])
            if not match:
                continue
            kanji = match.group(1)
            if len(kanji.replace(" ", "")) > 8:
                continue
            if not re.match(r"^[\u4e00-\u9fff々〆〇]", kanji):
                continue  # identity names start with a Han character, never Latin
            rows.append(
                (
                    kanji,
                    to_katakana(kanji),
                    to_romaji(kanji),
                    row["sex"],
                    int(row["age"]),
                    row["prefecture"],
                    row["region"],
                    row["occupation"],
                    row["education_level"],
                    row["marital_status"],
                )
            )
        frame = pd.DataFrame(
            rows,
            columns=[
                "name_kanji",
                "name_kana",
                "name_romaji",
                "sex",
                "age",
                "prefecture",
                "region",
                "occupation",
                "education_level",
                "marital_status",
            ],
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(cache_path, index=False)
        print(f"  cached {len(frame):,} extracted identities -> {cache_path}")

    def sample(self, rng: np.random.Generator, n: int, rng_person: bool = False) -> list[Person]:
        """Uniform-ish sample of personas; ``n`` persons (with replacement,
        preserving the corpus's real frequency skew)."""
        idx = rng.integers(0, len(self.persons), size=n)
        return [self.persons[i] for i in idx]


def build_homophone_map(
    persona: PersonaSource, min_variants: int = 2
) -> dict[str, tuple[str, ...]]:
    """kana(reading) -> distinct kanji spellings actually present in the real
    name corpus (同音異字: e.g. 佐藤/左藤 both read サトウ). Data-driven; no
    hardcoded lexicon. Reading (kana) is the identity; kanji may diverge."""
    groups: dict[str, set[str]] = {}
    for person in persona.persons:
        key = re.sub(r"\s+", "", person.name_kana)
        groups.setdefault(key, set()).add(person.name_kanji.replace(" ", ""))
    return {
        key: tuple(sorted(spellings))
        for key, spellings in groups.items()
        if len(spellings) >= min_variants
    }


# --------------------------------------------------------------------------
# Companies (real legal names from EDINET)
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Company:
    name_kanji: str
    name_kana: str
    name_romaji: str


class CompanySource:
    """EDINET company names with romaji/kana derived once and cached."""

    def __init__(
        self,
        sources: DataSources = DataSources(),
        fallback: tuple[str, ...] = (
            "株式会社東京商事",
            "有限会社大阪物産",
            "合同会社名古屋開発",
            "株式会社福岡企画",
        ),
    ):
        names = self._load(sources)
        if not names:
            print("EDINET unavailable - using small fallback list")
            names = list(fallback)
        self.companies: tuple[Company, ...] = tuple(
            Company(n, to_katakana(n), to_romaji(n).title()) for n in names
        )
        print(f"Company source: {len(self.companies):,} real EDINET names (kana=katakana)")

    @staticmethod
    def _load(sources: DataSources) -> list[str]:
        try:
            from datasets import load_dataset

            ds = load_dataset(sources.edinet_dataset, split=sources.edinet_split)
            return list(dict.fromkeys(row["company"] for row in ds if row.get("company")))
        except (ImportError, KeyError, OSError, RuntimeError, ValueError):
            return []

    def sample(self, rng: np.random.Generator) -> Company:
        return self.companies[int(rng.integers(len(self.companies)))]


# --------------------------------------------------------------------------
# Addresses (Japan Post ken_all, per-prefecture pools)
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AddressRow:
    postal_code: str
    prefecture: str
    city: str
    town: str
    has_chome: bool


class AddressSource:
    """Japan Post ken_all pools keyed by prefecture.

    Since the persona carries a real prefecture, addresses sampled from that
    prefecture's pool keep the persona's address ge-consistent with its
    demographics (Tokyo-heavy market, island prefectures rare, ...).
    """

    def __init__(self, data_dir: Path, sources: DataSources = DataSources()):
        csv_path = data_dir / sources.postal_csv_name
        zip_path = data_dir / sources.postal_zip_name
        if not csv_path.exists():
            self._download(zip_path, csv_path, sources)
        by_prefecture: dict[str, list[AddressRow]] = {}
        for row in self._parse(csv_path):
            by_prefecture.setdefault(row.prefecture, []).append(row)
        self.by_prefecture: dict[str, tuple[AddressRow, ...]] = {
            k: tuple(v) for k, v in by_prefecture.items()
        }
        self.prefectures: tuple[str, ...] = tuple(self.by_prefecture)
        self._pref_counts = np.array([len(v) for v in self.by_prefecture.values()], dtype=np.int64)
        total = self._pref_counts.sum()
        self._pref_weights = self._pref_counts / total
        print(f"Address source: {total:,} towns across {len(self.prefectures)} prefectures")

    @staticmethod
    def _download(zip_path: Path, csv_path: Path, sources: DataSources) -> None:
        if not zip_path.exists():
            print(f"Downloading Japan Post postal dataset -> {zip_path}")
            request = urllib.request.Request(
                sources.japan_post_zip_url,
                headers={"User-Agent": "Mozilla/5.0 (nayose synthetic data)"},
            )
            context = None
            try:
                import certifi

                context = ssl.create_default_context(cafile=certifi.where())
            except (ImportError, OSError, ValueError):
                context = None
            with urllib.request.urlopen(request, timeout=120, context=context) as response:
                zip_path.write_bytes(response.read())
        with zipfile.ZipFile(zip_path) as archive:
            member = next(name for name in archive.namelist() if name.upper().endswith(".CSV"))
            csv_path.write_bytes(archive.read(member))
            print(f"  extracted {member} -> {csv_path}")

    @staticmethod
    def _parse(csv_path: Path) -> list[AddressRow]:
        rows: list[AddressRow] = []
        seen: set[str] = set()
        with open(csv_path, encoding="cp932", newline="") as handle:
            for raw in csv.reader(handle):
                if len(raw) < 12:
                    continue
                postal = raw[2].strip()
                town = raw[8]
                if town in PLACEHOLDER_TOWNS or postal == "0000000" or postal in seen:
                    continue
                seen.add(postal)
                rows.append(AddressRow(postal, raw[6], raw[7], town, raw[11] == "1"))
        return rows

    def random_prefecture(self, rng: np.random.Generator) -> str:
        """Prefecture weighted by real town counts (proxy for population)."""
        return self.prefectures[int(rng.choice(len(self.prefectures), p=self._pref_weights))]

    def sample(self, rng: np.random.Generator, prefecture: str | None = None) -> AddressRow:
        pool_key = prefecture if prefecture in self.by_prefecture else self.random_prefecture(rng)
        pool = self.by_prefecture[pool_key]
        return pool[int(rng.integers(len(pool)))]

    def format(
        self, row: AddressRow, rng: np.random.Generator, building_composer: BuildingComposer
    ) -> tuple[str, str]:
        """Postal + full address: apartments (70%) with a composed building
        name; chome-specific towns skip the extra 丁目."""
        is_apartment = rng.random() < 0.7
        if is_apartment:
            room = f"{int(rng.integers(1, 30))}{rng.choice(['A', 'B', 'C', ''])}号室"
            building = building_composer.compose(row, rng)
            suffix = f" {building} {room}"
        else:
            suffix = ""
        if row.has_chome:
            block = f"{int(rng.integers(1, 20))}番{int(rng.integers(1, 10))}号"
        else:
            block = (
                f"{int(rng.integers(1, 20))}丁目{int(rng.integers(1, 20))}番"
                f"{int(rng.integers(1, 10))}号"
            )
        address = f"{row.prefecture}{row.city}{row.town}{block}{suffix}"
        return row.postal_code, address


class BuildingComposer:
    """Compose realistic, prefecture-local Japanese building names from a
    compact real lexicon (no per-prefecture hardcoded catalogues)."""

    STYLES: tuple[str, ...] = (
        "パークタワー",
        "シティタワー",
        "グランドタワー",
        "パークハウス",
        "プラウド",
        "グランメゾン",
        "パークシティ",
        "リヴェール",
        "サンハイツ",
        "グリーンコーポ",
        "パレス",
        "エクセルハウス",
        "アイリス",
        "コスモタワー",
        "ルミナス",
        "フローラ",
        "ベイサイド",
        "フォレスト",
        "ヒルズ",
        "レジデンス",
    )
    PLACE_WORDS: tuple[str, ...] = (
        "さくら",
        "もみじ",
        "みどり",
        "しらかば",
        "はやて",
        "あおぞら",
        "ゆうひ",
        "ひかり",
        "ほし",
        "つき",
        "かえで",
        "ふじ",
        "あさひ",
        "ゆめ",
        "そよかぜ",
    )

    def compose(self, row: AddressRow, rng: np.random.Generator) -> str:
        style = self.STYLES[int(rng.integers(len(self.STYLES)))]
        place = self.PLACE_WORDS[int(rng.integers(len(self.PLACE_WORDS)))]
        return f"{style}{place}"
