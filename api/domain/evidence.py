from typing import Any

import pandas as pd

from api.domain.normalize import normalize_record

VISIBLE_FIELDS = (
    "record_id",
    "policy_id",
    "agency_id",
    "channel",
    "observed_at",
    "party_type",
    "name_kanji",
    "name_kana",
    "name_romaji",
    "date_birth",
    "postal_code",
    "address",
    "phone",
    "email",
)
NORMALIZED_FIELDS = {
    "party_type": "party_type",
    "name_kanji": "n_name_loose",
    "name_kana": "n_kana",
    "name_romaji": "n_romaji",
    "date_birth": "n_dob",
    "postal_code": "n_postal",
    "address": "n_address",
    "phone": "n_phone",
    "email": "n_email",
}


def text(value: Any) -> str:
    return "" if value is None or pd.isna(value) else str(value)


def visible_record(record: dict[str, Any]) -> dict[str, str]:
    return {field: text(record.get(field)) for field in VISIBLE_FIELDS}


def compare_fields(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    a, b = visible_record(left), visible_record(right)
    na, nb = normalize_record(a), normalize_record(b)
    result = {}
    for field, key in NORMALIZED_FIELDS.items():
        av, bv = a[field], b[field]
        an, bn = na[key] if av.strip() else "", nb[key] if bv.strip() else ""
        if not an or not bn:
            status = "missing"
        elif av == bv:
            status = "exact"
        elif an == bn:
            status = "normalized"
        else:
            status = "different"
        result[field] = {
            "left": av,
            "right": bv,
            "left_normalized": an,
            "right_normalized": bn,
            "status": status,
        }
    return result


def sufficient(record: dict[str, str]) -> bool:
    normalized = normalize_record(record)
    return any(normalized[key] for key in ("n_name_loose", "n_kana", "n_phone", "n_email"))
