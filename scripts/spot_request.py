"""Answer a spot request: read the "Request a swim spot" issue form, place the spot on the river
network the way the build places spots.csv rows, and write one comment for the issue.

Run by .github/workflows/spot-request.yml on an issue with the spot-request label, or by hand
from the Actions tab (workflow_dispatch, the same fields as the form). Locally:

    uv run python scripts/spot_request.py --name "River Wharfe, Burnsall" --location "54.047, -1.953" --kind river

The location may be latitude and longitude (decimal or degrees-minutes-seconds, either order, a
Google Maps address with them in it) or an OS grid reference. A what3words address or a place
name is looked up only when a key is set (W3W_API_KEY, OS_API_KEY); without one, or when the
lookup finds nothing, the comment asks the requester for coordinates instead.

Untrusted input: the issue body is read from the event file and never reaches a shell. What the
comment repeats of it sits in code spans or the CSV block, so it cannot mention anyone.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("spot_request")

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- swimsignal-spot-request -->"   # the workflow updates the comment that starts with this
FIELDS = {"spot name": "name", "location": "location", "water body": "kind", "anything else": "notes"}
NO_RESPONSE = "_No response_"
MAX_FIELD = 1000           # characters kept of any one field (a Google Maps address can be long)
MIN_DECIMALS = 3           # 0.001 degrees is about 110 m of latitude: the form asks for about 100 m
MIN_GRID_DIGITS = 3        # per axis: 100 m squares
NEARBY_M = 300.0           # an existing spot this close is probably the same place
GB = {"lat": (49.8, 60.95), "lon": (-8.7, 1.8)}
W3W_URL = "https://api.what3words.com/v3/convert-to-coordinates"
OS_NAMES_URL = "https://api.os.uk/search/names/v1/find"
USER_AGENT = "SwimSignal spot-request bot (github.com/ethanbuckley/swimsignal)"
EXAMPLE = "`54.04700, -1.95300`"
HOW_TO = ("Please edit this issue and put the spot's latitude and longitude in the Location field, for example "
          f"{EXAMPLE}. In Google Maps, right-click the spot and click the two numbers at the top of the menu to "
          "copy them. The check runs again when the issue is edited.")


# ------------------------------------------------------------------------------------- reading


@dataclass
class Request:
    name: str = ""
    location: str = ""
    kind: str | None = None      # "river", "lake", or None when the form gave neither
    notes: str = ""
    issue: int | None = None
    missing: list[str] = field(default_factory=list)   # form fields not found or empty


def _clean(text: str | None) -> str:
    """One line of plain text: control characters and backticks out, whitespace collapsed, cut short."""
    text = "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(text or ""))
    return re.sub(r"\s+", " ", text.replace("`", "'")).strip()[:MAX_FIELD]


def parse_issue_form(body: str | None) -> dict[str, str]:
    """The answers in an issue created from the form, keyed by field id. GitHub writes each answer
    under a "### <label>" heading and writes "_No response_" for an optional field left empty."""
    out: dict[str, str] = {}
    parts = re.split(r"^###[ \t]+(.+?)[ \t]*$", (body or "").replace("\r\n", "\n"), flags=re.MULTILINE)
    for label, answer in zip(parts[1::2], parts[2::2], strict=True):
        key = FIELDS.get(label.strip().lower())
        if key and key not in out:
            answer = answer.strip()
            out[key] = "" if answer == NO_RESPONSE else answer
    return out


def _kind(text: str | None) -> str | None:
    t = (text or "").strip().lower()
    return t if t in ("river", "lake") else None


def request_from_event(event: dict) -> Request:
    """A request from a GitHub event: an issue (opened or edited) or a workflow_dispatch's inputs."""
    if "issue" in event:
        issue = event["issue"]
        got = parse_issue_form(issue.get("body"))
        name = got.get("name") or re.sub(r"^\s*spot request:\s*", "", issue.get("title") or "", flags=re.IGNORECASE)
        number = issue.get("number")
    else:
        got = event.get("inputs") or {}
        name, number = got.get("name"), got.get("issue")
    req = Request(name=_clean(name), location=_clean(got.get("location")), kind=_kind(got.get("kind")),
                  notes=_clean(got.get("notes")), issue=int(number) if str(number or "").strip().isdigit() else None)
    req.missing = [label for label, key in (("Spot name", "name"), ("Location", "location"), ("Water body", "kind"))
                   if not getattr(req, key)]
    return req


