"""End-to-end: a point on the map -> risk now and for the coming days."""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.ingest.flows import nearest_level_station
from dipcast.ingest.rainfall import cells_for_sites, fetch_forecast
from dipcast.model import ecoli
from dipcast.model.features import ALL_FEATURES, build_site_days, daily_rain_features
from dipcast.model.spill_model import MODEL_PATH, SpillModel
from dipcast.model.transport import (
    combine_daily,
    history_days,
    live_now_risk,
    locate_pin,
    missing_share,
    risk_label,
    river_velocity,
    upstream_overflows,
)
from dipcast.network.rivers import RiverNetwork, bng_to_lonlat
from dipcast.overflows import load_overflows

log = logging.getLogger(__name__)

LOCAL_TZ = "Europe/London"   # swimmers think in local days, not UTC days
MAX_MISSING_SHARE = 0.1      # more transport weight than this from days without rain data: no figure
ECOLI_SEASON_MONTHS = range(5, 10)   # the EA samples the model was fitted on run May-September


import threading

_LOAD_LOCK = threading.RLock()  # one loader at a time: two concurrent unpickles of the
                                # 900 MB network would exceed a 2 GB machine


@lru_cache(maxsize=1)
def _net_uncached() -> RiverNetwork:
    return RiverNetwork.load()


def _net() -> RiverNetwork:
    with _LOAD_LOCK:
        return _net_uncached()


@lru_cache(maxsize=1)
def _overflows_uncached() -> pd.DataFrame:
    return load_overflows(_net())


def _overflows() -> pd.DataFrame:
    with _LOAD_LOCK:
        return _overflows_uncached()


@lru_cache(maxsize=1)
def _lead_calibration() -> dict[int, dict]:
    """Per-lead calibration from scripts/verify_leads.py, if present: Platt (a, b) and,
    when fitted, an isotonic map as knots (iso_x -> iso_y). The isotonic map is used
    when present; it is the year-level correction for the top-end over-confidence."""
    import json
    p = config.PROCESSED / "lead_calibration.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    return {int(k): v for k, v in d.get("leads", {}).items()}


def _apply(cal: dict, p: np.ndarray) -> np.ndarray:
    if "iso_x" in cal:
        return np.interp(p, np.asarray(cal["iso_x"], dtype=float), np.asarray(cal["iso_y"], dtype=float))
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return 1 / (1 + np.exp(-(cal["a"] + cal["b"] * np.log(q / (1 - q)))))


def calibrate_by_lead(p: np.ndarray, first_lead: int) -> np.ndarray:
    """p: (n_overflows, n_days); column j is lead first_lead + j. Leads beyond the
    fitted range use the last available parameters."""
    cal = _lead_calibration()
    if not cal or p.size == 0:
        return p
    out = p.copy()
    kmax = max(cal)
    for j in range(p.shape[1]):
        # Past days are analysis rain: the hindcast treats them as lead 0 (validate_ecoli.py).
        k = max(first_lead + j, 0)
        out[:, j] = _apply(cal[min(k, kmax)], out[:, j])
    return out


def calibrate_at_lead(p: np.ndarray, lead: int) -> np.ndarray:
    """Apply one lead's calibration to every column (hindcasts: every day is lead 0)."""
    cal = _lead_calibration()
    if not cal or p.size == 0:
        return p
    return _apply(cal[min(lead, max(cal))], p)


@lru_cache(maxsize=1)
def model_version() -> str:
    """Compact stamp of what produces a forecast: content hashes of the spill model,
    calibration map and E. coli model, the code version (git sha when available),
    and the weather source. Logged with every forecast so live scores can be split
    by version; a change to any of these starts a new row in the table."""
    import hashlib
    import os
    import subprocess

    def h(p) -> str:
        return hashlib.md5(p.read_bytes()).hexdigest()[:8] if p.exists() else "none"

    sha = os.environ.get("GITHUB_SHA", "")[:7]
    if not sha:
        try:
            sha = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, check=False,
                                 cwd=config.ROOT, timeout=5).stdout.strip() or "nogit"
        except Exception:  # noqa: BLE001 - a missing git is not an error
            sha = "nogit"
    from dipcast import __version__
    return (f"spill={h(MODEL_PATH)};cal={h(config.PROCESSED / 'lead_calibration.json')};"
            f"ecoli={h(ecoli.MODEL_PATH)};code={__version__}+{sha};rain=open-meteo-forecast")


