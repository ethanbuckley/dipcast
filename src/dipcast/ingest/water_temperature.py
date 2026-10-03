"""Water temperature from the Environment Agency's water-quality sensors (Hydrology API, OGL v3).

The Hydrology API lists 2,012 temperature measures, but most are finished sonde deployments: 102
reported in the week to 3 Oct 2026. All 102 were water-quality sondes whose station has no
`riverName`; the label carries the river and the place instead, as RIVER_PLACE_<team>_<YYYYMM>
("STOUR_BURES MILL_E_201704"), sometimes after an area code ("WMD_MANIFOLD_DS HULME END_E_202604").

The API writes times without a zone. They are UTC: on 3 Oct 2026 its 15-minute level series at
Evesham matched the flood-monitoring API's, which ends each time in Z, reading for reading, and the
EA's Hydrology Data Explorer labels the same sonde readings GMT.

What the site shows is an observation beside the forecast, never an input to it, and never an
estimate: a spot gets the nearest sensor within 15 km whose river has the spot's river's name, that
the river network joins to the spot, on the same side of the tidal limit, and read in the last day;
or nothing.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from dipcast import config
from dipcast.ingest.flows import MAX_READING_AGE_H
from dipcast.network.names import same_river

log = logging.getLogger(__name__)

READINGS = f"{config.EA_HYDROLOGY}/data/readings.json"
STATIONS = f"{config.EA_HYDROLOGY}/id/stations.json"
EXPLORER = "https://environment.data.gov.uk/hydrology/station/"   # the EA's own page for a station
TIMEOUT_S = 60.0
READINGS_LIMIT = 100_000         # the API's own cap on one request
PLAUSIBLE_C = (-1.0, 35.0)       # outside this a reading is a fault, not the water
MAX_KM = 15.0                    # straight line, as for the level gauge
RIVER_CAP_M = 45_000.0           # how far along the network to look for the path between spot and sensor

# Words in a station label that are not names: the side of a works or a weir it stands on, and
# abbreviations kept as the site writes them in overflow names.
SIDE = {"DS": "below", "US": "above"}
ABBR = {"WWTW": "WwTW", "STW": "STW", "WTW": "WTW", "TW": "TW", "PS": "PS", "SPS": "SPS", "CSO": "CSO",
        "RD": "Road", "BR": "Bridge"}
_TEAM = re.compile(r"[A-Z]{1,3}")   # E, K, CE, PS: the team that runs the sonde
_YYYYMM = re.compile(r"\d{6}")      # when the deployment started


@dataclass(frozen=True)
class Sensor:
    station_id: str
    label: str
    river: str | None    # "Stour" from a sonde's label, or the station's riverName ("River Avon")
    place: str           # "Bures Mill", "Wharncliffe WwTW"
    where: str           # "at Bures Mill", "below Wharncliffe WwTW"
    lat: float
    lon: float
    temp_c: float
    observed_at: str     # ISO 8601, UTC, with Z
    age_hours: float
    quality: str | None  # the API's word for the reading: "Unchecked" until the EA has checked it

    @property
    def url(self) -> str:
        return EXPLORER + quote(self.station_id, safe="")


def _tidy(words: str) -> str:
    """"BURES MILL" -> "Bures Mill", "MOSS HALL FARM (DS AUDLEM STW)" -> "Moss Hall Farm (DS Audlem STW)"."""
    out = []
    for w in words.split():
        lead, core, trail = re.fullmatch(r"([(\[]*)(.*?)([)\],.]*)", w).groups()
        u = core.upper()
        if u in ABBR or u in SIDE:
            core = ABBR.get(u, u)
        elif any(c.isdigit() for c in core):
            core = u
        else:
            core = "-".join(p[:1].upper() + p[1:].lower() for p in core.split("-"))
        out.append(lead + core + trail)
    return " ".join(out)


def _drop_side(words: str) -> tuple[str | None, str]:
    """("below", "Wharncliffe WwTW") from "DS Wharncliffe WwTW", and ("above", "Barley Bridge Weir")
    from "Barley Bridge Weir US"."""
    w = words.split()
    side = SIDE.get(w[0].upper()) if w else None
    if side:
        w = w[1:]
    elif len(w) > 1 and w[-1].upper() in SIDE:
        side, w = SIDE[w[-1].upper()], w[:-1]
    return side, " ".join(w)


def parse_label(label: str | None, river_name: str | None = None) -> tuple[str | None, str, str]:
    """(river, place, where) from a station's label and riverName.

    "STOUR_BURES MILL_E_201704" -> ("Stour", "Bures Mill", "at Bures Mill");
    "DON_DS WHARNCLIFFE WWTW_E_202603" -> ("Don", "Wharncliffe WwTW", "below Wharncliffe WwTW");
    "WMD_MANIFOLD_DS HULME END_E_202604" -> ("Manifold", "Hulme End", "below Hulme End");
    a hydrometric station, "Evesham" on "River Avon" -> ("River Avon", "Evesham", "at Evesham")."""
    raw = str(label or "").strip()
    parts = [p.strip() for p in raw.split("_") if p.strip()]
    if len(parts) >= 3 and _YYYYMM.fullmatch(parts[-1]) and _TEAM.fullmatch(parts[-2]):
        parts = parts[:-2]
    if river_name:
        river, place = river_name.strip(), " ".join(parts) or raw
    elif len(parts) >= 2:
        river, place = _drop_side(_tidy(parts[-2]))[1], parts[-1]   # an area code, if any, goes first
    elif parts:
        river = place = _drop_side(_tidy(parts[0]))[1]                # a place that names its water
    else:
        return None, raw, f"at {raw}"
    side, place = _drop_side(_tidy(place))
    place = place or _tidy(raw)
    return (river or None), place, f"{side or 'at'} {place}"


def _utc(stamp) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _last_segment(ref) -> str:
    if isinstance(ref, dict):
        ref = ref.get("@id")
    return str(ref or "").rstrip("/").rsplit("/", 1)[-1]


def live_sensors(readings: list[dict], stations: list[dict], now: datetime | None = None,
                 max_age_h: float = MAX_READING_AGE_H) -> list[Sensor]:
    """The latest plausible reading of each station, for stations read within `max_age_h` of `now`.
    `readings` and `stations` are the `items` of the Hydrology API's readings and stations lists."""
    now = now or datetime.now(UTC)
    by_measure: dict[str, dict] = {}
    by_station: dict[str, dict] = {}
    for s in stations:
        sid = str(s.get("notation") or _last_segment(s))
        by_station[sid] = s
        for m in s.get("measures") or []:
            by_measure[_last_segment(m)] = s
    latest: dict[str, tuple[float, datetime, str | None, dict]] = {}
    unknown = 0
    for x in readings:
        m = _last_segment(x.get("measure"))
        s = by_measure.get(m) or by_station.get(m.split("-temp-")[0])
        if s is None:
            unknown += 1
            continue
        try:
            v = float(x["value"])
        except (KeyError, TypeError, ValueError):
            continue
        t = _utc(x.get("dateTime"))
        if t is None or not math.isfinite(v) or not PLAUSIBLE_C[0] <= v <= PLAUSIBLE_C[1]:
            continue
        sid = str(s.get("notation") or _last_segment(s))
        if sid not in latest or t > latest[sid][1]:
            latest[sid] = (v, t, x.get("quality"), s)
    if unknown:
        log.info("water temperature: %d readings from stations not in the station list", unknown)
    out = []
    for sid, (v, t, quality, s) in latest.items():
        age = (now - t).total_seconds() / 3600.0
        if age > max_age_h or age < -1.0:   # an old reading is not the water now; a future one is a bad clock
            continue
        try:
            lat, lon = float(s["lat"]), float(s["long"])
        except (KeyError, TypeError, ValueError):
            continue
        river, place, where = parse_label(s.get("label"), s.get("riverName"))
        out.append(Sensor(station_id=sid, label=str(s.get("label") or sid), river=river, place=place, where=where,
                          lat=lat, lon=lon, temp_c=round(v, 1), observed_at=t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          age_hours=round(max(age, 0.0), 1), quality=quality))
    return out


