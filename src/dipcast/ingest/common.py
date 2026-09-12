"""Shared helpers for ingestion: timestamp normalisation and parquet I/O."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def to_utc(s: pd.Series) -> pd.Series:
    """Coerce ArcGIS timestamps (epoch ms ints or ISO strings) to tz-aware UTC."""
    if s.dtype.kind in "iuf":
        return pd.to_datetime(s, unit="ms", utc=True, errors="coerce")
    # Mixed: some layers return ISO strings with offsets, some epoch ms as strings.
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().mean() > 0.9:
        return pd.to_datetime(num, unit="ms", utc=True, errors="coerce")
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")


def write_parquet(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log.info("wrote %s rows -> %s", len(df), path)
    return path
