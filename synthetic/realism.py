"""Baseline contact realism: phone styles and email domains (SRP).

The engine and query builders ASK for a realistic contact value; the format
knowledge (fixed-line vs mobile, era-appropriate carrier/ISP domains) lives
here and in config.py — never hardcoded in the engine.

Privacy: domain suffixes are real shapes only; every address is synthetic.
"""

from __future__ import annotations

import numpy as np

from .config import CHANNEL_CONTACT_PROFILE, DEFAULT_LANDLINE_SHARE, EMAIL_DOMAINS
from .jp import ascii_romaji

LANDLINE_CODES = ("03", "06", "011", "022", "045", "052", "075", "082", "092")
MOBILE_PREFIXES = ("090", "080", "070")


def generate_phone(rng: np.random.Generator, *, landline: bool = False) -> str:
    code = str(rng.choice(LANDLINE_CODES)) if landline else str(rng.choice(MOBILE_PREFIXES))
    return f"{code}-{int(rng.integers(1000, 9999)):04d}-{int(rng.integers(1000, 9999)):04d}"


def default_phone(rng: np.random.Generator) -> str:
    """Party-level default: a realistic mobile/fixed-line mix (no channel yet)."""
    return generate_phone(rng, landline=rng.random() < DEFAULT_LANDLINE_SHARE)


def generate_email(kanji: str, romaji: str, rng: np.random.Generator, family: str = "") -> str:
    """'{first}.{last}@domain' for two-part names, else a synthetic local part.

    ``family`` selects the era-appropriate domain pool (``modern``/``isp``); an
    empty family draws from ALL pools (mixed personal mail).
    """
    if family:
        domains = EMAIL_DOMAINS.get(family, EMAIL_DOMAINS["modern"])
    else:
        domains = tuple(d for pool in EMAIL_DOMAINS.values() for d in pool)
    domain = str(rng.choice(domains))
    romaji_parts = romaji.split()
    if len(kanji.split()) == 2 and len(romaji_parts) == 2:
        return f"{ascii_romaji(romaji_parts[0])}.{ascii_romaji(romaji_parts[1])}@{domain}"
    return f"user{int(rng.integers(100000, 999999))}@{domain}"


def channel_family(channel: str) -> str:
    """Email domain pool for a channel (ISP flavor on vintage/OCR docs)."""
    profile = CHANNEL_CONTACT_PROFILE.get(channel, CHANNEL_CONTACT_PROFILE["web"])
    family = "isp" if float(profile["email_family"]) >= 1.0 else "modern"
    return family


def channel_landline_share(channel: str) -> float:
    profile = CHANNEL_CONTACT_PROFILE.get(channel, CHANNEL_CONTACT_PROFILE["web"])
    return float(profile["landline_share"])
