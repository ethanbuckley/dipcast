"""Log every forecast and score it later against what the overflows actually did.

Two DuckDB tables in the state directory:

* forecast_overflows: one row per (issue, overflow, target day, lead) with the
  raw and calibrated spill probability. These are verifiable: the live feeds
  tell us afterwards whether the overflow discharged that day.
* forecast_points: one row per (issue, target day) with the point risk shown to
  the user. Not directly verifiable without water-quality samples; kept for
  the record and for the E. coli study.

`verify_live()` scores overflow-day forecasts whose target day has passed,
using the accumulated live history, and writes verification_live.json.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from dipcast import config
from dipcast.model.verify import reliability_table, scores

log = logging.getLogger(__name__)
_LOCK = threading.Lock()
LOCAL_TZ = "Europe/London"


def _conn() -> duckdb.DuckDBPyConnection:
    config.STATE.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.DUCKDB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS forecast_overflows (
            issued_at TIMESTAMPTZ, issue_day DATE, lat DOUBLE, lon DOUBLE,
            site_id VARCHAR, has_live BOOLEAN, target_day DATE, lead INTEGER,
            p_raw DOUBLE, p_cal DOUBLE, weight DOUBLE)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS forecast_points (
            issued_at TIMESTAMPTZ, lat DOUBLE, lon DOUBLE, mode VARCHAR, watercourse VARCHAR,
            now_risk DOUBLE, target_day DATE, lead INTEGER, risk DOUBLE)""")
    # Added 15 Sep 2026: the E. coli exceedance forecast and the rain it used.
    con.execute("ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS p_ecoli DOUBLE")
    con.execute("ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS rain_48h DOUBLE")
    return con


