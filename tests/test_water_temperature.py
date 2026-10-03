"""Water temperature beside the forecast (build_site.attach_water_temperature, ingest/water_temperature.py).
Network-free: the readings and stations are fixtures shaped as the Hydrology API returned them on
3 Oct 2026, and the river network is a few links built in memory, in British National Grid metres."""

from __future__ import annotations

import importlib.util
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from dipcast.ingest import water_temperature as wt
from dipcast.model import transport
from dipcast.network.rivers import RiverNetwork, bng_to_lonlat

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
HYD = "http://environment.data.gov.uk/hydrology/id/"


def _build_site():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _station(notation: str, label: str, lat: float, lon: float, river_name: str | None = None) -> dict:
    s = {"@id": HYD + "stations/" + notation, "notation": notation, "label": label, "lat": lat, "long": lon,
         "measures": [{"@id": HYD + f"measures/{notation}-temp-i-subdaily-C"}, {"@id": HYD + f"measures/{notation}-ph-i-subdaily"}]}
    return {**s, "riverName": river_name} if river_name else s


def _reading(notation: str, hours_ago: float, value: float) -> dict:
    t = NOW - timedelta(hours=hours_ago)
    return {"measure": {"@id": HYD + f"measures/{notation}-temp-i-subdaily-C"}, "date": t.date().isoformat(),
            "dateTime": t.strftime("%Y-%m-%dT%H:%M:%S"), "value": value, "quality": "Unchecked"}   # no zone, as the API writes it


# Friars Meadow on the Stour at Sudbury, and the sensors around it. Bures Mill is the real one, 8.1 km
# away; the others are made up to test each rule. Norwich on the Wensum has one sensor, 40 km north.
FRIARS = {"id": "bw-ukh1401-11051", "name": "Friars Meadow, River Stour", "kind": "river", "river": "River Stour",
          "lat": 52.03408, "lon": 0.73489, "location": {"watercourse": "River Stour"}}
STATIONS = [
    _station("E02254A", "STOUR_BURES MILL_E_201704", 51.967084, 0.77963),        # 8.1 km, the answer
    _station("BOX1", "BOX_POLSTEAD_E_202601", 52.01, 0.75),                         # 2.9 km, another river
    _station("STALE1", "STOUR_LONG MELFORD_E_202601", 52.07, 0.72),                 # 4.1 km, read 30 h ago
    _station("TIDE1", "STOUR_DS SUDBURY_E_202601", 52.00, 0.75),                     # 3.9 km, the network says no
    _station("FAULT1", "STOUR_US BURES_E_202601", 52.02, 0.76),                      # 2.2 km, reading a fault
    _station("WENS1", "WENSUM_FAR MILL_E_202601", 52.99, 1.30),                      # 40 km from Norwich
    _station("EVESHAM", "Evesham", 52.091994, -1.94292, river_name="River Avon"),  # a hydrometric station
]
READINGS = [
    _reading("E02254A", 2.5, 16.1), _reading("E02254A", 1.0, 16.331), _reading("E02254A", 26, 15.0),
    _reading("BOX1", 1.0, 14.0), _reading("STALE1", 30, 15.5), _reading("TIDE1", 1.0, 19.0),
    _reading("FAULT1", 1.0, -9999.0), _reading("WENS1", 1.0, 13.0), _reading("EVESHAM", 0.5, 14.2),
    {"measure": {"@id": HYD + "measures/GONE-temp-i-subdaily-C"}, "dateTime": "2026-10-03T13:00:00", "value": 12.0},   # no station
]


def _get(seen: list):
    def get(url, params):
        seen.append((url, params))
        return READINGS if url.endswith("/data/readings.json") else STATIONS
    return get


