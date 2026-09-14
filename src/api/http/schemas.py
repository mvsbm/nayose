"""Request models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from api.domain.evidence import NORMALIZED_FIELDS, VISIBLE_FIELDS


class AdminJobIn(BaseModel):
    action: str = Field(..., max_length=32)
    params: dict[str, Any] = Field(default_factory=dict, max_length=32)


class AdminActionIn(BaseModel):
    action: str = Field(..., max_length=32)
    params: dict[str, Any] = Field(default_factory=dict, max_length=32)


class RegisterIn(BaseModel):
    record: dict[str, Any] = Field(..., max_length=32)


class ImportIn(BaseModel):
    rows: list[dict[str, Any]] = Field(..., max_length=10_000)
    dry_run: bool = False


class UndoIn(BaseModel):
    record_ids: list[str] = Field(..., max_length=500)


class RecommendIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    record: dict[str, str]
    thresholds: dict[str, float] | None = None

    @field_validator("record")
    @classmethod
    def validate_record(cls, record: dict[str, str]) -> dict[str, str]:
        if set(record) - set(VISIBLE_FIELDS) or any(len(value) > 1000 for value in record.values()):
            raise ValueError("unsupported field or input too long")
        return {key: value.strip() for key, value in record.items() if key in NORMALIZED_FIELDS}

    @field_validator("thresholds")
    @classmethod
    def validate_thresholds(cls, thresholds: dict[str, float] | None) -> dict[str, float] | None:
        if thresholds is None:
            return None
        unknown = set(thresholds) - {"auto", "review"}
        if unknown:
            raise ValueError(f"unsupported threshold keys: {sorted(unknown)}")
        if any(not 0.0 <= value <= 1.0 for value in thresholds.values()):
            raise ValueError("thresholds must be in [0, 1]")
        if (
            thresholds.get("auto") is not None
            and thresholds.get("review") is not None
            and thresholds["auto"] < thresholds["review"]
        ):
            raise ValueError("auto threshold must be >= review threshold")
        return thresholds


class DecisionItemIn(BaseModel):
    left: str = Field(..., max_length=200)
    right: str = Field(..., max_length=200)
    ts: str = Field(default="", max_length=100)
    reviewer: str | None = Field(default=None, max_length=100)


class DecisionsIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    items: list[DecisionItemIn] = Field(default_factory=list, max_length=500)
    revision: int = Field(default=0, ge=0)


class QueryIn(BaseModel):
    run_id: str = Field(..., max_length=200)
    sql: str = Field(..., max_length=8000)
