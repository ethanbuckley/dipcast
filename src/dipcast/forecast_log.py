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
    return con


def log_forecast(issued_at: pd.Timestamp, lat: float, lon: float, mode: str, watercourse: str | None,
                 now_risk: float, days: pd.DatetimeIndex, risk: np.ndarray,
                 ov: pd.DataFrame, p_raw: np.ndarray, p_cal: np.ndarray) -> None:
    """Append one forecast. `days[0]` is yesterday (lead -1); only leads >= 0 are logged."""
    issue_day = issued_at.tz_convert(LOCAL_TZ).date()
    pts, rows = [], []
    for j, d in enumerate(days):
        lead = (d.date() - issue_day).days
        if lead < 0:
            continue
        pts.append((issued_at, lat, lon, mode, watercourse, float(now_risk), d.date(), lead, float(risk[j])))
        if len(ov):
            for i, sid in enumerate(ov["site_id"].to_numpy()):
                rows.append((issued_at, issue_day, lat, lon, str(sid), bool(ov["has_live"].iloc[i]),
                             d.date(), lead, float(p_raw[i, j]), float(p_cal[i, j]), float(ov["weight"].iloc[i])))
    with _LOCK:
        con = _conn()
        try:
            con.executemany("INSERT INTO forecast_points VALUES (?,?,?,?,?,?,?,?,?)", pts)
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
        _write(out)
        return out
    hist = pd.read_parquet(hist_path)
    # Only score days on which we were actually polling, so a gap in polling does
    # not read as 'no spill'.
    polled_days = set(pd.to_datetime(hist["fetched_at"], utc=True).dt.tz_convert(LOCAL_TZ).dt.date.unique())
    fc = fc[fc["target_day"].isin(polled_days)]
    if fc.empty:
        _write(out)
        return out
    obs = observed_spill_days(hist)
    obs["y"] = 1
    fc = fc.merge(obs, left_on=["site_id", "target_day"], right_on=["site_id", "day"], how="left")
    fc["y"] = fc["y"].fillna(0).astype(int)
    y = fc["y"].to_numpy()
    out["n_scored"] = len(fc)
    out["overall"] = {"raw": scores(y, fc["p_raw"].to_numpy()), "calibrated": scores(y, fc["p_cal"].to_numpy())}
    clim = np.full(len(y), y.mean()) if len(y) else np.array([])
    out["overall"]["climatology_brier"] = float(np.mean((clim - y) ** 2)) if len(y) else None
    by_lead = []
    for k, g in fc.groupby("lead"):
        yy = g["y"].to_numpy()
        by_lead.append({"lead": int(k), "n": len(g), "base_rate": float(yy.mean()),
                        "brier_raw": float(np.mean((g["p_raw"].to_numpy() - yy) ** 2)),
                        "brier_cal": float(np.mean((g["p_cal"].to_numpy() - yy) ** 2))})
    out["by_lead"] = by_lead
    fc["week"] = pd.to_datetime(fc["target_day"]).dt.to_period("W").dt.start_time.dt.date.astype(str)
    out["by_week"] = [{"week": w, "n": len(g), "base_rate": float(g["y"].mean()),
                       "brier_cal": float(np.mean((g["p_cal"].to_numpy() - g["y"].to_numpy()) ** 2))}
                      for w, g in fc.groupby("week")]
    if len(fc) >= 200:
        out["reliability"] = reliability_table(y, fc["p_cal"].to_numpy()).round(4).to_dict("records")
    _write(out)
    return out


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
    for key, name in [("ecoli", "ecoli_validation.json"), ("ecoli_combined", "ecoli_validation_combined.json")]:
        q = config.PROCESSED / name
        res[key] = json.loads(q.read_text()) if q.exists() else None
    return res
