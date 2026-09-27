"""Deterministic corruption for adversarial external benchmark evaluation."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from api.benchmarking.contracts import BenchmarkDataset, BenchmarkQuery


@dataclass(frozen=True, slots=True)
class StressConfig:
    seed: int = 42
    drop_probability: float = 0.2


def stress_dataset(
    dataset: BenchmarkDataset, config: StressConfig = StressConfig()
) -> BenchmarkDataset:
    if not 0.0 <= config.drop_probability < 1.0:
        raise ValueError("drop_probability must be in [0, 1)")
    rng = random.Random(config.seed)
    queries = tuple(_stress_query(query, rng, config.drop_probability) for query in dataset.queries)
    metadata = {
        **dataset.metadata,
        "stress_profile": "compound_typo_missingness",
        "stress_seed": str(config.seed),
        "stress_drop_probability": str(config.drop_probability),
    }
    return replace(dataset, queries=queries, metadata=metadata)


def _stress_query(
    query: BenchmarkQuery, rng: random.Random, drop_probability: float
) -> BenchmarkQuery:
    values = dict(query.fields)
    families: list[str] = []
    original = list(values.items())
    for field, value in original:
        if not value or field == "party_type":
            continue
        if rng.random() < drop_probability and len(values) > 1:
            values.pop(field, None)
            families.append(f"missing_{field}")
            continue
        mutated, family = _mutate(field, value, rng)
        values[field] = mutated
        families.append(family)
    observed_fields = [field for field in values if field != "party_type"]
    original_observed = [(field, value) for field, value in original if field != "party_type"]
    if not observed_fields and original_observed:
        field, value = original_observed[0]
        values[field], family = _mutate(field, value, rng)
        families.append(family)
    return replace(
        query,
        fields=values,
        slice_name=f"stress:{','.join(sorted(set(families))) or 'unchanged'}",
    )


def _mutate(field: str, value: str, rng: random.Random) -> tuple[str, str]:
    if field in {"phone", "postal_code", "date_birth"}:
        mutated, _family = _digit_typo(value, rng)
        return mutated, f"{field}_typo"
    if field in {"name_kanji", "name_kana", "name_romaji"}:
        mutated, _family = _text_typo(value, rng)
        return mutated, f"{field}_typo"
    if field == "address":
        mutated, _family = _text_typo(value, rng)
        return mutated, "address_typo"
    mutated, _family = _text_typo(value, rng)
    return mutated, f"{field}_typo"


def _digit_typo(value: str, rng: random.Random) -> tuple[str, str]:
    positions = [index for index, char in enumerate(value) if char.isdigit()]
    if not positions:
        return _text_typo(value, rng)
    index = rng.choice(positions)
    digit = value[index]
    replacement = str((int(digit) + rng.randint(1, 9)) % 10)
    return value[:index] + replacement + value[index + 1 :], "digit"


def _text_typo(value: str, rng: random.Random) -> tuple[str, str]:
    positions = [index for index, char in enumerate(value) if char.isalnum()]
    if not positions:
        return value + "x", "insertion"
    index = rng.choice(positions)
    operation = rng.randrange(3)
    if operation == 0 and len(value) > 1:
        return value[:index] + value[index + 1 :], "deletion"
    if operation == 1:
        return value[:index] + value[index] + value[index:], "duplication"
    replacement = "x" if value[index].casefold() != "x" else "q"
    return value[:index] + replacement + value[index + 1 :], "substitution"
