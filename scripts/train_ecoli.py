"""Fit and evaluate the E. coli exceedance model on the bathing-water samples.

Input: ecoli_validation_rows.parquet from validate_ecoli.py (one row per EA sample
with reanalysis rain and dipcast's corrected hindcast risk). Evaluation is
leave-one-year-out, so every score is out of sample in time; the final model is
refitted on all years and written to ecoli_model.json for the site.

Baselines: climatology by water-body type (the exceedance rate at rivers / lakes in
the training years) and rain-only.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.model.ecoli import FEATURES, THRESHOLD, features, fit
from dipcast.model.verify import reliability_table, scores

VARIANTS = {
    "climatology by type": [],
    "rain only": ["lrain48", "lrain24", "lake", "lake_x_lrain48"],
    "rain + season": ["lrain48", "lrain24", "lake", "lake_x_lrain48", "doy_sin", "doy_cos"],
    "spill exposure only": ["logit_risk", "lake"],
    "rain + spill exposure": ["lrain48", "lrain24", "logit_risk", "lake", "lake_x_lrain48"],
    "rain + spill exposure + season (dipcast)": FEATURES,
}


def loyo(X: pd.DataFrame, y: np.ndarray, years: np.ndarray, lake: np.ndarray, cols: list[str]) -> np.ndarray:
    pred = np.full(len(y), np.nan)
    for yr in np.unique(years):
        tr, te = years != yr, years == yr
        if not cols:   # climatology by type
            for is_lake in (0.0, 1.0):
                m = tr & (lake == is_lake)
                pred[te & (lake == is_lake)] = y[m].mean() if m.any() else y[tr].mean()
            continue
        pred[te] = fit(X[tr], y[tr], cols).predict(X[te])
    return pred


def main() -> None:
    rows = pd.read_parquet(config.PROCESSED / "ecoli_validation_rows.parquet")
    rows = rows[rows.upstream > 0].dropna(subset=["rain_48h", "rain_24h", "risk_model", "ecoli"]).reset_index(drop=True)
    rows["lake"] = (rows["kind"] == "lake").astype(float)
    X = features(rows.rain_48h, rows.rain_24h, rows.risk_model, rows.lake, rows.sample_time)
    y = (rows["ecoli"] > THRESHOLD).astype(int).to_numpy()
    years = rows["sample_time"].dt.year.to_numpy()
    lake = rows["lake"].to_numpy()
    print(f"{len(rows)} samples, {rows.bw_id.nunique()} sites, {y.mean():.3f} exceed {THRESHOLD}; years {sorted(set(years))}")
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
    print("\nleave-one-year-out:")
    print(tab.round(3).to_string())
    best = "rain + spill exposure + season (dipcast)"
    rel = reliability_table(y, preds[best]).round(3)
    print("\nreliability (dipcast variant):")
    print(rel.to_string(index=False))
    final = fit(X, y, FEATURES, meta={
        "target": f"E. coli > {THRESHOLD} cfu/100 ml", "n_samples": len(rows), "n_sites": int(rows.bw_id.nunique()),
        "years": [int(v) for v in sorted(set(years))], "sites": "EA inland designated bathing waters with monitored overflows upstream",
        "loyo": {k: {"brier": round(v["brier"], 4), "auc": round(v["auc"], 3)} for k, v in results.items()},
        "base_rate": round(float(y.mean()), 4),
    })
    final.save()
    print("\ncoefficients (per standard deviation):", {k: round(v, 3) for k, v in final.coef.items()})
    out = {"n_samples": len(rows), "n_sites": int(rows.bw_id.nunique()), "threshold": THRESHOLD,
           "base_rate": float(y.mean()), "years": [int(v) for v in sorted(set(years))],
           "loyo": tab.round(4).reset_index().rename(columns={"index": "model"}).to_dict("records"),
           "reliability": rel.to_dict("records"), "coef_per_sd": final.coef}
    (config.PROCESSED / "ecoli_model_eval.json").write_text(json.dumps(out, indent=1))
    print("saved ecoli_model.json and ecoli_model_eval.json")


if __name__ == "__main__":
    main()
