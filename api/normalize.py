"""Record normalization — canonical preprocessing shared by matching, retrieval
and serving. Moved out of the legacy inference module so new code does not
depend on it; the inference engine re-exports these (single source of truth)."""

from __future__ import annotations

import re
import unicodedata as ud
from typing import Any

PUNCT_RE = re.compile(r"[\s\u3000\-‐‑‒–—―ーｰ・･,，.．/／\\()（）\[\]【】]+")
DIGITS_RE = re.compile(r"\D+")
OLD_KANJI = str.maketrans(
    {"髙": "高", "﨑": "崎", "邉": "辺", "邊": "辺", "濱": "浜", "澤": "沢", "齋": "斎", "國": "国"}
)
LEGAL_FORMS = ["株式会社", "有限会社", "合同会社", "（株）", "(株)", "㈱"]


def _compact(s: str) -> str:
    return PUNCT_RE.sub("", ud.normalize("NFKC", s)).lower()


def normalize_name(raw: str) -> str:
    v = _compact(raw).translate(OLD_KANJI)
    for f in sorted(LEGAL_FORMS, key=len, reverse=True):
        v = v.replace(_compact(f), "")
    return v.strip()


def normalize_kana(raw: str) -> str:
    return _compact(raw)


def normalize_phone(raw: str) -> str:
    d = DIGITS_RE.sub("", ud.normalize("NFKC", raw))
    return ("0" + d[2:]) if d.startswith("81") and len(d) > 10 else d


def normalize_postal(raw: str) -> str:
    return DIGITS_RE.sub("", ud.normalize("NFKC", raw)).zfill(7)[:7]


def normalize_address(raw: str) -> str:
    v = _compact(raw)
    for old, new in {"丁目": "-", "番地": "-", "番": "-", "号": ""}.items():
        v = v.replace(old, new)
    return re.sub(r"-+", "-", v).strip("-")


def normalize_email(raw: str) -> str:
    return _compact(raw)


def normalize_dob(raw: str) -> str:
    d = DIGITS_RE.sub("", ud.normalize("NFKC", raw))
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
    name = str(rec.get("name_kanji", "")).strip()
    kana = str(rec.get("name_kana", "")).strip()
    n_name = normalize_name(name)
    n_kana = normalize_kana(kana)
    n_phone = normalize_phone(str(rec.get("phone", "")).strip())
    n_postal = normalize_postal(str(rec.get("postal_code", "")).strip())
    n_address = normalize_address(str(rec.get("address", "")).strip())
    n_email = normalize_email(str(rec.get("email", "")).strip())
    n_dob = normalize_dob(str(rec.get("date_birth", "")).strip())

    return {
        "party_type": _compact(str(rec.get("party_type", "person"))),
        "n_name_loose": n_name,
        "n_name_signature": n_name[:4] if len(n_name) >= 4 else n_name,
        "n_kana": n_kana,
        "n_romaji": _compact(str(rec.get("name_romaji", "")).strip()),
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
                f"type={_compact(str(rec.get('party_type', 'person')))}",
                f"name={n_name}",
                f"kana={n_kana}",
                f"romaji={_compact(str(rec.get('name_romaji', '')).strip())}",
                f"dob={n_dob}",
                f"postal={n_postal}",
                f"address={n_address}",
                f"phone={n_phone}",
                f"email={n_email}",
            ]
        ),
    }
