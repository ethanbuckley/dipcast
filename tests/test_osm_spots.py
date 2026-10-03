"""Spots from OpenStreetMap: kept in spots-osm.csv, apart from spots.csv, because the ODbL would
cover any file that mixed the two (LICENSE-DATA.md). The build reads both, checks both and credits
OpenStreetMap; the candidate script's rules drop pools, signs and the sea. Network-free."""

from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path

import pandas as pd

from dipcast.network.names import river_words

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_spots_osm_csv_is_a_separate_file_with_no_key_into_spots_csv():
    ours = pd.read_csv(ROOT / "spots.csv").fillna("")
    osm = pd.read_csv(ROOT / "spots-osm.csv").fillna("")
    assert list(osm.columns) == list(ours.columns) + ["osm_id"]   # the same columns plus osm_id, nothing joining them
    assert "osm_id" not in ours.columns
    assert osm["id"].str.startswith("osm-").all() and osm["id"].is_unique
    assert not set(osm["id"]) & set(ours["id"])
    assert osm["id"].map(lambda i: bool(re.fullmatch(r"[A-Za-z0-9_-]+", i))).all()   # each gets a page of its own
    assert (osm["source"] == "openstreetmap").all() and "openstreetmap" not in set(ours["source"])
    assert osm["osm_id"].str.fullmatch(r"(node|way|relation)/\d+").all() and osm["osm_id"].is_unique
    rivers = osm[osm["kind"] == "river"]
    assert (rivers["river"] != "").all() and rivers["river"].map(lambda r: bool(river_words(r))).all()
    assert (osm.loc[osm["kind"] == "lake", "river"] == "").all() and set(osm["kind"]) <= {"river", "lake"}


def test_every_osm_spot_comes_from_a_reviewed_candidate_at_its_position():
    osm = pd.read_csv(ROOT / "spots-osm.csv").fillna("")
    cand = pd.read_csv(ROOT / "data" / "raw" / "osm_swim_candidates.csv").fillna("").set_index("osm_id")
    for r in osm.itertuples():
        c = cand.loc[r.osm_id]
        assert (round(c["lat"], 5), round(c["lon"], 5)) == (round(r.lat, 5), round(r.lon, 5)), r.id
        assert c["listed_as"] == r.id and c["inland"]


def test_the_build_reads_both_files_and_never_lets_two_spots_share_an_id(tmp_path, caplog):
    bs = _script("build_site")
    spots = bs.load_spots()
    n_ours = len(pd.read_csv(ROOT / "spots.csv"))
    n_osm = len(pd.read_csv(ROOT / "spots-osm.csv"))
    assert len(spots) == n_ours + n_osm and spots["id"].is_unique
    osm = spots[spots["source"] == "openstreetmap"]
    assert len(osm) == n_osm and (osm["osm_id"] != "").all()
    assert (spots.loc[spots["source"] != "openstreetmap", "osm_id"] == "").all()

    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("id,name,lat,lon,kind,river,source,notes\nx,X,54.0,-2.0,lake,,curated,\n")
    b.write_text("id,name,lat,lon,kind,river,source,notes,osm_id\nx,Y,53.0,-1.0,lake,,openstreetmap,,node/1\n"
                 "osm-y,Y,53.0,-1.0,lake,,openstreetmap,,node/2\n")
    with caplog.at_level(logging.WARNING):
        both = bs.load_spots([a, b, tmp_path / "missing.csv"])
    assert list(both["id"]) == ["x", "osm-y"] and both.loc[0, "name"] == "X"
    assert "'x' is used twice" in caplog.text


def test_openstreetmap_is_credited_in_the_data_and_on_the_terms_page():
    bs = _script("build_site")
    c = bs.data_credits("https://example.org/")
    line = "Swim spot locations from OpenStreetMap, © OpenStreetMap contributors, ODbL 1.0"
    assert c["spot_locations"].startswith(line) and "https://www.openstreetmap.org/copyright" in c["spot_locations"]
    assert c["licences"]["ODbL 1.0"] == "https://opendatacommons.org/licenses/odbl/1-0/"
    terms = (ROOT / "src" / "dipcast" / "api" / "static" / "terms.html").read_text()
    assert line in re.sub(r"<[^>]+>", "", terms)
    assert '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>, <a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL 1.0</a>' in terms
    note = (ROOT / "LICENSE-DATA.md").read_text()
    assert "spots-osm.csv" in note and "Open Database Licence" in note and "spots.csv is not under the ODbL" in note
    # Each OSM spot's page carries the notice in its notes line.
    osm = pd.read_csv(ROOT / "spots-osm.csv").fillna("")
    assert (osm["notes"] == "Location © OpenStreetMap contributors").all()


def test_placement_check_names_the_file_a_spot_came_from():
    bs = _script("build_site")
    loc = {"mode": "river", "watercourse": "River Dart", "snap_distance_m": 9, "form": "inlandRiver"}
    spots = [{"id": "osm-x", "kind": "river", "river": None, "source": "openstreetmap", "location": loc, "days": [{}]},
             {"id": "y", "kind": "river", "river": None, "source": "curated", "location": loc, "days": [{}]}]
    reasons = {m["id"]: m["reason"] for m in bs.placement_check(spots)}
    assert reasons == {"osm-x": "no river named in spots-osm.csv", "y": "no river named in spots.csv"}
    assert "(check spots.csv and spots-osm.csv)" in bs.build_health(spots)["warnings"][0]


def test_the_candidate_rules_drop_pools_signs_and_the_sea_and_keep_open_water():
    osc = _script("osm_spot_candidates")
    drop = osc.dropped_by
    assert drop({"leisure": "swimming_pool", "sport": "swimming"}) == "tagged leisure=swimming_pool"
    assert drop({"name": "Swimming Pool", "highway": "bus_stop"}) == "tagged highway=bus_stop"
    assert drop({"name": "Bathing Place Lane"}) == "a name only"
    assert drop({"name": "Swimming pool", "natural": "water", "water": "pond"}) == "a pool by its name or tags"
    assert drop({"name": "Bathing pool (disused)", "natural": "water"}) == "disused"
    assert drop({"leisure": "swimming_area", "tidal": "yes"}) == "tidal or sea"
    assert drop({"leisure": "swimming_area", "name": "Trevone Bay Sea Pool"}) == "tidal or sea"
    # Kept for a person to read: a swim area (even one called a lido), open water with sport=swimming,
    # and a private one (the review drops it, with the reason in view).
    assert drop({"leisure": "swimming_area", "name": "Serpentine Lido", "fee": "yes"}) is None
    assert drop({"sport": "swimming", "natural": "water", "water": "stream_pool", "name": "Mel Pool"}) is None
    assert drop({"leisure": "bathing_place", "access": "private"}) is None


def test_candidates_within_150_m_are_one_and_the_one_with_most_to_say_is_kept():
    osc = _script("osm_spot_candidates")
    df = pd.DataFrame([
        {"osm_id": "node/1", "lat": 52.0, "lon": -1.0, "tags": {"sport": "swimming"}},
        {"osm_id": "way/2", "lat": 52.0009, "lon": -1.0, "tags": {"leisure": "bathing_place", "name": "A"}},   # 100 m north
        {"osm_id": "node/3", "lat": 52.0027, "lon": -1.0, "tags": {"leisure": "bathing_place"}},   # 200 m from way/2
    ])
    out = osc.dedupe(df).set_index("osm_id")
    assert sorted(out.index) == ["node/3", "way/2"] and out.loc["way/2", "duplicates"] == "node/1"