@lru_cache(maxsize=1)
def _model() -> SpillModel | None:
    if MODEL_PATH.exists():
        return SpillModel.load()
    log.warning("no trained spill model at %s; forecasts use climatology only", MODEL_PATH)
    return None


def reload_caches() -> None:
    with _LOAD_LOCK:
        _overflows_uncached.cache_clear(); _model.cache_clear(); _lead_calibration.cache_clear(); ecoli.load.cache_clear()
        model_version.cache_clear()
        # the network itself is immutable at runtime; keep it loaded


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.floating, np.integer)):
        return _clean(v.item())
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.isoformat()
    if v is pd.NaT:
        return None
    return v


def spill_probabilities(ov: pd.DataFrame, days: pd.DatetimeIndex,
                        model: SpillModel | None, return_rain: bool = False) -> tuple[np.ndarray, ...]:
    """(n_overflows, n_days) probability each overflow spills on each day, and a
    bool mask of the same shape: True where that overflow's cell has rainfall data
    for the day. Where it has none the probability is NaN, never 0. With
    `return_rain`, also the day's rainfall (mm) at each overflow's cell that the
    probability was computed from, NaN where missing."""
    if ov.empty:
        empty = (np.zeros((0, len(days))), np.zeros((0, len(days)), dtype=bool))
        return (*empty, np.zeros((0, len(days)))) if return_rain else empty
    cells = cells_for_sites(ov["lat"], ov["lon"])
    rain = fetch_forecast(cells)
    rain["time"] = rain["time"].dt.tz_convert(LOCAL_TZ)   # local-midnight day boundaries
    daily = daily_rain_features(rain)
    sites = ov[["site_id", "lat", "lon", "company", "lta_spills", "spill_hours", "edm_operational_pct"]]
    sd = build_site_days(sites, daily, days)
    for f in ALL_FEATURES:
        sd[f] = sd[f].astype(np.float32)
    sd["ok"] = sd["rain_d"].notna()
    if model is None:
        # Climatology from the annual returns: spill-days per year.
        p = (sd["lta_spills"].to_numpy() if "lta_spills" in sd else np.full(len(sd), 20.0)) / 365.0
        sd["p"] = np.clip(np.nan_to_num(p, nan=0.05), 0.001, 0.95)
    else:
        sd["p"] = model.predict(sd.fillna({f: 0.0 for f in ALL_FEATURES}))
    sd.loc[~sd["ok"], "p"] = np.nan
    mat = sd.pivot(index="site_id", columns="day", values="p").reindex(index=ov["site_id"], columns=days)
    okm = sd.pivot(index="site_id", columns="day", values="ok").reindex(index=ov["site_id"], columns=days)
    out = (mat.to_numpy(dtype=float), okm.fillna(False).to_numpy(dtype=bool))
    if return_rain:
        rain_mm = sd.pivot(index="site_id", columns="day", values="rain_d").reindex(index=ov["site_id"], columns=days)
        return (*out, rain_mm.to_numpy(dtype=float))
    return out


