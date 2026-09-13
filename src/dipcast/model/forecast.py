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
from dipcast.model.features import ALL_FEATURES, build_site_days, daily_rain_features
from dipcast.model.spill_model import MODEL_PATH, SpillModel
from dipcast.model.transport import (
    combine_daily,
    live_now_risk,
    locate_pin,
    risk_label,
    river_velocity,
    upstream_overflows,
)
from dipcast.network.rivers import RiverNetwork, bng_to_lonlat
from dipcast.overflows import load_overflows

log = logging.getLogger(__name__)

LOCAL_TZ = "Europe/London"   # swimmers think in local days, not UTC days


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
def _lead_calibration() -> dict[int, tuple[float, float]]:
    """Per-lead Platt parameters from scripts/verify_leads.py, if present."""
    import json
    p = config.PROCESSED / "lead_calibration.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    return {int(k): (v["a"], v["b"]) for k, v in d.get("leads", {}).items()}


def calibrate_by_lead(p: np.ndarray, first_lead: int) -> np.ndarray:
    """p: (n_overflows, n_days); column j is lead first_lead + j. Leads beyond the
    fitted range use the last available parameters."""
    cal = _lead_calibration()
    if not cal or p.size == 0:
        return p
    out = p.copy()
    kmax = max(cal)
    for j in range(p.shape[1]):
        k = first_lead + j
        if k < 0:
            continue
        a, b = cal[min(k, kmax)]
        z = np.log(np.clip(out[:, j], 1e-6, 1 - 1e-6) / (1 - np.clip(out[:, j], 1e-6, 1 - 1e-6)))
        out[:, j] = 1 / (1 + np.exp(-(a + b * z)))
    return out


@lru_cache(maxsize=1)
def _model() -> SpillModel | None:
    if MODEL_PATH.exists():
        return SpillModel.load()
    log.warning("no trained spill model at %s; forecasts use climatology only", MODEL_PATH)
    return None


def reload_caches() -> None:
    with _LOAD_LOCK:
        _overflows_uncached.cache_clear(); _model.cache_clear(); _lead_calibration.cache_clear()
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


def spill_probabilities(ov: pd.DataFrame, days: pd.DatetimeIndex, model: SpillModel | None) -> np.ndarray:
    """(n_overflows, n_days) probability each overflow spills on each day."""
    if ov.empty:
        return np.zeros((0, len(days)))
    cells = cells_for_sites(ov["lat"], ov["lon"])
    rain = fetch_forecast(cells)
    rain["time"] = rain["time"].dt.tz_convert(LOCAL_TZ)   # local-midnight day boundaries
    daily = daily_rain_features(rain)
    sites = ov[["site_id", "lat", "lon", "company", "lta_spills", "spill_hours", "edm_operational_pct"]]
    sd = build_site_days(sites, daily, days)
    for f in ALL_FEATURES:
        sd[f] = sd[f].astype(np.float32)
    if model is None:
        # Climatology from the annual returns: spill-days per year.
        p = (sd["lta_spills"].to_numpy() if "lta_spills" in sd else np.full(len(sd), 20.0)) / 365.0
        sd["p"] = np.clip(np.nan_to_num(p, nan=0.05), 0.001, 0.95)
    else:
        sd["p"] = model.predict(sd.fillna({f: 0.0 for f in ALL_FEATURES}))
    mat = sd.pivot(index="site_id", columns="day", values="p").reindex(index=ov["site_id"], columns=days)
    return np.nan_to_num(mat.to_numpy(dtype=float), nan=0.0)


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
        },
    }
    if pin.mode in ("none", "isolated"):
        pool.shutdown(wait=False)
        out["error"] = ("No river or lake within 1.5 km of this point." if pin.mode == "none" else
                        "An isolated lake with no river connection in the network: storm overflows cannot reach it "
                        "by water, so dipcast has nothing to say about it. Risk from wildlife, runoff and bathers is not modelled.")
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

    days = pd.date_range(now.floor("D") - pd.Timedelta(days=1), periods=days_ahead + 2, freq="D")
    p_raw = spill_probabilities(ov, days, model)
    p = calibrate_by_lead(p_raw, first_lead=-1)   # column 0 is yesterday, column 1 is today (lead 0)
    weights = ov["weight"].to_numpy(dtype=float) if not ov.empty else np.zeros(0)
    travel = ov["travel_h"].to_numpy(dtype=float) if not ov.empty else np.zeros(0)
    risk = combine_daily(p, weights, travel)
    # Expected number of spilling upstream overflows per day (unweighted), for context.
    exp_spills = p.sum(axis=0) if len(p) else np.zeros(len(days))

    day_rows = []
    for j, d in enumerate(days):
        if d < now.floor("D"):
            continue  # yesterday is only there to absorb travel-time shifts
        day_rows.append({
            "date": d.date().isoformat(), "risk": round(float(risk[j]), 3),
            "label": risk_label(float(risk[j])), "expected_spilling_overflows": round(float(exp_spills[j]), 1),
        })

    out["now"] = {
        "risk": round(now_risk, 3), "label": risk_label(now_risk),
        "discharging_upstream": int((ov["status"] == 1).sum()) if not ov.empty else 0,
        "recent_upstream": int(now_contrib.gt(0).sum()) - (int((ov["status"] == 1).sum()) if not ov.empty else 0),
        "monitored_upstream": live_n,
    }
    out["days"] = day_rows
    out["upstream_summary"] = {
        "overflows": len(ov), "with_live_feed": live_n,
        "without_live_feed": int(len(ov) - live_n),
        "sum_weight": round(float(weights.sum()), 3),
    }
    today_idx = 1
    contrib = []
    if not ov.empty:
        ov = ov.copy()
        ov["p_today"] = p[:, today_idx] if p.shape[1] > today_idx else 0.0
        ov["p_tomorrow"] = p[:, today_idx + 1] if p.shape[1] > today_idx + 1 else 0.0
        ov["now_contribution"] = now_contrib
        ov["impact_today"] = ov["weight"] * ov["p_today"]
        ov["relevance"] = np.maximum.reduce([
            ov["now_contribution"].to_numpy(dtype=float),
            (ov["weight"] * np.maximum(ov["p_today"], ov["p_tomorrow"])).to_numpy(dtype=float),
            0.1 * ov["weight"].to_numpy(dtype=float),   # keep close, quiet overflows visible
        ])
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
                "now_contribution": round(float(r["now_contribution"]), 3),
            })
    out["contributors"] = contrib
    if pin.snap is not None:
        slon, slat = bng_to_lonlat(pin.snap.x, pin.snap.y)
        out["location"]["snapped"] = {"lat": round(slat, 5), "lon": round(slon, 5)}
    if log_to_store:
        try:
            from dipcast.forecast_log import log_forecast
            log_forecast(now, lat, lon, pin.mode, pin.watercourse, now_risk, days, risk, ov, p_raw, p)
        except Exception as e:  # noqa: BLE001 - logging must never fail a forecast
            log.warning("forecast log failed: %s", e)
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
