"""Train the spill model on United Utilities event history and verify it.

Train: 2023-2024. Test: 2025 (held out). Then refit on all years for production.
Covariates from the EA annual returns are taken from the return of the
*previous* calendar year, so no test-year information leaks into features.
"""

from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.model.features import (
    ALL_FEATURES,
    build_site_days,
    daily_rain_features,
    spill_days,
)
from dipcast.model.spill_model import SpillModel
from dipcast.model.verify import brier_skill, climatology, rain_rule, reliability_table, scores

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("train")
_T0 = __import__("time").time()


def step(msg: str) -> None:
    import resource
    import time
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
    log.info("[%5.0fs, peak %5.0f MB] %s", time.time() - _T0, rss, msg)

COMPANY = "United Utilities"
TEST_YEAR = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2025


def _arg(name: str, default: float) -> float:
    if name in sys.argv:
        return float(sys.argv[sys.argv.index(name) + 1])
    return default


NEG_FRAC = _arg("--neg-frac", 0.25)
RECENCY = _arg("--recency", 1.0)
SAVE = "--no-save" not in sys.argv
MIN_EDM_PCT = 50.0


def load_rain_archive() -> pd.DataFrame:
    files = sorted((config.CACHE / "rain").glob("archive_*.parquet"))
    log.info("loading %d rain cell-year files", len(files))
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def site_year_covariates(ar: pd.DataFrame) -> pd.DataFrame:
    """Annual-return covariates keyed by (site_id, year_applied) where the
    covariates come from the return of year_applied - 1."""
    a = ar[(ar.company == COMPANY) & ar.lat.notna()].copy()
    a["year_applied"] = a["year"].astype(int) + 1
    keep = ["site_id", "year_applied", "lat", "lon", "lta_spills", "spill_hours", "edm_operational_pct"]
    return a[keep].rename(columns={"year_applied": "year"})