def log_forecast(issued_at: pd.Timestamp, lat: float, lon: float, mode: str, watercourse: str | None,
                 now_risk: float, days: pd.DatetimeIndex, risk: np.ndarray,
                 ov: pd.DataFrame, p_raw: np.ndarray, p_cal: np.ndarray,
                 p_ecoli: np.ndarray | None = None, rain_48h: np.ndarray | None = None) -> None:
    """Append one forecast. `days[0]` is yesterday (lead -1); only leads >= 0 are logged.
    `p_ecoli` and `rain_48h` are aligned with `days` (NaN where unavailable)."""
    issue_day = issued_at.tz_convert(LOCAL_TZ).date()
    pts, rows = [], []

    def _f(arr, j):
        if arr is None or j >= len(arr) or np.isnan(arr[j]):
            return None
        return float(arr[j])

    for j, d in enumerate(days):
        lead = (d.date() - issue_day).days
        if lead < 0:
            continue
        pts.append((issued_at, lat, lon, mode, watercourse, float(now_risk), d.date(), lead, float(risk[j]),
                    _f(p_ecoli, j), _f(rain_48h, j)))
        if len(ov):
            for i, sid in enumerate(ov["site_id"].to_numpy()):
                rows.append((issued_at, issue_day, lat, lon, str(sid), bool(ov["has_live"].iloc[i]),
                             d.date(), lead, float(p_raw[i, j]), float(p_cal[i, j]), float(ov["weight"].iloc[i])))
    with _LOCK:
        con = _conn()
        try:
            con.executemany("INSERT INTO forecast_points VALUES (?,?,?,?,?,?,?,?,?,?,?)", pts)
            if rows:
                con.executemany("INSERT INTO forecast_overflows VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        finally:
            con.close()


def observed_spill_days(history: pd.DataFrame) -> pd.DataFrame:
    """(site_id, day) pairs with a recorded discharge, from accumulated live polls.
    An overflow still discharging at poll time is open-ended, so its end is the poll time."""
    h = history.copy()
    start = pd.to_datetime(h["latest_event_start"], utc=True)
    end = pd.to_datetime(h["latest_event_end"], utc=True)
    fetched = pd.to_datetime(h["fetched_at"], utc=True)
    end = end.where(~((h["status"] == 1) & end.isna()), fetched)
    end = end.fillna(start)
    ok = start.notna()
    ev = pd.DataFrame({"site_id": h.loc[ok, "site_id"].to_numpy(),
                       "event_start": start[ok].dt.tz_convert(LOCAL_TZ).to_numpy(),
                       "event_end": end[ok].dt.tz_convert(LOCAL_TZ).to_numpy()})
    ev["event_start"] = pd.to_datetime(ev["event_start"]).dt.tz_localize(None) if ev["event_start"].dt.tz is None else ev["event_start"].dt.tz_localize(None)
    ev["event_end"] = pd.to_datetime(ev["event_end"]).dt.tz_localize(None) if ev["event_end"].dt.tz is None else ev["event_end"].dt.tz_localize(None)
    d0 = ev["event_start"].dt.floor("D")
    d1 = ev["event_end"].dt.floor("D")
    n = ((d1 - d0).dt.days.clip(lower=0, upper=60) + 1).to_numpy()
    site = np.repeat(ev["site_id"].to_numpy(), n)
    base = np.repeat(d0.to_numpy().astype("datetime64[D]"), n)
    offs = np.concatenate([np.arange(k) for k in n]) if len(n) else np.array([], dtype=int)
    out = pd.DataFrame({"site_id": site, "day": base + offs.astype("timedelta64[D]")})
    out["day"] = pd.to_datetime(out["day"]).dt.date
    return out.drop_duplicates()


def verify_live(as_of: date | None = None) -> dict:
    """Score logged overflow-day forecasts whose target day is complete."""
    as_of = as_of or pd.Timestamp.now(tz=LOCAL_TZ).date()
    cutoff = as_of - timedelta(days=1)
    with _LOCK:
        con = _conn()
        try:
            fc = con.execute("""
                SELECT site_id, issue_day, target_day, lead, p_raw, p_cal
                FROM forecast_overflows WHERE has_live AND target_day <= ?
                QUALIFY row_number() OVER (PARTITION BY site_id, issue_day, target_day ORDER BY issued_at DESC) = 1
            """, [cutoff]).df()
            first_issue = con.execute("SELECT min(issue_day) FROM forecast_overflows").fetchone()[0]
            n_points = con.execute("SELECT count(*) FROM forecast_points").fetchone()[0]
        finally:
            con.close()
    out = {"generated_at": pd.Timestamp.now(tz=LOCAL_TZ).isoformat(), "first_forecast_day": str(first_issue) if first_issue else None,
           "n_point_forecasts": int(n_points), "n_scored": 0}
    hist_path = config.state_read("live_history.parquet")
    if fc.empty or not hist_path.exists():
        return _finish(out, as_of)
    hist = pd.read_parquet(hist_path)
    # DuckDB hands DATE columns back as datetime64; the polled-day filter and the
    # spill-day join below compare against Python dates, so normalise first.
    # (Without this nothing ever scored: the filter silently emptied the frame.)
    for c in ("target_day", "issue_day"):
        fc[c] = pd.to_datetime(fc[c]).dt.date
    # Only score days on which we were actually polling, so a gap in polling does
    # not read as 'no spill'.
    polled_days = set(pd.to_datetime(hist["fetched_at"], utc=True).dt.tz_convert(LOCAL_TZ).dt.date.unique())
    fc = fc[fc["target_day"].isin(polled_days)]
    if fc.empty:
        return _finish(out, as_of)
    obs = observed_spill_days(hist)
    obs["y"] = 1
    fc = fc.merge(obs, left_on=["site_id", "target_day"], right_on=["site_id", "day"], how="left")
    fc["y"] = fc["y"].fillna(0).astype(int)
    y = fc["y"].to_numpy()
    out["n_scored"] = len(fc)
    # Baseline: each overflow's long-run spill-day rate from the annual returns, as in
    # the offline tests. The in-period mean would be an in-sample, hindsight baseline
    # and over a few dry days it makes any forecast look bad.
    fc["p_clim"] = _site_climatology(fc["site_id"])
    out["overall"] = {"raw": scores(y, fc["p_raw"].to_numpy()), "calibrated": scores(y, fc["p_cal"].to_numpy())}
    out["overall"]["climatology_brier"] = float(np.mean((fc["p_clim"].to_numpy() - y) ** 2))
    out["overall"]["period_base_rate_brier"] = float(np.mean((y.mean() - y) ** 2))
    by_lead = []
    for k, g in fc.groupby("lead"):
        yy = g["y"].to_numpy()
        by_lead.append({"lead": int(k), "n": len(g), "base_rate": float(yy.mean()),
                        "brier_raw": float(np.mean((g["p_raw"].to_numpy() - yy) ** 2)),
                        "brier_cal": float(np.mean((g["p_cal"].to_numpy() - yy) ** 2))})
    out["by_lead"] = by_lead
    # By water company: the spill model was trained on United Utilities only, so this is
    # the geographic-transfer check. Companies with too few scored days are pooled as "other".
    comp = _site_companies()
    if comp is not None:
        fc["company"] = fc["site_id"].map(comp).fillna("unknown")
        by_company = []
        for c, g in fc.groupby("company"):
            yy = g["y"].to_numpy(); pc = g["p_cal"].to_numpy()
            cb = float(np.mean((g["p_clim"].to_numpy() - yy) ** 2))
            by_company.append({"company": c, "n": len(g), "sites": int(g["site_id"].nunique()), "base_rate": float(yy.mean()),
                               "brier_cal": float(np.mean((pc - yy) ** 2)), "climatology_brier": cb,
                               "skill": float(1 - np.mean((pc - yy) ** 2) / cb) if cb > 0 else None,
                               "auc": float(roc_auc_score(yy, pc)) if 0 < yy.mean() < 1 and len(g) >= 30 else None})
        out["by_company"] = sorted(by_company, key=lambda r: -r["n"])
    fc["week"] = pd.to_datetime(fc["target_day"]).dt.to_period("W").dt.start_time.dt.date.astype(str)
    out["by_week"] = [{"week": w, "n": len(g), "base_rate": float(g["y"].mean()),
                       "brier_cal": float(np.mean((g["p_cal"].to_numpy() - g["y"].to_numpy()) ** 2))}
                      for w, g in fc.groupby("week")]
    if len(fc) >= 200:
        out["reliability"] = reliability_table(y, fc["p_cal"].to_numpy()).round(4).to_dict("records")
    return _finish(out, as_of)


def _finish(out: dict, as_of: date) -> dict:
    """Attach the E. coli scores (independent of the spill scores) and write the file."""
    try:
        out["ecoli_live"] = verify_ecoli(as_of)
    except Exception as e:  # noqa: BLE001 - an optional layer must not block the spill scores
        log.warning("E. coli live scoring failed: %s", e)
    _write(out)
    return out


# --- E. coli: score the map's exceedance forecast against new EA lab samples ----------------

ECOLI_SAMPLES = "ecoli_samples.parquet"
ECOLI_REFRESH_H = 24


def _bathing_sites() -> pd.DataFrame:
    p = config.RAW / "bathing_waters_inland.json"
    if not p.exists():
        return pd.DataFrame(columns=["bw_id", "name", "kind", "lat", "lon"])
    d = pd.DataFrame(json.loads(p.read_text()))
    return d.rename(columns={"id": "bw_id"})[["bw_id", "name", "kind", "lat", "lon"]]


def refresh_ecoli_samples(force: bool = False) -> pd.DataFrame:
    """This season's EA samples at the inland bathing waters, refreshed at most once a day
    (38 small requests). Kept in the state directory alongside the live polls."""
    from dipcast.ingest.bwq import fetch_point
    p = config.state_read(ECOLI_SAMPLES)
    if p.exists() and not force:
        age_h = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC")).total_seconds() / 3600
        if age_h < ECOLI_REFRESH_H:
            return pd.read_parquet(p)
    sites = _bathing_sites()
    since = f"{pd.Timestamp.now(tz=LOCAL_TZ).year}-05-01T00:00:00"
    frames = []
    for s in sites.itertuples(index=False):
        try:
            rows = fetch_point(s.bw_id.split("-")[-1], since)
        except Exception as e:  # noqa: BLE001 - one site's outage must not lose the rest
            log.warning("EA samples %s: %s", s.name, e)
            continue
        if rows:
            frames.append(pd.DataFrame(rows).assign(bw_id=s.bw_id, name=s.name, kind=s.kind))
    if not frames:
        return pd.read_parquet(p) if p.exists() else pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["sample_time"] = pd.to_datetime(df["sample_time"]).dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="shift_forward")
    df["ecoli"] = pd.to_numeric(df["ecoli"], errors="coerce")
    df = df.dropna(subset=["sample_time", "ecoli"])
    df.to_parquet(config.state_write(ECOLI_SAMPLES), index=False)
    log.info("EA samples refreshed: %d this season at %d sites", len(df), df.bw_id.nunique())
    return df


