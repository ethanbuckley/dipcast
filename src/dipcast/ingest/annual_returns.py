"""Environment Agency EDM annual returns: per-overflow spill counts by year."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from pyproj import Transformer

from dipcast import config
from dipcast.arcgis import fetch_all
from dipcast.ids import canonicalise_annual_returns
from dipcast.ingest.common import write_parquet

log = logging.getLogger(__name__)

FIELDS = [
    "unique_id", "old_unique_id_pre_2024", "water_company_name", "annual_return_year",
    "site_name_wasc_op_name", "storm_discharge_asset_type", "counted_spills_12_24hr_calculated",
    "total_spill_duration_hrs_calculated", "longterm_average_spill_count_calculated",
    "edm_operation_percent_calculated", "wfd_waterbody_id", "wfd_waterbody_name",
    "receiving_water_environment_common_name_ea_condat", "bathing_water", "ngr_easting",
    "ngr_northing", "ngr_valid", "has_data",
]
RENAME = {
    "unique_id": "site_id", "old_unique_id_pre_2024": "old_site_id",
    "water_company_name": "company", "annual_return_year": "year",
    "site_name_wasc_op_name": "site_name", "storm_discharge_asset_type": "asset_type",
    "counted_spills_12_24hr_calculated": "spills", "total_spill_duration_hrs_calculated": "spill_hours",
    "longterm_average_spill_count_calculated": "lta_spills",
    "edm_operation_percent_calculated": "edm_operational_pct",
    "receiving_water_environment_common_name_ea_condat": "receiving_water",
}


def fetch_annual_returns() -> pd.DataFrame:
    rows = fetch_all(config.EDM_ANNUAL_RETURNS, geometry=False, out_fields=",".join(FIELDS))
    df = pd.DataFrame(rows).rename(columns=RENAME)
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    for c in ["spills", "spill_hours", "lta_spills", "edm_operational_pct", "ngr_easting", "ngr_northing"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    ok = df["ngr_easting"].notna() & df["ngr_northing"].notna() & (df.get("ngr_valid", 1) == 1)
    tr = Transformer.from_crs(27700, 4326, always_xy=True)
    lon, lat = tr.transform(df.loc[ok, "ngr_easting"].to_numpy(), df.loc[ok, "ngr_northing"].to_numpy())
    df["lon"] = np.nan
    df["lat"] = np.nan
    df.loc[ok, "lon"] = lon
    df.loc[ok, "lat"] = lat
    df = df.drop(columns=[c for c in ["ngr_valid"] if c in df]).reset_index(drop=True)
    return canonicalise_annual_returns(df)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ar = fetch_annual_returns()
    write_parquet(ar, config.PROCESSED / "annual_returns.parquet")
    print(ar.groupby(["company", "year"]).size().unstack(fill_value=0))