# ------------------------------------------------------------------------------------- location


@dataclass
class Point:
    lat: float
    lon: float
    how: str                     # "coordinates", "grid reference", "what3words", "place name"
    shown: str = ""              # what the point was read from, as the comment repeats it
    note: str | None = None      # one more sentence about how it was read
    approximate: bool = False    # a place name's point, not the spot's own


@dataclass
class Parsed:
    point: Point | None = None
    what3words: str | None = None
    text: str | None = None      # a description to look up by name
    problem: str | None = None   # why the location cannot be used as written, as a sentence


def _in_gb(lat: float, lon: float) -> bool:
    return GB["lat"][0] <= lat <= GB["lat"][1] and GB["lon"][0] <= lon <= GB["lon"][1]


def _decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0


def _code(text: str) -> str:
    return "`" + _clean(text)[:80] + "`"


def _pair(a: float, b: float, shown: str, coarse: bool, how: str = "coordinates") -> Parsed:
    """A latitude and longitude in either order, checked against Great Britain."""
    note = None
    if not _in_gb(a, b) and _in_gb(b, a):
        a, b = b, a
        note = "The two numbers were the other way round (longitude first); they are read here as latitude, longitude."
    if not _in_gb(a, b):
        return Parsed(problem=f"The point {_code(shown)} is outside Great Britain.")
    if coarse:
        return Parsed(problem=f"The coordinates {_code(shown)} are only precise to about a kilometre.")
    return Parsed(point=Point(round(a, 6), round(b, 6), how, shown=shown, note=note))


DEC = r"[-+−]?\d{1,3}(?:\.\d+)?"
HEMI_PAIR = re.compile(rf"({DEC})\s*°?\s*([NSns])\s*[,;/]?\s*({DEC})\s*°?\s*([EWew])\b")
DEC_PAIR = re.compile(rf"(?<![\w.])({DEC})\s*°?\s*[,;/ ]\s*({DEC})\s*°?(?!\w|\.\d)")
GMAPS_PLACE = re.compile(rf"!3d({DEC})!4d({DEC})")
GMAPS_AT = re.compile(rf"@({DEC}),({DEC})")
DMS = re.compile(r"(\d{1,3})\s*°\s*(\d{1,2})\s*['′’]\s*(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|''|’’|”)\s*)?([NSns])"
                 r"[\s,;]*(\d{1,3})\s*°\s*(\d{1,2})\s*['′’]\s*(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|''|’’|”)\s*)?([EWew])")
GRID = re.compile(r"(?<![A-Za-z0-9])([HNOSThnost][A-HJ-Za-hj-z])\s*(\d{2,5})\s+(\d{2,5})(?!\d)|"
                  r"(?<![A-Za-z0-9])([HNOSThnost][A-HJ-Za-hj-z])\s*(\d{4}|\d{6}|\d{8}|\d{10})(?!\d)")
W3W = re.compile(r"(?:^|\s|///|w3w\.co/|what3words\.com/)(?:///)?([a-z]{2,}\.[a-z]{2,}\.[a-z]{2,})(?=$|[\s,.;)])",
                 re.IGNORECASE)
SHORT_LINK = re.compile(r"\b(?:maps\.app\.goo\.gl|goo\.gl/maps)/", re.IGNORECASE)
DOMAINS = {"com", "uk", "org", "net", "gov", "io", "app", "html", "php", "co"}


def _num(s: str) -> float:
    return float(s.replace("−", "-"))


