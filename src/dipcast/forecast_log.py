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

Scoring rules (16 Sep 2026):

* Decision time. For each (overflow, issue day, target day) the forecast scored is
  the latest one issued by DECISION_HOUR local time on the issue day: what a
  swimmer planning the day would have seen. Issue days with no forecast by the
  cutoff are a missed deadline: counted and reported, not scored.
* Coverage. An overflow-day scores as "no spill" only if that overflow was
  observed with a known status (discharging or not) at least MIN_KNOWN_POLLS times
  that day, with no gap between observations (or between midnight and the first,
  or the last and midnight) longer than MAX_GAP_H, from a feed that looked current,
  and at least once the next day (live_coverage.parquet, written by
  ingest.live.save_live). A poll gap, an offline monitor, a stale or dead company
  feed is a missing observation, not a dry day. A feed that stamps no record
  (South West Water's: no LastUpdated, no event times) is never current, so its
  overflows are not scored. Days from before the observation masks existed
  (observations_from) are not scored and are left out of the scoring window.
  Scores under a stricter gap rule are reported alongside.
* Spills. An overflow-day is a spill if a poll saw an event that touched that day
  (latest event start to end, or to the poll time while still discharging), or saw
  the overflow discharging with no event times, which counts on the local day of
  that poll.
* Versions. Every logged forecast carries a compact version stamp (spill model,
  calibration map, E. coli model, code, weather source); scores are broken down
  by stamp so successive changes are not mixed in one table.
* Rain. Overflow-days forecast without rainfall data (p logged as 0 with
  rain_available = false) are excluded.
* E. coli. A point forecast is compared with a sample only if it was issued
  before the sample was taken; lead 0 ("same day") is reported separately from
  leads 1-4 ("in advance").
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
DECISION_HOUR = 8         # local time: forecasts available by then count for that issue day
MIN_KNOWN_POLLS = 6       # known-status polls on the target day needed to score a non-event
# The longest unobserved stretch allowed on a scored day. The rule was written for a poller
# running every 30 minutes, but GitHub starts the schedule when it can: on 29 and 30 Sep 2026 it
# ran 4 and 5 times, with daytime gaps of 6.4-6.8 h, so at 6 h no overflow-day could pass and
# nothing was scored (0 of 80,753 candidates on 1 Oct). Over 17-30 Sep the longest gap was
# under 6 h on 10 days of 14 and under 8 h on all 14. At 8 h the rule still guards against a
# second event starting and ending unseen inside one stretch; the next-day rule catches a single
# late one. The stricter rule reported alongside is the old 6 h, so the change itself is visible.
MAX_GAP_H = 8.0
STRICT_GAP_H = 6.0        # the stricter rule reported alongside, for sensitivity
COVERAGE_FILE = "live_coverage.parquet"
SLOTS_PER_DAY = 48
RAIN_BANDS = [0.0, 1.0, 5.0, 10.0, np.inf]   # target-day rain at the overflow's cell, mm
RAIN_BAND_LABELS = ["under 1 mm", "1-5 mm", "5-10 mm", "10 mm or more"]


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
    # Added 16 Sep 2026: whether the overflow's cell had rainfall data for the day.
    con.execute("ALTER TABLE forecast_overflows ADD COLUMN IF NOT EXISTS rain_available BOOLEAN DEFAULT TRUE")
    # Added 17 Sep 2026: which models and code produced the forecast.
    con.execute("ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS version VARCHAR")
    con.execute("ALTER TABLE forecast_overflows ADD COLUMN IF NOT EXISTS version VARCHAR")
    # Added 28 Sep 2026: the target day's rain (mm) at the overflow's cell that the forecast used.
    con.execute("ALTER TABLE forecast_overflows ADD COLUMN IF NOT EXISTS rain_mm DOUBLE")
    return con


def log_forecast(issued_at: pd.Timestamp, lat: float, lon: float, mode: str, watercourse: str | None,
                 now_risk: float, days: pd.DatetimeIndex, risk: np.ndarray,
                 ov: pd.DataFrame, p_raw: np.ndarray, p_cal: np.ndarray,
                 p_ecoli: np.ndarray | None = None, rain_48h: np.ndarray | None = None,
                 available: np.ndarray | None = None, version: str | None = None,
                 rain_mm: np.ndarray | None = None) -> None:
    """Append one forecast. `days` may start before today (history for travel time);
    only leads >= 0 are logged. `p_ecoli` and `rain_48h` are aligned with `days` (NaN
    where unavailable); `available` is the (n_overflows, n_days) rain-data mask and
    `rain_mm` the day's rain at each overflow's cell, same shape."""
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
        pts.append((issued_at, lat, lon, mode, watercourse, float(now_risk), d.date(), lead,
                    None if np.isnan(risk[j]) else float(risk[j]), _f(p_ecoli, j), _f(rain_48h, j), version))
        if len(ov):
            for i, sid in enumerate(ov["site_id"].to_numpy()):
                ok = True if available is None else bool(available[i, j])
                rain = None if rain_mm is None or np.isnan(rain_mm[i, j]) else float(rain_mm[i, j])
                rows.append((issued_at, issue_day, lat, lon, str(sid), bool(ov["has_live"].iloc[i]),
                             d.date(), lead, float(p_raw[i, j]), float(p_cal[i, j]), float(ov["weight"].iloc[i]), ok, version, rain))
    with _LOCK:
        con = _conn()
        try:
            con.executemany("INSERT INTO forecast_points VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", pts)
            if rows:
                con.executemany("INSERT INTO forecast_overflows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        finally:
            con.close()


def observed_spill_days(history: pd.DataFrame) -> pd.DataFrame:
    """(site_id, day) pairs with a recorded discharge, from accumulated live polls.
    An overflow still discharging at poll time is open-ended, so its end is the poll time.
    An overflow discharging at poll time with no event start (South West Water's feed
    publishes none) discharged at least at that moment: a spill on the poll's local day."""
    h = history.copy()
    start = pd.to_datetime(h["latest_event_start"], utc=True)
    end = pd.to_datetime(h["latest_event_end"], utc=True)
    fetched = pd.to_datetime(h["fetched_at"], utc=True)
    discharging = h["status"] == 1
    start = start.where(~(discharging & start.isna()), fetched)
    end = end.where(~(discharging & end.isna()), fetched)
    end = end.fillna(start)
    ok = start.notna()
    if not ok.any():   # a fresh history with no recorded event at all
        return pd.DataFrame({"site_id": pd.Series(dtype=str), "day": pd.Series(dtype=object)})
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


def select_decision_forecasts(con: duckdb.DuckDBPyConnection, cutoff: date,
                              decision_hour: int = DECISION_HOUR) -> pd.DataFrame:
    """One row per (overflow, issue day, target day): the latest forecast issued by
    `decision_hour` local time on the issue day. Where none was issued by then the
    row carries the earliest issue of the day with `before_cutoff` False; those are
    missed deadlines, reported by the caller and excluded from the headline scores.
    Rows without rainfall data are dropped."""
    fc = con.execute(f"""
        WITH f AS (
            SELECT site_id, issue_day, target_day, lead, p_raw, p_cal, issued_at, version, rain_mm,
                   epoch_ms(issued_at) AS issued_ms,
                   (issued_at AT TIME ZONE '{LOCAL_TZ}') <= (issue_day::TIMESTAMP + INTERVAL {int(decision_hour)} HOUR) AS before_cutoff
            FROM forecast_overflows
            WHERE has_live AND target_day <= ? AND coalesce(rain_available, TRUE))
        SELECT site_id, issue_day, target_day, lead, p_raw, p_cal, issued_ms, before_cutoff, version, rain_mm FROM f
        QUALIFY row_number() OVER (PARTITION BY site_id, issue_day, target_day
            ORDER BY before_cutoff DESC, CASE WHEN before_cutoff THEN issued_at END DESC NULLS LAST, issued_at ASC) = 1
    """, [cutoff]).df()
    for c in ("target_day", "issue_day"):
        fc[c] = pd.to_datetime(fc[c]).dt.date
    fc["issued_at"] = pd.to_datetime(fc["issued_ms"], unit="ms", utc=True).dt.tz_convert(LOCAL_TZ)
    return fc.drop(columns=["issued_ms"])


def load_coverage() -> pd.DataFrame:
    """(site_id, day, n_known, n_unknown, n_stale, slots) from the live poller; empty if none yet."""
    p = config.state_read(COVERAGE_FILE)
    if not p.exists():
        return pd.DataFrame(columns=["site_id", "day", "n_known", "n_unknown", "n_stale", "slots"])
    cov = pd.read_parquet(p)
    cov["day"] = pd.to_datetime(cov["day"]).dt.date
    for c in ("n_stale", "slots"):
        if c not in cov:
            cov[c] = 0
    return cov


def slot_stats(slots: np.ndarray, slots_per_day: int = SLOTS_PER_DAY) -> pd.DataFrame:
    """From a bitmask of observed half-hour slots: number of distinct slots, first and
    last observed hour, and the longest gap in hours, counting the stretch from
    midnight to the first observation and from the last to midnight. A mask of 0 is
    a whole day unobserved (gap 24 h)."""
    slots = np.asarray(slots, dtype="int64")
    step = 24.0 / slots_per_day
    bits = ((slots[:, None] >> np.arange(slots_per_day)) & 1).astype(bool)   # (n, slots)
    n = bits.sum(axis=1)
    idx = np.arange(slots_per_day)
    first = np.where(n > 0, np.where(bits, idx, slots_per_day).min(axis=1), slots_per_day)
    last = np.where(n > 0, np.where(bits, idx, -1).max(axis=1), -1)
    gap = np.full(len(slots), 24.0)
    for i in range(len(slots)):
        if n[i] == 0:
            continue
        obs = idx[bits[i]]
        inner = np.diff(obs).max() * step if len(obs) > 1 else 0.0
        gap[i] = max(first[i] * step, (slots_per_day - 1 - last[i]) * step, inner)
    return pd.DataFrame({"n_slots": n, "first_h": np.where(n > 0, first * step, np.nan),
                         "last_h": np.where(n > 0, (last + 1) * step, np.nan), "max_gap_h": gap})


def observations_from(cov: pd.DataFrame) -> str | None:
    """The first day on which any overflow carries a half-hour observation mask. The coverage
    rule needs the mask, so no earlier day can be scored. Rows written before the masks
    existed read as 0. The mask code was written on 16 Sep 2026 but reached the live site on
    28 Sep, so the page must not imply scoring could have started earlier."""
    if cov.empty or "slots" not in cov:
        return None
    masked = cov.loc[cov["slots"].astype("int64") != 0, "day"]
    return str(masked.min()) if len(masked) else None


def coverage_checks(cov: pd.DataFrame, min_known: int = MIN_KNOWN_POLLS,
                    max_gap_h: float = MAX_GAP_H) -> pd.DataFrame:
    """Per observed (site_id, day): the stats and which of the coverage rules it
    passes (ok_gap, ok_polls, ok_next). covered_site_days keeps the rows passing all
    three; uncovered_reasons names the first rule each failing row breaks."""
    cols = ["site_id", "day", "n_known", "n_slots", "max_gap_h", "ok_gap", "ok_polls", "ok_next"]
    if cov.empty:
        return pd.DataFrame(columns=cols)
    c = cov.groupby(["site_id", "day"], as_index=False).agg(
        n_known=("n_known", "sum"), n_stale=("n_stale", "sum"),
        slots=("slots", lambda s: int(np.bitwise_or.reduce(s.to_numpy(dtype="int64")))))
    st = slot_stats(c["slots"].to_numpy())
    c = pd.concat([c, st], axis=1)
    nxt = c[["site_id", "day", "n_known"]].assign(day=pd.to_datetime(c["day"]) - pd.Timedelta(days=1))
    nxt["day"] = nxt["day"].dt.date
    nxt = nxt.rename(columns={"n_known": "n_known_next"})
    m = c.merge(nxt, on=["site_id", "day"], how="left")
    m["ok_gap"] = m["max_gap_h"] <= max_gap_h
    m["ok_polls"] = (m["n_known"] - m["n_stale"]) >= min_known
    m["ok_next"] = m["n_known_next"].fillna(0) >= 1
    return m[cols].reset_index(drop=True)


def covered_site_days(cov: pd.DataFrame, min_known: int = MIN_KNOWN_POLLS,
                      max_gap_h: float = MAX_GAP_H) -> pd.DataFrame:
    """(site_id, day) pairs whose observation supports a 'no spill' verdict: at
    least `min_known` known-status polls that day from a current feed, no
    unobserved stretch longer than `max_gap_h` (including the edges of the day),
    and one observation the day after (so an event that ended late is still visible
    in the feed's latest-event fields). Returns the stats too, for reporting."""
    m = coverage_checks(cov, min_known, max_gap_h)
    if m.empty:
        return m[["site_id", "day", "n_known", "n_slots", "max_gap_h"]]
    ok = m["ok_gap"] & m["ok_polls"] & m["ok_next"]
    return m.loc[ok, ["site_id", "day", "n_known", "n_slots", "max_gap_h"]].reset_index(drop=True)


POLL_LOG_FILE = "poll_log.parquet"
# Why an overflow-day was not scored, in the order the rules are checked; the first one
# a day breaks is the one it is counted under. They sum to n_uncovered.
UNCOVERED_REASONS = ["before_observations", "feed_not_current", "max_gap", "min_known_polls", "next_day_unobserved"]


def unstamped_feed_days(poll_log: pd.DataFrame | None) -> pd.DataFrame:
    """(company, day) pairs, local days, on which a company's feed returned rows but no
    poll carried a record stamp (feed_age_h NaN): nothing showed the feed was current.
    South West Water's feed has no LastUpdated and no event times, so its polls see only
    what is discharging at that moment. The coverage file marks such polls stale from
    2 Oct 2026; this applies the same rule to the days polled before then.

    Poll-log rows written before the log recorded feed ages (before 28 Sep 2026 16:51 UTC)
    have a NaN age for every company, which says nothing about the feed. Rows from before
    the first poll in which any company has an age are ignored, so only a company whose
    feed carried no stamp while ages were being recorded is flagged."""
    empty = pd.DataFrame({"company": pd.Series(dtype=str), "day": pd.Series(dtype=object)})
    if poll_log is None or poll_log.empty:
        return empty
    t = pd.to_datetime(poll_log["fetched_at"], utc=True)
    ages_from = t[poll_log["feed_age_h"].notna()].min()
    if pd.isna(ages_from):
        return empty
    pl = poll_log[(poll_log["n_rows"] > 0) & (t >= ages_from)].copy()
    if pl.empty:
        return empty
    pl["day"] = pd.to_datetime(pl["fetched_at"], utc=True).dt.tz_convert(LOCAL_TZ).dt.date
    stamped = pl.groupby(["company", "day"])["feed_age_h"].apply(lambda a: bool(a.notna().any())).reset_index(name="stamped")
    return stamped.loc[~stamped["stamped"], ["company", "day"]].reset_index(drop=True)


def load_poll_log() -> pd.DataFrame | None:
    p = config.state_read(POLL_LOG_FILE)
    return pd.read_parquet(p) if p.exists() else None


def uncovered_reasons(keys: pd.DataFrame, checks: pd.DataFrame, not_current: pd.DataFrame,
                      observations_from: str | None) -> pd.Series:
    """For each (site_id, day) in `keys`, the first coverage rule it breaks (one of
    UNCOVERED_REASONS), or None if it can be scored. `checks` is coverage_checks();
    `not_current` the (site_id, day) pairs whose feed was not current that day."""
    k = keys[["site_id", "day"]].reset_index(drop=True)
    m = k.merge(checks, on=["site_id", "day"], how="left")
    m = m.merge(not_current[["site_id", "day"]].drop_duplicates().assign(nc=True), on=["site_id", "day"], how="left")
    first = pd.Timestamp(observations_from).date() if observations_from else None
    before = m["day"].map(lambda d: first is None or d < first).astype(bool)

    def flag(c):   # a day with no coverage row was never observed: it fails the gap rule
        return m[c].astype("boolean").fillna(False).astype(bool)
    reason = np.select([before, flag("nc"), ~flag("ok_gap"), ~flag("ok_polls"), ~flag("ok_next")], UNCOVERED_REASONS, default="")
    return pd.Series(np.where(reason == "", None, reason), index=keys.index, dtype=object)


def _brier_block(g: pd.DataFrame) -> dict:
    yy = g["y"].to_numpy()
    return {"n": len(g), "base_rate": float(yy.mean()),
            "brier_cal": float(np.mean((g["p_cal"].to_numpy() - yy) ** 2)),
            "climatology_brier": float(np.mean((g["p_clim"].to_numpy() - yy) ** 2))}


def verify_live(as_of: date | None = None) -> dict:
    """Score logged overflow-day forecasts whose target day is complete."""
    as_of = as_of or pd.Timestamp.now(tz=LOCAL_TZ).date()
    cutoff = as_of - timedelta(days=1)
    with _LOCK:
        con = _conn()
        try:
            fc = select_decision_forecasts(con, cutoff)
            first_issue = con.execute("SELECT min(issue_day) FROM forecast_overflows").fetchone()[0]
            first_stamped = con.execute("SELECT min(issue_day) FROM forecast_overflows WHERE version IS NOT NULL").fetchone()[0]
            n_points = con.execute("SELECT count(*) FROM forecast_points").fetchone()[0]
        finally:
            con.close()
    out = {"generated_at": pd.Timestamp.now(tz=LOCAL_TZ).isoformat(), "first_forecast_day": str(first_issue) if first_issue else None,
           "n_point_forecasts": int(n_points), "n_scored": 0,
           "rules": {"decision_hour_local": DECISION_HOUR, "min_known_polls": MIN_KNOWN_POLLS, "max_gap_h": MAX_GAP_H,
                     "strict_gap_h": STRICT_GAP_H, "feed_current_h": 6.0,
                     "unstamped_feed": "a feed with no record stamp (South West Water's) is never current: its overflows are not scored",
                     "discharging_without_event_times": "a spill on the local day of the poll that saw it",
                     "baseline": "annual spill count / 365, an approximation (spill-days per counted spill = "
                                 f"{_spill_days_per_spill():.2f} pooled over United Utilities 2023-25; not checked per overflow or company)"},
           "units": {"n_point_forecasts": "rows in the spot forecast log: one per spot, issue and target day (not overflow-days)",
                     "n_candidates": "overflow-days: one monitored overflow on one past target day, with a forecast and rainfall data",
                     "n_scored": "overflow-days"}}
    hist_path = config.state_read("live_history.parquet")
    if fc.empty or not hist_path.exists():
        return _finish(out, as_of)
    hist = pd.read_parquet(hist_path)
    out["n_candidates"] = len(fc)
    # Missed deadlines: issue days with no forecast by the decision hour. Reported,
    # not scored: the headline is what was available at 08:00.
    missed = fc[~fc["before_cutoff"]]
    out["missed_deadline"] = {"n": len(missed), "issue_days": sorted(str(d) for d in missed["issue_day"].unique())[-14:],
                              "share_of_candidates": float(len(missed) / len(fc))}
    fc = fc[fc["before_cutoff"]]
    # Score only overflow-days whose observation supports a verdict either way.
    cov_all = load_coverage()
    obs_from = observations_from(cov_all)
    out["observations_from"] = obs_from
    # Feeds that stamp no record are never current (see unstamped_feed_days).
    site_company = hist.drop_duplicates("site_id", keep="last").set_index("site_id")["company"]
    sites = pd.DataFrame({"site_id": site_company.index.astype(str), "company": site_company.to_numpy()})
    not_current = sites.merge(unstamped_feed_days(load_poll_log()), on="company")[["site_id", "day"]]
    keys = fc[["site_id", "target_day"]].rename(columns={"target_day": "day"})
    fc["reason"] = uncovered_reasons(keys, coverage_checks(cov_all), not_current, obs_from).to_numpy()
    cov = covered_site_days(cov_all).merge(not_current.assign(nc=True), on=["site_id", "day"], how="left")
    cov = cov[cov["nc"].isna()].drop(columns="nc")
    out["coverage_from"] = str(cov["day"].min()) if len(cov) else None
    if len(cov):
        out["coverage_stats"] = {"covered_site_days": len(cov), "median_known_polls": float(cov["n_known"].median()),
                                 "median_max_gap_h": float(cov["max_gap_h"].median())}
    strict = covered_site_days(cov_all, max_gap_h=STRICT_GAP_H)[["site_id", "day"]].assign(strict=True)
    uncovered = fc[fc["reason"].notna()]
    fc = fc[fc["reason"].isna()].drop(columns="reason")
    fc = fc.merge(strict, left_on=["site_id", "target_day"], right_on=["site_id", "day"], how="left").drop(columns=["day"])
    fc["strict"] = fc["strict"].astype("boolean").fillna(False).astype(bool)
    out["n_uncovered"] = int(out["n_candidates"] - out["missed_deadline"]["n"] - len(fc))
    # n_uncovered by the first rule each overflow-day breaks; the parts sum to n_uncovered.
    out["uncovered_by_reason"] = {r: int((uncovered["reason"] == r).sum()) for r in UNCOVERED_REASONS}
    out["scoring_window"] = _scoring_window(obs_from, cutoff, missed, uncovered, fc)
    if fc.empty:
        return _finish(out, as_of)
    obs = observed_spill_days(hist)
    obs["y"] = 1
    fc = fc.merge(obs, left_on=["site_id", "target_day"], right_on=["site_id", "day"], how="left")
    fc["y"] = fc["y"].fillna(0).astype(int)
    y = fc["y"].to_numpy()
    out["n_scored"] = len(fc)
    # Baseline: each overflow's long-run daily spill rate from the annual returns, as in
    # the offline tests. The in-period mean would be an in-sample, hindsight baseline
    # and over a few dry days it makes any forecast look bad.
    fc["p_clim"] = _site_climatology(fc["site_id"])
    out["overall"] = {"raw": scores(y, fc["p_raw"].to_numpy()), "calibrated": scores(y, fc["p_cal"].to_numpy())}
    out["overall"]["climatology_brier"] = float(np.mean((fc["p_clim"].to_numpy() - y) ** 2))
    # A flat forecast at the period's own spill rate uses hindsight, so it is not a rival
    # forecast; a real forecast that loses to it is miscalibrated for the period.
    out["overall"]["period_base_rate_brier"] = float(np.mean((y.mean() - y) ** 2))
    out["overall"]["mean_forecast"] = float(fc["p_cal"].mean())
    by_lead = []
    for k, g in fc.groupby("lead"):
        yy = g["y"].to_numpy()
        by_lead.append({"lead": int(k), "n": len(g), "base_rate": float(yy.mean()),
                        "brier_raw": float(np.mean((g["p_raw"].to_numpy() - yy) ** 2)),
                        "brier_cal": float(np.mean((g["p_cal"].to_numpy() - yy) ** 2)),
                        "climatology_brier": float(np.mean((g["p_clim"].to_numpy() - yy) ** 2)),
                        "same_day": bool(k == 0)})
    out["by_lead"] = by_lead
    adv = fc[fc["lead"] >= 1]
    if len(adv):
        ya = adv["y"].to_numpy()
        out["in_advance"] = {"n": len(adv), "brier_cal": float(np.mean((adv["p_cal"].to_numpy() - ya) ** 2)),
                             "climatology_brier": float(np.mean((adv["p_clim"].to_numpy() - ya) ** 2)),
                             "auc": float(roc_auc_score(ya, adv["p_cal"])) if 0 < ya.mean() < 1 else None}
    # Sensitivity to the coverage rule: the same scores on days that also pass the stricter gap rule.
    st = fc[fc["strict"]]
    out["coverage_sensitivity"] = {"default": {"max_gap_h": MAX_GAP_H, **_brier_block(fc)},
                                   "strict": {"max_gap_h": STRICT_GAP_H, **_brier_block(st)} if len(st) else None}
    # By version stamp, so a model or code change is not averaged into the old table.
    fc["version"] = fc["version"].fillna(unstamped_label(first_stamped))
    out["by_version"] = [{"version": v, **_brier_block(g), "first_day": str(g["issue_day"].min()), "last_day": str(g["issue_day"].max())}
                         for v, g in fc.groupby("version")]
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
                               "n_spill_days": int(yy.sum()), "mean_forecast": float(pc.mean()),
                               "brier_cal": float(np.mean((pc - yy) ** 2)), "climatology_brier": cb,
                               "flat_brier": float(np.mean((yy.mean() - yy) ** 2)),
                               "skill": float(1 - np.mean((pc - yy) ** 2) / cb) if cb > 0 else None,
                               "auc": float(roc_auc_score(yy, pc)) if 0 < yy.mean() < 1 and len(g) >= 30 else None})
        out["by_company"] = sorted(by_company, key=lambda r: -r["n"])
    fc["week"] = pd.to_datetime(fc["target_day"]).dt.to_period("W").dt.start_time.dt.date.astype(str)
    out["by_week"] = [{"week": w, "n": len(g), "base_rate": float(g["y"].mean()),
                       "brier_cal": float(np.mean((g["p_cal"].to_numpy() - g["y"].to_numpy()) ** 2))}
                      for w, g in fc.groupby("week")]
    # By the rain the forecast assumed for the target day (logged from 28 Sep 2026). If the
    # excess sits on forecast-dry days the model's floor is too high; if on wet ones, the
    # rain forecast or the model's rain response is.
    r = fc.dropna(subset=["rain_mm"]) if "rain_mm" in fc else fc.iloc[0:0]
    out["n_rain_unlogged"] = int(len(fc) - len(r))
    if len(r):
        band = pd.cut(r["rain_mm"], RAIN_BANDS, right=False, labels=RAIN_BAND_LABELS)
        out["by_rain"] = [{"band": str(b), "n": len(g), "base_rate": float(g["y"].mean()), "mean_forecast": float(g["p_cal"].mean()),
                           "brier_cal": float(np.mean((g["p_cal"].to_numpy() - g["y"].to_numpy()) ** 2))}
                          for b, g in r.groupby(band, observed=True)]
    if len(fc) >= 200:
        out["reliability"] = reliability_table(y, fc["p_cal"].to_numpy()).round(4).to_dict("records")
    return _finish(out, as_of, fc)