def match_points_to_sites(points: pd.DataFrame, sites: pd.DataFrame) -> pd.Series:
    """bw_id for each logged point whose coordinates round to a bathing water's (4 dp ~ 10 m)."""
    key = sites.assign(k=sites["lat"].round(4).astype(str) + "," + sites["lon"].round(4).astype(str)).set_index("k")["bw_id"]
    pk = points["lat"].round(4).astype(str) + "," + points["lon"].round(4).astype(str)
    return pk.map(key)


def verify_ecoli(as_of: date | None = None) -> dict:
    """Score logged P(E. coli > 900) against EA samples taken on the target day."""
    as_of = as_of or pd.Timestamp.now(tz=LOCAL_TZ).date()
    with _LOCK:
        con = _conn()
        try:
            pts = con.execute("""
                SELECT issued_at, lat, lon, target_day, lead, p_ecoli, rain_48h, risk
                FROM forecast_points WHERE p_ecoli IS NOT NULL AND target_day <= ?
                QUALIFY row_number() OVER (PARTITION BY lat, lon, target_day, lead ORDER BY issued_at DESC) = 1
            """, [as_of]).df()
        finally:
            con.close()
    out = {"n_forecasts": len(pts), "n_scored": 0}
    if pts.empty:
        return out
    pts["target_day"] = pd.to_datetime(pts["target_day"]).dt.date
    sites = _bathing_sites()
    pts["bw_id"] = match_points_to_sites(pts, sites)
    pts = pts.dropna(subset=["bw_id"])
    samples = refresh_ecoli_samples()
    if pts.empty or samples.empty:
        return out
    samples = samples.assign(day=samples["sample_time"].dt.date)
    m = pts.merge(samples[["bw_id", "day", "sample_time", "ecoli", "name", "kind"]], left_on=["bw_id", "target_day"],
                  right_on=["bw_id", "day"], how="inner")
    if m.empty:
        return out
    m["y"] = (m["ecoli"] > 900).astype(int)
    out["n_scored"] = len(m); out["n_samples"] = int(m[["bw_id", "day"]].drop_duplicates().shape[0])
    out["n_sites"] = int(m["bw_id"].nunique())
    from dipcast.model.ecoli import load as load_ecoli
    em = load_ecoli()
    base = (em.meta.get("base_rate_by_type") if em else None) or {}
    m["p_clim"] = m["kind"].map(base).astype(float).fillna(float(m["y"].mean()))
    y, p = m["y"].to_numpy(), m["p_ecoli"].to_numpy()
    out["overall"] = {"brier": float(np.mean((p - y) ** 2)), "base_rate": float(y.mean()),
                      "climatology_brier": float(np.mean((m["p_clim"].to_numpy() - y) ** 2)),
                      "auc": float(roc_auc_score(y, p)) if 0 < y.mean() < 1 and len(m) >= 30 else None}
    out["by_lead"] = [{"lead": int(k), "n": len(g), "base_rate": float(g["y"].mean()),
                       "brier": float(np.mean((g["p_ecoli"].to_numpy() - g["y"].to_numpy()) ** 2))}
                      for k, g in m.groupby("lead")]
    recent = (m[m["lead"].isin([0, 1])].sort_values(["sample_time", "lead"]).groupby(["bw_id", "day"]).first()
              .reset_index().sort_values("sample_time", ascending=False).head(30))
    out["recent"] = [{"site": r["name"], "kind": r["kind"], "day": str(r["day"]), "lead": int(r["lead"]),
                      "forecast": round(float(r["p_ecoli"]), 3), "rain_48h": None if pd.isna(r["rain_48h"]) else round(float(r["rain_48h"]), 1),
                      "ecoli": int(r["ecoli"])} for _, r in recent.iterrows()]
    return out