def grid_to_bng(letters: str, east: str, north: str) -> tuple[float, float]:
    """An OS grid reference to British National Grid metres, at the centre of its square."""
    l1, l2 = (ord(c) - ord("A") for c in letters.upper())
    l1, l2 = l1 - (l1 > 7), l2 - (l2 > 7)   # the grid has no I
    e100 = ((l1 - 2) % 5) * 5 + l2 % 5
    n100 = 19 - (l1 // 5) * 5 - l2 // 5
    digits = len(east)
    unit = 10 ** (5 - digits)
    return e100 * 100_000 + int(east) * unit + unit / 2, n100 * 100_000 + int(north) * unit + unit / 2


def parse_location(text: str | None) -> Parsed:
    """Read a point from the form's Location answer, or say what to look up, or why it cannot be used."""
    t = _clean(text)
    if not t:
        return Parsed(problem="The Location field is empty.")
    if m := GMAPS_PLACE.search(t) or GMAPS_AT.search(t):
        a, b = m.group(1), m.group(2)
        return _pair(_num(a), _num(b), f"{a}, {b}", min(_decimals(a), _decimals(b)) < MIN_DECIMALS)
    if m := DMS.search(t):
        d1, m1, s1, h1, d2, m2, s2, h2 = m.groups()
        lat = (int(d1) + int(m1) / 60 + float(s1 or 0) / 3600) * (-1 if h1.upper() == "S" else 1)
        lon = (int(d2) + int(m2) / 60 + float(s2 or 0) / 3600) * (-1 if h2.upper() == "W" else 1)
        return _pair(lat, lon, m.group(0), s1 is None or s2 is None)   # whole minutes are about 1.8 km
    if m := HEMI_PAIR.search(t):
        a, ha, b, hb = m.groups()
        lat = abs(_num(a)) * (-1 if ha.upper() == "S" else 1)
        lon = abs(_num(b)) * (-1 if hb.upper() == "W" else 1)
        return _pair(lat, lon, m.group(0), min(_decimals(a), _decimals(b)) < MIN_DECIMALS)
    for m in DEC_PAIR.finditer(t):
        a, b = m.groups()
        if "." in a or "." in b:   # two whole numbers in a description are not coordinates
            return _pair(_num(a), _num(b), m.group(0), min(_decimals(a), _decimals(b)) < MIN_DECIMALS)
    if m := GRID.search(t):
        if m.group(1):
            letters, east, north = m.group(1), m.group(2), m.group(3)
        else:
            letters, digits = m.group(4), m.group(5)
            east, north = digits[: len(digits) // 2], digits[len(digits) // 2:]
        shown = f"{letters.upper()} {east} {north}"
        if len(east) == len(north):
            if len(east) < MIN_GRID_DIGITS:
                if letters.isupper():   # "on 12 34" in a description is not a grid reference
                    return Parsed(problem=f"The grid reference {_code(shown)} is only precise to a kilometre square.")
                return Parsed(text=t)
            from dipcast.network.rivers import bng_to_lonlat
            lon, lat = bng_to_lonlat(*grid_to_bng(letters, east, north))
            if _in_gb(lat, lon):
                return Parsed(point=Point(round(lat, 6), round(lon, 6), "grid reference", shown=shown))
    if SHORT_LINK.search(t):
        return Parsed(problem="A Google Maps share link does not include the coordinates.")
    if (m := W3W.search(t)) and ("///" in t or "w3w" in t.lower() or "what3words" in t.lower()
                                 or m.group(1).rsplit(".", 1)[-1].lower() not in DOMAINS):
        return Parsed(what3words=m.group(1).lower())
    return Parsed(text=t)


def _lookup(get, url: str, params: dict, what: str) -> tuple[int, dict] | None:
    """(HTTP status, JSON body) from a lookup, or None when it did not answer with JSON."""
    try:
        r = get(url, params)
        body = r.json()
    except Exception as e:  # noqa: BLE001 - a lookup that fails becomes a question for the requester
        log.warning("%s lookup failed: %s", what, e)
        return None
    return int(getattr(r, "status_code", 200)), (body if isinstance(body, dict) else {})


def geocode(parsed: Parsed, env: dict | None = None, get=None) -> Parsed:
    """Turn a what3words address or a description into a point with the lookup whose key is set.
    Without a key, or when the lookup finds nothing, the result carries a problem instead. `get`
    is (url, params) -> a response with .status_code and .json(); tests pass a fake."""
    env = os.environ if env is None else env
    if parsed.point or parsed.problem:
        return parsed
    if get is None:
        import httpx

        def get(url: str, params: dict):
            return httpx.get(url, params=params, timeout=15, headers={"User-Agent": USER_AGENT})
    if parsed.what3words:
        shown = _code("///" + parsed.what3words)
        key = (env.get("W3W_API_KEY") or "").strip()
        if not key:
            return Parsed(problem=f"SwimSignal cannot read what3words addresses such as {shown} (it has no "
                                  "what3words key).")
        got = _lookup(get, W3W_URL, {"words": parsed.what3words, "key": key}, "what3words")
        status, body = got or (0, {})
        c = body.get("coordinates") or {}
        if status == 200 and "lat" in c and "lng" in c:
            return _pair(float(c["lat"]), float(c["lng"]), "///" + parsed.what3words, coarse=False, how="what3words")
        code = str((body.get("error") or {}).get("code") or "")
        if code.startswith("Bad") or code == "MissingWords":   # the address itself, not the key or the quota
            return Parsed(problem=f"what3words did not recognise {shown}.")
        log.warning("what3words lookup: HTTP %s %s", status, code or "(no error code)")
        return Parsed(problem=f"The what3words lookup for {shown} did not answer.")
    text = parsed.text or ""
    key = (env.get("OS_API_KEY") or "").strip()
    if not key:
        return Parsed(problem="The location is a description, and SwimSignal has no place-name lookup to turn it "
                              "into a point.")
    got = _lookup(get, OS_NAMES_URL, {"query": text[:100], "maxresults": 1, "key": key}, "OS Names")
    if got is None or got[0] != 200:
        if got:
            log.warning("OS Names lookup: HTTP %s", got[0])
        return Parsed(problem="The place-name lookup did not answer.")
    entry = ((got[1].get("results") or [{}])[0] or {}).get("GAZETTEER_ENTRY") or {}
    if "GEOMETRY_X" not in entry or "GEOMETRY_Y" not in entry:
        return Parsed(problem=f"No place called {_code(text)} was found in OS Open Names.")
    from dipcast.network.rivers import bng_to_lonlat
    lon, lat = bng_to_lonlat(float(entry["GEOMETRY_X"]), float(entry["GEOMETRY_Y"]))   # OS Names gives BNG metres
    place = ", ".join(_clean(entry.get(k)) for k in ("NAME1", "COUNTY_UNITARY") if entry.get(k))
    out = _pair(lat, lon, text, coarse=False, how="place name")
    if out.point:
        out.point.approximate = True
        out.point.note = (f"It is the point OS Open Names gives for {_code(place or text)}, the middle of a named "
                          "place, which may be some way from where you swim.")
    return out


# ------------------------------------------------------------------------------------- placing


WATER_WORDS = r"beck|brook|burn|stream|gill|ghyll|river|water|afon|cut|leat"
SPLIT = re.compile(r"\s*(?:[,;()]|\s[-–—]\s|\b(?:at|in|near|above|below|by|upstream of|downstream of|between)\b)\s*",
                   re.IGNORECASE)


def river_from_name(name: str | None) -> str | None:
    """The river a requester's spot name says it is on: "River Wharfe, Burnsall" gives "River Wharfe",
    "Janet's Foss, Gordale Beck" gives "Gordale Beck", "Wharfe at Cromwheel" gives "Wharfe". None when
    the name does not say."""
    chunks = [c.strip() for c in SPLIT.split(name or "") if c and c.strip()]
    for c in chunks:
        if m := re.search(r"\b(?:river|afon)\s+\S.*$", c, re.IGNORECASE):
            return m.group(0)
    for c in chunks:
        if re.search(rf"\S\s+(?:{WATER_WORDS})$", c, re.IGNORECASE):
            return c
    if len(chunks) > 1 and re.fullmatch(r"[^\W\d_][\w'’-]*", chunks[0]):
        return chunks[0]   # a bare first word, as in "Wharfe at Cromwheel": a guess the map has to confirm
    return None


def _names_water(hint: str) -> bool:
    """Whether a hint says outright that it is a watercourse ("River Lune", "Gordale Beck")."""
    return bool(re.search(rf"\b(?:river|afon)\b|\s(?:{WATER_WORDS})$", hint, re.IGNORECASE))


def spot_id(name: str, taken: set[str]) -> str:
    """A spots.csv id from the spot's name, in the curated rows' form ("wharfe-burnsall"), not already used."""
    s = unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", " ", name)).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", s.replace("'", "").replace("’", ""))
             if w not in {"river", "afon", "the", "at", "in", "on", "near", "above", "below", "by", "and"}]
    base = "-".join(words[:5])[:48].strip("-") or "spot"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def nearest_listed(lat: float, lon: float, spots: list[dict], within_m: float = NEARBY_M) -> dict | None:
    """The spots.csv row nearest the point, if one is within `within_m`."""
    best = None
    for s in spots:
        try:
            d = _metres(lat, lon, float(s["lat"]), float(s["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d <= within_m and (best is None or d < best["metres"]):
            best = {"id": s.get("id"), "name": s.get("name"), "metres": d}
    return best


def read_spots(path: Path = ROOT / "spots.csv") -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def location_fields(pin) -> dict:
    """The fields forecast_point puts under "location", which placement_check reads."""
    return {"mode": pin.mode, "watercourse": pin.watercourse,
            "snap_distance_m": None if pin.snap is None else round(pin.snap.dist_m),
            "form": None if pin.snap is None else pin.snap.form,
            "lake_area_km2": None if pin.lake_area_km2 is None else round(pin.lake_area_km2, 1),
            "lake_source": pin.lake_source, "adopted_main_channel": pin.adopted_main_channel,
            "placement": pin.placement}


def build_placement_check():
    """placement_check from scripts/build_site.py, the check every build runs on spots.csv."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.placement_check


def place(point: Point, req: Request, net, overflows, spots: list[dict], check=None) -> dict:
    """Where the point lands on the network, placed as the build places a spots.csv row, with its
    upstream overflows, the build's placement check and a spots.csv row."""
    from dipcast import config
    from dipcast.model.transport import locate_pin, river_velocity, upstream_overflows
    from dipcast.network.names import same_river
    check = check or build_placement_check()
    hint = river_from_name(req.name) if req.kind != "lake" else None
    pin = locate_pin(net, point.lon, point.lat, kind_hint=req.kind, river_hint=hint)
    if hint and pin.placement == "river not found" and not _names_water(hint):
        hint = None   # a bare word from the name that no nearby link carries was not a river's name
    nearest = locate_pin(net, point.lon, point.lat, kind_hint=req.kind) if pin.placement == "moved to river" else None
    if nearest is not None and same_river(nearest.watercourse, pin.watercourse):
        nearest = None   # an unnamed link that drains to the same river: not a different water to mention
    loc = location_fields(pin)
    kind = req.kind or ("lake" if pin.mode in ("lake", "isolated") else "river")
    out = {"location": loc, "kind": kind, "hint": hint, "max_km": config.MAX_UPSTREAM_KM,
           "nearest": None if nearest is None or nearest.snap is None else
           {"watercourse": nearest.watercourse, "metres": round(nearest.snap.dist_m)},
           "nearby": nearest_listed(point.lat, point.lon, spots)}
    if pin.mode == "none":
        return out
    up = upstream_overflows(net, pin, overflows, velocity_ms=river_velocity(None))
    out["overflows"] = {"n": len(up),
                        "live": int(up["has_live"].astype(bool).sum()) if "has_live" in up and len(up) else 0,
                        "nearest_km": round(float(up["distance_m"].min()) / 1000, 1) if len(up) else None}
    river = ""
    if kind == "river":
        wc = loc["watercourse"]
        river = wc if wc and (not hint or same_river(wc, hint)) else (hint or "")
        out["river_from"] = "name" if hint else "map"
    sid = spot_id(req.name or loc["watercourse"] or "spot", {s.get("id") for s in spots})
    out["flags"] = check([{"id": sid, "kind": kind, "river": river or None, "location": loc}])
    out["row"] = csv_row(sid, req.name or loc["watercourse"] or "", point, kind, river)
    return out


def csv_row(sid: str, name: str, point: Point, kind: str, river: str) -> str:
    """One spots.csv line. Notes stay blank: the page shows them to swimmers, so a maintainer writes them."""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(
        [sid, _clean(name)[:100], f"{point.lat:.5f}", f"{point.lon:.5f}", kind, river, "curated", ""])
    return buf.getvalue()


# ------------------------------------------------------------------------------------- the comment


def _km_or_m(metres: float) -> str:
    return f"{metres / 1000:.1f} km" if metres >= 1000 else f"{metres:.0f} m"


def _the(name: str | None) -> str:
    """'the River Wharfe', 'Gordale Beck', 'an unnamed watercourse'."""
    if not name:
        return "an unnamed watercourse"
    return f"the {name}" if re.match(r"(?:river|afon)\b", name, re.IGNORECASE) else name


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _water(loc: dict) -> str:
    mode, d = loc.get("mode"), loc.get("snap_distance_m")
    if mode == "isolated":
        return ("A lake with no river flowing in on the map. Storm overflows cannot reach it by water, so "
                "SwimSignal could list it but would have no sewage risk to forecast there.")
    if mode == "lake":
        name = loc.get("watercourse") or "An unnamed lake"
        if loc.get("lake_source") == "polygon" and loc.get("lake_area_km2"):
            return f"{name}, a lake of {loc['lake_area_km2']} km² in the Environment Agency's lake outlines."
        return f"{name}, a lake on the river map (no lake outline, so its area is not known)."
    text = _cap(_the(loc.get("watercourse"))) + (f", {_km_or_m(d)} from the point." if d is not None else ".")
    if loc.get("form") == "tidalRiver":
        text += " That stretch is tidal, and the forecast does not model tides."
    if loc.get("adopted_main_channel"):
        text += " The point is on a side channel, so the forecast traces the main river beside it."
    return text


def _overflow_line(ov: dict, max_km: float) -> str:
    n, live, near = ov["n"], ov["live"], ov["nearest_km"]
    if not n:
        return f"None monitored within {max_km:.0f} km upstream, so a forecast here would have no sewage spills to show."
    if n == 1:
        return f"1 monitored within {max_km:.0f} km upstream, {near} km away ({'with' if live else 'without'} a live feed)."
    feed = "all with a live feed" if live == n else "none with a live feed" if not live else f"{live} of them with a live feed"
    return f"{n} monitored within {max_km:.0f} km upstream, {feed}; the nearest is {near} km away."


def _check_line(res: dict) -> str:
    if res["kind"] == "lake":
        return "Lakes are not checked automatically: confirm the lake named above is the one asked for."
    if res.get("flags"):
        return "Needs a look: " + "; ".join(f["reason"] for f in res["flags"]) + "."
    if res.get("river_from") == "map":
        return ("Passes on distance and the kind of water. The name does not say which river, so the row uses "
                "the river on the map: confirm it.")
    return "Passes, the same check the build runs on every river spot."


THANKS = "Thanks for the request."
FAILED = ("The automatic check could not run this time, so a maintainer will place the spot by hand. Nothing more "
          "is needed from you for now.")
INTRO = ("This is an automatic check of where the spot sits on SwimSignal's river map. A maintainer reads every "
         "request before a spot is added.")
FOOT = ("<sub>River map: OS Open Rivers, contains OS data © Crown copyright and database right {year}. Overflows: "
        "the water companies' live feeds and the Environment Agency's annual returns. This comment is updated when "
        "the issue is edited.</sub>")


def compose(req: Request, point: Point | None, problem: str | None, res: dict | None, error: bool = False) -> str:
    """The comment, as Markdown: a question for coordinates, a placed spot, or a note that the check failed."""
    lines = [MARKER, ""]
    if error:
        return "\n".join([*lines, f"{THANKS} {FAILED}"]) + "\n"
    if problem or point is None or res is None:
        ask = f"{THANKS} The spot could not be placed on the map yet. {problem or ''}".rstrip()
        return "\n".join([*lines, ask, "", HOW_TO]) + "\n"
    loc = res["location"]
    read = {"coordinates": "the coordinates given", "grid reference": f"the grid reference `{point.shown}`",
            "what3words": f"the what3words address `{point.shown}`", "place name": "the place name given"}[point.how]
    if loc.get("mode") == "none":
        none = (f"{THANKS} There is no river or lake on SwimSignal's map within 1.5 km of {point.lat:.5f}, "
                f"{point.lon:.5f} (read from {read}).")
        return "\n".join([*lines, none, "", HOW_TO]) + "\n"
    lines += [f"{THANKS} {INTRO}", ""]
    lines.append(" ".join(filter(None, [f"- **Point:** {point.lat:.5f}, {point.lon:.5f}, from {read}.", point.note])))
    if req.kind is None:
        lines.append(f"- **Water body:** not given, so it is taken from the map: a {res['kind']}.")
    lines.append(f"- **Water:** {_water(loc)}")
    if res.get("nearest"):
        nr = res["nearest"]
        lines.append(f"- **Nearest water:** {_cap(_the(nr['watercourse']))}, {_km_or_m(nr['metres'])} away. "
                     f"The spot's name says {_the(res['hint'])}, so it goes there instead.")
    if loc.get("mode") != "isolated":
        lines.append(f"- **Storm overflows upstream:** {_overflow_line(res['overflows'], res['max_km'])}")
    lines.append(f"- **Placement check:** {_check_line(res)}")
    if res.get("nearby"):
        nb = res["nearby"]
        lines.append(f"- **Already listed:** `{_clean(nb['name'])}` is {_km_or_m(nb['metres'])} away.")
    if point.approximate:
        lines += ["", ("The point comes from a place name, so there is no `spots.csv` row yet. If you can, edit the "
                       f"issue with the spot's own latitude and longitude, for example {EXAMPLE}.")]
    else:
        lines += ["", "Row for `spots.csv`, for the maintainer who adds it:", "", "```csv", res["row"], "```"]
    lines += ["", FOOT.format(year=datetime.now(UTC).year)]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------- running


def answer(req: Request, env: dict | None = None, get=None, net=None, overflows=None, spots=None,
           check=None) -> tuple[str, bool]:
    """The comment for a request, and whether the check ran without an internal error."""
    parsed = geocode(parse_location(req.location), env=env, get=get)
    if parsed.problem or parsed.point is None:
        return compose(req, None, parsed.problem, None), True
    try:
        if net is None or overflows is None:
            from dipcast import config
            from dipcast.model.forecast import _net, _overflows
            if not (config.PROCESSED / "river_network.pkl").exists():   # else load() would try to rebuild it
                raise FileNotFoundError("data/processed/river_network.pkl (the data-v1 release) is missing")
            net, overflows = _net(), _overflows()
        res = place(parsed.point, req, net, overflows, read_spots() if spots is None else spots, check=check)
    except Exception:   # the requester gets a reply either way; the run log has the cause
        log.exception("placing the spot failed")
        return compose(req, parsed.point, None, None, error=True), False
    return compose(req, parsed.point, None, res), True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--event", help="a GitHub event file (issues or workflow_dispatch)")
    ap.add_argument("--name", default="")
    ap.add_argument("--location", default="")
    ap.add_argument("--kind", default="")
    ap.add_argument("--out", default="-", help="where to write the comment (- for stdout)")
    a = ap.parse_args(argv)
    if a.event:
        req = request_from_event(json.loads(Path(a.event).read_text(encoding="utf-8")))
    else:
        req = request_from_event({"inputs": {"name": a.name, "location": a.location, "kind": a.kind}})
    log.info("request: name=%r location=%r kind=%s issue=%s missing=%s", req.name, req.location, req.kind,
             req.issue, req.missing)
    text, ok = answer(req)
    if a.out == "-":
        sys.stdout.write(text)
    else:
        Path(a.out).write_text(text, encoding="utf-8")
    if not ok and os.environ.get("GITHUB_ACTIONS") == "true":
        print("::error title=spot request::the automatic check failed; the comment says a maintainer will place it",
              flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(main())
