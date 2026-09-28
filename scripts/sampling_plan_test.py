"""Would a rain-led sampling plan have caught more E. coli exceedances than a fixed schedule?

Uses only days the EA actually sampled (validate_ecoli.py rows). At each site, rank the
sample days by rain in the 48 h before sampling and keep the top half / quarter; report
the share of exceedances (> 900 cfu/100 ml) those kept days contain, against random
selection (= the budget fraction). Rain comes from:

  observed       reanalysis, i.e. a perfect forecast (upper bound)
  lead k         Open-Meteo's archived forecast for each hour issued k days earlier
                 (precipitation_previous_dayk), the rain a planner deciding k days
                 ahead would have seen. Slightly pessimistic: a real plan issued k days
                 before the sample uses leads k-2..k across the 48 h window.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.ingest.rainfall import grid_cell
from dipcast.model.ecoli import rain_windows

THRESHOLD = 900
BUDGETS = (0.5, 0.25)
LEADS = (0, 1, 2, 3, 4)
RULE_MM = 5.0


def load_leads(cells: set[tuple[float, float]]) -> pd.DataFrame:
    frames = []
    for f in sorted((config.CACHE / "rain").glob("leads_*.parquet")):
        _, lat, lon, _ = f.stem.split("_")
        if (float(lat), float(lon)) in cells:
            frames.append(pd.read_parquet(f))
    return pd.concat(frames, ignore_index=True)


def capture(sub: pd.DataFrame, col: str, frac: float) -> float:
    keep = sub.groupby("bw_id")[col].rank(pct=True, method="first") > 1 - frac
    return float(sub.loc[keep, "exc"].sum() / sub["exc"].sum())


def main() -> None:
    rows = pd.read_parquet(config.PROCESSED / "ecoli_validation_rows.parquet")
    rows = rows[rows.upstream > 0].dropna(subset=["rain_48h"]).copy()
    rows["exc"] = (rows["ecoli"] > THRESHOLD).astype(int)
    samples = pd.read_parquet(config.RAW / "bwq_samples.parquet").drop_duplicates("bw_id").set_index("bw_id")
    cl, cn = grid_cell(samples.loc[rows.bw_id, "lat"].to_numpy(), samples.loc[rows.bw_id, "lon"].to_numpy())
    rows["cell_lat"], rows["cell_lon"] = np.round(cl, 3), np.round(cn, 3)
    leads = load_leads(set(zip(rows.cell_lat, rows.cell_lon, strict=True)))
    leads["time"] = leads["time"].dt.tz_convert("Europe/London")
    by_cell = {k: g.pivot(index="time", columns="lead", values="precip_mm").sort_index()
               for k, g in leads.groupby(["cell_lat", "cell_lon"])}
    for k in LEADS:
        rows[f"fc{k}_48h"] = np.nan
    # The production window rule (ecoli.rain_windows): (t - 48 h, t], NaN unless 90% of the
    # hours carry a value. Until 28 Sep 2026 this summed with pandas, so a lead with no
    # archived rain (all of 2023 and January 2024 at leads 1-4) counted as 0 mm, a dry day.
    for (clat, clon), idx in rows.groupby(["cell_lat", "cell_lon"]).groups.items():
        tab = by_cell.get((clat, clon))
        if tab is None:
            continue
        ends = pd.DatetimeIndex(rows.loc[idx, "sample_time"])
        for k in LEADS:
            if k in tab.columns:
                rows.loc[idx, f"fc{k}_48h"] = rain_windows(tab[k], ends)[0]
    cols = ["rain_48h"] + [f"fc{k}_48h" for k in LEADS]
    d = rows.dropna(subset=cols)
    years = d["sample_time"].dt.year.value_counts().sort_index().to_dict()
    print(f"{len(d)} samples with forecast rain at every lead ({d.bw_id.nunique()} sites, "
          f"{int(d.exc.sum())} exceedances, by year {years}), of {len(rows)} scored")
    out = {"n_samples": len(d), "samples_by_year": {int(k): int(v) for k, v in years.items()},
           "rule": "leads compared on the same samples; a window needs 90% of its 48 hours"}
    for kind, sub in [("all", d), ("river", d[d.kind == "river"]), ("lake", d[d.kind == "lake"])]:
        if sub.exc.sum() < 10:
            continue
        tab = pd.DataFrame({c: {f"top {int(b * 100)}%": round(capture(sub, c, b), 3) for b in BUDGETS} for c in cols}).T
        tab.index = ["observed"] + [f"lead {k}" for k in LEADS]
        print(f"\n=== {kind}: n={len(sub)}, exceedances={int(sub.exc.sum())}, random = budget fraction ===")
        print(tab.to_string())
        out[kind] = tab.reset_index().rename(columns={"index": "rain_source"}).to_dict("records")
        # A fixed-threshold rule a volunteer group could follow: sample only if more than
        # RULE_MM of rain is forecast for the 48 h before the sampling time.
        rule = {}
        for c, lab in zip(cols, tab.index, strict=True):
            keep = sub[c] > RULE_MM
            rule[lab] = {"days_kept": round(float(keep.mean()), 3), "exceedances_kept": round(float(sub.loc[keep, "exc"].sum() / sub["exc"].sum()), 3),
                         "rate_on_kept": round(float(sub.loc[keep, "exc"].mean()), 3), "rate_on_skipped": round(float(sub.loc[~keep, "exc"].mean()), 3)}
        print(f"rule: sample only if more than {RULE_MM:g} mm is forecast")
        print(pd.DataFrame(rule).T.to_string())
        out[f"{kind}_rule_{RULE_MM:g}mm"] = rule
    # how well does forecast rain reproduce observed 48 h rain, and the >10 mm days a planner acts on
    fit = {f"lead {k}": {"mae_mm": round(float((d[f"fc{k}_48h"] - d.rain_48h).abs().mean()), 2),
                         "spearman": round(float(d[f"fc{k}_48h"].corr(d.rain_48h, method="spearman")), 3),
                         "hit_rate_gt10mm": round(float(((d[f"fc{k}_48h"] > 10) & (d.rain_48h > 10)).sum() / max(1, (d.rain_48h > 10).sum())), 3)}
           for k in LEADS}
    print("\nforecast vs observed 48 h rain at the sample times:")
    print(pd.DataFrame(fit).T.to_string())
    out["forecast_skill"] = fit
    (config.PROCESSED / "sampling_plan_test.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
