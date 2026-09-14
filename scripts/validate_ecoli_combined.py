"""Second stage of the E. coli validation: does spill risk add anything to rainfall?

Reads the per-sample rows written by validate_ecoli.py and answers two questions
that the single-predictor table blurs together:

  between sites  Spearman correlation of site-mean risk (and site-mean rain) with
                 site-mean log E. coli: does dipcast rank *which* bathing waters are dirty?
  within site    leave-one-year-out linear fits of site-demeaned log E. coli on
                 log1p(rain_48h), log1p(rain_24h), logit(risk_model) and season; the
                 held-out predictions are pooled and scored by Spearman correlation with
                 the demeaned target, RMSE, and AUC for exceedance of 900 cfu/100 ml
                 (site mean from the training years added back for the AUC).

Writes ecoli_validation_combined.json, which the verification page reads.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from dipcast import config

MODELS = {
    "rain only": ["lrain48", "lrain24"],
    "spill risk only": ["logit_risk"],
    "rain + spill risk": ["lrain48", "lrain24", "logit_risk"],
    "rain + spill risk + season": ["lrain48", "lrain24", "logit_risk", "doy_sin", "doy_cos"],
}


def features(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["lrain48"] = np.log1p(d["rain_48h"])
    d["lrain24"] = np.log1p(d["rain_24h"])
    r = d["risk_model"].clip(1e-3, 1 - 1e-3)
    d["logit_risk"] = np.log(r / (1 - r))
    doy = d["sample_time"].dt.dayofyear
    d["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    d["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    d["year"] = d["sample_time"].dt.year
    return d


def demean(d: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return d[cols] - d.groupby("bw_id")[cols].transform("mean")


def loyo(d: pd.DataFrame, cols: list[str]) -> dict:
    """Leave-one-year-out OLS on site-demeaned data; pooled held-out scores."""
    pred_within = pd.Series(index=d.index, dtype=float)
    pred_level = pd.Series(index=d.index, dtype=float)
    for yr in sorted(d["year"].unique()):
        tr, te = d[d.year != yr], d[d.year == yr]
        # demean within the training years only, so held-out samples see no target information
        mu_x = tr.groupby("bw_id")[cols].mean(); mu_y = tr.groupby("bw_id")["log_ecoli"].mean()
        te = te[te.bw_id.isin(mu_y.index)]
        if len(te) == 0 or len(tr) < 50:
            continue
        xtr = (tr[cols] - tr.groupby("bw_id")[cols].transform("mean")).to_numpy()
        ytr = (tr["log_ecoli"] - tr.groupby("bw_id")["log_ecoli"].transform("mean")).to_numpy()
        beta, *_ = np.linalg.lstsq(xtr, ytr, rcond=None)
        xte = (te[cols].to_numpy() - mu_x.loc[te.bw_id].to_numpy())
        pw = xte @ beta
        pred_within.loc[te.index] = pw
        pred_level.loc[te.index] = pw + mu_y.loc[te.bw_id].to_numpy()
    m = pred_within.notna()
    dd = d[m]
    y_within = demean(dd, ["log_ecoli"])["log_ecoli"]
    y_exc = (dd["ecoli"] > 900).astype(int)
    return {"n": int(m.sum()),
            "rho_within": round(float(spearmanr(pred_within[m], y_within).statistic), 3),
            "rmse": round(float(np.sqrt(np.mean((pred_within[m] - y_within) ** 2))), 3),
            "auc_gt900": round(float(roc_auc_score(y_exc, pred_level[m])), 3) if 0 < y_exc.mean() < 1 else None}


def between_site(d: pd.DataFrame) -> dict:
    s = d.groupby("bw_id").agg(risk=("risk_model", "mean"), rain=("rain_48h", "mean"), ecoli=("log_ecoli", "mean"))
    return {"sites": len(s),
            "between_site_rho_risk": round(float(spearmanr(s.risk, s.ecoli).statistic), 3),
            "between_site_rho_rain": round(float(spearmanr(s.rain, s.ecoli).statistic), 3)}


def main() -> None:
    rows = pd.read_parquet(config.PROCESSED / "ecoli_validation_rows.parquet")
    d = features(rows[rows.upstream > 0].dropna(subset=["rain_48h", "rain_24h", "risk_model", "log_ecoli"]))
    out: dict = {"n_samples": len(d), **between_site(d),
                 "loyo_within_site": {name: loyo(d, cols) for name, cols in MODELS.items()}}
    for kind in ("river", "lake"):
        dk = d[d.kind == kind]
        if len(dk) >= 100:
            out[f"loyo_within_site_{kind}"] = {name: loyo(dk, cols) for name, cols in MODELS.items()}
    full = demean(d, MODELS["rain + spill risk"] + ["log_ecoli"])
    beta, *_ = np.linalg.lstsq(full[MODELS["rain + spill risk"]].to_numpy(), full["log_ecoli"].to_numpy(), rcond=None)
    out["coef_lrain48_lrain24_logitrisk"] = [round(float(b), 3) for b in beta]
    (config.PROCESSED / "ecoli_validation_combined.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
