"""Replay dipcast's overflow exposure at each bathing water from the rain forecast
issued k days before the sample, k = 0..4, for the E. coli evaluation.

train_ecoli.py used to score the lead-k model with lead-k rain at the spot but the
same hindcast exposure (reanalysis rain, lead 0) at every lead, so the four-day
evaluation did not test what the site would have known four days earlier. Here
the whole exposure step is replayed as the live pipeline does it: for a target
day T forecast on issue day I = T - k, the spill probability at an upstream
overflow on source day S uses rainfall at lead max(k - (T - S), 0) (a forecast
for days after the issue, the model's analysis for days before), the per-lead
calibration for that lead, then the transport step.

Approximations, stated: the 30-day rainfall total and the antecedent index use
the lead-0 (analysis) series throughout (at most k of their 30 days are forecast
days); Open-Meteo's previous-runs fields give, per hour, the value from the run
k days earlier rather than one run's full trajectory, so a k-day window is a
stitch of runs issued k days before each hour, not one run (see the README).

Two exposures are written per (site, day, lead):
  risk_prod     the production spill model (fitted 2023-25) with the deployed
                per-lead isotonic calibration: what the map computes
  risk_holdout  the model fitted on 2023-24 only, uncalibrated: for the
                forward-in-time E. coli test where 2025-26 must be unseen

Output: data/processed/ecoli_exposure_leads.parquet
Needs lead rain for every upstream cell (scripts/fetch_rain_leads_upstream.py);
site-years with a cell missing get NaN and are reported.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.model.features import ALL_FEATURES, build_site_days, daily_rain_features
from dipcast.model.forecast import calibrate_at_lead
from dipcast.model.spill_model import SpillModel
from dipcast.model.transport import history_days, locate_pin, river_velocity, upstream_overflows
from dipcast.network.rivers import RiverNetwork
from dipcast.overflows import load_overflows

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("replay_ecoli_leads")
TZ = "Europe/London"
LEADS = [0, 1, 2, 3, 4]
OUT = config.PROCESSED / "ecoli_exposure_leads.parquet"


def load_lead_rain() -> pd.DataFrame:
    files = sorted((config.CACHE / "rain").glob("leads_*.parquet"))
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    df["time"] = df["time"].dt.tz_convert(TZ)
    return df


def lead_features(sites: pd.DataFrame, daily: dict[int, pd.DataFrame], days: pd.DatetimeIndex, k: int) -> pd.DataFrame:
    """Site-day features as known on issue day T - k for target day T: the day's rain
    and intensities at lead k, day-1 at lead k-1, day-2 at lead k-2 (floored at 0),
    the 3- and 7-day totals from those, the 30-day total and API from lead 0."""
    base = build_site_days(sites, daily[0], days)
    key = ["cell_lat", "cell_lon", "day"]
    same = daily[k][key + ["rain_d", "max1h_d", "max3h_d", "max6h_d"]]
    df = base.drop(columns=["rain_d", "max1h_d", "max3h_d", "max6h_d"]).merge(same, on=key, how="left")
    shifted = {}
    for j in range(1, 7):
        src = daily[max(k - j, 0)][key + ["rain_d"]].copy()
        src["day"] = src["day"] + pd.Timedelta(days=j)
        shifted[j] = df[key].merge(src, on=key, how="left")["rain_d"].to_numpy()
    df["rain_d1"], df["rain_d2"] = shifted[1], shifted[2]
    r = np.nan_to_num(df["rain_d"].to_numpy(), nan=0.0)
    df["rain_3d"] = r + sum(np.nan_to_num(shifted[j], nan=0.0) for j in (1, 2))
    df["rain_7d"] = r + sum(np.nan_to_num(shifted[j], nan=0.0) for j in range(1, 7))
    return df


def combine_daily_leads(P: dict[int, np.ndarray], weights: np.ndarray, travel_h: np.ndarray, k: int) -> np.ndarray:
    """Exposure per target day at lead k. Each overflow's spill on source day S
    arrives kk or kk+1 days later; the probability used for S is the one from lead
    max(k - (T - S), 0), the lead at which S was forecast on the issue day."""
    n, d = next(iter(P.values())).shape
    shift = travel_h / 24.0
    kk = np.floor(shift).astype(int)
    a = shift - kk
    eff = np.zeros((n, d))
    for i in range(n):
        for T in range(d):
            for extra, frac in ((0, 1 - a[i]), (1, a[i])):
                S = T - kk[i] - extra
                if S < 0 or frac == 0:
                    continue
                m = max(k - (T - S), 0)
                eff[i, T] += frac * P[m][i, S]
    eff = np.clip(eff, 0, 1) * weights[:, None]
    return 1.0 - np.prod(1.0 - eff, axis=0)


def main() -> None:
    samples = pd.read_parquet(config.RAW / "bwq_samples.parquet").dropna(subset=["ecoli", "sample_time"])
    samples["day"] = samples["sample_time"].dt.floor("D")
    net = RiverNetwork.load(); ov = load_overflows(net)
    prod = SpillModel.load()
    hold = SpillModel.load(config.PROCESSED / "spill_model_holdout_2025.pkl")
    leads = load_lead_rain()
    have_cells = set(map(tuple, leads[["cell_lat", "cell_lon"]].drop_duplicates().to_numpy()))
    daily = {k: daily_rain_features(leads[leads.lead == k].drop(columns="lead")) for k in LEADS}
    cols = ["site_id", "lat", "lon", "company", "lta_spills", "spill_hours", "edm_operational_pct"]
    out, missing = [], []
    for bw_id, g in samples.groupby("bw_id"):
        s = g.iloc[0]
        pin = locate_pin(net, float(s.lon), float(s.lat))
        up = upstream_overflows(net, pin, ov, velocity_ms=river_velocity(None))
        sample_days = pd.DatetimeIndex(sorted(g["day"].unique()))
        if len(up) == 0:
            for d in sample_days:
                for k in LEADS:
                    out.append({"bw_id": bw_id, "day": d, "lead": k, "risk_prod": 0.0, "risk_holdout": 0.0})
            continue
        from dipcast.ingest.rainfall import grid_cell
        cl, cn = grid_cell(up["lat"].to_numpy(), up["lon"].to_numpy())
        cells = set(zip(np.round(cl, 3), np.round(cn, 3), strict=True))
        if not cells <= have_cells:
            missing.append({"site": s["name"], "missing_cells": len(cells - have_cells)})
        hist = history_days(up["travel_h"])
        days = pd.date_range(sample_days.min() - pd.Timedelta(days=hist), sample_days.max(), freq="D")
        P_prod, P_hold = {}, {}
        for k in LEADS:
            sd = lead_features(up[cols], daily, days, k)
            avail = sd["rain_d"].notna().to_numpy()
            sd = sd.astype({f: np.float32 for f in ALL_FEATURES}).fillna({f: 0.0 for f in ALL_FEATURES})
            for model, store, calibrate in ((prod, P_prod, True), (hold, P_hold, False)):
                sd["p"] = model.predict(sd)
                sd.loc[~avail, "p"] = np.nan
                mat = sd.pivot(index="site_id", columns="day", values="p").reindex(index=up["site_id"], columns=days)
                pm = mat.to_numpy(dtype=float)
                store[k] = calibrate_at_lead(np.nan_to_num(pm, nan=0.0), lead=k) if calibrate else np.nan_to_num(pm, nan=0.0)
                store[k] = np.where(np.isnan(pm), np.nan, store[k])
        w = up["weight"].to_numpy(dtype=float); tr = up["travel_h"].to_numpy(dtype=float)
        for k in LEADS:
            # NaN where any needed probability is missing: propagate by combining a mask the same way
            rp = combine_daily_leads({m: np.nan_to_num(P_prod[m], nan=0.0) for m in LEADS}, w, tr, k)
            rh = combine_daily_leads({m: np.nan_to_num(P_hold[m], nan=0.0) for m in LEADS}, w, tr, k)
            miss = combine_daily_leads({m: np.isnan(P_prod[m]).astype(float) for m in LEADS}, np.ones_like(w), tr, k)
            rp = np.where(miss > 0, np.nan, rp); rh = np.where(miss > 0, np.nan, rh)
            sp = pd.Series(rp, index=days).reindex(sample_days); sh = pd.Series(rh, index=days).reindex(sample_days)
            for d in sample_days:
                out.append({"bw_id": bw_id, "day": d, "lead": k, "risk_prod": float(sp[d]), "risk_holdout": float(sh[d])})
        log.info("%s: %d overflows, %d sample days, hist %d d", s["name"], len(up), len(sample_days), hist)
    df = pd.DataFrame(out)
    df.to_parquet(OUT, index=False)
    n_ok = df.groupby("lead")["risk_prod"].apply(lambda s: int(s.notna().sum())).to_dict()
    log.info("wrote %s: %d rows; sample-days with exposure by lead %s", OUT, len(df), n_ok)
    if missing:
        log.warning("sites with upstream cells lacking lead rain: %s", missing)
    print("REPLAY DONE")


if __name__ == "__main__":
    main()