def forecast_point(lat: float, lon: float, days_ahead: int = 4, max_km: float = config.MAX_UPSTREAM_KM,
                   include_contributors: int = 25, log_to_store: bool = True, gauge: bool = True,
                   kind_hint: str | None = None) -> dict:
    net, ov_all, model = _net(), _overflows(), _model()
    now = pd.Timestamp.now(tz=LOCAL_TZ)
    # The EA gauge lookup is slow and optional: run it alongside everything else.
    pool = ThreadPoolExecutor(max_workers=1)
    state_future = pool.submit(nearest_level_station, lat, lon) if gauge else None
    pin = locate_pin(net, lon, lat, kind_hint=kind_hint)

    out: dict = {
        "query": {"lat": lat, "lon": lon, "issued_at": now.isoformat()},
        "location": {
            "mode": pin.mode, "watercourse": pin.watercourse,
            "snap_distance_m": None if pin.snap is None else round(pin.snap.dist_m),
            "form": None if pin.snap is None else pin.snap.form,
            "lake_area_km2": None if pin.lake_area_km2 is None else round(pin.lake_area_km2, 1),
            "lake_source": pin.lake_source,
            "adopted_main_channel": pin.adopted_main_channel,
        },
        "river_state": None,
        "assumptions": {
            "river_velocity_ms": config.RIVER_VELOCITY_MS, "lake_velocity_ms": config.LAKE_VELOCITY_MS,
            "t90_hours": config.T90_HOURS, "max_upstream_km": max_km,
            "recent_spill_hours": config.RECENT_SPILL_HOURS,
            "model": None if model is None else model.trained_on,
            "lead_calibration": bool(_lead_calibration()),
            "version": model_version(),
        },
    }
    if pin.mode in ("none", "isolated"):
        pool.shutdown(wait=False)
        out["error"] = ("No river or lake within 1.5 km of this point." if pin.mode == "none" else
                        "An isolated lake with no river connection in the network: storm overflows cannot reach it "
                        "by water, so the forecast has nothing to say about it. Risk from wildlife, runoff and bathers is not modelled.")
        out["now"] = {"risk": 0.0, "label": "unknown"}
        out["days"] = []
        out["contributors"] = []
        return out

    state = None
    if state_future is not None:
        try:
            state = state_future.result(timeout=12)
        except Exception as e:  # noqa: BLE001
            log.warning("river state unavailable: %s", e)
    pool.shutdown(wait=False)
    v = river_velocity(state.index if state else None)
    out["river_state"] = None if state is None else {
        "station": state.station, "river": state.river, "level_m": state.level_m,
        "typical_low_m": state.typical_low, "typical_high_m": state.typical_high,
        "index": None if state.index is None else round(state.index, 2),
        "label": state.label, "observed_at": state.observed_at,
    }
    out["assumptions"]["river_velocity_ms"] = round(v, 2)
    ov = upstream_overflows(net, pin, ov_all, velocity_ms=v, max_km=max_km)
    now_risk, now_contrib = live_now_risk(ov, now)
    live_n = int(ov["has_live"].sum()) if not ov.empty else 0
    feed_down = feed_down_summary(ov)

    weights = ov["weight"].to_numpy(dtype=float) if not ov.empty else np.zeros(0)
    travel = ov["travel_h"].to_numpy(dtype=float) if not ov.empty else np.zeros(0)
    # The grid starts far enough back for the longest travel time (same rule as the
    # hindcast), so today's risk includes spills that left distant overflows days ago.
    hist = history_days(travel)
    days = pd.date_range(now.floor("D") - pd.Timedelta(days=hist), periods=hist + days_ahead + 1, freq="D")
    today_idx = hist
    p_raw, avail, rain_mm = spill_probabilities(ov, days, model, return_rain=True)
    p = calibrate_by_lead(p_raw, first_lead=-hist)   # column `hist` is today (lead 0)
    risk = combine_daily(p, weights, travel)
    # Share of today's transport weight that comes from days with no rainfall data.
    missing = missing_share(avail, weights, travel)
    risk = np.where(missing > MAX_MISSING_SHARE, np.nan, risk)
    # Expected number of spilling upstream overflows per day (unweighted), for context.
    exp_spills = np.nansum(p, axis=0) if len(p) else np.zeros(len(days))

    def _status(j: int) -> str:
        if missing[j] > MAX_MISSING_SHARE:
            return "rain unavailable"
        return "partial" if missing[j] > 0 else "ok"

    day_rows = []
    for j, d in enumerate(days):
        if d < now.floor("D"):
            continue  # earlier days are only there to absorb travel-time shifts
        known = not np.isnan(risk[j])
        day_rows.append({
            "date": d.date().isoformat(), "risk": round(float(risk[j]), 3) if known else None,
            "label": risk_label(float(risk[j])) if known else "unknown",
            "expected_spilling_overflows": round(float(exp_spills[j]), 1) if known else None,
            "data_status": _status(j), "rain_missing_share": round(float(missing[j]), 3),
            "in_validated_season": d.month in ECOLI_SEASON_MONTHS,
        })

    # E. coli exceedance: rain at the spot itself (not at the overflows) plus the day's exposure.
    em = ecoli.load()
    p_ec = r48 = None
    if em is not None:
        try:
            spot_rain = fetch_forecast(cells_for_sites(pd.Series([lat]), pd.Series([lon])))
            hourly = spot_rain.assign(time=spot_rain["time"].dt.tz_convert(LOCAL_TZ)).set_index("time")["precip_mm"].sort_index()
            ends = pd.DatetimeIndex([d + pd.Timedelta(hours=ecoli.SAMPLE_HOUR) for d in days])
            r48, r24, cov48, _ = ecoli.rain_windows(hourly, ends, return_coverage=True)
            X = ecoli.features(r48, r24, risk, np.full(len(days), 1.0 if pin.mode == "lake" else 0.0), days)
            p_ec = em.predict(X)
            for row in day_rows:
                j = days.get_loc(pd.Timestamp(row["date"], tz=LOCAL_TZ))
                ok = not (np.isnan(r48[j]) or np.isnan(p_ec[j]))
                row["rain_48h_mm"] = None if np.isnan(r48[j]) else round(float(r48[j]), 1)
                row["rain_48h_coverage"] = round(float(cov48[j]), 3)   # finite share of the window's 48 hours
                row["p_ecoli_gt900"] = round(float(p_ec[j]), 3) if ok else None
                if not ok and row["data_status"] == "ok":
                    row["data_status"] = "rain unavailable"
            out["assumptions"]["ecoli_model"] = {"target": em.meta.get("target"), "fitted_on": em.meta.get("sites"),
                                                 "n_samples": em.meta.get("n_samples"), "loyo_brier": em.meta.get("loyo", {}).get(
                                                     "rain + spill exposure + season (dipcast)", {}).get("brier")}
            # Where the E. coli figure has been checked: rivers, in the May-September sampling
            # season, at designated bathing waters. Lakes showed no ranking skill.
            out["ecoli_scope"] = {
                "water_body_validated": pin.mode == "river",
                "season_validated_months": [int(m) for m in ECOLI_SEASON_MONTHS],
                "note": ("Not validated on lakes: no ranking skill in the fitting data (16 exceedances in 803 lake samples)."
                         if pin.mode == "lake" else
                         "Validated on Environment Agency river bathing waters, May-September; other sites and months are extrapolation."),
            }
        except Exception as e:  # noqa: BLE001 - an optional layer must not fail the forecast
            log.warning("E. coli model skipped: %s", e)

    out["now"] = {
        "risk": round(now_risk, 3), "label": risk_label(now_risk),
        "discharging_upstream": int((ov["status"] == 1).sum()) if not ov.empty else 0,
        "recent_upstream": int(now_contrib.gt(0).sum()) - (int((ov["status"] == 1).sum()) if not ov.empty else 0),
        # Reporting in this update: an overflow whose company feed failed is carried with its
        # last snapshot (ingest.live.carry_forward), so it has a live feed but no status now.
        "monitored_upstream": live_n - sum(f["overflows"] for f in feed_down),
        "feed_down_upstream": sum(f["overflows"] for f in feed_down),
        "feed_down": feed_down,
    }
    out["days"] = day_rows
    out["upstream_summary"] = {
        "overflows": len(ov), "with_live_feed": live_n,
        "without_live_feed": int(len(ov) - live_n),
        "sum_weight": round(float(weights.sum()), 3),
        "history_days": int(hist), "max_travel_h": round(float(travel.max()), 1) if len(travel) else 0.0,
    }
    contrib = []
    if not ov.empty:
        ov = ov.copy()
        p0 = np.nan_to_num(p, nan=0.0)
        ov["p_today"] = p0[:, today_idx] if p0.shape[1] > today_idx else 0.0
        ov["p_tomorrow"] = p0[:, today_idx + 1] if p0.shape[1] > today_idx + 1 else 0.0
        # Every day shown, so a spot's page can say which overflows drive a day three days out, and
        # rank them over all of those days. NaN (no rain data at the overflow's cell) goes out as
        # None: 0 would read as "will not spill".
        p_ahead = p[:, today_idx:today_idx + len(day_rows)]
        ov["now_contribution"] = now_contrib
        ov["impact_today"] = ov["weight"] * ov["p_today"]
        ov["relevance"] = np.maximum.reduce([
            ov["now_contribution"].to_numpy(dtype=float),
            ov["weight"].to_numpy(dtype=float) * np.nan_to_num(p_ahead, nan=0.0).max(axis=1, initial=0.0),
            0.1 * ov["weight"].to_numpy(dtype=float),   # keep close, quiet overflows visible
        ])
        ov["p_days"] = [[None if np.isnan(v) else round(float(v), 3) for v in row] for row in p_ahead]
        top = ov.sort_values("relevance", ascending=False).head(include_contributors)
        for _, r in top.iterrows():
            contrib.append({k: _clean(r.get(k)) for k in [
                "site_id", "company", "site_name", "receiving_watercourse", "lat", "lon", "status",
                "has_live", "latest_event_start", "latest_event_end", "lta_spills", "spill_hours",
                "snap_confidence",
            ]} | {
                "distance_km": round(float(r["distance_m"]) / 1000, 1),
                "lake_distance_km": round(float(r["lake_distance_m"]) / 1000, 1),
                "travel_h": round(float(r["travel_h"]), 1),
                "weight": round(float(r["weight"]), 3),
                "p_spill_today": round(float(r["p_today"]), 3),
                "p_spill_tomorrow": round(float(r["p_tomorrow"]), 3),
                "p_spill_days": r["p_days"],   # one per entry in "days", None where unknown
                "now_contribution": round(float(r["now_contribution"]), 3),
            })
    out["contributors"] = contrib
    if pin.snap is not None:
        slon, slat = bng_to_lonlat(pin.snap.x, pin.snap.y)
        out["location"]["snapped"] = {"lat": round(slat, 5), "lon": round(slon, 5)}
    if log_to_store:
        try:
            from dipcast.forecast_log import log_forecast
            log_forecast(now, lat, lon, pin.mode, pin.watercourse, now_risk, days, risk, ov,
                         np.nan_to_num(p_raw, nan=0.0), np.nan_to_num(p, nan=0.0), p_ecoli=p_ec, rain_48h=r48,
                         available=avail, rain_mm=rain_mm, version=model_version())
        except Exception as e:  # noqa: BLE001 - logging must never fail a forecast
            log.warning("forecast log failed: %s", e)
    return out