def unstamped_label(first_stamped) -> str:
    """The version label for forecasts logged without a stamp, from the first stamped issue
    day in the log. Stamping was written on 17 Sep 2026 but reached the live site on 28 Sep."""
    return f"unstamped (stamps begin {first_stamped})" if first_stamped else "unstamped"


def _scoring_window(obs_from: str | None, cutoff: date, missed: pd.DataFrame, uncovered: pd.DataFrame,
                    scored: pd.DataFrame) -> dict:
    """The overflow-days that could be scored: target days from the first day with observation
    masks to yesterday. Days before it cannot be scored under the coverage rule, so they are
    counted apart (n_before_window) and left out of n_candidates here, which is
    n_missed_deadline + n_uncovered + n_scored."""
    first = pd.Timestamp(obs_from).date() if obs_from else None
    m_in = int(sum(first is not None and d >= first for d in missed["target_day"]))
    u_in = uncovered[uncovered["reason"] != "before_observations"]
    n_before = int(len(missed) - m_in + (uncovered["reason"] == "before_observations").sum())
    return {"unit": "overflow-day", "from_day": obs_from, "to_day": str(cutoff),
            "n_candidates": m_in + len(u_in) + len(scored), "n_missed_deadline": m_in, "n_uncovered": len(u_in),
            "uncovered_by_reason": {r: int((u_in["reason"] == r).sum()) for r in UNCOVERED_REASONS[1:]},
            "n_scored": len(scored), "n_before_window": n_before,
            "first_scored_day": str(scored["target_day"].min()) if len(scored) else None,
            "last_scored_day": str(scored["target_day"].max()) if len(scored) else None}