def test_station_labels_give_the_river_and_the_place():
    assert wt.parse_label("STOUR_BURES MILL_E_201704") == ("Stour", "Bures Mill", "at Bures Mill")
    assert wt.parse_label("DON_DS WHARNCLIFFE WWTW_E_202603") == ("Don", "Wharncliffe WwTW", "below Wharncliffe WwTW")
    assert wt.parse_label("WITHAM_US GRANGE FARM_K_202109") == ("Witham", "Grange Farm", "above Grange Farm")
    assert wt.parse_label("KENT_BARLEY BRIDGE WEIR US_E_202601")[2] == "above Barley Bridge Weir"
    assert wt.parse_label("WMD_MANIFOLD_DS HULME END_E_202604") == ("Manifold", "Hulme End", "below Hulme End")   # area code first
    assert wt.parse_label("BROCK DS_MATSHEAD_E_202502")[0] == "Brock"
    assert wt.parse_label("TYNE_SWING BR_E_202204")[1] == "Swing Bridge"
    assert wt.parse_label("HOVETON GREAT BROAD_E_202505") == ("Hoveton Great Broad", "Hoveton Great Broad", "at Hoveton Great Broad")
    assert wt.parse_label("Evesham", "River Avon") == ("River Avon", "Evesham", "at Evesham")
    assert wt.parse_label("WEAVER_MOSS HALL FARM (DS AUDLEM STW)_E_202510")[1] == "Moss Hall Farm (DS Audlem STW)"


def test_the_latest_plausible_reading_per_station_read_in_the_last_day():
    seen = []
    sensors = {s.station_id: s for s in wt.fetch_sensors(NOW, get=_get(seen))}
    # One request for the readings since the start of yesterday, one for the active stations.
    assert seen[0][1]["observedProperty"] == "temperature" and seen[0][1]["mineq-date"] == "2026-10-02"
    assert seen[1][1] == {"observedProperty": "temperature", "status": "statusActive", "_limit": 10_000}
    bures = sensors["E02254A"]
    assert bures.temp_c == 16.3 and bures.age_hours == 1.0 and bures.observed_at == "2026-10-03T13:00:00Z"   # the API's times are UTC
    assert (bures.river, bures.where, bures.quality) == ("Stour", "at Bures Mill", "Unchecked")
    assert bures.url == "https://environment.data.gov.uk/hydrology/station/E02254A"
    assert "STALE1" not in sensors      # 30 h old: not the water now
    assert "FAULT1" not in sensors      # -9999 is a fault, not the water
    assert "GONE" not in sensors        # a reading with no station has no place
    assert sensors["EVESHAM"].river == "River Avon"
    assert set(sensors) == {"E02254A", "BOX1", "TIDE1", "WENS1", "EVESHAM"}


def test_a_spot_gets_the_nearest_sensor_on_its_own_river_and_none_40_km_away(caplog):
    bs = _build_site()
    norwich = {"id": "norwich", "name": "Norwich", "kind": "river", "river": "River Wensum", "lat": 52.63, "lon": 1.30}
    lake = {"id": "lake", "name": "A lake", "kind": "lake", "river": None, "lat": 52.0, "lon": 0.77, "location": {"watercourse": "River Stour"}}
    broken = {"id": "broken", "name": "Broken", "kind": "river", "river": "River Stour", "lat": 52.03, "lon": 0.73,
              "error": "forecast failed: boom"}
    spots = [dict(FRIARS), norwich, lake, broken]
    asked = []

    def relate(spot, sensor):   # stands in for the network: TIDE1 is on tidal water, so passed over
        asked.append((spot["id"], sensor.station_id))
        return None if sensor.station_id == "TIDE1" else ("downstream", 10.9)

    with caplog.at_level(logging.INFO, logger="build_site"):
        n = bs.attach_water_temperature(spots, fetch=lambda now: wt.fetch_sensors(NOW, get=_get([])), relate=relate, now=NOW)
    assert n == 1
    w = spots[0]["water_temp"]
    assert (w["station_id"], w["temp_c"], w["where"], w["river"]) == ("E02254A", 16.3, "at Bures Mill", "Stour")
    assert (w["direction"], w["river_km"], w["distance_km"], w["age_hours"]) == ("downstream", 10.9, 8.1, 1.0)
    assert w["observed_at"] == "2026-10-03T13:00:00Z" and w["url"].endswith("/station/E02254A")
    # Tried nearest first: the tidal one (3.9 km) was passed over; the other river's (2.9 km) never asked.
    assert asked == [("bw-ukh1401-11051", "TIDE1"), ("bw-ukh1401-11051", "E02254A")]
    assert round(wt.km_between(norwich["lat"], norwich["lon"], 52.99, 1.30)) == 40
    assert "water_temp" not in norwich and "water_temp" not in lake and "water_temp" not in broken
    assert "water temperature: 1 of 2 river spots have an EA sensor on their river within 15 km" in caplog.text
    # The Wensum's sensor is turned away for its distance alone: at 50 km it would be taken.
    again = [dict(norwich)]
    assert bs.attach_water_temperature(again, fetch=lambda now: wt.fetch_sensors(NOW, get=_get([])), relate=relate,
                                       now=NOW, max_km=50) == 1 and again[0]["water_temp"]["station_id"] == "WENS1"


