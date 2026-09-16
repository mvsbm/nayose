"""Record normalization — canonical preprocessing shared by matching, retrieval
and serving. This module is the single source of truth for normalization; the
inference engine re-exports these helpers."""

from __future__ import annotations

import re
import unicodedata as ud
from typing import Any

import pandas as pd

PUNCT_RE = re.compile(r"[\s\u3000\-‐‑‒–—―ーｰ・･,，.．/／\\()（）\[\]【】]+")
DIGITS_RE = re.compile(r"\D+")
OLD_KANJI = str.maketrans(
    {"髙": "高", "﨑": "崎", "邉": "辺", "邊": "辺", "濱": "浜", "澤": "沢", "齋": "斎", "國": "国"}
)
LEGAL_FORMS = [
    "株式会社",
    "有限会社",
    "合同会社",
    "一般社団法人",
    "一般財団法人",
    "医療法人",
    "学校法人",
    "社会福祉法人",
    "（株）",
    "(株)",
    "㈱",
]
ADDRESS_WORDS = {"丁目": "-", "番地": "-", "番": "-", "号": "", "ノ": "-"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    return "" if isinstance(missing, bool) and missing else str(value)


def _compact(s: Any) -> str:
    return PUNCT_RE.sub("", ud.normalize("NFKC", _text(s))).lower()


def normalize_name_strict(raw: Any) -> str:
    return _compact(raw)


def normalize_name(raw: Any) -> str:
    v = ud.normalize("NFKC", _text(raw)).translate(OLD_KANJI)
    for form in LEGAL_FORMS:
        v = v.replace(form, "")
    return _compact(v).strip()


def normalize_name_signature(raw: Any) -> str:
    return "".join(sorted(normalize_name(raw)))


def normalize_kana(raw: Any) -> str:
    value = ud.normalize("NFKC", _text(raw))
    value = "".join(chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char for char in value)
    return _compact(value)


def normalize_romaji(raw: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", ud.normalize("NFKC", _text(raw)).lower())


def normalize_phone(raw: Any) -> str:
    d = DIGITS_RE.sub("", ud.normalize("NFKC", _text(raw)))
    return ("0" + d[2:]) if d.startswith("81") and len(d) > 10 else d


def normalize_postal(raw: Any) -> str:
    digits = DIGITS_RE.sub("", ud.normalize("NFKC", _text(raw)))
    return digits.zfill(7)[:7] if digits else ""


def normalize_address(raw: Any) -> str:
    value = ud.normalize("NFKC", _text(raw)).translate(OLD_KANJI)
    for before, after in ADDRESS_WORDS.items():
        value = value.replace(before, after)
    value = re.sub(r"-+", "-", value)
    return _compact(value).strip("-")


def normalize_email(raw: Any) -> str:
    return _compact(raw)


def normalize_dob(raw: Any) -> str:
    d = DIGITS_RE.sub("", ud.normalize("NFKC", _text(raw)))
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d


def normalize_query_fields(query: str) -> dict[str, str]:
    """Normalize one free-text query into the server-owned field views."""
    has_digit = any(char.isdigit() for char in query)
    return {
        "name_kanji": normalize_name(query),
        "name_kana": normalize_kana(query),
        "name_romaji": normalize_name(query),
        "phone": normalize_phone(query) if has_digit else "",
        "postal_code": normalize_postal(query) if has_digit else "",
        "address": normalize_address(query),
        "email": normalize_email(query),
        "date_birth": normalize_dob(query) if has_digit else "",
        "party_type": normalize_name(query),
    }


def normalize_record(rec: dict[str, Any]) -> dict[str, str]:
    """Normalize a raw record into all required views."""
    values = {
        "name_kanji": rec.get("name_kanji", ""),
        "name_kana": rec.get("name_kana", ""),
        "name_romaji": rec.get("name_romaji", ""),
        "date_birth": rec.get("date_birth", ""),
        "postal_code": rec.get("postal_code", ""),
        "address": rec.get("address", ""),
        "phone": rec.get("phone", ""),
        "email": rec.get("email", ""),
    }
    n_name = normalize_name(values["name_kanji"])
    n_kana = normalize_kana(values["name_kana"])
    n_phone = normalize_phone(values["phone"])
    n_postal = normalize_postal(values["postal_code"])
    n_address = normalize_address(values["address"])
    n_email = normalize_email(values["email"])
    n_dob = normalize_dob(values["date_birth"])
    n_romaji = normalize_romaji(values["name_romaji"])
    party_type = _compact(rec.get("party_type", "person"))
    return {
        "party_type": party_type,
        "n_party_type": party_type,
        "n_name_strict": normalize_name_strict(values["name_kanji"]),
        "n_name_loose": n_name,
        "n_name_signature": normalize_name_signature(values["name_kanji"]),
        "n_kana": n_kana,
        "n_romaji": n_romaji,
        "n_dob": n_dob,
        "n_postal": n_postal,
        "n_address": n_address,
        "n_phone": n_phone,
        "n_phone_suffix8": n_phone[-8:] if len(n_phone) >= 8 else n_phone,
        "n_email": n_email,
        "n_email_local": n_email.split("@")[0] if "@" in n_email else n_email,
        "n_email_domain": n_email.split("@")[1] if "@" in n_email else "",
        "retrieval_text": " ".join(
            [
                f"type={party_type}",
                f"name={n_name}",
                f"kana={n_kana}",
                f"romaji={n_romaji}",
                f"dob={n_dob}",
                f"postal={n_postal}",
                f"address={n_address}",
                f"phone={n_phone}",
                f"email={n_email}",
            ]
        ),
    }


def normalize_frame(frame: pd.DataFrame, id_column: str) -> pd.DataFrame:
    """Build the same normalized views for a model-visible table."""
    out = frame.copy()
    out["n_name_strict"] = out["name_kanji"].map(normalize_name_strict)
    out["n_name_loose"] = out["name_kanji"].map(normalize_name)
    out["n_name_signature"] = out["n_name_loose"].map(lambda value: "".join(sorted(value)))
    out["n_kana"] = out["name_kana"].map(normalize_kana)
    out["n_romaji"] = out["name_romaji"].map(normalize_romaji)
    out["n_dob"] = out["date_birth"].map(normalize_dob)
    out["n_postal"] = out["postal_code"].map(normalize_postal)
    out["n_address"] = out["address"].map(normalize_address)
    out["n_phone"] = out["phone"].map(normalize_phone)
    out["n_email"] = out["email"].map(normalize_email)
    out["n_party_type"] = out["party_type"].map(_compact)
    out["n_phone_suffix8"] = out.n_phone.str[-8:].where(out.n_phone.str.len() >= 8, "")
    out["n_email_local"] = (
        out.n_email.str.split("@", n=1).str[0].where(out.n_email.str.contains("@"), out.n_email)
    )
    out["n_email_domain"] = (
        out.n_email.str.split("@", n=1).str[-1].where(out.n_email.str.contains("@"), "")
    )
    out["n_postal3"] = out.n_postal.str[:3].where(out.n_postal.str.len() >= 3, "")
    out["n_name_initial"] = out.n_kana.str[:1].mask(out.n_kana.eq(""), out.n_name_loose.str[:1])
    out["populated_fields"] = (
        out[
            [
                "n_name_loose",
                "n_kana",
                "n_romaji",
                "n_dob",
                "n_postal",
                "n_address",
                "n_phone",
                "n_email",
            ]
        ]
        .ne("")
        .sum(axis=1)
    )
    out["retrieval_text"] = (
        "type="
        + out.n_party_type
        + " name="
        + out.n_name_loose
        + " kana="
        + out.n_kana
        + " romaji="
        + out.n_romaji
        + " dob="
        + out.n_dob
        + " postal="
        + out.n_postal
        + " address="
        + out.n_address
        + " phone="
        + out.n_phone
        + " email="
        + out.n_email
    )
    assert out[id_column].is_unique
    return out
