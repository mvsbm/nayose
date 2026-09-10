"""Typed, conservative fusion for heterogeneous candidate-discovery channels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RetrievalFusionPolicy:
    """Rank-fusion policy that prevents correlated weak views from dominating.

    The record-level dense view is the primary semantic signal. Field views are
    intentionally lower-weight because name and alias evidence are correlated
    and are not identity decisions by themselves.
    """

    rank_constant: int = 60
    exact_block_weight: float = 0.08
    sparse_weight: float = 0.8
    record_dense_weight: float = 1.0
    name_view_weight: float = 0.25
    address_view_weight: float = 0.4
    alias_view_weight: float = 0.15

    def __post_init__(self) -> None:
        if self.rank_constant < 1:
            raise ValueError("rank_constant must be positive")
        for name in (
            "exact_block_weight",
            "sparse_weight",
            "record_dense_weight",
            "name_view_weight",
            "address_view_weight",
            "alias_view_weight",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if (
            self.name_view_weight + self.address_view_weight + self.alias_view_weight
            >= self.record_dense_weight
        ):
            raise ValueError("field-view weights must remain below record-dense weight")

    def view_weights(self, query: dict[str, Any] | None = None) -> dict[str, float]:
        weights = {
            "name": self.name_view_weight,
            "address": self.address_view_weight,
            "alias": self.alias_view_weight,
        }
        if query is None:
            return weights
        has_name = any(query.get(field, "") for field in ("n_name_loose", "n_kana", "n_romaji"))
        has_address = any(query.get(field, "") for field in ("n_postal", "n_address"))
        has_stable = any(query.get(field, "") for field in ("n_dob", "n_phone", "n_email"))
        weights["name"] = 0.30 if has_name and not has_stable else 0.18 if has_name else 0.0
        weights["alias"] = 0.12 if has_name and not has_stable else 0.08 if has_name else 0.0
        weights["address"] = 0.45 if has_address else 0.0
        return weights

    def view_weight(self, view: str, query: dict[str, Any] | None = None) -> float:
        return self.view_weights(query).get(view, 0.0)

    def _rank_contribution(self, rank: int, weight: float) -> float:
        return weight / (self.rank_constant + rank) if rank > 0 else 0.0

    def score(self, state: dict[str, Any], query: dict[str, Any] | None = None) -> float:
        exact_blocks = state.get("exact_blocks", set())
        exact_count = int(state.get("exact_count", len(exact_blocks)))
        score = self.exact_block_weight * min(exact_count, 3)
        score += self._rank_contribution(int(state.get("sparse_rank", 0)), self.sparse_weight)
        score += self._rank_contribution(int(state.get("dense_rank", 0)), self.record_dense_weight)
        for view, rank in state.get("dense_view_ranks", {}).items():
            score += self._rank_contribution(int(rank), self.view_weight(str(view), query))
        return score


DEFAULT_RETRIEVAL_FUSION_POLICY = RetrievalFusionPolicy()


def retrieval_fusion_score(
    state: dict[str, Any],
    policy: RetrievalFusionPolicy = DEFAULT_RETRIEVAL_FUSION_POLICY,
    query: dict[str, Any] | None = None,
) -> float:
    return policy.score(state, query)


def retrieval_methods(state: dict[str, Any]) -> list[str]:
    methods = [f"exact:{block}" for block in sorted(state.get("exact_blocks", []))]
    if int(state.get("sparse_rank", 0)) > 0:
        methods.append("sparse")
    if int(state.get("dense_rank", 0)) > 0:
        methods.append("dense")
    methods.extend(f"dense:{view}" for view in sorted(state.get("dense_view_ranks", {})))
    return methods
