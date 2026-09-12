"""Site-day feature table: rainfall history and forecast joined to overflows.

One row per (site, day). The same builder serves training (rain from the
archive, labels from event history) and inference (rain from the forecast,
labels absent), so the two cannot drift apart.

Rainfall features are computed per grid cell, then joined to sites by cell.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from dipcast.ingest.rainfall import grid_cell

log = logging.getLogger(__name__)

API_DECAY = 0.85  # daily decay of the antecedent precipitation index

RAIN_FEATURES = [
    "rain_d", "rain_d1", "rain_d2", "rain_3d", "rain_7d", "rain_30d",
    "api", "max1h_d", "max3h_d", "max6h_d",
]
SEASON_FEATURES = ["doy_sin", "doy_cos"]
SITE_FEATURES = ["log_lta_spills", "log_spill_hours", "edm_pct"]
ALL_FEATURES = RAIN_FEATURES + SEASON_FEATURES + SITE_FEATURES

# Physically, more rain can only raise spill probability. Used by the model.
MONOTONE = {f: 1 for f in RAIN_FEATURES}


def daily_rain_features(rain_hourly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate hourly cell rainfall to daily features. Input columns:
    cell_lat, cell_lon, time (UTC), precip_mm. Output one row per cell-day."""
    df = rain_hourly.copy()
    df["day"] = df["time"].dt.floor("D")
    df = df.sort_values(["cell_lat", "cell_lon", "time"])
    g = df.groupby(["cell_lat", "cell_lon"], sort=False)
    df["r3"] = g["precip_mm"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    df["r6"] = g["precip_mm"].transform(lambda s: s.rolling(6, min_periods=1).sum())
    daily = (
        df.groupby(["cell_lat", "cell_lon", "day"], sort=True)
        .agg(rain_d=("precip_mm", "sum"), max1h_d=("precip_mm", "max"),
             max3h_d=("r3", "max"), max6h_d=("r6", "max"), n_hours=("precip_mm", "size"))
        .reset_index()
    )
    out = []
    for (cl, cn), d in daily.groupby(["cell_lat", "cell_lon"], sort=False):
        d = d.set_index("day").asfreq("D")  # fill missing days with NaN rows
        r = d["rain_d"]
        d["rain_d1"] = r.shift(1)
        d["rain_d2"] = r.shift(2)
        d["rain_3d"] = r.rolling(3, min_periods=1).sum()
        d["rain_7d"] = r.rolling(7, min_periods=1).sum()
        d["rain_30d"] = r.rolling(30, min_periods=1).sum()
        api = np.zeros(len(r))
        acc = 0.0
        for i, v in enumerate(r.to_numpy()):
            acc = API_DECAY * acc + (0.0 if np.isnan(v) else v)
            api[i] = acc
        d["api"] = api
        d["cell_lat"], d["cell_lon"] = cl, cn
        out.append(d.reset_index())
    res = pd.concat(out, ignore_index=True)
    doy = res["day"].dt.dayofyear
    res["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    res["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return res


def site_static_features(sites: pd.DataFrame) -> pd.DataFrame:
    """Per-site covariates from the EA annual returns.

    `sites` needs: site_id, lat, lon, lta_spills, spill_hours, edm_operational_pct.
    Missing values fall back to company medians, then to global medians."""
    s = sites.copy()
    for c, dflt in [("lta_spills", 20.0), ("spill_hours", 100.0), ("edm_operational_pct", 90.0)]:
        if c not in s:
            s[c] = np.nan
        if "company" in s:
            s[c] = s[c].fillna(s.groupby("company")[c].transform("median"))
        s[c] = s[c].fillna(s[c].median()).fillna(dflt)
    s["log_lta_spills"] = np.log1p(s["lta_spills"].clip(lower=0))
    s["log_spill_hours"] = np.log1p(s["spill_hours"].clip(lower=0))
    s["edm_pct"] = s["edm_operational_pct"].clip(0, 100) / 100.0
    cl, cn = grid_cell(s["lat"].to_numpy(), s["lon"].to_numpy())
    s["cell_lat"], s["cell_lon"] = cl, cn
    return s[["site_id", "lat", "lon", "cell_lat", "cell_lon", *SITE_FEATURES]]


def spill_days(events: pd.DataFrame, max_span_days: int = 60) -> pd.DataFrame:
    """Explode events to the set of (site_id, day) with at least one spill."""
    ev = events.dropna(subset=["event_start"]).copy()
    d0 = ev["event_start"].dt.floor("D")
    d1 = ev["event_end"].fillna(ev["event_start"]).dt.floor("D")
    n = ((d1 - d0).dt.days.clip(lower=0, upper=max_span_days) + 1).to_numpy()
    site = np.repeat(ev["site_id"].to_numpy(), n)
    base = np.repeat(d0.dt.tz_convert(None).to_numpy().astype("datetime64[D]"), n)
    offs = np.concatenate([np.arange(k) for k in n]) if len(n) else np.array([], dtype=int)
    day = base + offs.astype("timedelta64[D]")
    out = pd.DataFrame({"site_id": site, "day": pd.to_datetime(day).tz_localize("UTC")})
    return out.drop_duplicates()


def build_site_days(sites: pd.DataFrame, daily_rain: pd.DataFrame, days: pd.DatetimeIndex,
                    labels: pd.DataFrame | None = None) -> pd.DataFrame:
    """Cross sites with days, join rain by cell and day, attach labels if given."""
    st = site_static_features(sites)
    grid = st.merge(pd.DataFrame({"day": days}), how="cross")
    grid = grid.merge(daily_rain, on=["cell_lat", "cell_lon", "day"], how="left")
    if labels is not None:
        lab = labels.assign(y=1)
        grid = grid.merge(lab, on=["site_id", "day"], how="left")
        grid["y"] = grid["y"].fillna(0).astype(np.int8)
    return grid
