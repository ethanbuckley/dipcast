"""Where a spot lands on the river network, and which gauge it is matched to. Network-free: the
networks here are a few links built in memory, in British National Grid metres."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from dipcast.model import transport
from dipcast.network.names import river_words, same_river
from dipcast.network.rivers import RiverNetwork, bng_to_lonlat

ROOT = Path(__file__).resolve().parents[1]


def _build_site():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _net(links: list[tuple]) -> RiverNetwork:
    """links: (id, name, alternative name, start node, end node, [(x, y), ...]), all in flow direction."""
    gdf = gpd.GeoDataFrame(
        [{"id": i, "flow_direction": "in direction", "form": "inlandRiver", "fictitious": False,
          "watercourse_name": n, "watercourse_name_alternative": alt, "start_node": s, "end_node": e,
          "geometry": LineString(xy)} for i, n, alt, s, e, xy in links], crs=27700)
    return RiverNetwork.from_links(gdf)


# The Lune runs east along y = 460000; Escow Beck comes down from the north and joins it at x = 351000.
# A pin 100 m from the beck and 600 m from the Lune is nearest the beck, which has no overflow upstream.
LUNE = [
    ("L1", "River Lune", None, "n0", "n1", [(348000, 460000), (350000, 460000)]),
    ("L2", "River Lune", "Crook o' Lune", "n1", "n2", [(350000, 460000), (351000, 460000)]),
    ("L3", "River Lune", None, "n2", "n3", [(351000, 460000), (352000, 460000)]),
    ("B1", "Escow Beck", None, "b0", "n2", [(351000, 461500), (351000, 460000)]),
]
PIN = bng_to_lonlat(351100, 460600)   # (lon, lat)
OVERFLOWS = pd.DataFrame({"site_id": ["o1"], "link_id": ["L1"], "frac": [0.5], "link_length": [2000.0],
                          "start_node": ["n0"], "has_live": [True], "status": [0]})


@pytest.fixture(autouse=True)
def _no_lake_polygons(monkeypatch):
    monkeypatch.setattr(transport, "_lake_polygon_at", lambda x, y: None)


def test_welsh_names_match_english_ones():
    assert river_words("Afon Gwy") == river_words("River Wye") == {"wye"}
    assert same_river("Afon Hafren", "River Severn") and same_river("Afon Tefeidiad", "River Teme")
    assert same_river("River Wharfe", "Wharfe") and not same_river("River Wye", "Garren Brook")
    assert river_words("Crook o' Lune") == {"crook", "lune"}           # punctuation and one-letter words drop out
    assert not same_river(None, "River Lune") and river_words("River") == frozenset()
    from dipcast.ingest.flows import _river_words  # the gauge matcher uses the same words
    assert _river_words("Afon Hafren") & _river_words("River Severn")


def test_a_river_hint_beats_a_nearer_beck():
    net = _net(LUNE)
    lon, lat = PIN
    plain = transport.locate_pin(net, lon, lat, kind_hint="river")
    assert plain.watercourse == "Escow Beck" and plain.placement is None
    assert transport.upstream_overflows(net, plain, OVERFLOWS, velocity_ms=0.5).empty

    pin = transport.locate_pin(net, lon, lat, kind_hint="river", river_hint="River Lune")
    assert pin.mode == "river" and pin.watercourse == "River Lune" and pin.placement == "moved to river"
    assert pin.snap.link_id == "L3" and round(pin.snap.dist_m) == 600
    assert len(transport.upstream_overflows(net, pin, OVERFLOWS, velocity_ms=0.5)) == 1

    # A pin already on the named river stays where it is.
    on = transport.locate_pin(net, *bng_to_lonlat(349000, 460050), kind_hint="river", river_hint="River Lune")
    assert on.snap.link_id == "L1" and on.placement == "on river"


def test_no_named_river_nearby_keeps_the_nearest_with_a_warning(caplog):
    net = _net(LUNE)
    pin = transport.locate_pin(net, *PIN, kind_hint="river", river_hint="River Dart")
    assert pin.watercourse == "Escow Beck" and pin.placement == "river not found"
    assert "River Dart" in caplog.text
    # Beyond HINT_SNAP_M the named river is not taken either.
    far = transport.locate_pin(net, *bng_to_lonlat(351050, 461300), kind_hint="river", river_hint="River Lune")
    assert far.watercourse == "Escow Beck" and far.placement == "river not found"


def test_a_lake_ignores_the_river_hint():
    net = _net(LUNE)
    pin = transport.locate_pin(net, *PIN, kind_hint="lake", river_hint="River Lune")
    assert pin.mode == "isolated" and pin.placement is None   # no lake centreline here: unchanged behaviour


def test_welsh_named_link_is_found_and_shown_in_english():
    net = _net([
        ("W1", "Afon Gwy", "River Wye", "w0", "w1", [(355000, 216000), (357000, 216000)]),
        ("G1", "Garren Brook", None, "g0", "w1", [(356500, 216800), (357000, 216000)]),
    ])
    lon, lat = bng_to_lonlat(356550, 216500)   # about 100 m from the brook, 500 m from the Wye
    pin = transport.locate_pin(net, lon, lat, kind_hint="river", river_hint="River Wye")
    assert pin.snap.link_id == "W1" and pin.watercourse == "River Wye"
    welsh = transport.locate_pin(net, *bng_to_lonlat(356000, 216010), kind_hint="river", river_hint="Afon Gwy")
    assert welsh.watercourse == "Afon Gwy" and welsh.placement == "on river"


def test_build_health_warns_on_a_spot_on_the_wrong_water():
    bs = _build_site()
    day = {"data_status": "ok"}

    def spot(i, river, wc, d, **loc):
        return {"id": i, "name": i, "kind": "river", "river": river, "days": [day],
                "location": {"mode": "river", "watercourse": wc, "snap_distance_m": d, **loc}}

    good = [spot("ilkley", "River Wharfe", "River Wharfe", 3), spot("ludlow", "River Teme", "Afon Tefeidiad", 20),
            spot("wolvercote", "River Thames", "River Thames", 342, adopted_main_channel=True),
            {"id": "tarn", "name": "tarn", "kind": "lake", "days": [day], "location": {"mode": "lake", "watercourse": None}}]
    assert bs.build_health(good)["warnings"] == []
    bad = [spot("crook", "River Lune", "Escow Beck", 262), spot("warleigh", "River Avon", "River Avon", 1227),
           spot("blank", None, "River Eden", 10)]
    h = bs.build_health(good + bad)
    assert len(h["warnings"]) == 1
    w = h["warnings"][0]
    assert w.startswith("3 river spots may be on the wrong water")
    assert "crook: snapped to Escow Beck, not the River Lune, 262 m from the river network" in w
    assert "warleigh: 1227 m from the river network" in w and "blank: no river named in spots.csv" in w
    assert [m["id"] for m in h["placement_check"]] == ["crook", "warleigh", "blank"]


def test_spots_csv_names_a_river_for_every_river_spot_and_none_for_lakes():
    spots = pd.read_csv(ROOT / "spots.csv").fillna("")
    rivers = spots[spots["kind"] == "river"]
    assert (rivers["river"] != "").all(), list(rivers.loc[rivers["river"] == "", "id"])
    assert (spots.loc[spots["kind"] == "lake", "river"] == "").all()
    assert rivers["river"].map(lambda r: bool(river_words(r))).all()


def test_a_stale_gauge_reading_is_flagged_not_shown_as_current():
    from dipcast.ingest.flows import RiverState
    bs = _build_site()
    now = datetime(2026, 10, 2, 21, 0, tzinfo=UTC)

    def reading(hours_old):
        t = (now - timedelta(hours=hours_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return RiverState(station="Temple Sowerby", river="River Eden", lat=54.65, lon=-2.6, level_m=0.222,
                          typical_low=0.2, typical_high=1.6, observed_at=t)

    ages = iter([1, 77])
    spots = [{"id": k, "name": k, "lat": 54.75, "lon": -2.7, "river": "River Eden",
              "location": {"watercourse": "River Eden"}} for k in ("fresh", "old")]
    assert bs.attach_river_levels(spots, lookup=lambda lat, lon, d, river: reading(next(ages)), workers=1, now=now) == 1
    fresh, old = spots[0]["river_state"], spots[1]["river_state"]
    assert fresh["stale"] is False and fresh["level_m"] == 0.222 and fresh["label"] == "low" and fresh["age_hours"] == 1.0
    assert old["stale"] is True and old["age_hours"] == 77.0 and old["last_level_m"] == 0.222
    assert old["level_m"] is None and old["index"] is None and old["label"] == "unknown"
    assert old["observed_at"] == (now - timedelta(hours=77)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # The rule itself: over 24 h, or no time at all, is not the level now.
    assert reading(25).is_stale(now) and not reading(23).is_stale(now)
    assert RiverState("x", None, 0, 0, 1.0, 0, 1, None).is_stale(now)


def test_the_spots_own_river_picks_the_gauge():
    """spots.csv's river is asked for, not the snapped watercourse: "Afon Gwy" found no Wye gauge."""
    bs = _build_site()
    asked = []
    spots = [{"id": "yat", "name": "Symonds Yat", "lat": 51.844, "lon": -2.642, "river": "River Wye",
              "location": {"watercourse": "Afon Gwy"}}]
    bs.attach_river_levels(spots, lookup=lambda lat, lon, d, river: asked.append(river), workers=1)
    assert asked == ["River Wye"]


