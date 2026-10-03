"""List OpenStreetMap's swim places in England as candidates for spots-osm.csv, for a person to
read one by one. Writes data/raw/osm_swim_candidates.csv and prints the candidates within 300 m of
a listed spot (in spots.csv, or in spots-osm.csv from another element).

    uv run python scripts/osm_spot_candidates.py            # asks Overpass, keeps the answers
    uv run python scripts/osm_spot_candidates.py --cached   # reuses the kept answers (all five)

Four Overpass queries within England (ISO3166-2=GB-ENG), a few seconds apart:
leisure=bathing_place, leisure=swimming_area, sport=swimming, and names matching swim, bathing or
plunge. A fifth asks for the villages, towns and hamlets within 3 km of each candidate, so that a
row without a name can still be found. Elements are then dropped by these rules, in this order,
and each rule's count is printed:

1. tagged as something else: any leisure but bathing_place and swimming_area (pools, sports
   centres, gyms, parks), any amenity, building, shop, office, club or highway;
2. a name only: none of the three swim tags and no water tag (roads, bus stops, villages, woods,
   artworks, signs);
3. a pool by its name or tags: "swimming pool", "baths", "lido", "swim school", "leisure centre"
   or "swim centre" in the name of an element without a swim-area tag, or water=swimming_pool,
   pool, reflecting_pool or basin without one;
4. disused: "disused", "former", "old" or "ruins" in the name;
5. tidal or sea: tidal=yes, natural=beach, or "tidal", "sea pool", "rock pool", "sea bathing" or
   "beach" in the name;
6. a duplicate: of two left within 150 m, the one with less to say (no name, fewer tags).

What is left is a candidate, not a spot. Each is placed on the river network by locate_pin with
no hint, as a click would be (its watercourse, distance and form, and the lake's name if it is in
one), and marked inland unless no inland river or lake link is within 1.5 km or a tidal link is
nearer than any inland one. Then a person reads every row and copies the natural river and lake
spots the public can reach into spots-osm.csv, typing `river` for each river spot (README, "Spot
placement"), and the build checks them like the rest.

The data is © OpenStreetMap contributors under the Open Database Licence 1.0, and so is the CSV
this writes (LICENSE-DATA.md).
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

from dipcast import __version__, config

ROOT = Path(__file__).resolve().parents[1]
OUT = config.RAW / "osm_swim_candidates.csv"
CACHE = config.CACHE / "osm_swim"
SPOT_FILES = [ROOT / "spots.csv", ROOT / "spots-osm.csv"]
OVERPASS = "https://overpass-api.de/api/interpreter"
# Overpass asks every client to say who it is; this names the project and where to reach it.
USER_AGENT = (f"SwimSignal/{__version__} swim-spot candidates "
              "(+https://github.com/ethanbuckley/swimsignal; hello@swimsignal.co.uk)")
AREA = 'area["ISO3166-2"="GB-ENG"]->.eng;'
QUERIES = {
    "bathing_place": 'nwr["leisure"="bathing_place"](area.eng);',
    "swimming_area": 'nwr["leisure"="swimming_area"](area.eng);',
    "sport_swimming": 'nwr["sport"="swimming"](area.eng);',
    "name": 'nwr["name"~"swim|bathing|plunge",i](area.eng);',
}
PLACES = "city|town|village|hamlet|suburb"
PAUSE_S = 5.0          # between queries: Overpass is a shared, volunteer-run service
DUP_M = 150.0
NEAR_SPOT_M = 300.0
INLAND_M = 1_500.0
SWIM_AREAS = {"bathing_place", "swimming_area"}
POOL_NAME = re.compile(r"swimming pool|swimming baths|\bbaths\b|\blido\b|swim school|leisure centre|swim centre|"
                       r"swimming centre", re.IGNORECASE)
POOL_WATER = {"swimming_pool", "pool", "reflecting_pool", "swimmimg_pool", "basin"}
DISUSED = re.compile(r"disused|former|\bold\b|ruins", re.IGNORECASE)
SEA_NAME = re.compile(r"tidal|sea pool|rock pool|sea bathing|beach", re.IGNORECASE)
SHOWN_TAGS = ("name", "leisure", "sport", "natural", "water", "waterway", "access", "fee", "swimming", "supervised",
              "lifeguard", "tidal", "description", "note", "website")


def overpass(name: str, query: str, client: httpx.Client, cached: bool) -> list[dict]:
    """One query's elements. A 504 or 429 (the server busy) is retried once after a longer
    pause; anything else stops the script. The answer is kept in CACHE for --cached."""
    path = CACHE / f"{name}.json"
    if cached and path.exists():
        return json.loads(path.read_text())["elements"]
    for attempt in (1, 2):
        r = client.post(OVERPASS, data={"data": query})
        if r.status_code in (429, 504) and attempt == 1:
            print(f"  {name}: HTTP {r.status_code}, retrying once in 30 s", file=sys.stderr)
            time.sleep(30)
            continue
        r.raise_for_status()
        break
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text)
    return r.json()["elements"]


def fetch(client: httpx.Client, cached: bool = False) -> pd.DataFrame:
    """Every element any of the four queries matched, once, with the queries that matched it and
    a position (a way's or relation's centre)."""
    rows: dict[str, dict] = {}
    for i, (name, body) in enumerate(QUERIES.items()):
        if i and not cached:
            time.sleep(PAUSE_S)
        els = overpass(name, f"[out:json][timeout:180];{AREA}({body});out center tags;", client, cached)
        print(f"  {name}: {len(els)} elements", file=sys.stderr)
        for e in els:
            centre = e.get("center") or {}
            row = rows.setdefault(f"{e['type']}/{e['id']}", {
                "osm_id": f"{e['type']}/{e['id']}", "lat": e.get("lat", centre.get("lat")),
                "lon": e.get("lon", centre.get("lon")), "tags": e.get("tags") or {}, "matched_by": []})
            row["matched_by"].append(name)
    return pd.DataFrame(list(rows.values()))


def dropped_by(tags: dict) -> str | None:
    """The rule (1 to 5 in the module's notes) that drops an element, or None to keep it."""
    leisure, name = tags.get("leisure"), tags.get("name", "")
    swim_area = leisure in SWIM_AREAS
    swim = swim_area or tags.get("sport") == "swimming"
    if leisure and not swim_area:
        return f"tagged leisure={leisure}"
    for k in ("amenity", "building", "shop", "office", "club", "highway"):
        if k in tags:
            return f"tagged {k}={tags[k]}"
    if not swim and not (tags.get("natural") == "water" or "water" in tags):
        return "a name only"
    if not swim_area and (POOL_NAME.search(name) or tags.get("water") in POOL_WATER):
        return "a pool by its name or tags"
    if DISUSED.search(name):
        return "disused"
    if tags.get("tidal") == "yes" or tags.get("natural") == "beach" or SEA_NAME.search(name):
        return "tidal or sea"
    return None


def _xy(df: pd.DataFrame) -> tuple[list[float], list[float]]:
    from dipcast.network.rivers import lonlat_to_bng
    pts = [lonlat_to_bng(lo, la) for lo, la in zip(df["lon"], df["lat"])]
    return [p[0] for p in pts], [p[1] for p in pts]


def dedupe(df: pd.DataFrame, within_m: float = DUP_M) -> pd.DataFrame:
    """Of candidates within `within_m` of each other, keep the one with most to say: a swim-area
    tag, then a name, then more tags. The dropped ones' ids go in `duplicates`."""
    df = df.assign(_rank=[(t.get("leisure") in SWIM_AREAS, bool(t.get("name")), len(t)) for t in df["tags"]])
    df = df.sort_values("_rank", ascending=False).reset_index(drop=True)
    xs, ys = _xy(df)
    kept: list[int] = []
    dups: dict[int, list[str]] = {}
    for i in range(len(df)):
        j = next((k for k in kept if math.hypot(xs[i] - xs[k], ys[i] - ys[k]) <= within_m), None)
        if j is None:
            kept.append(i)
        else:
            dups.setdefault(j, []).append(df.at[i, "osm_id"])
    out = df.loc[kept].drop(columns="_rank")
    out["duplicates"] = [" ".join(dups.get(i, [])) for i in kept]
    return out


def nearby_places(df: pd.DataFrame, client: httpx.Client, cached: bool, within_m: int = 3000) -> pd.Series:
    """The nearest village, town or hamlet within `within_m` of each candidate, with its distance:
    one query for all of them."""
    body = "".join(f'node(around:{within_m},{la:.5f},{lo:.5f})["place"~"^({PLACES})$"];'
                   for la, lo in zip(df["lat"], df["lon"]))
    els = overpass("places", f"[out:json][timeout:180];({body});out;", client, cached)
    if not els:
        return pd.Series("", index=df.index)
    places = pd.DataFrame([{"name": e["tags"].get("name", ""), "lat": e["lat"], "lon": e["lon"]} for e in els
                           if e.get("tags", {}).get("name")])
    px, py = _xy(places)
    xs, ys = _xy(df)
    out = []
    for x, y in zip(xs, ys):
        d = [math.hypot(x - a, y - b) for a, b in zip(px, py)]
        k = min(range(len(d)), key=d.__getitem__)
        out.append(f"{places.at[k, 'name']} ({d[k] / 1000:.1f} km)" if d[k] <= within_m else "")
    return pd.Series(out, index=df.index)


def place_on_network(df: pd.DataFrame) -> pd.DataFrame:
    """locate_pin for each candidate with no hint, and the inland test."""
    from dipcast.model.forecast import _net
    from dipcast.model.transport import locate_pin
    net = _net()
    rows = []
    for r in df.itertuples():
        pin = locate_pin(net, float(r.lon), float(r.lat))
        inland = net.snap_xy(pin.x, pin.y, max_m=INLAND_M, forms=("inlandRiver", "lake"))
        tidal = net.snap_xy(pin.x, pin.y, max_m=INLAND_M, forms=("tidalRiver",))
        rows.append({
            "mode": pin.mode, "watercourse": pin.watercourse or "",
            "snap_distance_m": None if pin.snap is None else round(pin.snap.dist_m),
            "form": "" if pin.snap is None else pin.snap.form,
            "lake_area_km2": None if pin.lake_area_km2 is None else round(pin.lake_area_km2, 2),
            "inland": pin.mode == "lake" or (inland is not None and (tidal is None or inland.dist_m <= tidal.dist_m)),
        })
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def nearest_spot(df: pd.DataFrame, spots: pd.DataFrame) -> pd.DataFrame:
    """`listed_as`: the spots-osm.csv id made from this element, if any. `nearest_spot` and its
    distance: the nearest other listed spot, in either file."""
    own = spots.get("osm_id", pd.Series("", index=spots.index)).fillna("")
    xs, ys = _xy(df)
    sx, sy = _xy(spots)
    listed, ids, dist = [], [], []
    for oid, x, y in zip(df["osm_id"], xs, ys):
        d = [math.inf if o == oid else math.hypot(x - a, y - b) for o, a, b in zip(own, sx, sy)]
        k = min(range(len(d)), key=d.__getitem__)
        listed.append(next((i for i, o in zip(spots["id"], own) if o == oid), ""))
        ids.append(spots["id"].iloc[k])
        dist.append(round(d[k]))
    return df.assign(listed_as=listed, nearest_spot=ids, nearest_spot_m=dist)


def listed_spots() -> pd.DataFrame:
    return pd.concat([pd.read_csv(p).fillna("") for p in SPOT_FILES if p.exists()], ignore_index=True)


def main(cached: bool = False) -> pd.DataFrame:
    with httpx.Client(timeout=240, headers={"User-Agent": USER_AGENT}) as client:
        df = fetch(client, cached)
        as_of = json.loads((CACHE / "bathing_place.json").read_text())["osm3s"]["timestamp_osm_base"]
        print(f"{len(df)} distinct elements, OpenStreetMap data as of {as_of}")
        df["dropped_by"] = df["tags"].map(dropped_by)
        for rule, n in df["dropped_by"].str.split("=").str[0].value_counts().items():
            print(f"  dropped, {rule}: {n}")
        df = df[df["dropped_by"].isna()].drop(columns="dropped_by")
        print(f"{len(df)} left")
        df = dedupe(df)
        print(f"{len(df)} after removing duplicates within {DUP_M:.0f} m")
        if not cached:
            time.sleep(PAUSE_S)
        df["near_place"] = nearby_places(df, client, cached)
    df = place_on_network(df)
    print(f"{int(df['inland'].sum())} inland by the network test")
    df = nearest_spot(df, listed_spots())
    for k in SHOWN_TAGS:
        df[f"tag_{k}"] = [str(t.get(k, "")) for t in df["tags"]]
    df["tags"] = [json.dumps(t, ensure_ascii=False, sort_keys=True) for t in df["tags"]]
    df["matched_by"] = [" ".join(m) for m in df["matched_by"]]
    df = df.sort_values(["inland", "lat"], ascending=[False, False]).reset_index(drop=True)
    df["lat"], df["lon"] = df["lat"].round(5), df["lon"].round(5)
    df["snap_distance_m"] = df["snap_distance_m"].astype("Int64")
    cols = (["osm_id", "lat", "lon", "tag_name", "near_place", "mode", "watercourse", "snap_distance_m", "form",
             "lake_area_km2", "inland", "listed_as", "nearest_spot", "nearest_spot_m", "matched_by", "duplicates"]
            + [f"tag_{k}" for k in SHOWN_TAGS if k != "name"] + ["tags"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df[cols].to_csv(OUT, index=False)
    print(f"wrote {OUT}: {len(df)} candidates")
    print(f"{int((df['listed_as'] != '').sum())} already in spots-osm.csv")
    near = df[df["nearest_spot_m"] <= NEAR_SPOT_M]
    print(f"{len(near)} within {NEAR_SPOT_M:.0f} m of another listed spot:")
    for r in near.itertuples():
        print(f"  {r.osm_id}  {r.tag_name or '(no name)'}  {r.nearest_spot_m} m from {r.nearest_spot}")
    return df


if __name__ == "__main__":
    main(cached="--cached" in sys.argv)
