"""The spot-request bot (scripts/spot_request.py, run by .github/workflows/spot-request.yml): it
reads the issue form, turns the location into a point or asks for coordinates, places the point
on the network as the build places spots.csv rows, and writes one comment. Network-free: the
placing tests use a few links built in memory, in British National Grid metres."""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from dipcast.model import transport
from dipcast.network.names import same_river
from dipcast.network.rivers import RiverNetwork, bng_to_lonlat, lonlat_to_bng

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("spot_request", ROOT / "scripts" / "spot_request.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["spot_request"] = mod   # its dataclasses look their module up by name
    spec.loader.exec_module(mod)
    return mod


sr = _load()

# The body GitHub writes for an issue made with .github/ISSUE_TEMPLATE/spot-request.yml.
FORM = """### Spot name

River Wharfe, Burnsall

### Location

54.04700, -1.95300

### Water body

river

### Anything else

_No response_"""


def issue_event(body: str, number: int = 7, title: str = "Spot request: ") -> dict:
    return {"action": "opened", "issue": {"number": number, "title": title, "state": "open", "body": body,
                                          "labels": [{"name": "spot-request"}]}}


# ------------------------------------------------------------------------------------- reading


def test_reads_the_issue_form():
    got = sr.parse_issue_form(FORM)
    assert got == {"name": "River Wharfe, Burnsall", "location": "54.04700, -1.95300", "kind": "river", "notes": ""}
    req = sr.request_from_event(issue_event(FORM))
    assert (req.name, req.location, req.kind, req.issue, req.missing) == (
        "River Wharfe, Burnsall", "54.04700, -1.95300", "river", 7, [])


def test_an_edited_body_with_windows_line_ends_and_extra_text_still_reads():
    body = ("Hello, a request below.\r\n\r\n### Spot name\r\n\r\nSemerwater\r\n\r\n### Location\r\n\r\n"
            "54.2667, -2.1167\r\nby the boat launch\r\n\r\n### Water body\r\n\r\nLake\r\n\r\n### My own heading\r\n\r\nx")
    req = sr.request_from_event(issue_event(body))
    assert req.name == "Semerwater" and req.kind == "lake"
    assert req.location == "54.2667, -2.1167 by the boat launch"   # one line, whitespace collapsed
    assert sr.parse_location(req.location).point is not None


def test_a_free_text_issue_falls_back_to_the_title_and_lists_what_is_missing():
    req = sr.request_from_event(issue_event("Please add the Wharfe at Burnsall", title="Spot request: Burnsall"))
    assert req.name == "Burnsall" and req.location == "" and req.kind is None
    assert req.missing == ["Location", "Water body"]


def test_reads_a_hand_run_from_the_actions_tab():
    req = sr.request_from_event({"inputs": {"name": "Buttermere", "location": "NY 1760 1560", "kind": "lake",
                                            "notes": "", "issue": "12"}})
    assert (req.name, req.kind, req.issue) == ("Buttermere", "lake", 12)
    assert sr.request_from_event({"inputs": {"name": "x", "location": "y", "kind": "sea", "issue": ""}}).issue is None
    assert sr.request_from_event({"inputs": {"name": "x", "location": "y", "kind": "sea"}}).kind is None


def test_text_from_the_issue_cannot_break_out_of_its_code_span():
    req = sr.request_from_event(issue_event(FORM.replace("River Wharfe, Burnsall", "Wharfe ``` @someone \x07 pool")))
    assert "`" not in req.name and "\x07" not in req.name and req.name == "Wharfe ''' @someone pool"


# ------------------------------------------------------------------------------------- location


@pytest.mark.parametrize(("text", "lat", "lon", "how"), [
    ("54.04700, -1.95300", 54.047, -1.953, "coordinates"),
    ("54.047 -1.953", 54.047, -1.953, "coordinates"),
    ("Below the bridge at 54.047, -1.953.", 54.047, -1.953, "coordinates"),
    ("54.047N, 1.953W", 54.047, -1.953, "coordinates"),
    ("53°55'54.8\"N 1°48'58.2\"W", 53.93189, -1.81617, "coordinates"),
    ("https://www.google.com/maps/place/X/@53.9,-1.8,17z/data=!3m1!4b1!8m2!3d53.93189!4d-1.81618!16s", 53.93189,
     -1.81618, "coordinates"),
    ("https://www.google.com/maps/@54.04700,-1.95300,17z", 54.047, -1.953, "coordinates"),
    ("SE 0754 4598", None, None, "grid reference"),
    ("se07544598", None, None, "grid reference"),
    ("Grid ref SE 07544 45981, by the weir", None, None, "grid reference"),
])
def test_reads_a_point(text, lat, lon, how):
    p = sr.parse_location(text)
    assert p.problem is None and p.point is not None and p.point.how == how
    if lat is not None:
        assert p.point.lat == pytest.approx(lat, abs=1e-5) and p.point.lon == pytest.approx(lon, abs=1e-5)


def test_longitude_first_is_turned_round_and_said():
    p = sr.parse_location("-1.953, 54.047")
    assert (p.point.lat, p.point.lon) == (54.047, -1.953) and "other way round" in p.point.note


def test_a_grid_reference_lands_in_the_middle_of_its_square():
    assert sr.grid_to_bng("SE", "0754", "4598") == (407545.0, 445985.0)
    assert sr.grid_to_bng("SD", "726", "413") == (372650.0, 441350.0)
    assert sr.grid_to_bng("TQ", "30080", "80972") == (530080.5, 180972.5)
    assert sr.grid_to_bng("NY", "1760", "1560") == (317605.0, 515605.0)
    assert sr.grid_to_bng("HP", "000", "000") == (400050.0, 1200050.0)
    p = sr.parse_location("SD 7262 4130").point
    x, y = lonlat_to_bng(p.lon, p.lat)
    assert x == pytest.approx(372625, abs=1) and y == pytest.approx(441305, abs=1)


@pytest.mark.parametrize(("text", "says"), [
    ("", "The Location field is empty."),
    ("54.0, -1.9", "only precise to about a kilometre"),
    ("54°2'N 1°57'W", "only precise to about a kilometre"),
    ("SE 07 45", "only precise to a kilometre square"),
    ("48.85837, 2.29448", "outside Great Britain"),
    ("https://maps.app.goo.gl/AbCdEf123", "share link does not include the coordinates"),
])
def test_a_location_that_cannot_be_used_says_why(text, says):
    p = sr.parse_location(text)
    assert p.point is None and says in p.problem


@pytest.mark.parametrize(("text", "words"), [
    ("///filled.count.soap", "filled.count.soap"),
    ("what3words: Filled.Count.Soap", "filled.count.soap"),
    ("https://w3w.co/filled.count.soap", "filled.count.soap"),
    ("filled.count.soap", "filled.count.soap"),
])
def test_recognises_a_what3words_address(text, words):
    assert sr.parse_location(text).what3words == words


@pytest.mark.parametrize("text", ["the beach below the stepping stones", "www.example.co.uk", "on 12 34 the left"])
def test_anything_else_is_a_description(text):
    p = sr.parse_location(text)
    assert p.point is None and p.what3words is None and p.problem is None and p.text == text


class Fake:
    """A stand-in for an HTTP response."""

    def __init__(self, status: int, body):
        self.status_code, self.body = status, body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def test_no_key_means_a_request_for_coordinates_and_no_lookup():
    def never(url, params):
        raise AssertionError("looked up without a key")
    w = sr.geocode(sr.parse_location("///filled.count.soap"), env={}, get=never)
    assert w.point is None and "no what3words key" in w.problem
    t = sr.geocode(sr.parse_location("by the old mill"), env={}, get=never)
    assert t.point is None and "no place-name lookup" in t.problem


def test_what3words_with_a_key():
    seen = []

    def get(url, params):
        seen.append((url, params))
        return Fake(200, {"coordinates": {"lat": 54.0471, "lng": -1.9532}, "words": "filled.count.soap"})
    p = sr.geocode(sr.parse_location("///filled.count.soap"), env={"W3W_API_KEY": "k"}, get=get)
    assert (p.point.lat, p.point.lon, p.point.how, p.point.shown) == (54.0471, -1.9532, "what3words", "///filled.count.soap")
    assert seen == [(sr.W3W_URL, {"words": "filled.count.soap", "key": "k"})]
    env = {"W3W_API_KEY": "k"}
    bad = sr.geocode(sr.parse_location("///not.real.words"), env=env,
                     get=lambda u, q: Fake(400, {"error": {"code": "BadWords", "message": "Invalid"}}))
    assert "did not recognise `///not.real.words`" in bad.problem
    # A bad key or a spent quota is the lookup's fault, not the address's.
    key = sr.geocode(sr.parse_location("///aa.bb.cc"), env=env, get=lambda u, q: Fake(401, {"error": {"code": "InvalidKey"}}))
    down = sr.geocode(sr.parse_location("///aa.bb.cc"), env=env, get=lambda u, q: Fake(502, ValueError("not JSON")))
    assert "did not answer" in key.problem and "did not answer" in down.problem


def test_a_place_name_with_a_key_is_approximate():
    x, y = 407500.0, 454500.0
    entry = {"NAME1": "Bolton Abbey", "LOCAL_TYPE": "Village", "COUNTY_UNITARY": "North Yorkshire",
             "GEOMETRY_X": x, "GEOMETRY_Y": y}
    p = sr.geocode(sr.parse_location("Bolton Abbey"), env={"OS_API_KEY": "k"},
                   get=lambda u, q: Fake(200, {"header": {}, "results": [{"GAZETTEER_ENTRY": entry}]}))
    lon, lat = bng_to_lonlat(x, y)
    assert p.point.approximate and p.point.how == "place name"
    assert (p.point.lat, p.point.lon) == pytest.approx((lat, lon), abs=1e-6)
    assert "`Bolton Abbey, North Yorkshire`" in p.point.note
    none = sr.geocode(sr.parse_location("Nowhere at all"), env={"OS_API_KEY": "k"},
                      get=lambda u, q: Fake(200, {"header": {"totalresults": 0}}))
    assert "No place called `Nowhere at all`" in none.problem


# ------------------------------------------------------------------------------------- names and rows


@pytest.mark.parametrize(("name", "river"), [
    ("River Wharfe, Burnsall", "River Wharfe"),
    ("Sheep's Green, River Cam", "River Cam"),
    ("River Teme in Ludlow", "River Teme"),
    ("River Ouse (Sussex), Barcombe Mills", "River Ouse"),
    ("river great ouse at houghton", "river great ouse"),
    ("Janet's Foss, Gordale Beck, Malham", "Gordale Beck"),
    ("Wharfe at Cromwheel, Ilkley", "Wharfe"),
    ("Swale above Richmond Falls", "Swale"),
    ("Sandy Lane, Chester", None),
    ("Buttermere", None),
])
def test_the_river_a_name_gives(name, river):
    assert sr.river_from_name(name) == river


def test_most_names_in_spots_csv_give_their_own_river():
    """Measured on spots.csv as of 3 Oct 2026: 56 of 58 river spots' names give the river in the
    `river` column. The two that do not are "Sandy Lane, Chester" (no river in the name) and
    "Wolvercote Mill Stream" (a side channel of the Thames)."""
    with open(ROOT / "spots.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["kind"] == "river"]
    misses = [r["name"] for r in rows if not same_river(sr.river_from_name(r["name"]), r["river"])]
    assert len(misses) <= 0.1 * len(rows), misses


def test_a_new_id_follows_the_curated_ones_and_never_repeats():
    taken = {"wharfe-bolton-abbey"}
    assert sr.spot_id("River Wharfe, Bolton Abbey (Cavendish Pavilion)", set()) == "wharfe-bolton-abbey"
    assert sr.spot_id("River Wharfe, Bolton Abbey", taken) == "wharfe-bolton-abbey-2"
    assert sr.spot_id("Llyn Tegid – Afon Dyfrdwy at Llanuwchllyn", set()) == "llyn-tegid-dyfrdwy-llanuwchllyn"
    assert sr.spot_id("!!!", set()) == "spot"
    assert re.fullmatch(r"[a-z0-9-]+", sr.spot_id("Ü bèr, 'Q' ✓ (x)", set()))


def test_the_row_is_one_valid_spots_csv_line():
    p = sr.Point(54.047, -1.953, "coordinates")
    row = sr.csv_row("wharfe-burnsall", 'River Wharfe, "the" beach', p, "river", "River Wharfe")
    header = (ROOT / "spots.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    parsed = next(csv.reader([row]))
    assert dict(zip(header, parsed, strict=True)) == {
        "id": "wharfe-burnsall", "name": 'River Wharfe, "the" beach', "lat": "54.04700", "lon": "-1.95300",
        "kind": "river", "river": "River Wharfe", "source": "curated", "notes": ""}


# ------------------------------------------------------------------------------------- placing and the comment


# The Lune runs east along y = 460000; Escow Beck comes down from the north and joins it at x = 351000.
LUNE = [
    ("L1", "River Lune", None, "n0", "n1", [(348000, 460000), (350000, 460000)]),
    ("L2", "River Lune", None, "n1", "n2", [(350000, 460000), (351000, 460000)]),
    ("L3", "River Lune", None, "n2", "n3", [(351000, 460000), (352000, 460000)]),
    ("B1", "Escow Beck", None, "b0", "n2", [(351000, 461500), (351000, 460000)]),
]
OVERFLOWS = pd.DataFrame({"site_id": ["o1"], "link_id": ["L1"], "frac": [0.5], "link_length": [2000.0],
                          "start_node": ["n0"], "has_live": [True], "status": [0]})


@pytest.fixture(scope="module")
def net() -> RiverNetwork:
    gdf = gpd.GeoDataFrame(
        [{"id": i, "flow_direction": "in direction", "form": "inlandRiver", "fictitious": False,
          "watercourse_name": n, "watercourse_name_alternative": alt, "start_node": s, "end_node": e,
          "geometry": LineString(xy)} for i, n, alt, s, e, xy in LUNE], crs=27700)
    return RiverNetwork.from_links(gdf)


@pytest.fixture(scope="module")
def check():
    return sr.build_placement_check()


@pytest.fixture(autouse=True)
def _no_lake_polygons(monkeypatch):
    monkeypatch.setattr(transport, "_lake_polygon_at", lambda x, y: None)


def ask(net, check, name, x, y, kind="river", spots=()):
    lon, lat = bng_to_lonlat(x, y)
    req = sr.request_from_event({"inputs": {"name": name, "location": f"{lat:.6f}, {lon:.6f}", "kind": kind}})
    return sr.answer(req, env={}, net=net, overflows=OVERFLOWS, spots=list(spots), check=check)


def test_a_spot_on_the_named_river_gets_the_full_answer(net, check):
    text, ok = ask(net, check, "River Lune, Crook o' Lune", 351100, 460600,
                   spots=[{"id": "lune-crook-o-lune", "name": "River Lune, Crook o' Lune, Caton",
                           "lat": "54.0", "lon": "-2.0"}])
    assert ok and text.startswith(sr.MARKER + "\n")
    assert "- **Water:** The River Lune, 600 m from the point." in text
    assert "- **Nearest water:** Escow Beck, 100 m away. The spot's name says the River Lune, so it goes there instead." in text
    # 1 km of L1 below the overflow, 1 km of L2, 100 m of L3 down to the pin.
    assert "- **Storm overflows upstream:** 1 monitored within 60 km upstream, 2.1 km away (with a live feed)." in text
    assert "- **Placement check:** Needs a look: 600 m from the river network." in text   # the build's 250 m rule
    row = re.search(r"```csv\n(.+)\n```", text).group(1)
    assert next(csv.reader([row]))[:2] == ["lune-crook-o-lune-2", "River Lune, Crook o' Lune"]
    assert next(csv.reader([row]))[4:7] == ["river", "River Lune", "curated"]
    assert "Already listed" not in text   # the listed spot is far away
    assert "Crown copyright" in text and "https://swimsignal.co.uk/terms.html#data" in text   # the data's credits


def test_a_spot_close_to_its_river_passes_the_check(net, check):
    text, ok = ask(net, check, "Lune at the old ford", 349000, 460040)
    assert ok and "The River Lune, 40 m from the point." in text
    assert "Placement check:** Passes, the same check the build runs on every river spot." in text
    assert "Nearest water" not in text


def test_a_name_that_names_no_river_takes_the_river_from_the_map(net, check):
    text, _ = ask(net, check, "Sandy Lane, Caton", 349000, 460040)
    assert "Passes on distance and the kind of water. The name does not say which river" in text
    assert ",river,River Lune,curated," in text


def test_a_spot_said_to_be_on_another_river_is_flagged(net, check):
    text, _ = ask(net, check, "River Dart, Escow", 351050, 461300)
    assert "Placement check:** Needs a look: snapped to Escow Beck, not the River Dart." in text
    assert ",river,River Dart,curated," in text


def test_a_lake_with_no_river_in_is_said_plainly(net, check):
    text, ok = ask(net, check, "Hidden Tarn", 349000, 461000, kind="lake")
    assert ok and "A lake with no river flowing in on the map. Storm overflows cannot reach it by water" in text
    assert "Storm overflows upstream" not in text and "Lakes are not checked automatically" in text
    assert ",lake,,curated," in text


def test_no_water_within_reach_asks_for_coordinates(net, check):
    """With a river named and no link within 1 km, locate_pin's name match met an empty string column
    and raised (3 Oct 2026, on the real network too); the bot answered "could not run"."""
    text, ok = ask(net, check, "River Lune, far off", 340000, 470000)
    assert ok and "There is no river or lake on SwimSignal's map within 1.5 km of" in text
    assert sr.HOW_TO in text and "```csv" not in text


def test_a_bad_location_is_a_polite_question_not_a_crash(net, check):
    req = sr.request_from_event(issue_event(FORM.replace("54.04700, -1.95300", "the field behind the pub")))
    text, ok = sr.answer(req, env={}, net=net, overflows=OVERFLOWS, spots=[], check=check)
    assert ok and text.startswith(sr.MARKER)
    assert "Thanks for the request. The spot could not be placed on the map yet." in text
    assert "Please edit this issue and put the spot's latitude and longitude in the Location field" in text
    assert "```csv" not in text and "Traceback" not in text


def test_an_internal_failure_still_answers_and_reports_it(net, check):
    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("network file is corrupt")
    req = sr.request_from_event(issue_event(FORM))
    text, ok = sr.answer(req, env={}, net=Broken(), overflows=OVERFLOWS, spots=[], check=check)
    assert not ok and "a maintainer will place the spot by hand" in text and "corrupt" not in text


def test_a_place_name_point_gets_no_row(net, check):
    lon, lat = bng_to_lonlat(349000, 460040)
    entry = {"NAME1": "Caton", "COUNTY_UNITARY": "Lancashire", "GEOMETRY_X": 349000.0, "GEOMETRY_Y": 460040.0}
    req = sr.request_from_event({"inputs": {"name": "River Lune, Caton", "location": "Caton", "kind": "river"}})
    text, _ = sr.answer(req, env={"OS_API_KEY": "k"}, get=lambda u, q: Fake(200, {"results": [{"GAZETTEER_ENTRY": entry}]}),
                        net=net, overflows=OVERFLOWS, spots=[], check=check)
    assert f"**Point:** {lat:.5f}, {lon:.5f}, from the place name given." in text
    assert "no `spots.csv` row yet" in text and "```csv" not in text


def test_the_command_line_writes_the_answer(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(issue_event(FORM.replace("54.04700, -1.95300", "SE 07 45"))))
    out = tmp_path / "comment.md"
    assert sr.main(["--event", str(event), "--out", str(out)]) == 0
    assert "only precise to a kilometre square" in out.read_text()


# ------------------------------------------------------------------------------------- the workflow and the form


def test_the_form_labels_are_the_ones_the_parser_reads():
    yaml = pytest.importorskip("yaml")
    form = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "spot-request.yml").read_text())
    fields = {b["id"]: b["attributes"]["label"] for b in form["body"] if b["type"] != "markdown"}
    assert {sr.FIELDS[label.lower()] for label in fields.values()} == set(fields)
    assert form["labels"] == ["spot-request"]


def test_the_workflow_asks_for_little_and_never_puts_issue_text_in_a_shell_line():
    yaml = pytest.importorskip("yaml")
    text = (ROOT / ".github" / "workflows" / "spot-request.yml").read_text()
    wf = yaml.safe_load(text)
    on = wf[True]   # YAML 1.1 reads the key `on` as true
    assert on["issues"]["types"] == ["opened", "edited"]
    assert set(on["workflow_dispatch"]["inputs"]) == {"name", "location", "kind", "notes", "issue"}
    assert wf["permissions"] == {"contents": "read", "issues": "write"}
    assert all("permissions" not in job for job in wf["jobs"].values())
    assert "spot-request" in wf["jobs"]["answer"]["if"]
    # Free text from the issue or the run form only ever reaches the script through the event file.
    for expr in re.findall(r"\$\{\{(.*?)\}\}", text):
        assert not re.search(r"issue\.(body|title)|inputs\.(name|location|kind|notes)|comment", expr), expr
    assert sr.MARKER in text   # the post step finds the earlier answer by the script's marker