def test_qualifiers_do_not_make_two_rivers_the_same():
    assert river_words("River Great Ouse") == {"ouse"}
    assert not same_river("River Great Ouse", "Great Agill Beck")
    assert river_words("Kennet and Avon Canal") == {"kennet", "avon"}   # "and" and "canal" are not names
    assert same_river("East Dart River", "River Dart") and river_words("New River") == {"new"}
    from dipcast.network.names import QUALIFIERS
    from dipcast.overflows import GENERIC
    assert QUALIFIERS <= GENERIC   # one list, shared with the outfall snapper


def test_a_name_borrowed_from_downstream_does_not_count_as_on_the_river():
    """Spitchwick, 2 Oct 2026: the nearest link was an unnamed side stream flowing into the Dart, so
    it showed as "River Dart" with nothing upstream. The hint moves it onto the named river."""
    net = _net(LUNE[:3] + [("T1", None, None, "t0", "n2", [(351000, 461500), (351000, 460000)])])
    lon, lat = PIN
    plain = transport.locate_pin(net, lon, lat, kind_hint="river")
    assert plain.snap.link_id == "T1" and plain.watercourse == "River Lune"   # borrowed from downstream
    assert transport.upstream_overflows(net, plain, OVERFLOWS, velocity_ms=0.5).empty
    pin = transport.locate_pin(net, lon, lat, kind_hint="river", river_hint="River Lune")
    assert pin.snap.link_id == "L3" and pin.placement == "moved to river"
    assert len(transport.upstream_overflows(net, pin, OVERFLOWS, velocity_ms=0.5)) == 1