def test_a_refusal_is_tried_once_more_and_then_given_up(monkeypatch):
    import httpx
    answers = []

    def get(url, params, headers, timeout):
        answers.append(url)
        code = 403 if len(answers) == 1 or "stations" in url else 200
        return httpx.Response(code, json={"items": [1]}, request=httpx.Request("GET", url))
    monkeypatch.setattr(wt.httpx, "get", get)
    monkeypatch.setattr(wt.time, "sleep", lambda s: None)
    assert wt._get(wt.READINGS, {}) == [1] and len(answers) == 2
    with pytest.raises(httpx.HTTPStatusError):
        wt._get(wt.STATIONS, {})
    assert len(answers) == 4


def test_a_failed_request_leaves_every_spot_without_and_the_build_counts_them():
    bs = _build_site()
    spots = [dict(FRIARS)]

    def down(now):
        raise RuntimeError("hydrology API timed out")
    assert bs.attach_water_temperature(spots, fetch=down) == 0 and "water_temp" not in spots[0]
    src = (ROOT / "scripts" / "build_site.py").read_text()
    assert 'health["water_temperature"] = n_water_temp' in src


# A river running east along y = 240000: S1 and S2 inland, S3 tidal below n2. A beck joins at n1, and a
# second "River Stour" at y = 250000 joins nothing.
LINKS = [
    ("S1", "River Stour", "inlandRiver", "n0", "n1", [(590000, 240000), (595000, 240000)]),
    ("S2", "River Stour", "inlandRiver", "n1", "n2", [(595000, 240000), (600000, 240000)]),
    ("S3", "River Stour", "tidalRiver", "n2", "n3", [(600000, 240000), (605000, 240000)]),
    ("B1", "Box Beck", "inlandRiver", "b0", "n1", [(595000, 245000), (595000, 240000)]),
    ("X1", "River Stour", "inlandRiver", "x0", "x1", [(590000, 250000), (595000, 250000)]),
]


@pytest.fixture
def net(monkeypatch):
    monkeypatch.setattr(transport, "_lake_polygon_at", lambda x, y: None)
    gdf = gpd.GeoDataFrame(
        [{"id": i, "flow_direction": "in direction", "form": f, "fictitious": False, "watercourse_name": n,
          "watercourse_name_alternative": None, "start_node": s, "end_node": e, "geometry": LineString(xy)}
         for i, n, f, s, e, xy in LINKS], crs=27700)
    return RiverNetwork.from_links(gdf)


def _sensor_at(x: float, y: float) -> wt.Sensor:
    lon, lat = bng_to_lonlat(x, y)
    return wt.Sensor("s", "STOUR_X_E_202601", "Stour", "X", "at X", lat, lon, 15.0, "2026-10-03T13:00:00Z", 1.0, "Unchecked")


def test_the_network_says_upstream_or_downstream_and_how_far_along_the_river(net):
    lon, lat = bng_to_lonlat(597000, 240050)   # on S2, 2 km below the beck
    rel = lambda x, y: wt.river_relation(net, lat, lon, "River Stour", _sensor_at(x, y))
    assert rel(591000, 240020) == ("upstream", 6.0)       # 4 km down S1, then 2 km down S2
    assert rel(599000, 240020) == ("downstream", 2.0)     # further down the same link
    assert rel(602000, 240020) is None                    # below the tidal limit: other water
    assert rel(592000, 250010) is None                    # a river of the same name the network does not join