def feed_down_summary(ov: pd.DataFrame) -> list[dict]:
    """Per company whose live feed did not answer the last poll: how many of these
    overflows it covers and when its feed last answered."""
    from dipcast.ingest.live import FEED_DOWN
    if ov.empty or "status" not in ov:
        return []
    down = ov[ov["status"] == FEED_DOWN]
    if down.empty:
        return []
    since = pd.to_datetime(down["feed_down_since"], utc=True) if "feed_down_since" in down else pd.Series(pd.NaT, index=down.index)
    out = []
    for c, g in down.groupby(down["company"].fillna("unknown")):
        t = since.loc[g.index].min()
        out.append({"company": str(c), "overflows": int(len(g)), "since": None if pd.isna(t) else t.isoformat()})
    return out


def overflows_geojson(bbox: tuple[float, float, float, float] | None = None, limit: int = 5000) -> dict:
    """Overflow points for the map. bbox = (min_lon, min_lat, max_lon, max_lat)."""
    ov = _overflows()
    if bbox:
        ov = ov[(ov.lon >= bbox[0]) & (ov.lat >= bbox[1]) & (ov.lon <= bbox[2]) & (ov.lat <= bbox[3])]
    ov = ov.head(limit)
    feats = []
    for _, r in ov.iterrows():
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r.lon), float(r.lat)]},
            "properties": {k: _clean(r.get(k)) for k in [
                "site_id", "company", "site_name", "receiving_watercourse", "status", "has_live",
                "latest_event_start", "latest_event_end", "lta_spills"]},
        })
    return {"type": "FeatureCollection", "features": feats}