def test_reading_fields_clear_a_stale_level_for_the_api_too():
    """forecast_point (the API) publishes a gauge reading through the same helper as the site."""
    from dipcast.ingest.flows import RiverState, reading_fields
    now = datetime(2026, 10, 2, 21, 0, tzinfo=UTC)
    st = RiverState(station="Temple Sowerby", river="River Eden", lat=54.65, lon=-2.6, level_m=0.222,
                    typical_low=0.2, typical_high=1.6, observed_at="2026-09-29T16:00:00Z")
    old = reading_fields(st, now)
    assert old["stale"] is True and old["age_hours"] == 77.0 and old["last_level_m"] == 0.222
    assert old["level_m"] is None and old["index"] is None and old["label"] == "unknown"
    st.observed_at = "2026-10-02T20:00:00Z"
    fresh = reading_fields(st, now)
    assert fresh["stale"] is False and fresh["level_m"] == 0.222 and fresh["label"] == "low"
    assert "last_level_m" not in fresh
    assert "reading_fields(state)" in (ROOT / "src" / "dipcast" / "model" / "forecast.py").read_text()


def test_placement_check_flags_a_spot_on_a_canal():
    bs = _build_site()
    r = {"id": "warleigh", "kind": "river", "river": "River Avon", "days": [{}],
         "location": {"mode": "river", "watercourse": "Kennet and Avon Canal", "snap_distance_m": 40, "form": "canal"}}
    (m,) = bs.placement_check([r])
    assert m["reason"] == "snapped to a canal link"
