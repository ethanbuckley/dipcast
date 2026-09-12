"""Verify the spill model with the rain a user would have seen: archived
forecasts by lead time (Open-Meteo previous-runs API) instead of reanalysis.

Lead 0 = the latest model run for that day (what 'today' uses); lead k = the
forecast issued k days before the target day ('tomorrow' is lead 1, and so on).

Approximation, stated in the README: for lead k the target day's rainfall and
intensity features and the two previous days use lead-appropriate forecasts
(k, k-1, k-2, floored at 0); the 7-day, 30-day and antecedent-index features
use reanalysis, because on the issue day those windows are almost entirely
observed history.
"""

from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.model.features import ALL_FEATURES, build_site_days, daily_rain_features, spill_days
from dipcast.model.spill_model import SpillModel
from dipcast.model.verify import reliability_table, scores

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("verify_leads")
YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
COMPANY = "United Utilities"
LEADS = [0, 1, 2, 3, 4]


def load(pattern: str) -> pd.DataFrame:
    files = sorted((config.CACHE / "rain").glob(pattern))
    log.info("%d files for %s", len(files), pattern)
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def main() -> None:
    holdout = config.PROCESSED / f"spill_model_holdout_{YEAR}.pkl"
    if not holdout.exists():
        log.error("no held-out model for %d at %s; run scripts/train.py %d first", YEAR, holdout, YEAR)
        sys.exit(1)
    model = SpillModel.load(holdout)
    log.info("model: %s", model.trained_on)
    ev = pd.read_parquet(config.PROCESSED / "edm_events.parquet")
    ar = pd.read_parquet(config.PROCESSED / "annual_returns.parquet")
    labels = spill_days(ev)
    labels = labels[labels.day.dt.year == YEAR]

    era = load(f"archive_*_{YEAR}.parquet")
    leads = load(f"leads_*_{YEAR}.parquet")
    cells_with_leads = set(map(tuple, leads[["cell_lat", "cell_lon"]].drop_duplicates().to_numpy()))
    era = era[[tuple(x) in cells_with_leads for x in era[["cell_lat", "cell_lon"]].to_numpy()]]
    daily_era = daily_rain_features(era)
    daily_lead = {k: daily_rain_features(leads[leads.lead == k].drop(columns="lead")) for k in LEADS}

    # Rainfall skill itself: daily total by lead vs reanalysis.
    key = ["cell_lat", "cell_lon", "day"]
    print(f"\n=== Daily rainfall vs ERA5-Land, {YEAR}, {len(cells_with_leads)} cells ===")
    print(f"{'lead':>4} {'MAE mm':>7} {'bias mm':>8} {'corr':>6} {'hit>10mm':>9} {'FA>10mm':>8}")
    for k in LEADS:
        m = daily_era[key + ["rain_d"]].merge(daily_lead[k][key + ["rain_d"]], on=key, suffixes=("_obs", "_fc")).dropna()
        obs, fc = m["rain_d_obs"].to_numpy(), m["rain_d_fc"].to_numpy()
        wet = obs > 10
        hit = (fc[wet] > 10).mean() if wet.any() else np.nan
        fa = (fc[~wet] > 10).mean() if (~wet).any() else np.nan
        print(f"{k:>4} {np.abs(fc - obs).mean():7.2f} {(fc - obs).mean():8.2f} {np.corrcoef(obs, fc)[0, 1]:6.3f} {hit:9.2f} {fa:8.3f}")

    # Covariates from the previous year's return, as in training.
    a = ar[(ar.company == COMPANY) & ar.lat.notna() & (ar.year == YEAR - 1)]
    sites = a[["site_id", "lat", "lon", "lta_spills", "spill_hours", "edm_operational_pct"]].assign(company=COMPANY)
    days = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="D", tz="UTC")
    base = build_site_days(sites, daily_era, days, labels)
    base = base[base["rain_d"].notna()].copy()
    y = base["y"].to_numpy()

    def with_lead(k: int) -> pd.DataFrame:
        df = base.copy()
        same = daily_lead[k][key + ["rain_d", "max1h_d", "max3h_d", "max6h_d"]]
        df = df.drop(columns=["rain_d", "max1h_d", "max3h_d", "max6h_d"]).merge(same, on=key, how="left")
        for shift, col in ((1, "rain_d1"), (2, "rain_d2")):
            src = daily_lead[max(k - shift, 0)][key + ["rain_d"]].copy()
            src["day"] = src["day"] + pd.Timedelta(days=shift)
            df = df.drop(columns=[col]).merge(src.rename(columns={"rain_d": col}), on=key, how="left")
        df["rain_3d"] = df["rain_d"].fillna(0) + df["rain_d1"].fillna(0) + df["rain_d2"].fillna(0)
        return df

    rows = []
    calib: dict[str, dict[str, float]] = {}
    print(f"\n=== Spill model on {YEAR} by rainfall source ===")
    p_era = model.predict(base.astype({f: np.float32 for f in ALL_FEATURES}).fillna({f: 0.0 for f in ALL_FEATURES}))
    s = scores(y, p_era); rows.append({"source": "reanalysis (upper bound)", **s})
    for k in LEADS:
        dfk = with_lead(k)
        ok = dfk["rain_d"].notna().to_numpy()
        pk = model.predict(dfk.astype({f: np.float32 for f in ALL_FEATURES}).fillna({f: 0.0 for f in ALL_FEATURES}))
        s = scores(y[ok], pk[ok]); rows.append({"source": f"forecast lead {k}", **s})
        # Per-lead Platt scaling: logit(p') = a + b * logit(p). Two parameters per
        # lead, fitted on this year; corrects the over-confidence that comes from
        # treating forecast rain as if it were observed.
        from sklearn.linear_model import LogisticRegression
        z = np.log(pk[ok] / (1 - pk[ok])).reshape(-1, 1)
        lr = LogisticRegression(C=1e6).fit(z, y[ok])
        a, b = float(lr.intercept_[0]), float(lr.coef_[0][0])
        pc = 1 / (1 + np.exp(-(a + b * z[:, 0])))
        sc = scores(y[ok], pc); rows.append({"source": f"forecast lead {k}, Platt-calibrated", **sc})
        calib[str(k)] = {"a": a, "b": b, "brier_before": s["brier"], "brier_after": sc["brier"]}
        if k in (0, 2):
            print(f"\n--- reliability, lead {k} (raw) ---")
            print(reliability_table(y[ok], pk[ok]).round(3).to_string())
            print(f"--- reliability, lead {k} (calibrated) ---")
            print(reliability_table(y[ok], pc).round(3).to_string())
    from dipcast.model.verify import brier_skill
    res = pd.DataFrame(rows).set_index("source")
    clim = 0.0669  # site climatology Brier on the same year from train.py
    res["brier_skill_vs_clim"] = [brier_skill(b, clim) for b in res["brier"]]
    pd.set_option("display.width", 160)
    print()
    print(res.round(4).to_string())
    res.to_csv(config.PROCESSED / f"verification_leads_{YEAR}.csv")
    import json
    (config.PROCESSED / "lead_calibration.json").write_text(json.dumps(
        {"fitted_on": YEAR, "model": model.trained_on, "leads": calib}, indent=1))
    log.info("saved per-lead calibration -> %s", config.PROCESSED / "lead_calibration.json")


if __name__ == "__main__":
    main()