def _site_climatology(site_ids: pd.Series) -> np.ndarray:
    """Long-run daily spill probability per overflow from the annual returns
    (spill-days per year / 365), the same baseline as the offline tests."""
    p = config.state_read("overflows.parquet")
    default = 20.0 / 365.0
    if not p.exists():
        return np.full(len(site_ids), default)
    ov = pd.read_parquet(p, columns=["site_id", "lta_spills"]).drop_duplicates("site_id").set_index("site_id")["lta_spills"]
    clim = site_ids.map(ov).astype(float).fillna(default * 365.0).to_numpy() / 365.0
    return np.clip(clim, 0.001, 0.95)


def _site_companies() -> pd.Series | None:
    p = config.state_read("overflows.parquet")
    if not p.exists():
        return None
    ov = pd.read_parquet(p, columns=["site_id", "company"]).drop_duplicates("site_id")
    return ov.set_index("site_id")["company"]


def _write(out: dict) -> None:
    config.state_write("verification_live.json").write_text(json.dumps(out, indent=1, default=str))


def load_verification() -> dict:
    """Everything the public verification page needs: offline tables plus live scores."""
    res: dict = {"live": None, "holdout": None, "leads": None, "reliability": None, "lead_calibration": None}
    p = config.state_read("verification_live.json")
    if p.exists():
        res["live"] = json.loads(p.read_text())
    for key, name in [("holdout", "verification_2025.csv"), ("leads", "verification_leads_2025.csv"),
                      ("reliability", "reliability_2025.csv")]:
        q = config.PROCESSED / name
        if q.exists():
            res[key] = pd.read_csv(q).round(4).to_dict("records")
    q = config.PROCESSED / "lead_calibration.json"
    if q.exists():
        res["lead_calibration"] = json.loads(q.read_text())
    for key, name in [("ecoli", "ecoli_validation.json"), ("ecoli_combined", "ecoli_validation_combined.json"),
                      ("ecoli_model", "ecoli_model_eval.json"), ("sampling_plan", "sampling_plan_test.json")]:
        q = config.PROCESSED / name
        res[key] = json.loads(q.read_text()) if q.exists() else None
    return res