def _finish(out: dict, as_of: date, scored: pd.DataFrame | None = None) -> dict:
    """Attach the E. coli scores (independent of the spill scores) and write the file, after the
    scored rows (SCORED_CSV), so the two always come from the same run: a failed CSV write leaves
    the previous pair in place."""
    try:
        out["ecoli_live"] = verify_ecoli(as_of)
    except Exception as e:  # noqa: BLE001 - an optional layer must not block the spill scores
        log.warning("E. coli live scoring failed: %s", e)
    rows = scored_rows(scored)
    rows.to_csv(config.state_write(SCORED_CSV), index=False, float_format="%.4f")
    out["scored_csv"] = {"file": SCORED_CSV, "rows": len(rows), "columns": SCORED_COLUMNS}
    _write(out)
    return out


# One row per scored overflow-day, so anyone can recompute the live scores. The build publishes it
# as data/verification_live.csv with the credits in a header comment (build_site.publish_scored_csv).
SCORED_CSV = "verification_live.csv"
SCORED_COLUMNS = ["overflow_id", "company", "day", "lead", "issued_at", "forecast_raw", "forecast_calibrated",
                  "climatology", "observed"]


def scored_rows(fc: pd.DataFrame | None) -> pd.DataFrame:
    """The scored overflow-days as published: overflow id, its company, the target day, the lead,
    when the scored forecast was issued, the spill probability before and after lead calibration,
    the climatology baseline and what happened (1 a spill, 0 none). Sorted, so a rerun on the same
    log writes the same file. Empty (header only) when nothing was scored."""
    if fc is None or fc.empty:
        return pd.DataFrame(columns=SCORED_COLUMNS)
    comp = _site_companies()
    out = pd.DataFrame({
        "overflow_id": fc["site_id"].astype(str).to_numpy(),
        "company": fc["site_id"].map(comp).to_numpy() if comp is not None else None,
        "day": pd.to_datetime(fc["target_day"]).dt.strftime("%Y-%m-%d").to_numpy(),
        "lead": fc["lead"].astype(int).to_numpy(),
        "issued_at": pd.to_datetime(fc["issued_at"]).dt.strftime("%Y-%m-%dT%H:%M%:z").to_numpy(),
        "forecast_raw": fc["p_raw"].astype(float).to_numpy(),
        "forecast_calibrated": fc["p_cal"].astype(float).to_numpy(),
        "climatology": fc["p_clim"].astype(float).to_numpy(),
        "observed": fc["y"].astype(int).to_numpy()})
    return out.sort_values(["day", "overflow_id", "lead"], kind="stable").reset_index(drop=True)


