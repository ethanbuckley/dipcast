"""River state from Environment Agency gauges.

Near-real-time *flow* is only published for ~350 stations, so we use *level*
stations (thousands) and express the latest level relative to the station's
typical range. That gives a 0..1+ "river state" index good enough to scale
travel speed and to show the swimmer whether the river is up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from dipcast import config

log = logging.getLogger(__name__)

EA_TIMEOUT_S = 4.0          # the EA API is sometimes very slow; never let it stall a forecast
_STATION_CACHE: dict[tuple[float, float], tuple[float, RiverState | None]] = {}
_STATION_TTL_S = 1800.0
_EA_HOST = urlsplit(config.EA_FLOOD_MONITORING).hostname


def _ea_link(url: str) -> str | None:
    """A link the EA API gave us, as https; None if it points off the EA's host.

    The flood-monitoring API writes its own links as http://, and those answer 301 to
    https://. httpx does not follow redirects, so the http form always failed. Rewriting
    the scheme avoids the redirect, and checking the host means we never fetch a URL
    from a response body on some other server.
    """
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or u.hostname != _EA_HOST:
        return None
    return urlunsplit(u._replace(scheme="https"))


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
    rloi: str | None = None   # the station's id on check-for-flooding.service.gov.uk, for a link

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


def nearest_level_station(lat: float, lon: float, dist_km: int = 15, river: str | None = None) -> RiverState | None:
    import time
    key = (round(lat, 2), round(lon, 2), _river_words(river))
    hit = _STATION_CACHE.get(key)
    if hit and time.time() - hit[0] < _STATION_TTL_S:
        return hit[1]
    try:
        st = _nearest_level_station(lat, lon, dist_km, river)
    except Exception as e:  # noqa: BLE001 - enrichment only; a forecast must never fail on it
        log.warning("river state lookup failed: %s", e)
        st = None
    _STATION_CACHE[key] = (time.time(), st)
    return st


_GENERIC = {"river", "beck", "brook", "water", "the", "stream", "burn", "canal", "lake", "mere", "tarn", "reservoir"}


def _river_words(name: str | None) -> frozenset[str]:
    """The distinctive words of a watercourse name: "River Wharfe" and "Wharfe" both give {wharfe}."""
    return frozenset(w for w in (name or "").lower().replace("-", " ").split() if w.isalpha() and w not in _GENERIC)


def _nearest_level_station(lat: float, lon: float, dist_km: int = 15, river: str | None = None) -> RiverState | None:
    try:
        r = httpx.get(f"{config.EA_FLOOD_MONITORING}/id/stations",
                      params={"lat": lat, "long": lon, "dist": dist_km, "parameter": "level",
                              "type": "SingleLevel"}, headers=config.EA_HEADERS, timeout=EA_TIMEOUT_S)
        r.raise_for_status()
        items = r.json().get("items", [])
    except httpx.HTTPError as e:
        log.warning("EA stations lookup failed: %s", e)
        return None
    if not items:
        return None
    # The closest station with a stage scale on the spot's own river, if its name is known and any
    # station carries it; else the closest with a scale. Without this, Burnsall on the Wharfe got
    # Hebden Beck, a tributary 3 km away, over Netherside Hall on the Wharfe 6 km up (1 Oct 2026).
    want = _river_words(river)
    best = None
    for s in items:
        if not s.get("stageScale"):
            continue  # accept dict or URL; both are resolved below
        same = bool(want) and bool(want & _river_words(s.get("riverName")))
        d = (float(s["lat"]) - lat) ** 2 + (float(s["long"]) - lon) ** 2
        rank = (0 if same else 1, d)
        if best is None or rank < best[0]:
            best = (rank, s)
    if best is None:
        return None
    s = best[1]
    scale = s.get("stageScale", {})
    if isinstance(scale, str):  # some stations link to the scale instead of embedding it
        url = _ea_link(scale)
        try:
            if url is None:
                raise ValueError(f"stage scale link is not on the EA host: {scale}")
            rs = httpx.get(url, params={"_view": "full"}, headers=config.EA_HEADERS, timeout=EA_TIMEOUT_S)
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
                       params={"latest": ""}, headers=config.EA_HEADERS, timeout=EA_TIMEOUT_S)
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

    rloi = s.get("RLOIid")
    return RiverState(
        station=s.get("label", ref), river=s.get("riverName"), lat=float(s["lat"]),
        lon=float(s["long"]), level_m=level,
        typical_low=_num(scale.get("typicalRangeLow")), typical_high=_num(scale.get("typicalRangeHigh")),
        observed_at=observed, rloi=str(rloi) if rloi not in (None, "") else None,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    st = nearest_level_station(54.1995, -2.5945)
    print(st, st.index if st else None, st.label if st else None)
