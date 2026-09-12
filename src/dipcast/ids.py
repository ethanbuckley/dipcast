"""Overflow identifiers changed scheme in 2024 and older annual returns often
carry none at all. This module gives every row a canonical current site_id.

Order of resolution for a row with no current-format id:
  1. old-format id via the Hub lookup table (and the returns' own old ids)
  2. exact (site name, easting, northing) match to a row that has a current id
  3. exact (easting, northing) match when that location has exactly one id
  4. exact normalised site name when that name has exactly one id
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from dipcast import config

log = logging.getLogger(__name__)

CURRENT = re.compile(r"^[A-Z]{3}\d{5}$")     # e.g. UUP00123, YWS00144
OLD = re.compile(r"^[A-Z]{2,3}\d{4}$")       # e.g. UUG0123, AnW0194


def norm_name(s: pd.Series) -> pd.Series:
    return (s.fillna("").str.upper().str.replace(r"[^A-Z0-9]+", " ", regex=True).str.strip())


def load_lookup() -> dict[str, str]:
    p = config.PROCESSED / "id_lookup.parquet"
    if not p.exists():
        return {}
    lk = pd.read_parquet(p).dropna()
    return dict(zip(lk["old_site_id"], lk["site_id"], strict=True))


def canonicalise_annual_returns(ar: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with `site_id` canonical where resolvable and `id_source`
    saying how. Rows that cannot be resolved keep site_id = NaN."""
    df = ar.copy()
    df["raw_unique_id"] = df["site_id"]
    lookup = load_lookup()
    lookup.update({o: n for o, n in zip(df["old_site_id"], df["site_id"], strict=True)
                   if isinstance(o, str) and isinstance(n, str) and CURRENT.match(n)})

    sid = df["site_id"].where(df["site_id"].astype("string").str.match(CURRENT.pattern, na=False))
    src = pd.Series(pd.NA, index=df.index, dtype="string")
    src[sid.notna()] = "current"

    # 1. old ids, either in unique_id or old_unique_id
    for col in ("site_id", "old_site_id"):
        cand = df[col].map(lookup)
        fill = sid.isna() & cand.notna()
        sid[fill] = cand[fill]
        src[fill] = "lookup"

    # Reference table from resolved rows.
    ref = df.assign(sid=sid, nm=norm_name(df["site_name"]))
    ref = ref[ref["sid"].notna() & ref["ngr_easting"].notna()]
    by_name_xy = (ref.groupby(["company", "nm", "ngr_easting", "ngr_northing"])["sid"]
                  .agg(lambda s: s.iloc[0] if s.nunique() == 1 else pd.NA).dropna())
    by_xy = ref.groupby(["company", "ngr_easting", "ngr_northing"])["sid"].agg(
        lambda s: s.iloc[0] if s.nunique() == 1 else pd.NA).dropna()
    by_name = ref.groupby(["company", "nm"])["sid"].agg(
        lambda s: s.iloc[0] if s.nunique() == 1 else pd.NA).dropna()

    nm = norm_name(df["site_name"])
    for label, keys, table in (
        ("name+ngr", ["company", "nm", "ngr_easting", "ngr_northing"], by_name_xy),
        ("ngr", ["company", "ngr_easting", "ngr_northing"], by_xy),
        ("name", ["company", "nm"], by_name),
    ):
        todo = sid.isna()
        if not todo.any():
            break
        keyed = df.loc[todo].assign(nm=nm[todo])
        idx = pd.MultiIndex.from_frame(keyed[keys])
        cand = pd.Series(table.reindex(idx).to_numpy(), index=keyed.index)
        fill = cand.notna()
        sid[fill[fill].index] = cand[fill]
        src[fill[fill].index] = label

    df["site_id"] = sid
    df["id_source"] = src
    log.info("canonical ids: %s", df.groupby("year")["id_source"].value_counts(dropna=False)
             .unstack(fill_value=0).to_dict("index"))
    return df
