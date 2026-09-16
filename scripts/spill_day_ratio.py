"""How many spill-days does one counted spill in the annual returns stand for?

The EA annual returns count spills by the 12/24-hour block method (the first 12 h
of a discharge is one spill, each following 24 h block another), so an annual
count is not a count of days with a discharge. The live baseline divides the
long-term count by 365 and calls it a daily spill probability; this script
measures the conversion on United Utilities, the one company with event-level
history, and writes it to data/processed/spill_day_ratio.json for forecast_log.
"""
import json

import pandas as pd

from dipcast import config
from dipcast.model.features import spill_days

ev = pd.read_parquet(config.PROCESSED / "edm_events.parquet")
sd = spill_days(ev)
sd["year"] = sd.day.dt.year
days = sd.groupby(["site_id", "year"]).size().rename("spill_days").reset_index()
ar = pd.read_parquet(config.PROCESSED / "annual_returns.parquet")
ar = ar[ar.company == "United Utilities"][["site_id", "year", "spills"]]
m = days.merge(ar, on=["site_id", "year"])
m = m[m.spills > 0]
pooled = float(m.spill_days.sum() / m.spills.sum())
by_bucket = {}
for lo, hi in [(0, 5), (5, 20), (20, 50), (50, 10_000)]:
    s = m[(m.spills > lo) & (m.spills <= hi)]
    by_bucket[f"{lo}-{hi}"] = {"site_years": len(s), "ratio": round(float(s.spill_days.sum() / s.spills.sum()), 3)}
out = {"spill_days_per_spill": round(pooled, 3), "median_site_year_ratio": round(float((m.spill_days / m.spills).median()), 3),
       "site_years": len(m), "years": sorted(int(y) for y in m.year.unique()), "company": "United Utilities",
       "by_annual_count": by_bucket,
       "note": "spill-days from event history (features.spill_days) over the annual-return spill count, per site-year, pooled"}
(config.PROCESSED / "spill_day_ratio.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
