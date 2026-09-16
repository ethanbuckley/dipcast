"""Fit and evaluate the E. coli exceedance model on the bathing-water samples.

Input: ecoli_validation_rows.parquet from validate_ecoli.py (one row per EA sample
with reanalysis rain and dipcast's hindcast exposure) plus, when present,
ecoli_exposure_leads.parquet from replay_ecoli_leads.py (the exposure as the site
would have computed it k days ahead, and the exposure from the 2023-24 spill model
for the forward-in-time test).

Evaluations, all against the rain-only model as the competitor that matters:

* leave-one-year-out (LOYO): each year predicted by a model fitted on the others
* leave-one-site-out (LOSO): each bathing water predicted by a model that never
  saw it, the test that matches the promise of a forecast at any spot
* forward in time: fitted on 2023-24, scored on 2025-26, with the exposure
  feature from the spill model that also stops at 2024 and no calibration map
* by lead, replayed: the lead-k model scored with lead-k spot rain *and* lead-k
  exposure (only when the replay file exists; otherwise the fixed-exposure
  figures are reported and flagged as preliminary)
* uncertainty: a cluster bootstrap over (site, ISO week) blocks on the LOYO
  predictions, giving intervals for the Brier and AUC gain over rain alone
* rivers and lakes separately throughout

The final model is refitted on all rows (lead-0 forecast rain, production
exposure) and written to ecoli_model.json for the site.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from dipcast import config
from dipcast.ingest.rainfall import grid_cell
from dipcast.model.ecoli import FEATURES, THRESHOLD, features, fit, rain_windows
from dipcast.model.forecast import MAX_MISSING_SHARE
from dipcast.model.verify import reliability_table, scores

VARIANTS = {
    "climatology by type": [],
    "rain only": ["lrain48", "lrain24", "lake", "lake_x_lrain48"],
    "rain + season": ["lrain48", "lrain24", "lake", "lake_x_lrain48", "doy_sin", "doy_cos"],
    "spill exposure only": ["logit_risk", "lake"],
    "rain + spill exposure": ["lrain48", "lrain24", "logit_risk", "lake", "lake_x_lrain48"],
    "rain + spill exposure + season (dipcast)": FEATURES,
}
RAIN_ONLY = "rain only"
RAIN_SEASON = "rain + season"
FULL = "rain + spill exposure + season (dipcast)"
HEAVY_RAIN_MM = 10.0   # 48 h total above which a day counts as wet, for the availability check
LEADS = (0, 1, 2, 3, 4)
FORWARD_TRAIN_UNTIL = 2024
BOOT = 2000
EXPOSURE_FILE = config.PROCESSED / "ecoli_exposure_leads.parquet"


def forecast_rain_at_samples(rows: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """48 h and 24 h rain before each sample time from the archived forecast at each
    lead, and the finite fraction of the 48 h window (1.0 = complete)."""
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
    out = {k: (np.full(len(rows), np.nan), np.full(len(rows), np.nan), np.full(len(rows), np.nan)) for k in LEADS}
    for (a, b), g in leads.groupby(["cell_lat", "cell_lon"]):
        idx = np.where((rows.cell_lat == a) & (rows.cell_lon == b))[0]
        if len(idx) == 0:
            continue
        ends = pd.DatetimeIndex(rows["sample_time"].iloc[idx])
        for k in LEADS:
            hourly = g[g.lead == k].set_index("time")["precip_mm"].sort_index()
            r48, r24, c48, _ = rain_windows(hourly, ends, return_coverage=True)
            out[k][0][idx], out[k][1][idx], out[k][2][idx] = r48, r24, c48
    return out


def _fit_predict(X, y, tr, te, cols, lake, X_eval=None):
    X_eval = X if X_eval is None else X_eval
    if not cols:   # climatology by type
        pred = np.full(te.sum(), np.nan)
        lk = lake[te]
        for is_lake in (0.0, 1.0):
            m = tr & (lake == is_lake)
            pred[lk == is_lake] = y[m].mean() if m.any() else y[tr].mean()
        return pred
    return fit(X[tr], y[tr], cols).predict(X_eval[te])


def leave_one_out(X, y, groups, lake, cols, X_eval=None) -> np.ndarray:
    """Predictions where each group (year or site) is scored by a model fitted on the rest."""
    pred = np.full(len(y), np.nan)
    for g in np.unique(groups):
        tr, te = groups != g, groups == g
        if y[tr].sum() == 0:
            continue
        pred[te] = _fit_predict(X, y, tr, te, cols, lake, X_eval)
    return pred


def by_type(y, p, lake) -> dict:
    out = {}
    for name, v in (("river", 0.0), ("lake", 1.0)):
        m = (lake == v) & ~np.isnan(p)
        out[name] = scores(y[m], p[m]) if m.sum() >= 30 else None
    return out


def cluster_bootstrap(y, p_ref, p_new, clusters, mask=None, n_boot=BOOT, seed=1) -> dict:
    """Intervals for the gain of p_new over p_ref (Brier reduction, AUC increase),
    resampling whole clusters. Samples at one site in one week share weather and
    a water body, so they are not independent draws."""
    rng = np.random.default_rng(seed)
    m = np.ones(len(y), dtype=bool) if mask is None else mask
    m &= ~np.isnan(p_ref) & ~np.isnan(p_new)
    y, p_ref, p_new, clusters = y[m], p_ref[m], p_new[m], np.asarray(clusters)[m]
    uniq, inv = np.unique(clusters, return_inverse=True)
    idx_by = [np.where(inv == i)[0] for i in range(len(uniq))]
    d_brier, d_auc = [], []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate([idx_by[i] for i in pick])
        yy = y[idx]
        d_brier.append(np.mean((p_ref[idx] - yy) ** 2) - np.mean((p_new[idx] - yy) ** 2))
        if 0 < yy.mean() < 1:
            d_auc.append(roc_auc_score(yy, p_new[idx]) - roc_auc_score(yy, p_ref[idx]))
    d_brier, d_auc = np.array(d_brier), np.array(d_auc)
    return {"n": len(y), "clusters": len(uniq),
            "brier_ref": float(np.mean((p_ref - y) ** 2)), "brier_new": float(np.mean((p_new - y) ** 2)),
            "brier_gain": float(np.mean((p_ref - y) ** 2) - np.mean((p_new - y) ** 2)),
            "brier_gain_ci": [float(np.percentile(d_brier, 2.5)), float(np.percentile(d_brier, 97.5))],
            "p_gain_le_0": float((d_brier <= 0).mean()),
            "auc_ref": float(roc_auc_score(y, p_ref)) if 0 < y.mean() < 1 else None,
            "auc_new": float(roc_auc_score(y, p_new)) if 0 < y.mean() < 1 else None,
            "auc_gain_ci": [float(np.percentile(d_auc, 2.5)), float(np.percentile(d_auc, 97.5))] if len(d_auc) else None}


def main() -> None:
    rows = pd.read_parquet(config.PROCESSED / "ecoli_validation_rows.parquet")
    rows = rows[rows.upstream > 0].dropna(subset=["rain_48h", "rain_24h", "risk_model", "ecoli"]).reset_index(drop=True)
    rows["lake"] = (rows["kind"] == "lake").astype(float)
    rows["day"] = rows["sample_time"].dt.floor("D")
    sites = pd.read_parquet(config.RAW / "bwq_samples.parquet").drop_duplicates("bw_id").set_index("bw_id")
    rows["lat"], rows["lon"] = sites.loc[rows.bw_id, "lat"].to_numpy(), sites.loc[rows.bw_id, "lon"].to_numpy()
    n0 = len(rows)

    # Exposure by lead from the replay, if it exists.
    replayed = EXPOSURE_FILE.exists()
    if replayed:
        ex = pd.read_parquet(EXPOSURE_FILE)
        ex["day"] = pd.to_datetime(ex["day"])
        if ex["day"].dt.tz is None and rows["day"].dt.tz is not None:
            ex["day"] = ex["day"].dt.tz_localize(rows["day"].dt.tz)
        if "missing_share" not in ex:
            ex["missing_share"] = np.where(ex["risk_prod"].isna(), 1.0, 0.0)
        for k in LEADS:
            e = ex[ex.lead == k][["bw_id", "day", "risk_prod", "risk_holdout", "missing_share"]].rename(
                columns={"risk_prod": f"risk_lead{k}", "risk_holdout": f"risk_hold_lead{k}", "missing_share": f"miss_lead{k}"})
            rows = rows.merge(e, on=["bw_id", "day"], how="left")
    else:
        for k in LEADS:
            rows[f"risk_lead{k}"] = rows["risk_model"]; rows[f"risk_hold_lead{k}"] = np.nan; rows[f"miss_lead{k}"] = 0.0

    fc = forecast_rain_at_samples(rows)
    ok_rain = np.all([~np.isnan(fc[k][0]) for k in LEADS], axis=0)
    # Production's rule: a figure is shown when at most MAX_MISSING_SHARE of the transport
    # weight comes from overflow-days without rain data. The replay applies the same rule.
    shown = {k: (rows[f"miss_lead{k}"].fillna(1.0).to_numpy() <= MAX_MISSING_SHARE) & rows[f"risk_lead{k}"].notna().to_numpy()
             for k in LEADS}
    ok_exp = np.all([shown[k] for k in LEADS], axis=0)
    years_all = rows["sample_time"].dt.year.to_numpy()
    wet0 = rows["rain_48h"].to_numpy() >= HEAVY_RAIN_MM   # reanalysis rain: the same for every lead
    exceed_all = (rows["ecoli"] > THRESHOLD).to_numpy()
    # Availability: would the site have shown a figure, overall, on wet days, and on exceedance days?
    availability = {int(k): {"all": float(shown[k].mean()), "wet_days": float(shown[k][wet0].mean()) if wet0.any() else None,
                             "exceedance_days": float(shown[k][exceed_all].mean()) if exceed_all.any() else None,
                             "rain_window_complete": float((fc[k][2] >= 1.0).mean())}
                    for k in LEADS}
    counts = {"samples_with_upstream": n0,
              "dropped_missing_forecast_rain": int((~ok_rain).sum()),
              "dropped_missing_replayed_exposure": int((ok_rain & ~ok_exp).sum()),
              "by_year_before": {int(y): int((years_all == y).sum()) for y in np.unique(years_all)},
              "by_year_after": {int(y): int(((years_all == y) & ok_rain & ok_exp).sum()) for y in np.unique(years_all)},
              "by_lead_rain_available": {int(k): int((~np.isnan(fc[k][0])).sum()) for k in LEADS},
              "by_lead_exposure_available": {int(k): int(shown[k].sum()) for k in LEADS},
              "availability": availability, "max_missing_share": MAX_MISSING_SHARE}
    ok = ok_rain & ok_exp
    rows = rows[ok].reset_index(drop=True)
    fc = {k: (fc[k][0][ok], fc[k][1][ok], fc[k][2][ok]) for k in LEADS}
    complete48 = fc[0][2] >= 1.0
    y = (rows["ecoli"] > THRESHOLD).astype(int).to_numpy()
    years = rows["sample_time"].dt.year.to_numpy()
    lake = rows["lake"].to_numpy()
    site = rows["bw_id"].to_numpy()
    week = (rows["bw_id"] + "|" + rows["sample_time"].dt.strftime("%G-%V")).to_numpy()
    cal_week = rows["sample_time"].dt.strftime("%G-%V").to_numpy()      # shared weather across sites
    rivers = lake == 0.0
    print(f"{len(rows)} samples with forecast rain and exposure at every lead, {rows.bw_id.nunique()} sites, "
          f"{y.mean():.3f} exceed {THRESHOLD}; years {sorted({int(v) for v in years})}; replayed exposure: {replayed}")
    print("row counts:", json.dumps(counts))

    X_lead = {k: features(fc[k][0], fc[k][1], rows[f"risk_lead{k}"], rows.lake, rows.sample_time) for k in LEADS}
    X_fixed = {k: features(fc[k][0], fc[k][1], rows["risk_lead0"], rows.lake, rows.sample_time) for k in LEADS}
    X = X_lead[0]

    # ---- leave-one-year-out, all variants
    results, preds = {}, {}
    for name, cols in VARIANTS.items():
        p = leave_one_out(X, y, years, lake, cols)
        preds[name] = p
        s = scores(y, p); s["by_type"] = by_type(y, p, lake)
        results[name] = s

    def table(res: dict, ref_key: str = "climatology by type") -> pd.DataFrame:
        ref = res[ref_key]["brier"]
        return pd.DataFrame({n: {"brier": r["brier"], "log_loss": r["log_loss"], "auc": r["auc"], "n": r["n"],
                                 "skill_vs_clim": 1 - r["brier"] / ref,
                                 "brier_river": (r["by_type"]["river"] or {}).get("brier"), "auc_river": (r["by_type"]["river"] or {}).get("auc"),
                                 "brier_lake": (r["by_type"]["lake"] or {}).get("brier"), "auc_lake": (r["by_type"]["lake"] or {}).get("auc")}
                             for n, r in res.items()}).T
    tab = table(results)
    pd.set_option("display.width", 200)
    print("\nleave-one-year-out, fitted and scored on lead-0 forecast rain:")
    print(tab.round(3).to_string())

    # ---- leave-one-site-out
    loso_res = {}
    for name in ("climatology by type", RAIN_ONLY, RAIN_SEASON, "spill exposure only", FULL):
        p = leave_one_out(X, y, site, lake, VARIANTS[name])
        s = scores(y[~np.isnan(p)], p[~np.isnan(p)]); s["by_type"] = by_type(y, p, lake)
        loso_res[name] = s; preds["loso:" + name] = p
    loso_tab = table(loso_res)
    print("\nleave-one-site-out (each bathing water predicted by a model that never saw it):")
    print(loso_tab.round(3).to_string())

    # ---- forward in time: fit <= 2024, score 2025-26, exposure from the 2023-24 spill model
    forward = None
    if rows["risk_hold_lead0"].notna().all():
        Xh = features(fc[0][0], fc[0][1], rows["risk_hold_lead0"], rows.lake, rows.sample_time)
        tr, te = years <= FORWARD_TRAIN_UNTIL, years > FORWARD_TRAIN_UNTIL
        fw = {}
        for name in ("climatology by type", RAIN_ONLY, RAIN_SEASON, FULL):
            p = np.full(len(y), np.nan); p[te] = _fit_predict(Xh, y, tr, te, VARIANTS[name], lake)
            s = scores(y[te], p[te]); s["by_type"] = by_type(y, p, lake); fw[name] = s; preds["fwd:" + name] = p
        forward = {"train_years": sorted({int(v) for v in years[tr]}), "test_years": sorted({int(v) for v in years[te]}),
                   "n_train": int(tr.sum()), "n_test": int(te.sum()), "exposure": "spill model fitted 2023-24, uncalibrated",
                   "table": table(fw).round(4).reset_index().rename(columns={"index": "model"}).to_dict("records"),
                   "bootstrap_all": cluster_bootstrap(y, preds["fwd:" + RAIN_ONLY], preds["fwd:" + FULL], week, mask=te),
                   "bootstrap_rivers": cluster_bootstrap(y, preds["fwd:" + RAIN_ONLY], preds["fwd:" + FULL], week, mask=te & rivers),
                   "bootstrap_rivers_by_site": cluster_bootstrap(y, preds["fwd:" + RAIN_ONLY], preds["fwd:" + FULL], site, mask=te & rivers),
                   "bootstrap_rivers_by_calendar_week": cluster_bootstrap(y, preds["fwd:" + RAIN_ONLY], preds["fwd:" + FULL], cal_week, mask=te & rivers)}
        print(f"\nforward in time: fit {forward['train_years']} ({forward['n_train']}), score {forward['test_years']} ({forward['n_test']}):")
        print(table(fw).round(3).to_string())

    # ---- uncertainty of the gain over rain alone (LOYO and LOSO predictions), under three
    # dependence assumptions: site-weeks; whole sites (all weeks at a site move together);
    # calendar weeks across all sites (one storm hits many sites at once). Two comparisons:
    # rain only vs the full model (the headline) and rain + season vs the full model, which
    # attributes the gain to the exposure term alone.
    if RAIN_SEASON not in preds:
        preds[RAIN_SEASON] = leave_one_out(X, y, years, lake, VARIANTS[RAIN_SEASON])
    preds["loso:" + RAIN_SEASON] = leave_one_out(X, y, site, lake, VARIANTS[RAIN_SEASON])
    groupings = {"site_week": week, "site": site, "calendar_week": cal_week}
    boots = {}
    for test, ref_key, new_key in (("loyo", RAIN_ONLY, FULL), ("loso", "loso:" + RAIN_ONLY, "loso:" + FULL),
                                   ("loyo_vs_rain_season", RAIN_SEASON, FULL), ("loso_vs_rain_season", "loso:" + RAIN_SEASON, "loso:" + FULL)):
        for subset, mask in (("all", None), ("rivers", rivers), ("lakes", ~rivers)):
            if subset == "lakes" and "season" in test:
                continue
            for gname, g in groupings.items():
                boots[f"{test}|{subset}|{gname}"] = cluster_bootstrap(y, preds[ref_key], preds[new_key], g, mask=mask)
    print("\ngain of the full model over the reference (cluster bootstrap, 95% intervals):")
    for k, b in boots.items():
        if "rivers" not in k and "all" not in k:
            continue
        print(f"  {k:36s} n={b['n']:5d} clusters={b['clusters']:4d} Brier {b['brier_ref']:.4f} -> {b['brier_new']:.4f}, gain {1000*b['brier_gain']:+.1f}e-3 "
              f"[{1000*b['brier_gain_ci'][0]:+.1f}, {1000*b['brier_gain_ci'][1]:+.1f}], P(<=0)={b['p_gain_le_0']:.3f}")
    # Rain-window sensitivity: the same LOYO comparison on samples whose 48 h window was complete.
    window_sens = {"share_complete": float(complete48.mean()),
                   "complete_only": cluster_bootstrap(y, preds[RAIN_ONLY], preds[FULL], week, mask=rivers & complete48),
                   "all_accepted": cluster_bootstrap(y, preds[RAIN_ONLY], preds[FULL], week, mask=rivers)}
    print(f"\nrain-window sensitivity: {100*window_sens['share_complete']:.1f}% of accepted windows are complete; rivers LOYO gain "
          f"complete-only {1000*window_sens['complete_only']['brier_gain']:+.1f}e-3 vs all {1000*window_sens['all_accepted']['brier_gain']:+.1f}e-3")
    print("availability by lead:", json.dumps(availability))

    # ---- by lead: full replay (exposure and rain at lead k) vs fixed exposure, LOYO fit on lead 0
    by_lead = []
    for k in LEADS:
        for label, XX in (("replayed exposure", X_lead[k]), ("fixed lead-0 exposure", X_fixed[k])):
            p = leave_one_out(X, y, years, lake, FEATURES, X_eval=XX)
            pr = leave_one_out(X, y, years, lake, VARIANTS[RAIN_ONLY], X_eval=XX)
            s = scores(y, p); sr = scores(y, pr)
            by_lead.append({"lead": k, "exposure": label, "n": len(y), "brier": s["brier"], "auc": s["auc"],
                            "brier_rain_only": sr["brier"], "auc_rain_only": sr["auc"],
                            "brier_river": by_type(y, p, lake)["river"]["brier"], "auc_river": by_type(y, p, lake)["river"]["auc"],
                            "brier_river_rain_only": by_type(y, pr, lake)["river"]["brier"]})
    bl = pd.DataFrame(by_lead)
    print("\nby lead (leave-one-year-out; model fitted on lead 0 and scored with the rain and exposure available at lead k):")
    print(bl.round(4).to_string(index=False))
    if not replayed:
        print("  NOTE: no replay file; 'replayed exposure' rows equal the fixed ones. Run replay_ecoli_leads.py.")

    # ---- reliability and the final fit
    rel = reliability_table(y, preds[FULL]).round(3)
    print("\nreliability (dipcast variant, LOYO):")
    print(rel.to_string(index=False))
    final = fit(X, y, FEATURES, meta={
        "target": f"E. coli > {THRESHOLD} cfu/100 ml", "n_samples": len(rows), "n_sites": int(rows.bw_id.nunique()),
        "rain_source": "Open-Meteo archived forecast, lead 0 (48 h / 24 h to the sample time)",
        "exposure_source": "replayed lead-0 exposure" if replayed else "hindcast exposure (reanalysis rain)",
        "years": sorted({int(v) for v in years}), "sites": "EA inland designated bathing waters with monitored overflows upstream",
        "loyo": {k: {"brier": round(v["brier"], 4), "auc": round(v["auc"], 3)} for k, v in results.items()},
        "loso": {k: {"brier": round(v["brier"], 4), "auc": round(v["auc"], 3)} for k, v in loso_res.items()},
        "base_rate": round(float(y.mean()), 4),
        "base_rate_by_type": {"river": round(float(y[rivers].mean()), 4), "lake": round(float(y[~rivers].mean()), 4)},
        "validated_scope": "rivers, May-September; no ranking skill on lakes",
    })
    final.save()
    print("\ncoefficients (per standard deviation):", {k: round(v, 3) for k, v in final.coef.items()})
    out = {"n_samples": len(rows), "n_sites": int(rows.bw_id.nunique()), "threshold": THRESHOLD,
           "base_rate": float(y.mean()), "base_rate_by_type": final.meta["base_rate_by_type"],
           "years": sorted({int(v) for v in years}), "row_counts": counts, "exposure_replayed": replayed,
           "loyo": tab.round(4).reset_index().rename(columns={"index": "model"}).to_dict("records"),
           "loso": loso_tab.round(4).reset_index().rename(columns={"index": "model"}).to_dict("records"),
           "forward": forward, "bootstrap": boots, "rain_window_sensitivity": window_sens,
           "rain_window": "(t - 48 h, t]: 48 hour stamps, at least 90% finite",
           "by_lead": bl.round(4).to_dict("records"),
           "reliability": rel.to_dict("records"), "coef_per_sd": final.coef, "rain_source": "forecast lead 0",
           "notes": ["Leave-one-year-out and leave-one-site-out use the production spill model for exposure, which saw 2023-25 spills.",
                     "Forward test: exposure from the 2023-24 spill model without the 2025-fitted calibration map.",
                     "Lead-k replay: 30-day rain and antecedent index use the analysis series; previous-runs fields stitch runs issued k days before each hour."]}
    (config.PROCESSED / "ecoli_model_eval.json").write_text(json.dumps(out, indent=1, default=float))
    print("saved ecoli_model.json and ecoli_model_eval.json")


if __name__ == "__main__":
    main()
