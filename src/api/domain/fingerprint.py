"""Deterministic fingerprints for matcher-visible reference data."""

from __future__ import annotations

import hashlib

import pandas as pd


def data_fingerprint(frame: pd.DataFrame) -> str:
    """Return the serving fingerprint for a reference frame."""
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).values.tobytes()
    ).hexdigest()
