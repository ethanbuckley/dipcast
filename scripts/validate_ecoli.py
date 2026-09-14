"""Does dipcast's point risk predict measured E. coli at inland bathing waters?

For every EA lab sample at the 38 inland designated bathing waters (2023 on):

  rain_48h      rainfall at the site in the 48 h before sampling (the naive competitor)
  risk_model    dipcast hindcast for the sample day: rainfall-driven spill probabilities
                (reanalysis rain) through the transport step, lead-calibrated as lead 0
  risk_observed United Utilities sites only: the same transport step applied to the
                spills that actually happened (event history), so the transport layer
                is tested without the spill model's error

against log10 E. coli and against exceedance of 900 and 500 cfu/100 ml. Spearman
rank correlation pooled and within site (site means removed, so a site that is
always dirty cannot inflate the score), and AUC for exceedance.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from dipcast import config
from dipcast.model.features import ALL_FEATURES, build_site_days, daily_rain_features
from dipcast.model.forecast import calibrate_at_lead
from dipcast.model.spill_model import SpillModel
from dipcast.model.transport import combine_daily, locate_pin, river_velocity, upstream_overflows
from dipcast.network.rivers import RiverNetwork
from dipcast.overflows import load_overflows

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("validate_ecoli")
TZ = "Europe/London"
THRESHOLDS = (500, 900)


def load_rain() -> pd.DataFrame:
    files = sorted((config.CACHE / "rain").glob("archive_*.parquet"))
    rain = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    rain["time"] = rain["time"].dt.tz_convert(TZ)
    return rain


def main() -> None:
    samples = pd.read_parquet(config.RAW / "bwq_samples.parquet")
    samples = samples.dropna(subset=["ecoli", "sample_time"]).copy()
    samples["day"] = samples["sample_time"].dt.floor("D")
    samples["log_ecoli"] = np.log10(samples["ecoli"].clip(lower=1))
    log.info("%d samples at %d sites, %s -> %s", len(samples), samples.bw_id.nunique(),
             samples.sample_time.min().date(), samples.sample_time.max().date())

    net = RiverNetwork.load(); ov = load_overflows(net)
    model = SpillModel.load()
    rain = load_rain()
    hourly = rain.set_index(["cell_lat", "cell_lon"]).sort_index()
    daily = daily_rain_features(rain)
    events = pd.read_parquet(config.PROCESSED / "edm_events.parquet")
    events["event_start"] = events["event_start"].dt.tz_convert(TZ)
    events["event_end"] = events["event_end"].fillna(events["event_start"]).dt.tz_convert(TZ)

    out = []
    for bw_id, g in samples.groupby("bw_id"):
        s = g.iloc[0]
        pin = locate_pin(net, float(s.lon), float(s.lat))
        up = upstream_overflows(net, pin, ov, velocity_ms=river_velocity(None))
        from dipcast.ingest.rainfall import grid_cell
        cl, cn = (float(v) for v in grid_cell(float(s.lat), float(s.lon)))
        try:
            site_rain = hourly.loc[(cl, cn)].set_index("time")["precip_mm"].sort_index()
        except KeyError:
            log.warning("%s: no rainfall for site cell", s["name"]); continue
        # Model hindcast: spill probabilities at every upstream overflow on every day of the
        # sampling period (a continuous daily grid: combine_daily shifts by array position, so
        # it must see consecutive days, not just sample days), then pick out the sample days.
        sample_days = pd.DatetimeIndex(sorted(g["day"].unique()))
        p_by_day = {}
        if len(up):
            lead_days = int(np.ceil(up["travel_h"].max() / 24.0)) + 1
            days = pd.date_range(sample_days.min() - pd.Timedelta(days=lead_days), sample_days.max(), freq="D")
            sd = build_site_days(up[["site_id", "lat", "lon", "company", "lta_spills", "spill_hours", "edm_operational_pct"]],
                                 daily, days)
            sd = sd.astype({f: np.float32 for f in ALL_FEATURES}).fillna({f: 0.0 for f in ALL_FEATURES})
            sd["p"] = model.predict(sd)
            mat = sd.pivot(index="site_id", columns="day", values="p").reindex(index=up["site_id"], columns=days)
            pm = np.nan_to_num(mat.to_numpy(dtype=float))
            pm = calibrate_at_lead(pm, lead=0)  # a hindcast: every day is lead 0
            w = up["weight"].to_numpy(dtype=float); tr = up["travel_h"].to_numpy(dtype=float)
            risk = pd.Series(combine_daily(pm, w, tr), index=days)
            p_by_day = risk.reindex(sample_days).to_dict()
        for _, r in g.iterrows():
            t = r["sample_time"]
            win = site_rain.loc[t - pd.Timedelta(hours=48): t]
            rain_48 = float(win.sum()) if len(win) else np.nan
            win24 = site_rain.loc[t - pd.Timedelta(hours=24): t]
            rain_24 = float(win24.sum()) if len(win24) else np.nan
            risk_model = float(p_by_day.get(r["day"], 0.0)) if len(up) else 0.0
            # Observed-spill exposure (event history only covers United Utilities).
            risk_obs = np.nan
            if len(up) and (up["company"] == "United Utilities").any():
                contrib = []
                for _, o in up.iterrows():
                    if o["company"] != "United Utilities":
                        continue
                    arrive_by = t - pd.Timedelta(hours=float(o["travel_h"]))
                    ev = events[(events.site_id == o["site_id"]) & (events.event_start <= arrive_by)
                                & (events.event_end >= arrive_by - pd.Timedelta(hours=48))]
                    if len(ev):
                        age_h = max(0.0, (arrive_by - ev["event_end"].max()).total_seconds() / 3600)
                        contrib.append(float(o["weight"]) * 10 ** (-age_h / config.T90_HOURS))
                risk_obs = 1 - np.prod([1 - c for c in contrib]) if contrib else 0.0
            out.append({"bw_id": bw_id, "name": s["name"], "kind": s["kind"], "undertaker": s["undertaker"],
                        "sample_time": t, "ecoli": r["ecoli"], "log_ecoli": r["log_ecoli"], "ie": r["ie"],
                        "abnormal_weather": r["abnormal_weather"], "upstream": len(up),
                        "rain_24h": rain_24, "rain_48h": rain_48, "risk_model": risk_model, "risk_observed": risk_obs})
    df = pd.DataFrame(out)
    df.to_parquet(config.PROCESSED / "ecoli_validation_rows.parquet", index=False)
    log.info("scored %d samples at %d sites (%d with upstream overflows)", len(df), df.bw_id.nunique(),
             df[df.upstream > 0].bw_id.nunique())

    def within_site(d: pd.DataFrame, col: str) -> pd.Series:
        return d[col] - d.groupby("bw_id")[col].transform("mean")

    def evaluate(d: pd.DataFrame, preds: list[str]) -> pd.DataFrame:
        rows = []
        for p in preds:
            dd = d.dropna(subset=[p, "log_ecoli"])
            if len(dd) < 30:
                continue
            row = {"predictor": p, "n": len(dd), "sites": dd.bw_id.nunique(),
                   "spearman_pooled": spearmanr(dd[p], dd["log_ecoli"]).statistic,
                   "spearman_within_site": spearmanr(within_site(dd, p), within_site(dd, "log_ecoli")).statistic}
            for thr in THRESHOLDS:
                y = (dd["ecoli"] > thr).astype(int)
                row[f"auc_gt{thr}"] = roc_auc_score(y, dd[p]) if 0 < y.mean() < 1 else np.nan
                row[f"rate_gt{thr}"] = y.mean()
            rows.append(row)
        return pd.DataFrame(rows).set_index("predictor")

    pd.set_option("display.width", 180)
    results = {}
    print("\n=== All inland sites with upstream overflows (model hindcast vs rain) ===")
    d_all = df[df.upstream > 0]
    results["all_sites"] = evaluate(d_all, ["rain_24h", "rain_48h", "risk_model"])
    print(results["all_sites"].round(3).to_string())
    for kind in ("river", "lake"):
        dk = d_all[d_all.kind == kind]
        if len(dk) >= 30:
            print(f"\n--- {kind}s only ---")
            results[kind] = evaluate(dk, ["rain_48h", "risk_model"])
            print(results[kind].round(3).to_string())
    d_uu = df[(df.undertaker.str.contains("United Utilities", na=False)) & (df.upstream > 0)]
    if len(d_uu) >= 30:
        print("\n=== United Utilities sites: observed spills through the transport step ===")
        results["uu_observed"] = evaluate(d_uu, ["rain_48h", "risk_model", "risk_observed"])
        print(results["uu_observed"].round(3).to_string())
    print("\n=== per-site Spearman (risk_model vs log E. coli), sites with >= 15 samples ===")
    per = []
    for bw, g in d_all.groupby("bw_id"):
        if len(g) >= 15 and g.risk_model.std() > 0:
            per.append({"site": g.name.iloc[0], "kind": g.kind.iloc[0], "n": len(g), "upstream": int(g.upstream.iloc[0]),
                        "median_ecoli": g.ecoli.median(), "rho_model": spearmanr(g.risk_model, g.log_ecoli).statistic,
                        "rho_rain48": spearmanr(g.rain_48h, g.log_ecoli).statistic})
    per_df = pd.DataFrame(per).sort_values("rho_model", ascending=False)
    print(per_df.round(2).to_string(index=False))
    per_df.to_csv(config.PROCESSED / "ecoli_validation_per_site.csv", index=False)
    summary = {k: v.round(4).reset_index().to_dict("records") for k, v in results.items()}
    summary["n_samples"] = len(df); summary["n_sites"] = int(df.bw_id.nunique())
    summary["period"] = [str(df.sample_time.min().date()), str(df.sample_time.max().date())]
    (config.PROCESSED / "ecoli_validation.json").write_text(json.dumps(summary, indent=1, default=str))
    log.info("saved ecoli_validation.json / _rows.parquet / _per_site.csv")


if __name__ == "__main__":
    main()
