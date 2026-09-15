"""Fit and evaluate the E. coli exceedance model on the bathing-water samples.

Input: ecoli_validation_rows.parquet from validate_ecoli.py (one row per EA sample
with reanalysis rain and dipcast's corrected hindcast risk). Evaluation is
leave-one-year-out, so every score is out of sample in time; the final model is
refitted on all years and written to ecoli_model.json for the site.

Baselines: climatology by water-body type (the exceedance rate at rivers / lakes in
the training years) and rain-only.

Rain source. The validation rows carry reanalysis rain, but the site serves forecast
rain: for today the 48 h window is mostly the model's recent analysis (lead 0), for
day +k it is the lead-k forecast. So the model is fitted on lead-0 forecast rain from
Open-Meteo's archived runs (fetch_rain_leads_bathing.py) and scored with rain from
each lead, alongside a reanalysis-trained variant for comparison.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.ingest.rainfall import grid_cell
from dipcast.model.ecoli import FEATURES, THRESHOLD, features, fit, rain_windows
from dipcast.model.verify import reliability_table, scores

VARIANTS = {
    "climatology by type": [],
    "rain only": ["lrain48", "lrain24", "lake", "lake_x_lrain48"],
    "rain + season": ["lrain48", "lrain24", "lake", "lake_x_lrain48", "doy_sin", "doy_cos"],
    "spill exposure only": ["logit_risk", "lake"],
    "rain + spill exposure": ["lrain48", "lrain24", "logit_risk", "lake", "lake_x_lrain48"],
    "rain + spill exposure + season (dipcast)": FEATURES,
}


LEADS = (0, 1, 2, 3, 4)


def forecast_rain_at_samples(rows: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """48 h and 24 h rain before each sample time from the archived forecast at each lead."""
    cl, cn = grid_cell(rows["lat"].to_numpy(), rows["lon"].to_numpy())
    rows = rows.assign(cell_lat=np.round(cl, 3), cell_lon=np.round(cn, 3))
    cells = set(zip(rows.cell_lat, rows.cell_lon, strict=True))
    frames = []
    for f in sorted((config.CACHE / "rain").glob("leads_*.parquet")):
        _, lat, lon, _ = f.stem.split("_")
        if (float(lat), float(lon)) in cells:
            frames.append(pd.read_parquet(f))
    leads = pd.concat(frames, ignore_index=True)
    leads["time"] = leads["time"].dt.tz_convert("Europe/London")
    out = {k: (np.full(len(rows), np.nan), np.full(len(rows), np.nan)) for k in LEADS}
    for (a, b), g in leads.groupby(["cell_lat", "cell_lon"]):
        idx = np.where((rows.cell_lat == a) & (rows.cell_lon == b))[0]
        if len(idx) == 0:
            continue
        ends = pd.DatetimeIndex(rows["sample_time"].iloc[idx])
        for k in LEADS:
            hourly = g[g.lead == k].set_index("time")["precip_mm"].sort_index()
            r48, r24 = rain_windows(hourly, ends)
            out[k][0][idx], out[k][1][idx] = r48, r24
    return out


def loyo(X: pd.DataFrame, y: np.ndarray, years: np.ndarray, lake: np.ndarray, cols: list[str],
         X_eval: pd.DataFrame | None = None) -> np.ndarray:
    """Leave-one-year-out predictions. Fit on X; score on X_eval (same rows, other rain source) if given."""
    X_eval = X if X_eval is None else X_eval
    pred = np.full(len(y), np.nan)
    for yr in np.unique(years):
        tr, te = years != yr, years == yr
        if not cols:   # climatology by type
            for is_lake in (0.0, 1.0):
                m = tr & (lake == is_lake)
                pred[te & (lake == is_lake)] = y[m].mean() if m.any() else y[tr].mean()
            continue
        pred[te] = fit(X[tr], y[tr], cols).predict(X_eval[te])
    return pred


def main() -> None:
    rows = pd.read_parquet(config.PROCESSED / "ecoli_validation_rows.parquet")
    rows = rows[rows.upstream > 0].dropna(subset=["rain_48h", "rain_24h", "risk_model", "ecoli"]).reset_index(drop=True)
    rows["lake"] = (rows["kind"] == "lake").astype(float)
    sites = pd.read_parquet(config.RAW / "bwq_samples.parquet").drop_duplicates("bw_id").set_index("bw_id")
    rows["lat"], rows["lon"] = sites.loc[rows.bw_id, "lat"].to_numpy(), sites.loc[rows.bw_id, "lon"].to_numpy()
    fc = forecast_rain_at_samples(rows)
    ok = np.all([~np.isnan(fc[k][0]) for k in LEADS], axis=0)
    rows = rows[ok].reset_index(drop=True)
    fc = {k: (fc[k][0][ok], fc[k][1][ok]) for k in LEADS}
    X_era = features(rows.rain_48h, rows.rain_24h, rows.risk_model, rows.lake, rows.sample_time)
    X_lead = {k: features(fc[k][0], fc[k][1], rows.risk_model, rows.lake, rows.sample_time) for k in LEADS}
    X = X_lead[0]   # fitted on lead-0 forecast rain: what "today" on the map sees
    y = (rows["ecoli"] > THRESHOLD).astype(int).to_numpy()
    years = rows["sample_time"].dt.year.to_numpy()
    lake = rows["lake"].to_numpy()
    print(f"{len(rows)} samples with forecast rain at every lead, {rows.bw_id.nunique()} sites, "
          f"{y.mean():.3f} exceed {THRESHOLD}; years {[int(v) for v in sorted(set(years))]}")

    # Rain source comparison for the full model: fit on reanalysis or lead 0, score with each lead's rain.
    print("\nfull model, leave-one-year-out Brier / AUC by rain source (rows: fitted on; columns: scored with):")
    src_rows = []
    for fit_name, X_fit in (("reanalysis", X_era), ("forecast lead 0", X_lead[0])):
        r = {"fitted_on": fit_name}
        for ev_name, X_ev in [("reanalysis", X_era)] + [(f"lead {k}", X_lead[k]) for k in LEADS]:
            p = loyo(X_fit, y, years, lake, FEATURES, X_eval=X_ev)
            sc = scores(y, p); r[ev_name] = f"{sc['brier']:.4f} / {sc['auc']:.3f}"
            src_rows.append({"fitted_on": fit_name, "scored_with": ev_name, "brier": sc["brier"], "auc": sc["auc"]})
        print("  ", r)
    results, preds = {}, {}
    for name, cols in VARIANTS.items():
        p = loyo(X, y, years, lake, cols)
        preds[name] = p
        s = scores(y, p)
        s["by_type"] = {k: scores(y[lake == v], p[lake == v]) for k, v in (("river", 0.0), ("lake", 1.0))}
        results[name] = s
    clim = results["climatology by type"]["brier"]
    tab = pd.DataFrame({n: {"brier": r["brier"], "log_loss": r["log_loss"], "auc": r["auc"],
                            "skill_vs_clim": 1 - r["brier"] / clim,
                            "brier_river": r["by_type"]["river"]["brier"], "auc_river": r["by_type"]["river"]["auc"],
                            "brier_lake": r["by_type"]["lake"]["brier"], "auc_lake": r["by_type"]["lake"]["auc"]}
                        for n, r in results.items()}).T
    pd.set_option("display.width", 200)
    print("\nleave-one-year-out, fitted and scored on lead-0 forecast rain:")
    print(tab.round(3).to_string())
    best = "rain + spill exposure + season (dipcast)"
    rel = reliability_table(y, preds[best]).round(3)
    print("\nreliability (dipcast variant):")
    print(rel.to_string(index=False))
    final = fit(X, y, FEATURES, meta={
        "target": f"E. coli > {THRESHOLD} cfu/100 ml", "n_samples": len(rows), "n_sites": int(rows.bw_id.nunique()),
        "rain_source": "Open-Meteo archived forecast, lead 0 (48 h / 24 h to the sample time)",
        "years": [int(v) for v in sorted(set(years))], "sites": "EA inland designated bathing waters with monitored overflows upstream",
        "loyo": {k: {"brier": round(v["brier"], 4), "auc": round(v["auc"], 3)} for k, v in results.items()},
        "base_rate": round(float(y.mean()), 4),
        "base_rate_by_type": {"river": round(float(y[lake == 0.0].mean()), 4), "lake": round(float(y[lake == 1.0].mean()), 4)},
    })
    final.save()
    print("\ncoefficients (per standard deviation):", {k: round(v, 3) for k, v in final.coef.items()})
    out = {"n_samples": len(rows), "n_sites": int(rows.bw_id.nunique()), "threshold": THRESHOLD,
           "base_rate": float(y.mean()), "years": [int(v) for v in sorted(set(years))],
           "loyo": tab.round(4).reset_index().rename(columns={"index": "model"}).to_dict("records"),
           "reliability": rel.to_dict("records"), "coef_per_sd": final.coef,
           "rain_source": "forecast lead 0", "by_rain_source": src_rows}
    (config.PROCESSED / "ecoli_model_eval.json").write_text(json.dumps(out, indent=1))
    print("saved ecoli_model.json and ecoli_model_eval.json")


if __name__ == "__main__":
    main()