# --- E. coli: score the map's exceedance forecast against new EA lab samples ----------------

ECOLI_SAMPLES = "ecoli_samples.parquet"
ECOLI_STATUS = "ecoli_samples_status.json"   # the last fetch attempt, for build health and the page
ECOLI_REFRESH_H = 24


def _bathing_sites() -> pd.DataFrame:
    cols = ["bw_id", "name", "kind", "lat", "lon", "wqa_point"]
    p = config.RAW / "bathing_waters_inland.json"
    if not p.exists():
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame(json.loads(p.read_text()))
    return d.rename(columns={"id": "bw_id"}).reindex(columns=cols)


def _fetch_error(e: Exception) -> str:
    """A short, countable label for a failed request: 'HTTP 403', 'ConnectTimeout'."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    return f"HTTP {code}" if code else type(e).__name__


def _local_times(s: pd.Series) -> pd.Series:
    """Sample times as published (local, no offset) to Europe/London; converted if an offset is given."""
    t = pd.to_datetime(s)
    return t.dt.tz_convert(LOCAL_TZ) if t.dt.tz is not None else t.dt.tz_localize(
        LOCAL_TZ, ambiguous="NaT", nonexistent="shift_forward")


def _from_bathing_water_service(sites: pd.DataFrame, since: str) -> tuple[pd.DataFrame, set, dict]:
    """One request per site: (samples, sites that answered, errors by kind). Stops at the
    first HTTP 403, which is the service's gateway refusing this machine (GitHub's runners
    since 28 Sep 2026); the other sites would be refused too."""
    from dipcast.ingest.bwq import fetch_point
    frames, answered, errors = [], set(), {}
    for s in sites.itertuples(index=False):
        try:
            rows = fetch_point(s.bw_id.split("-")[-1], since)
        except Exception as e:  # noqa: BLE001 - one site's outage must not lose the rest
            k = _fetch_error(e)
            errors[k] = errors.get(k, 0) + 1
            if k == "HTTP 403":
                log.warning("EA bathing-water service refused the request for %s (%s); not asking for the other %d sites",
                            s.name, k, len(sites) - len(answered) - sum(errors.values()))
                break
            log.warning("EA samples %s: %s", s.name, e)
            continue
        answered.add(s.bw_id)
        if rows:
            frames.append(pd.DataFrame(rows).assign(bw_id=s.bw_id, name=s.name, kind=s.kind, source="bathing water"))
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), answered, errors


def _from_archive(sites: pd.DataFrame, since: str) -> tuple[pd.DataFrame | None, str | None]:
    """One request for every mapped site: (samples, or None if the request failed; error)."""
    from dipcast.ingest.wqa import fetch_ecoli
    mapped = sites.dropna(subset=["wqa_point"]).set_index("wqa_point")[["bw_id", "name", "kind"]]
    if mapped.empty:
        return None, "no site mapped"
    try:
        rows = fetch_ecoli(list(mapped.index), since)
    except Exception as e:  # noqa: BLE001 - the other source may still have answered
        log.warning("EA Water Quality Archive: %s", e)
        return None, _fetch_error(e)
    a = pd.DataFrame(rows, columns=["wqa_point", "sample_time", "ecoli", "ecoli_qual"])
    a = a[a["wqa_point"].isin(mapped.index)].join(mapped, on="wqa_point")
    return a.drop(columns=["wqa_point"]).assign(source="archive").reset_index(drop=True), None


def describe_fetch(status: dict) -> str:
    """'bathing-water service: HTTP 403 x1; archive: HTTP 502', for logs and warnings."""
    src = status.get("sources") or {}
    bw, ar = src.get("bathing_water") or {}, src.get("archive") or {}
    b = ", ".join(f"{k} x{v}" for k, v in (bw.get("errors") or {}).items())
    return f"bathing-water service: {b or 'answered'}; archive: {ar.get('error') or 'answered'}"


def refresh_ecoli_samples(force: bool = False) -> pd.DataFrame:
    """This season's EA samples at the inland bathing waters, refreshed at most once a day
    and kept in the state directory. Two sources: the bathing-water service (a request per
    site, first to publish) and the Water Quality Archive (one request; the same results
    days later, and it answers GitHub's runners, which the other refuses). A sample in both
    is taken from the bathing-water service; a site that service did not answer for keeps
    the samples it had, merged with the archive's. Each attempt is recorded in ECOLI_STATUS
    (see samples_status). When no source answers nothing is written, so the next build retries."""
    p = config.state_read(ECOLI_SAMPLES)
    if p.exists() and not force:
        age_h = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC")).total_seconds() / 3600
        if age_h < ECOLI_REFRESH_H:
            return pd.read_parquet(p)
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    sites = _bathing_sites()
    since = f"{pd.Timestamp.now(tz=LOCAL_TZ).year}-05-01T00:00:00"
    bw, bw_answered, bw_errors = _from_bathing_water_service(sites, since)
    archive, archive_error = _from_archive(sites, since)
    # A site counts as answered when the bathing-water service answered for it or the archive
    # returned rows for it; the archive's one request succeeding says nothing about each site.
    archive_sites = set(archive["bw_id"]) if archive is not None and len(archive) else set()
    answered = bw_answered | archive_sites
    reached = bool(bw_answered) or archive is not None   # some source answered at all

    def clean(part: pd.DataFrame | None) -> pd.DataFrame | None:
        if part is None or not len(part):
            return None
        part = part.assign(sample_time=_local_times(part["sample_time"]), ecoli=pd.to_numeric(part["ecoli"], errors="coerce"))
        return part.dropna(subset=["sample_time", "ecoli"])
    # Sites the bathing-water service did not answer for keep this season's earlier samples.
    kept = old[~old["bw_id"].isin(bw_answered) & (old["sample_time"] >= pd.Timestamp(since, tz=LOCAL_TZ))] if len(old) else None
    # In order of preference, so a sample found twice is kept from the first.
    parts = [x for x in (clean(bw), kept, clean(archive)) if x is not None and len(x)]
    df = pd.concat(parts, ignore_index=True).drop_duplicates(["bw_id", "sample_time"], keep="first") if parts else pd.DataFrame()
    now = pd.Timestamp.now(tz=LOCAL_TZ).isoformat(timespec="seconds")
    if reached:
        last_ok = now
    else:   # carried over; before the status file existed, the samples file's age says when a fetch last worked
        last_ok = (samples_status() or {}).get("last_ok_at")
        if last_ok is None and p.exists():
            last_ok = pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC").tz_convert(LOCAL_TZ).isoformat(timespec="seconds")
    use = df if reached and len(df) else old
    status = {"checked_at": now, "n_sites": len(sites), "n_failed": len(sites) - len(answered),
              "all_failed": bool(len(sites)) and not reached, "last_ok_at": last_ok, "n_samples": len(use),
              "sources": {"bathing_water": {"answered": len(bw_answered), "errors": bw_errors, "refused": "HTTP 403" in bw_errors},
                          "archive": {"answered": len(archive_sites), "error": archive_error,
                                      "n_samples": 0 if archive is None else len(archive)}}}
    config.state_write(ECOLI_STATUS).write_text(json.dumps(status, indent=1))
    if status["all_failed"]:
        log.error("EA samples: no source answered (%s); E. coli scoring gets no new samples. Last good fetch: %s",
                  describe_fetch(status), last_ok or "never")
    if not (reached and len(df)):
        return old
    df.to_parquet(config.state_write(ECOLI_SAMPLES), index=False)
    log.info("EA samples refreshed: %d this season at %d sites (%s)", len(df), df.bw_id.nunique(), describe_fetch(status))
    return df


def samples_status() -> dict | None:
    """The last EA sample-fetch attempt: when, how many sites failed and why, when a fetch
    last worked. None before the first attempt."""
    p = config.state_read(ECOLI_STATUS)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


def match_points_to_sites(points: pd.DataFrame, sites: pd.DataFrame) -> pd.Series:
    """bw_id for each logged point whose coordinates round to a bathing water's (4 dp ~ 10 m)."""
    key = sites.assign(k=sites["lat"].round(4).astype(str) + "," + sites["lon"].round(4).astype(str)).set_index("k")["bw_id"]
    pk = points["lat"].round(4).astype(str) + "," + points["lon"].round(4).astype(str)
    return pk.map(key)


def verify_ecoli(as_of: date | None = None) -> dict:
    """Score logged P(E. coli > 900) against EA samples taken on the target day.
    For each sample and lead the forecast used is the latest one issued *before the
    sample was taken*; a forecast issued after the sample is not a forecast of it."""
    as_of = as_of or pd.Timestamp.now(tz=LOCAL_TZ).date()
    with _LOCK:
        con = _conn()
        try:
            pts = con.execute("""
                SELECT epoch_ms(issued_at) AS issued_ms, lat, lon, target_day, lead, p_ecoli, rain_48h, risk, version
                FROM forecast_points WHERE p_ecoli IS NOT NULL AND target_day <= ?
            """, [as_of]).df()
            ms = con.execute("SELECT min(epoch_ms(issued_at)) FROM forecast_points WHERE version IS NOT NULL").fetchone()[0]
        finally:
            con.close()
    first_stamped = pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(LOCAL_TZ).date() if ms is not None else None
    out = {"n_forecasts": int(pts[["lat", "lon", "target_day", "lead"]].drop_duplicates().shape[0]) if len(pts) else 0,
           "n_scored": 0, "rule": "latest forecast issued before the sample time, per lead"}
    if pts.empty:
        return out
    pts["issued_at"] = pd.to_datetime(pts["issued_ms"], unit="ms", utc=True).dt.tz_convert(LOCAL_TZ)
    pts["target_day"] = pd.to_datetime(pts["target_day"]).dt.date
    sites = _bathing_sites()
    pts["bw_id"] = match_points_to_sites(pts, sites)
    pts = pts.dropna(subset=["bw_id"])
    samples = refresh_ecoli_samples()
    out["samples"] = samples_status()   # a fetch that failed everywhere must not read as "nothing to score yet"
    if pts.empty or samples.empty:
        return out
    samples = samples.assign(day=samples["sample_time"].dt.date)
    m = pts.merge(samples[["bw_id", "day", "sample_time", "ecoli", "name", "kind"]], left_on=["bw_id", "target_day"],
                  right_on=["bw_id", "day"], how="inner")
    n_pairs = len(m)
    m = m[m["issued_at"] < m["sample_time"]]
    out["n_issued_after_sample"] = int(n_pairs - len(m))
    m = (m.sort_values("issued_at").groupby(["bw_id", "sample_time", "lead"], as_index=False).last())
    if m.empty:
        return out
    m["y"] = (m["ecoli"] > 900).astype(int)
    out["n_scored"] = len(m); out["n_samples"] = int(m[["bw_id", "sample_time"]].drop_duplicates().shape[0])
    out["n_sites"] = int(m["bw_id"].nunique())
    from dipcast.model.ecoli import load as load_ecoli
    em = load_ecoli()
    base = (em.meta.get("base_rate_by_type") if em else None) or {}
    m["p_clim"] = m["kind"].map(base).astype(float).fillna(float(m["y"].mean()))
    y, p = m["y"].to_numpy(), m["p_ecoli"].to_numpy()
    out["overall"] = {"brier": float(np.mean((p - y) ** 2)), "base_rate": float(y.mean()),
                      "climatology_brier": float(np.mean((m["p_clim"].to_numpy() - y) ** 2)),
                      "auc": float(roc_auc_score(y, p)) if 0 < y.mean() < 1 and len(m) >= 30 else None}
    out["by_lead"] = [{"lead": int(k), "n": len(g), "base_rate": float(g["y"].mean()), "same_day": bool(k == 0),
                       "brier": float(np.mean((g["p_ecoli"].to_numpy() - g["y"].to_numpy()) ** 2)),
                       "climatology_brier": float(np.mean((g["p_clim"].to_numpy() - g["y"].to_numpy()) ** 2))}
                      for k, g in m.groupby("lead")]
    adv = m[m["lead"] >= 1]
    if len(adv):
        ya, pa = adv["y"].to_numpy(), adv["p_ecoli"].to_numpy()
        out["in_advance"] = {"n": len(adv), "brier": float(np.mean((pa - ya) ** 2)), "base_rate": float(ya.mean()),
                             "climatology_brier": float(np.mean((adv["p_clim"].to_numpy() - ya) ** 2)),
                             "auc": float(roc_auc_score(ya, pa)) if 0 < ya.mean() < 1 and len(adv) >= 30 else None}
    m["version"] = m["version"].fillna(unstamped_label(first_stamped))
    out["by_version"] = [{"version": v, "n": len(g), "base_rate": float(g["y"].mean()),
                          "brier": float(np.mean((g["p_ecoli"].to_numpy() - g["y"].to_numpy()) ** 2))}
                         for v, g in m.groupby("version")]
    recent = (m[m["lead"].isin([0, 1])].sort_values(["sample_time", "lead"]).groupby(["bw_id", "day"]).first()
              .reset_index().sort_values("sample_time", ascending=False).head(30))
    out["recent"] = [{"site": r["name"], "kind": r["kind"], "day": str(r["day"]), "lead": int(r["lead"]),
                      "forecast": round(float(r["p_ecoli"]), 3), "rain_48h": None if pd.isna(r["rain_48h"]) else round(float(r["rain_48h"]), 1),
                      "ecoli": int(r["ecoli"])} for _, r in recent.iterrows()]
    return out


def _spill_days_per_spill() -> float:
    """Spill-days per counted spill, measured on United Utilities event history against
    the annual returns (scripts/spill_day_ratio.py). The returns count spills by the
    12/24-hour block method, so a count is not a day count; on 5,886 site-years the
    pooled ratio is 1.00, so count/365 is the spill-day rate to within 1%."""
    p = config.PROCESSED / "spill_day_ratio.json"
    if p.exists():
        try:
            return float(json.loads(p.read_text()).get("spill_days_per_spill", 1.0))
        except (ValueError, TypeError):
            return 1.0
    return 1.0


def _site_climatology(site_ids: pd.Series) -> np.ndarray:
    """Long-run daily spill-day probability per overflow: the annual-return spill
    count times the measured spill-days-per-spill ratio, over 365. The same baseline
    as the offline tests."""
    p = config.state_read("overflows.parquet")
    default = 20.0 / 365.0
    ratio = _spill_days_per_spill()
    if not p.exists():
        return np.full(len(site_ids), default)
    ov = pd.read_parquet(p, columns=["site_id", "lta_spills"]).drop_duplicates("site_id").set_index("site_id")["lta_spills"]
    clim = site_ids.map(ov).astype(float).fillna(default * 365.0).to_numpy() * ratio / 365.0
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
                      ("ecoli_model", "ecoli_model_eval.json"), ("sampling_plan", "sampling_plan_test.json"),
                      ("prf", "prf_comparison.json")]:
        q = config.PROCESSED / name
        res[key] = json.loads(q.read_text()) if q.exists() else None
    return res