def _get(url: str, params: dict, tries: int = 2, wait_s: float = 5.0) -> list[dict]:
    """The `items` of one API list. In a local build on 3 Oct 2026 the EA's gateway answered 403 to
    this request and to many of the level lookups before it, and 200 to the same request a minute
    later, so a refusal is tried once more."""
    for i in range(tries):
        try:
            r = httpx.get(url, params=params, headers=config.EA_HEADERS, timeout=TIMEOUT_S)
            r.raise_for_status()
            return r.json().get("items", [])
        except httpx.HTTPStatusError as e:
            if i + 1 == tries or e.response.status_code not in (403, 429, 500, 502, 503, 504):
                raise
            log.info("water temperature: %s answered %d; trying again in %.0f s", url, e.response.status_code, wait_s)
            time.sleep(wait_s)
    return []


def fetch_sensors(now: datetime | None = None, get=None, max_age_h: float = MAX_READING_AGE_H) -> list[Sensor]:
    """Two requests for the whole build: every temperature reading since the start of the day `max_age_h`
    ago (about 1 MB; the week the roadmap measured is about 5 MB, and only the last day is shown), and
    the active stations that measure temperature (about 0.5 MB), for where each one is."""
    get = get or _get
    now = now or datetime.now(UTC)
    since = (now - timedelta(hours=max_age_h)).date().isoformat()
    readings = get(READINGS, {"observedProperty": "temperature", "mineq-date": since, "_limit": READINGS_LIMIT})
    if len(readings) >= READINGS_LIMIT:
        log.warning("water temperature: %d readings, the API's limit; some stations may be missing", len(readings))
    stations = get(STATIONS, {"observedProperty": "temperature", "status": "statusActive", "_limit": 10_000})
    return live_sensors(readings, stations, now, max_age_h)


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = math.pi / 180
    h = math.sin((lat2 - lat1) * r / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def sensors_on_river(sensors: list[Sensor], lat: float, lon: float, river: str | None,
                     max_km: float = MAX_KM) -> list[tuple[Sensor, float]]:
    """The sensors within `max_km` (straight line) whose river shares a name with `river`, nearest
    first, each with its distance in km. None without a river name: another water is not this one's."""
    near = [(s, km_between(lat, lon, s.lat, s.lon)) for s in sensors if same_river(river, s.river)]
    return sorted(((s, d) for s, d in near if d <= max_km), key=lambda x: x[1])


def along_river_m(net, upper, lower, cap_m: float = RIVER_CAP_M) -> float | None:
    """Metres along the network from pin `upper` down to pin `lower` (transport.PinLocation, river mode),
    when `upper` is upstream of `lower` within `cap_m`; None when it is not, or either is off a river."""
    if upper.mode != "river" or lower.mode != "river" or upper.snap is None or lower.snap is None:
        return None
    a, b = upper.snap, lower.snap
    if a.link_id == b.link_id:
        d = (b.frac - a.frac) * float(net.links.loc[a.link_id, "length"])
        return d if d >= 0 else None
    up = net.upstream_edges(lower.trace_node, cap_m)
    if a.link_id not in up:
        return None
    d = (1 - a.frac) * float(net.links.loc[a.link_id, "length"]) + up[a.link_id] + lower.trace_offset_m
    return d if d <= cap_m else None


def river_relation(net, lat: float, lon: float, river: str | None, sensor: Sensor,
                   cap_m: float = RIVER_CAP_M) -> tuple[str, float] | None:
    """("upstream" or "downstream", km along the river) from the spot to the sensor. None when the
    network does not join them within `cap_m`, or when one is on tidal water and the other is not: on
    3 Oct 2026 the six Thames sondes within 15 km of Ham and Kingston all snapped to tidal links, the
    nearest (Brentford Barge, 20.5 °C) 8.9 km down the river from a spot on a non-tidal link."""
    from dipcast.model.transport import locate_pin
    spot = locate_pin(net, lon, lat, kind_hint="river", river_hint=river)
    at = locate_pin(net, sensor.lon, sensor.lat, kind_hint="river", river_hint=sensor.river)
    if spot.snap is None or at.snap is None or (spot.snap.form == "tidalRiver") != (at.snap.form == "tidalRiver"):
        return None
    d = along_river_m(net, at, spot, cap_m)
    if d is not None:
        return "upstream", round(float(d) / 1000, 1)
    d = along_river_m(net, spot, at, cap_m)
    if d is not None:
        return "downstream", round(float(d) / 1000, 1)
    return None
