"""River state from Environment Agency gauges.

Near-real-time *flow* is only published for ~350 stations, so we use *level*
stations (thousands) and express the latest level relative to the station's
typical range. That gives a 0..1+ "river state" index good enough to scale
travel speed and to show the swimmer whether the river is up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from dipcast import config

log = logging.getLogger(__name__)

EA_TIMEOUT_S = 4.0          # the EA API is sometimes very slow; never let it stall a forecast
_STATION_CACHE: dict[tuple[float, float], tuple[float, RiverState | None]] = {}
_STATION_TTL_S = 1800.0


@dataclass
class RiverState:
    station: str
    river: str | None
    lat: float
    lon: float
    level_m: float | None
    typical_low: float | None
    typical_high: float | None
    observed_at: str | None

    @property
    def index(self) -> float | None:
        """0 at typical low, 1 at typical high; may exceed 1 in flood."""
        if None in (self.level_m, self.typical_low, self.typical_high):
            return None
        rng = self.typical_high - self.typical_low
        if rng <= 0:
            return None
        return (self.level_m - self.typical_low) / rng

    @property
    def label(self) -> str:
        i = self.index
        if i is None:
            return "unknown"
        if i < 0.15:
            return "low"
        if i < 0.6:
            return "normal"
        if i < 1.0:
            return "high"
        return "very high"


def nearest_level_station(lat: float, lon: float, dist_km: int = 15) -> RiverState | None:
    import time
    key = (round(lat, 2), round(lon, 2))
    hit = _STATION_CACHE.get(key)
    if hit and time.time() - hit[0] < _STATION_TTL_S:
        return hit[1]
    try:
        st = _nearest_level_station(lat, lon, dist_km)
    except Exception as e:  # enrichment only; a forecast must never fail on it
        log.warning("river state lookup failed: %s", e)
        st = None
    _STATION_CACHE[key] = (time.time(), st)
    return st


def _nearest_level_station(lat: float, lon: float, dist_km: int = 15) -> RiverState | None:
    try:
        r = httpx.get(f"{config.EA_FLOOD_MONITORING}/id/stations",
                      params={"lat": lat, "long": lon, "dist": dist_km, "parameter": "level",
                              "type": "SingleLevel"}, timeout=EA_TIMEOUT_S)
        r.raise_for_status()
        items = r.json().get("items", [])
    except httpx.HTTPError as e:
        log.warning("EA stations lookup failed: %s", e)
        return None
    if not items:
        return None
    # Pick the closest with a stage scale.
    best = None
    for s in items:
        if not s.get("stageScale"):
            continue  # accept dict or URL; both are resolved below
        d = (float(s["lat"]) - lat) ** 2 + (float(s["long"]) - lon) ** 2
        if best is None or d < best[0]:
            best = (d, s)
    if best is None:
        return None
    s = best[1]
    scale = s.get("stageScale", {})
    if isinstance(scale, str):  # some stations link to the scale instead of embedding it
        try:
            rs = httpx.get(scale, params={"_view": "full"}, timeout=EA_TIMEOUT_S)
            rs.raise_for_status()
            scale = rs.json().get("items", {})
            if isinstance(scale, list):
                scale = scale[0] if scale else {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("EA stage scale fetch failed: %s", e)
            scale = {}
    if not isinstance(scale, dict):
        scale = {}
    ref = s.get("stationReference")
    level, observed = None, None
    try:
        rr = httpx.get(f"{config.EA_FLOOD_MONITORING}/id/stations/{ref}/readings",
                       params={"latest": ""}, timeout=EA_TIMEOUT_S)
        rr.raise_for_status()
        for rd in rr.json().get("items", []):
            if "level" in rd.get("measure", ""):
                level, observed = float(rd["value"]), rd.get("dateTime")
                break
    except (httpx.HTTPError, ValueError) as e:
        log.warning("EA readings failed for %s: %s", ref, e)

    def _num(x):
        if isinstance(x, dict):
            x = x.get("value")
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    return RiverState(
        station=s.get("label", ref), river=s.get("riverName"), lat=float(s["lat"]),
        lon=float(s["long"]), level_m=level,
        typical_low=_num(scale.get("typicalRangeLow")), typical_high=_num(scale.get("typicalRangeHigh")),
        observed_at=observed,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    st = nearest_level_station(54.1995, -2.5945)
    print(st, st.index if st else None, st.label if st else None)