def main() -> None:
    ev = pd.read_parquet(config.PROCESSED / "edm_events.parquet")
    ar = pd.read_parquet(config.PROCESSED / "annual_returns.parquet")
    rain = load_rain_archive()
    log.info("events %d (%s -> %s); sites %d", len(ev), ev.event_start.min().date(),
             ev.event_start.max().date(), ev.site_id.nunique())

    step("rain loaded")
    daily_rain = daily_rain_features(rain)
    step(f"daily rain features: {len(daily_rain)} cell-days")
    labels = spill_days(ev)
    step("labels built")
    years = sorted(labels["day"].dt.year.unique())
    log.info("label years: %s; spill-days %d", years, len(labels))

    cov = site_year_covariates(ar)
    # Same-year operational percentage as a data-quality filter for labels.
    qual = ar[(ar.company == COMPANY)][["site_id", "year", "edm_operational_pct"]].rename(
        columns={"edm_operational_pct": "edm_pct_same_year"})

    frames = []
    for y in years:
        sites_y = cov[cov.year == y].drop(columns="year")
        if sites_y.empty:
            log.warning("no prior-year annual return for %d; skipping", y)
            continue
        days = pd.date_range(f"{y}-01-01", f"{y}-12-31", freq="D", tz="UTC")
        sd = build_site_days(sites_y.assign(company=COMPANY), daily_rain, days, labels)
        sd["year"] = y
        frames.append(sd)
        step(f"site-days for {y}: {len(sd)} rows")
    df = pd.concat(frames, ignore_index=True)
    step("concatenated")
    qual = qual.drop_duplicates(subset=["site_id", "year"])
    qual["year"] = qual["year"].astype("int64")
    df["year"] = df["year"].astype("int64")
    df = df.merge(qual, on=["site_id", "year"], how="left")
    step("quality merged")
    n0 = len(df)
    df = df[df["rain_d"].notna()]
    df = df[(df["edm_pct_same_year"].isna()) | (df["edm_pct_same_year"] >= MIN_EDM_PCT)]
    log.info("site-days: %d -> %d after rain/quality filters; base rate %.3f", n0, len(df), df.y.mean())
    df = df.astype({f: np.float32 for f in ALL_FEATURES})
    step("downcast")

    train = df[df.year < TEST_YEAR]
    test = df[df.year == TEST_YEAR]
    if train.empty or test.empty:
        log.error("need at least one train year and the test year %d; have %s", TEST_YEAR, years)
        sys.exit(1)
    log.info("train %d rows (%s), test %d rows (%d)", len(train), sorted(train.year.unique()), len(test), TEST_YEAR)

    m = SpillModel().fit_pooled(train, neg_frac=NEG_FRAC)
    step(f"pooled model fitted (neg_frac={NEG_FRAC})")
    m.fit_site_offsets(train, recency=RECENCY)
    step(f"site offsets fitted (recency={RECENCY})")
    y = test["y"].to_numpy()
    preds = {
        "dipcast (pooled + site offsets)": m.predict(test),
        "pooled only": m.predict_pooled(test),
        "climatology (site rate)": climatology(train, test),
        "rain rule (>10mm/48h)": rain_rule(test, 10.0),
        "rain rule (>5mm/48h)": rain_rule(test, 5.0),
    }
    ref = scores(y, preds["climatology (site rate)"])["brier"]
    rows = []
    for k, p in preds.items():
        s = scores(y, p)
        s["brier_skill_vs_clim"] = brier_skill(s["brier"], ref)
        rows.append({"model": k, **s})
    res = pd.DataFrame(rows).set_index("model")
    pd.set_option("display.width", 160)
    print("\n=== Verification on held-out", TEST_YEAR, "===")
    print(res.round(4).to_string())
    print("\n=== Reliability: dipcast ===")
    print(reliability_table(y, preds["dipcast (pooled + site offsets)"]).round(3).to_string())

    # Skill by wetness: does the model add value beyond 'it rained'?
    wet = (test["rain_d"] + test["rain_d1"]) > 10
    for name, mask in [("wet days", wet), ("dry days", ~wet)]:
        print(f"\n--- {name}: n={int(mask.sum())}, base={y[mask].mean():.3f}")
        for k in ["dipcast (pooled + site offsets)", "climatology (site rate)"]:
            print(f"  {k:36s} brier={scores(y[mask], preds[k][mask])['brier']:.4f}")

    res.to_csv(config.PROCESSED / f"verification_{TEST_YEAR}.csv")
    reliability_table(y, preds["dipcast (pooled + site offsets)"]).to_csv(
        config.PROCESSED / f"reliability_{TEST_YEAR}.csv", index=False)

    if not SAVE:
        log.info("--no-save: experiment only, model not written")
        return
    # The held-out model is kept under its own name for honest verification later
    # (scripts/verify_leads.py). It also becomes the production model unless
    # --refit-all replaces it with a fit on every year.
    m.trained_on = f"{COMPANY} {int(train.year.min())}-{int(train.year.max())}; verified on {TEST_YEAR}"
    holdout_path = config.PROCESSED / f"spill_model_holdout_{TEST_YEAR}.pkl"
    m.save(holdout_path)
    log.info("saved held-out model -> %s", holdout_path)
    if "--refit-all" not in sys.argv:
        m.save()
        log.info("saved as production model -> %s", config.PROCESSED / "spill_model.pkl")

    if "--refit-all" in sys.argv:
        prod = SpillModel().fit_pooled(df, neg_frac=NEG_FRAC).fit_site_offsets(df, recency=RECENCY)
        prod.trained_on = f"{COMPANY} {min(years)}-{max(years)} (refit on all years; verified on {TEST_YEAR} before refit)"
        prod.save()
        log.info("saved refit-on-all-years model -> %s", config.PROCESSED / "spill_model.pkl")


if __name__ == "__main__":
    main()
