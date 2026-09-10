from __future__ import annotations

from typing import cast

import numpy as np

from synthetic.config import SyntheticConfig
from synthetic.engine import iter_clean_party_rows
from synthetic.providers import (
    AddressRow,
    AddressSource,
    BuildingComposer,
    Company,
    CompanySource,
    Person,
    PersonaSource,
)


class _PersonaSource:
    def sample(self, rng: np.random.Generator, n: int) -> list[Person]:
        return [
            Person(
                "山田 太郎",
                "ヤマダ タロウ",
                "Yamada Taro",
                "M",
                40,
                "東京都",
                "関東",
                "会社員",
                "大学",
                "既婚",
            )
        ]


class _CompanySource:
    def sample(self, rng: np.random.Generator) -> Company:
        return Company("株式会社試験", "カブシキガイシャシケン", "Kabushiki Gaisya Shiken")


class _AddressSource:
    def sample(self, rng: np.random.Generator, prefecture: str | None = None) -> AddressRow:
        return AddressRow("1000001", "東京都", "千代田区", "千代田", True)

    def format(
        self, row: AddressRow, rng: np.random.Generator, buildings: BuildingComposer
    ) -> tuple[str, str]:
        return row.postal_code, "東京都千代田区千代田1-1"


def test_clean_party_stream_honors_exact_target_without_hidden_columns() -> None:
    cfg = SyntheticConfig(profile="preflight", n_parties_override=10, twin_rate=0.0)
    rows = list(
        iter_clean_party_rows(
            cfg,
            np.random.default_rng(7),
            cast(PersonaSource, _PersonaSource()),
            cast(CompanySource, _CompanySource()),
            cast(AddressSource, _AddressSource()),
            cast(BuildingComposer, object()),
            target_rows=5,
        )
    )

    assert len(rows) == 5
    assert {"truth_id", "record_quality", "transformation"}.isdisjoint(rows[0])
    assert {"party_type", "name_kanji", "date_birth", "postal_code"} <= rows[0].keys()
