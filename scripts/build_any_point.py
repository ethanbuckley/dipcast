"""Publish the data a page needs to forecast any clicked point, with no server (roadmap task 3).

The API forecasts a clicked point by loading the river network (113 MB on disk, about 900 MB in
memory) and tracing upstream. GitHub Pages cannot run that, so the build does the tracing here,
once per network release, and publishes what is left for the page: snap the click to a link, read
that link's upstream overflows, and do the transport arithmetic of model/transport.py with the
day's spill probabilities.

Everything is written under site/data/anypoint/:

  tiles.json           the 0.25-degree squares that have files, the scales and the versions
  overflow_ids.json    the overflows: id, name and position; every other file names an overflow
                       by its place in this list
  overflow_days.json   per overflow: spill probability by day (thousandths; null where its rain
                       cell had no data, which is the `ok` mask), the "right now" weight
                       (thousandths), live status, company, how old its rain forecast is, and
                       the low-confidence snaps
  link_index/<t>.bin   per link: number, form, name and a simplified line, packed Int32/Float32
                       (layout in `pack_link_index`), so the page can snap a click to a link
  links/<t>.json       per traced link: its length, the network length upstream of it, and its
                       upstream overflows as transport.upstream_overflows finds them, in a form
                       that gives the distance and dilution for a click anywhere along it
                       (`rows_at`); a lake link points at its lake in lakes.json
  lakes.json           the WFD lake polygons (England) and every lake an overflow reaches: its
                       inlets, each with the overflows whose water enters there (river distance
                       and dilution before the lake-area term)

A click is placed as transport._locate places it: inside or within 150 m of a WFD lake polygon it
is that lake; otherwise the nearest link within 1.5 km, or the nearest lake centreline when that
is less than twice as far; a click on a side channel near a much bigger river is traced as that
river (_adopt_main_channel, which the page applies with each link's upstream network length from
the index). River distances and dilutions then follow upstream_overflows exactly for the click's
position along the link.

The upstream lists depend on the network and on where each overflow sits on it. They are cached
in the state directory keyed by the network file's hash; when overflows are added, removed or
moved, only the links downstream of them (within config.MAX_UPSTREAM_KM) are traced again.

Rain: every overflow's cell (about 1,650) against the spots' 300. Open-Meteo stopped answering
this build after 600 locations in a minute (3 Oct 2026), so each build fetches at most
RAIN_CELLS_PER_BUILD of the oldest cells and uses cached ones up to RAIN_MAX_AGE_H old; an
overflow without either has no rain data, which the page shows as no data, as for a spot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

from dipcast import config
from dipcast.model import transport
from dipcast.model.transport import (
    L0_M,
    LAKE_A0_KM2,
    LAKE_SHORE_M,
    LAKE_SNAP_M,
    LOW_CONFIDENCE_FACTOR,
    PIN_SNAP_M,
    history_days,
    live_now_risk,
    river_velocity,
    upstream_lengths,
)
from dipcast.network.rivers import _TO_WGS, RiverNetwork

log = logging.getLogger("build_any_point")

TILE_DEG = 0.25           # squares of 0.25 degrees: about 28 km north-south, 17 km east-west
SIMPLIFY_M = 30.0         # line simplification for snapping; the snap radius is 1,500 m
LAKE_SIMPLIFY_M = 15.0    # lake outlines; the shore rule is 150 m
DAYS_AHEAD = 4            # today and four more, as forecast_point
DIL_SCALE = 10_000        # dilutions and dilution ratios in ten-thousandths
BASE_UNIT_M = 10          # base distances in links/<t>.json, in tens of metres
P_SCALE = 1_000           # probabilities and live weights in thousandths (three decimals)
FORMS = ["inlandRiver", "tidalRiver", "lake", "canal"]
INDEX_MAGIC = 0x494C5353  # "SSLI" read as a little-endian Int32
INDEX_VERSION = 1
CACHE_NAME = "anypoint_links.pkl"
CACHE_VERSION = 5         # bump when the table's rules change
OUT = "anypoint"
RAIN_CELLS_PER_BUILD = int(os.environ.get("DIPCAST_ANYPOINT_RAIN_CELLS", "300"))
RAIN_MAX_AGE_H = 24.0


# ------------------------------------------------------------------ helpers
def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def tile_key(lat: float, lon: float) -> str:
    """The square a point is in, as '<row>_<col>' with row = floor(lat / 0.25), col = floor(lon / 0.25)."""
    return f"{math.floor(lat / TILE_DEG)}_{math.floor(lon / TILE_DEG)}"


def placements(ov: pd.DataFrame) -> pd.DataFrame:
    """Where each snapped overflow sits on the network, one row per site id, sorted by id. These
    are the only overflow fields an upstream list depends on."""
    p = ov[ov["link_id"].notna()].drop_duplicates("site_id").set_index("site_id")
    p = p[["link_id", "frac", "link_length", "start_node", "end_node"]].copy()
    p["frac"] = p["frac"].astype(float)
    p["link_length"] = p["link_length"].astype(float)
    return p.sort_index()


def reach_of(net: RiverNetwork, end_nodes, cap_m: float) -> set[str]:
    """Nodes within cap_m downstream of any of the given nodes: the trace roots whose upstream
    search (RiverNetwork.upstream_edges) can reach an overflow ending there."""
    out: set[str] = set()
    for e in pd.unique(pd.Series(list(end_nodes), dtype=object).dropna()):
        if e in net.graph:
            out.update(net.downstream_nodes(e, cap_m))
    return out


def link_names(net: RiverNetwork, lids) -> dict[str, str | None]:
    """transport._link_name for many links: the link's own name, else the first named link
    downstream within 12 steps, following the first successor at a fork."""
    links, g = net.links, net.graph
    names = dict(zip(links.index, links["watercourse_name"], strict=True))
    end = dict(zip(links.index, links["end_node"], strict=True))
    first_succ: dict[str, str | None] = {}

    def succ_link(node: str) -> str | None:
        if node not in first_succ:
            nxt = next(iter(g.successors(node)), None) if node in g else None
            first_succ[node] = None if nxt is None else g.edges[node, nxt]["link"]
        return first_succ[node]

    out = {}
    for lid in lids:
        cur, name = lid, None
        for _ in range(12):
            if cur is None or cur not in names:
                break
            n = names[cur]
            if isinstance(n, str) and n:
                name = n
                break
            cur = succ_link(end[cur])
        out[lid] = name
    return out


class Tracer:
    """The river branch of transport.upstream_overflows, kept open in the click's position. For a
    link T traced from its start node (the root) and a click at fraction f along it,
    upstream_overflows gives, with offset = f x length(T):

      another link's overflow: distance = (1 - frac) x link length + network distance from that
                               link's end to the root + offset, kept if within the cap;
                               dilution = min(1, (L_up(overflow) + L0) / (L_up(root) + offset + L0))
      an overflow on T itself, above the click (frac < f): distance = (f - frac) x length(T),
                               dilution as above with L_up(overflow) = L_up(root)

    where L_up is the exact upstream network length within the root's capped search
    (upstream_lengths). L_up(overflow) is part of L_up(root), so the dilution is
    r x (L_up(root) + L0) / (L_up(root) + L0 + offset) with r = (L_up(overflow) + L0) / (L_up(root) + L0),
    at most 1. A link's record is its length, L_up(root), and per overflow either (base distance,
    r) or its position along T; `rows_at` turns that into the API's rows for any f. One search per
    root, and L_up only for the nodes a dilution needs: calling upstream_overflows per link would
    take over 12 minutes (measured on 3 Oct 2026)."""

    def __init__(self, net: RiverNetwork, ov: pd.DataFrame, cap_m: float = config.MAX_UPSTREAM_KM * 1000.0):
        self.net, self.g, self.cap = net, net.graph, cap_m
        self.length = dict(zip(net.links.index, net.links["length"].astype(float), strict=True))
        self.length.update(net.repairs)
        self.by_link: dict[str, list] = {}
        for r in ov.itertuples(index=False):
            self.by_link.setdefault(r.link_id, []).append((r.site_id, float(r.frac), float(r.link_length), r.start_node))

    def records(self, root: str, targets: list[str]) -> dict[str, dict]:
        """{link: {"lup", "len", "other": [(site_id, base_m, r)], "same": [(site_id, pos_m)]}} for links
        whose trace starts at `root`."""
        link_dist = self.net.upstream_edges(root, self.cap)
        length_of = {lid: self.length[lid] for lid in link_dist}
        graph, memo = self.g, {}

        def total(n: str) -> float:   # upstream_lengths' total(), for one node
            if n not in memo:
                seen: set[str] = set()
                stack = [n]
                while stack:
                    m = stack.pop()
                    for p in graph.predecessors(m):
                        lid = graph.edges[p, m]["link"]
                        if lid in length_of and lid not in seen:
                            seen.add(lid)
                            stack.append(p)
                memo[n] = float(sum(length_of[l] for l in seen))
            return memo[n]

        upstream = [(lid, d0, self.by_link[lid]) for lid, d0 in link_dist.items() if lid in self.by_link]
        out = {}
        for t in targets:
            spot = total(root) + L0_M
            other = [(sid, (1.0 - f) * ll + d0, (total(sn) + L0_M) / spot) for lid, d0, ovs in upstream if lid != t
                     for sid, f, ll, sn in ovs]
            out[t] = {"lup": total(root), "len": self.length[t],
                      "other": [r for r in other if r[1] <= self.cap],
                      "same": [(sid, f * ll) for sid, f, ll, _ in self.by_link.get(t, ())]}
        return out


def rows_at(rec: dict, frac: float, cap_m: float = config.MAX_UPSTREAM_KM * 1000.0) -> pd.DataFrame:
    """A link record's rows for a click at `frac` along the link: site_id, distance_m, dilution, as
    upstream_overflows computes them (the page's anypoint.js does the same)."""
    offset = frac * rec["len"]
    shrink = (rec["lup"] + L0_M) / (rec["lup"] + L0_M + offset)   # the click's own extra upstream length
    rows = [(sid, base + offset, min(1.0, r * shrink)) for sid, base, r in rec["other"] if base + offset <= cap_m]
    rows += [(sid, offset - pos, min(1.0, shrink)) for sid, pos in rec["same"] if pos < offset]
    return pd.DataFrame(rows, columns=["site_id", "distance_m", "dilution"])


# ------------------------------------------------------------------ lakes
def lake_link_sets(net: RiverNetwork, lakes) -> tuple[list[dict], dict[str, frozenset], dict[str, str]]:
    """(WFD lakes with the lake links transport._locate gives a click inside them, centreline
    lakes {id: links}, lake link -> centreline lake id). A WFD lake's links are the lake-form links
    crossing its polygon, else the centreline lake nearest its middle within 1.5 km, else none
    (isolated: no river connection in the network)."""
    links = net.links
    lake_links = links.index[links["form"] == "lake"]
    # Centreline lakes: lake links joined through shared nodes (RiverNetwork.lake_component).
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for lid in lake_links:
        s, e = links.at[lid, "start_node"], links.at[lid, "end_node"]
        parent[find(s)] = find(e)
    comps: dict[str, set] = {}
    for lid in lake_links:
        comps.setdefault(find(links.at[lid, "start_node"]), set()).add(lid)
    comp_sets = {f"c{i}": frozenset(v) for i, v in enumerate(sorted(comps.values(), key=lambda s: min(s)))}
    link_comp = {lid: cid for cid, s in comp_sets.items() for lid in s}
    wfd = []
    lake_frame = links.loc[lake_links]   # snap_xy(forms=("lake",)) builds this on every call
    if lakes is not None and not lakes.empty:
        for _, poly in lakes.iterrows():
            idx = links.sindex.query(poly.geometry, predicate="intersects")
            cand = links.iloc[idx]
            ll = frozenset(cand.index[cand["form"] == "lake"])
            if not ll and len(lake_frame):
                near = lake_frame.sindex.nearest(poly.geometry.representative_point(), return_all=False,
                                                 max_distance=LAKE_SNAP_M)
                if near.shape[1]:
                    ll = comp_sets[link_comp[lake_frame.index[near[1, 0]]]]
            name = poly.get("lake_name")
            wfd.append({"wb_id": str(poly.get("wb_id") or ""), "name": str(name) if isinstance(name, str) else None,
                        "area_km2": float(poly["area_km2"]), "geometry": poly.geometry, "links": ll or None})
    return wfd, comp_sets, link_comp


def lake_rows(net: RiverNetwork, ov: pd.DataFrame, comp: frozenset, outlet: str,
              cap_m: float = config.MAX_UPSTREAM_KM * 1000.0) -> pd.DataFrame:
    """Overflows upstream of a lake's outlet that reach the lake: site_id, river distance (m),
    where the water enters the lake (BNG x, y) and the dilution before the lake-area term, as
    the lake branch of transport.upstream_overflows computes them. The page adds the straight
    line from the entry to the click at the lake velocity and divides by 1 + area / 5 km2."""
    cols = ["site_id", "distance_m", "entry_x", "entry_y", "dilution"]
    link_dist, lup = upstream_lengths(net, outlet, cap_m)
    on_path = set(link_dist)
    sub = ov[ov["link_id"].isin(on_path)]
    spot_lup = lup.get(outlet, 0.0)
    rows = []
    for r in sub.itertuples(index=False):
        res = transport._path_to_lake(net, r.link_id, r.frac, set(comp), on_path)
        if res is None:
            continue
        d_river, (ex, ey) = res
        if d_river > cap_m:
            continue
        ov_lup = lup.get(r.start_node, 0.0)
        rows.append((r.site_id, d_river, ex, ey, min(1.0, (ov_lup + L0_M) / (spot_lup + L0_M))))
    return pd.DataFrame(rows, columns=cols)


# ------------------------------------------------------------------ the cached table
def _net_key(net_hash: str, lakes_hash: str) -> dict:
    return {"version": CACHE_VERSION, "network": net_hash, "lakes": lakes_hash, "cap_km": config.MAX_UPSTREAM_KM,
            "l0_m": L0_M}


def upstream_table(net: RiverNetwork, ov: pd.DataFrame, lakes, net_hash: str, lakes_hash: str = "",
                   cache_path: Path | None = None) -> tuple[dict, dict]:
    """The link records and lake inlets, from the cache where it still holds. Returns (table,
    stats). table: records {link: record}, wfd, comp_sets, link_comp, outlets, lake_rows {lake id:
    frame}, placements."""
    cap = config.MAX_UPSTREAM_KM * 1000.0
    stats: dict = {"cache": "cold"}
    key = _net_key(net_hash, lakes_hash)
    cached = None
    if cache_path is not None and cache_path.exists():
        try:
            cached = pickle.loads(cache_path.read_bytes())
            if cached.get("key") != key:
                stats["cache"] = "stale network"
                cached = None
        except Exception as e:  # noqa: BLE001 - a broken cache is rebuilt, never fatal
            log.warning("any-point cache unreadable, rebuilding: %s", e)
            cached = None
    place = placements(ov)
    ovp = place.reset_index()   # what upstream_overflows reads: site_id, link_id, frac, link_length, start_node
    links = net.links

    if cached is None:
        t = time.time()
        wfd, comp_sets, link_comp = lake_link_sets(net, lakes)
        stats["network_s"] = round(time.time() - t, 1)
        log.info("any-point: %d WFD lakes, %d centreline lakes (%.0f s)", len(wfd), len(comp_sets), time.time() - t)
        table = {"key": key, "wfd": wfd, "comp_sets": comp_sets, "link_comp": link_comp,
                 "outlets": {}, "placements": place.iloc[0:0], "records": {}, "lake_rows": {}}
        todo_nodes = todo_links = None   # everything
    else:
        table = cached
        old = table["placements"]
        both = old.index.union(place.index)
        a = old.reindex(both)
        b = place.reindex(both)
        same = ((a == b) | (a.isna() & b.isna())).all(axis=1)
        changed = both[~same.to_numpy()]
        stats["cache"] = "warm" if len(changed) == 0 else f"{len(changed)} overflows changed"
        if len(changed) == 0:
            stats["traced_links"] = 0
            return table, stats
        moved = pd.concat([a.loc[changed], b.loc[changed]]).dropna(subset=["link_id"])
        todo_nodes = reach_of(net, moved["end_node"], cap)
        todo_links = set(moved["link_id"])

    # The links a river click can be traced along: every link but a lake's. One with an overflow
    # upstream gets a record.
    start = links["start_node"]
    targets = list(links.index[links["form"] != "lake"])
    reach = reach_of(net, place["end_node"], cap)
    ov_links = set(place["link_id"])
    if todo_nodes is None:
        redo = targets
    else:
        redo = [l for l in targets if start.at[l] in todo_nodes or l in todo_links]
    for l in redo:
        table["records"].pop(l, None)
    t = time.time()
    groups: dict[str, list] = {}
    for l in redo:
        s = start.at[l]
        if s in reach or l in ov_links:
            groups.setdefault(s, []).append(l)
    tracer = Tracer(net, ovp, cap)
    for i, (root, ts) in enumerate(groups.items()):
        for l, rec in tracer.records(root, ts).items():
            if rec["other"] or rec["same"]:
                table["records"][l] = rec
        if (i + 1) % 10000 == 0:
            log.info("any-point: traced %d of %d roots (%.0f s)", i + 1, len(groups), time.time() - t)
    stats["traced_links"] = sum(len(v) for v in groups.values())
    stats["trace_s"] = round(time.time() - t, 1)

    # Lakes: every lake an overflow can reach is worked out again whenever any overflow changed
    # (a few hundred lakes, seconds). The outlet depends only on the network and is kept.
    t = time.time()
    sets = {f"w{i}": w["links"] for i, w in enumerate(table["wfd"]) if w["links"]}
    sets.update(table["comp_sets"])
    lake_out = {}
    for lake_id, comp in sets.items():
        nodes = {n for lid in comp for n in net.link_nodes(lid)}
        if not nodes & reach:
            continue
        if lake_id not in table["outlets"]:
            table["outlets"][lake_id] = transport._lake_outlet(net, set(comp))
        rows = lake_rows(net, ovp, comp, table["outlets"][lake_id], cap)
        if len(rows):
            lake_out[lake_id] = rows
    table["lake_rows"] = lake_out
    stats["lake_s"] = round(time.time() - t, 1)
    table["placements"] = place
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps(table, protocol=5))
        tmp.replace(cache_path)
    return table, stats


# ------------------------------------------------------------------ the files
def pack_link_index(lnos: np.ndarray, meta: np.ndarray, upstream: np.ndarray, offsets: np.ndarray,
                    lonlat: np.ndarray, names: list[str]) -> bytes:
    """One square's links, little-endian, every block 4-byte aligned:
      Int32[6]     magic "SSLI", version 1, n links, n vertices, bytes of names, 0
      Int32[n]     link number (its place in the network's links sorted by OS id)
      Int32[n]     form + 4 x (name + 1): form = meta & 3 (0 inland river, 1 tidal river, 2 lake,
                   3 canal), name = (meta >> 2) - 1, an index into the names, -1 for none
      Float32[n]   network length upstream of the link's start (m), RiverNetwork.upstream_m: the
                   page moves a click on a side channel to a much bigger river with it
      Int32[n + 1] where each link's vertices start; the last is the number of vertices
      Float32[2v]  longitude, latitude of each vertex, in order along the flow
      UTF-8        the names, as a JSON list
    """
    nb = json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode()
    head = np.array([INDEX_MAGIC, INDEX_VERSION, len(lnos), len(lonlat), len(nb), 0], dtype="<i4")
    parts = [head, lnos.astype("<i4"), meta.astype("<i4"), upstream.astype("<f4"), offsets.astype("<i4"),
             lonlat.astype("<f4").ravel()]
    return b"".join(p.tobytes() for p in parts) + nb


def unpack_link_index(buf: bytes) -> dict:
    """The inverse of pack_link_index (for tests and checks)."""
    h = np.frombuffer(buf, dtype="<i4", count=6)
    assert h[0] == INDEX_MAGIC and h[1] == INDEX_VERSION
    n, nv, nb = int(h[2]), int(h[3]), int(h[4])
    off = 24
    out = {}
    for k, cnt, dt in (("lno", n, "<i4"), ("meta", n, "<i4"), ("upstream", n, "<f4"), ("offsets", n + 1, "<i4")):
        out[k] = np.frombuffer(buf, dtype=dt, count=cnt, offset=off)
        off += 4 * cnt
    out["form"] = out["meta"] & 3
    out["name"] = (out["meta"] >> 2) - 1
    out["lonlat"] = np.frombuffer(buf, dtype="<f4", count=2 * nv, offset=off).reshape(-1, 2)
    off += 8 * nv
    out["names"] = json.loads(buf[off:off + nb].decode())
    return out


def encode_record(rec: dict, pos: dict[str, int]) -> dict:
    """A link record for links/<t>.json: n its length (m), u the network length upstream of its start
    (m), o flat triples (overflow index as the difference from the previous one, the indices
    ascending; base distance in tens of metres; r in ten-thousandths), s flat pairs (overflow index,
    position along the link in m) for the overflows on the link itself."""
    o, last = [], 0
    for i, base, r in sorted((pos[sid], base, r) for sid, base, r in rec["other"]):
        o += [i - last, round(float(base) / BASE_UNIT_M), round(float(r) * DIL_SCALE)]
        last = i
    s = []
    for i, p in sorted((pos[sid], p) for sid, p in rec["same"]):
        s += [i, round(float(p))]
    return {"n": round(float(rec["len"])), "u": round(float(rec["lup"])), "o": o, **({"s": s} if s else {})}


def decode_record(entry: dict, ids: list[str]) -> dict:
    """A links/<t>.json record back into the form rows_at takes (for tests and checks)."""
    o = np.asarray(entry.get("o", []), dtype=float).reshape(-1, 3)
    s = np.asarray(entry.get("s", []), dtype=float).reshape(-1, 2)
    idx = np.cumsum(o[:, 0]).astype(int) if len(o) else []
    return {"len": entry["n"], "lup": entry["u"],
            "other": [(ids[i], b * BASE_UNIT_M, r / DIL_SCALE) for i, (_, b, r) in zip(idx, o, strict=True)],
            "same": [(ids[int(i)], p) for i, p in s]}


def _to_lonlat(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lon, lat = _TO_WGS.transform(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    return np.asarray(lon), np.asarray(lat)


def _tiles_of_bounds(b: np.ndarray) -> list[list[str]]:
    """Squares each lon/lat bounding box (minx, miny, maxx, maxy) touches."""
    out = []
    for x0, y0, x1, y1 in b:
        r0, r1 = math.floor(y0 / TILE_DEG), math.floor(y1 / TILE_DEG)
        c0, c1 = math.floor(x0 / TILE_DEG), math.floor(x1 / TILE_DEG)
        out.append([f"{r}_{c}" for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)])
    return out


def ids_version(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:12]


def write_files(out: Path, net: RiverNetwork, ov: pd.DataFrame, table: dict, net_hash: str,
                credits: dict | None = None) -> dict:
    """Every file except overflow_days.json and tiles.json. Returns {ids, ids_version, tiles,
    past_days, counts}."""
    links = net.links
    place = table["placements"]
    ids = list(place.index)
    pos = {s: i for i, s in enumerate(ids)}
    idv = ids_version(ids)
    lno_of = pd.Series(np.arange(len(links), dtype=np.int64), index=links.index.sort_values())
    records = table["records"]

    # Squares with data: any with an overflow, any with a link that has one upstream.
    ovl = ov[ov["site_id"].isin(pos)].drop_duplicates("site_id").set_index("site_id").loc[ids]
    covered = {tile_key(a, b) for a, b in zip(ovl["lat"], ovl["lon"], strict=True)}
    mids = shapely.line_interpolate_point(links.geometry.values, 0.5, normalized=True)
    mlon, mlat = _to_lonlat(shapely.get_x(mids), shapely.get_y(mids))
    home = pd.Series([tile_key(a, b) for a, b in zip(mlat, mlon, strict=True)], index=links.index)
    lake_ids = sorted(table["lake_rows"], key=lambda k: (k[0], int(k[1:])))
    lake_pos = {k: i for i, k in enumerate(lake_ids)}
    entry_links = set(records) | {l for l, c in table["link_comp"].items() if c in lake_pos}
    covered |= set(home.loc[list(entry_links)])

    # Simplified lines in lon/lat, and every square each line's box touches.
    simp = shapely.simplify(links.geometry.values, SIMPLIFY_M)
    coords, which = shapely.get_coordinates(simp, return_index=True)
    lon, lat = _to_lonlat(coords[:, 0], coords[:, 1])
    lonlat = np.column_stack([lon, lat])
    starts = np.searchsorted(which, np.arange(len(links) + 1))
    bounds = np.column_stack([np.minimum.reduceat(lon, starts[:-1]), np.minimum.reduceat(lat, starts[:-1]),
                              np.maximum.reduceat(lon, starts[:-1]), np.maximum.reduceat(lat, starts[:-1])])
    by_tile: dict[str, list[int]] = {}
    for i, ts in enumerate(_tiles_of_bounds(bounds)):
        for t in ts:
            if t in covered:
                by_tile.setdefault(t, []).append(i)

    form_code = links["form"].map({f: i for i, f in enumerate(FORMS)}).fillna(0).astype(int).to_numpy()
    lids = links.index.to_numpy()
    own_name = links["watercourse_name"].to_numpy()
    in_tiles = sorted({i for ix in by_tile.values() for i in ix})
    river_names = link_names(net, [lids[i] for i in in_tiles if form_code[i] != 2])
    upstream = links["start_node"].map(net.upstream_m).fillna(0.0).to_numpy()
    name_of = {}   # by position
    for i in in_tiles:
        if form_code[i] == 2:   # a lake click keeps the lake's own name, never the outflow's
            n = own_name[i]
            name_of[i] = n if isinstance(n, str) and n else None
        else:
            name_of[i] = river_names[lids[i]]

    for d in ("link_index", "links"):
        (out / d).mkdir(parents=True, exist_ok=True)
        for f in (out / d).iterdir():
            f.unlink()
    n_rows = n_entries = 0
    for t, ix in sorted(by_tile.items()):
        ix = np.asarray(ix)
        names: list[str] = []
        npos: dict[str, int] = {}
        meta = []
        for i in ix:
            n = name_of[i]
            if n is not None and n not in npos:
                npos[n] = len(names)
                names.append(n)
            meta.append(form_code[i] + 4 * (npos[n] + 1 if n is not None else 0))
        seg = [lonlat[starts[i]:starts[i + 1]] for i in ix]
        offsets = np.concatenate([[0], np.cumsum([len(s) for s in seg])])
        lnos = lno_of.loc[lids[ix]].to_numpy()
        (out / "link_index" / f"{t}.bin").write_bytes(
            pack_link_index(lnos, np.asarray(meta), upstream[ix], offsets, np.concatenate(seg), names))
        entries = {}
        for i, lno in zip(ix, lnos, strict=True):
            lid = lids[i]
            if form_code[i] == 2:
                c = table["link_comp"].get(lid)
                if c in lake_pos:
                    entries[str(int(lno))] = {"lake": lake_pos[c]}
            elif lid in records:
                entries[str(int(lno))] = encode_record(records[lid], pos)
                n_rows += len(records[lid]["other"]) + len(records[lid]["same"])
        n_entries += len(entries)
        (out / "links" / f"{t}.json").write_text(json.dumps(
            {"ids": idv, "network": net_hash, **({"credits": credits["full"]} if credits and "full" in credits else {}), "links": entries}, separators=(",", ":")))

    # Lakes.
    past = history_days(np.array([config.MAX_UPSTREAM_KM * 1000.0 / river_velocity(None) / 3600.0]))
    lakes_json = []
    for k in lake_ids:
        r = table["lake_rows"][k]
        r = r[[sid in pos for sid in r["site_id"]]].copy()
        elon, elat = _to_lonlat(r["entry_x"].to_numpy(), r["entry_y"].to_numpy())
        r["lon"], r["lat"] = np.round(elon, 5), np.round(elat, 5)
        inlets = []
        for (a, b), g in r.groupby(["lon", "lat"], sort=True):
            flat = []
            for s, d, w in zip(g["site_id"], g["distance_m"], g["dilution"], strict=True):
                flat += [pos[s], round(float(d)), round(float(w) * DIL_SCALE)]
            inlets.append({"at": [float(a), float(b)], "o": flat})
        comp = table["wfd"][int(k[1:])]["links"] if k.startswith("w") else table["comp_sets"][k]
        # The furthest a click in this lake can be from an inlet: across the lake's box, plus the
        # shore rule (WFD lakes) or the snap radius (centreline lakes). It sets how many past days
        # of spill probabilities the page may need (transport.history_days).
        if k.startswith("w"):
            x0, y0, x1, y1 = table["wfd"][int(k[1:])]["geometry"].bounds
            pad = LAKE_SHORE_M
        else:
            x0, y0, x1, y1 = shapely.total_bounds(links.loc[list(comp), "geometry"].values)
            pad = PIN_SNAP_M
        if len(r):
            far = max(math.hypot(max(abs(ex - x0), abs(ex - x1)) + pad, max(abs(ey - y0), abs(ey - y1)) + pad)
                      for ex, ey in zip(r["entry_x"], r["entry_y"], strict=True))
            travel = (r["distance_m"].max() / river_velocity(None) + far / config.LAKE_VELOCITY_MS) / 3600.0
            past = max(past, history_days(np.array([travel])))
        name = None
        if k.startswith("c"):
            own = [own_name[j] for j in sorted(links.index.get_indexer(list(comp)))]
            name = next((n for n in own if isinstance(n, str) and n), None)
        lakes_json.append({"id": k, "name": name, "inlets": inlets})
    polys = []
    for i, w in enumerate(table["wfd"]):
        g = shapely.simplify(w["geometry"], LAKE_SIMPLIFY_M)
        rings = []
        for part in getattr(g, "geoms", [g]):
            x, y = np.asarray(part.exterior.coords).T
            plon, plat = _to_lonlat(x, y)
            rings.append(np.round(np.column_stack([plon, plat]), 5).tolist())
        allc = np.concatenate([np.asarray(r) for r in rings])
        lake = f"w{i}"
        polys.append({"name": w["name"], "wb_id": w["wb_id"], "area_km2": round(w["area_km2"], 3),
                      "bbox": [float(allc[:, 0].min()), float(allc[:, 1].min()), float(allc[:, 0].max()), float(allc[:, 1].max())],
                      "lake": (lake_pos.get(lake) if w["links"] else -1), "rings": rings})
    (out / "lakes.json").write_text(json.dumps({
        "ids": idv, "network": net_hash,
        "note": ("polygons: the Environment Agency's WFD lakes. lake: index into lakes; null when no monitored overflow "
                 "upstream reaches it; -1 when the lake has no river connection in the network (isolated). lakes[].inlets: "
                 "where water enters the lake, and o = flat triples (overflow index, river distance m, dilution x 10000 "
                 "before dividing by 1 + area / 5 km2)."),
        "polygons": polys, "lakes": lakes_json, **({"credits": credits} if credits else {})}, separators=(",", ":")))

    names = ovl["site_name"].where(ovl["site_name"].notna(), None) if "site_name" in ovl else pd.Series(None, index=ovl.index)
    (out / "overflow_ids.json").write_text(json.dumps({
        "ids_version": idv, "ids": ids, "name": [None if not isinstance(n, str) else n for n in names],
        "lon": [round(float(v), 4) for v in ovl["lon"]], "lat": [round(float(v), 4) for v in ovl["lat"]],
        **({"credits": credits} if credits else {})}, separators=(",", ":")))
    return {"ids": ids, "ids_version": idv, "tiles": sorted(by_tile), "past_days": int(past),
            "counts": {"links_indexed": len(in_tiles), "links_traced": len(records), "entries": n_entries,
                       "rows": n_rows, "lakes_reached": len(lake_ids), "wfd_lakes": len(polys),
                       "wfd_isolated": sum(p["lake"] == -1 for p in polys)}}


# ------------------------------------------------------------------ rain and the days
def any_point_rain(sub: pd.DataFrame, budget: int = RAIN_CELLS_PER_BUILD,
                   max_age_h: float = RAIN_MAX_AGE_H) -> tuple[pd.DataFrame, dict]:
    """Hourly rain for every overflow's cell: the cached forecast where it is under max_age_h old,
    after fetching the `budget` oldest cells that are due (over rainfall.FORECAST_TTL_S old, or
    never fetched). A failed fetch keeps what is cached; nothing here raises."""
    from dipcast.ingest import rainfall
    cells = rainfall.cells_for_sites(sub["lat"], sub["lon"])
    path = {c: rainfall.CACHE / f"forecast_{rainfall.cell_key(*c)}.parquet" for c in cells}

    def age(c) -> float:
        return time.time() - path[c].stat().st_mtime if path[c].exists() else math.inf

    due = sorted((c for c in cells if age(c) >= rainfall.FORECAST_TTL_S), key=lambda c: -age(c))
    take = due[:max(0, budget)]
    error = None
    if take:
        try:
            rainfall.fetch_forecast(take, attempts=2)   # one retry for a bad handshake; a refusal is not waited out
        except Exception as e:  # noqa: BLE001 - what was fetched before the failure is kept
            error = str(e)
            log.warning("any-point rain: %s", e)
    frames = [pd.read_parquet(path[c]) for c in cells if age(c) <= max_age_h * 3600]
    cols = ["cell_lat", "cell_lon", "time", "precip_mm", "issued_at"]
    rain = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    fetched = sum(1 for c in take if age(c) < rainfall.FORECAST_TTL_S)
    return rain, {"cells": len(cells), "due": len(due), "fetched": fetched, "usable": len(frames),
                  **({"error": error} if error else {})}


def write_overflow_days(out: Path, ov: pd.DataFrame, ids: list[str], idv: str, past: int, now: pd.Timestamp,
                        probabilities, credits: dict | None = None, rain=any_point_rain) -> dict:
    """overflow_days.json. `probabilities(ov, days, rain)` is forecast.spill_probabilities with the
    model bound, the same call the spots use, given the rain from `rain(ov)`."""
    sub = ov.drop_duplicates("site_id").set_index("site_id").loc[ids].reset_index()
    days = pd.date_range(now.floor("D") - pd.Timedelta(days=past), periods=past + DAYS_AHEAD + 1, freq="D")
    hourly, rstats = rain(sub)
    if len(hourly):
        p, ok = probabilities(sub, days, hourly)
        p = np.where(ok, p, np.nan)
    else:   # no cell has rain: every probability is unknown
        p = np.full((len(sub), len(days)), np.nan)
    # How old each overflow's rain forecast is, in hours, where it has one.
    from dipcast.ingest.rainfall import grid_cell
    issued = {}
    if len(hourly) and "issued_at" in hourly:
        got = hourly.groupby(["cell_lat", "cell_lon"])["issued_at"].max()
        issued = {k: pd.Timestamp(v) for k, v in got.items()}
    cl, cn = grid_cell(sub["lat"].to_numpy(), sub["lon"].to_numpy())
    rain_h = []
    for a, b in zip(cl, cn, strict=True):
        t = issued.get((float(a), float(b)))
        rain_h.append(None if t is None or pd.isna(t) else max(0, int((now - t).total_seconds() // 3600)))
    _, live = live_now_risk(sub.assign(weight=1.0), now)
    companies = sorted(sub["company"].fillna("unknown").astype(str).unique())
    cpos = {c: i for i, c in enumerate(companies)}
    from dipcast.ingest.live import FEED_DOWN
    down = sub[sub["status"] == FEED_DOWN]
    since = {}
    if len(down) and "feed_down_since" in down:
        for c, g in down.groupby(down["company"].fillna("unknown")):
            t = pd.to_datetime(g["feed_down_since"], utc=True).min()
            since[str(c)] = None if pd.isna(t) else t.isoformat()
    body = {
        "generated_at": now.isoformat(), "ids": idv, "days": [d.date().isoformat() for d in days], "today": int(past),
        "scale": P_SCALE,
        "p": [[None if np.isnan(v) else round(float(v) * P_SCALE) for v in row] for row in p],
        "live": [round(float(v) * P_SCALE) for v in np.asarray(live, dtype=float)],
        "status": [int(s) for s in sub["status"]],
        "company": [cpos[str(c)] for c in sub["company"].fillna("unknown")],
        "companies": companies, "feed_down_since": since, "rain_h": rain_h,
        "low": [int(i) for i in np.flatnonzero((sub.get("snap_confidence") == "low").to_numpy())],
        "assumptions": {"river_velocity_ms": river_velocity(None), "lake_velocity_ms": config.LAKE_VELOCITY_MS,
                        "t90_hours": config.T90_HOURS, "max_upstream_km": config.MAX_UPSTREAM_KM,
                        "recent_spill_hours": config.RECENT_SPILL_HOURS, "l0_m": L0_M, "lake_area_halving_km2": LAKE_A0_KM2,
                        "low_confidence_factor": LOW_CONFIDENCE_FACTOR, "max_missing_share": 0.1,
                        "rain_max_age_h": RAIN_MAX_AGE_H},
        "note": ("p: per overflow, spill probability per day in thousandths, before the lead calibration; null where "
                 "the overflow's rain cell had no data that day (the ok mask). live: the weight a spill counts with "
                 "right now, in thousandths (1000 discharging, decaying with T90 for 48 h after a spill ends). status: "
                 "1 discharging, 0 not, -1 monitor offline, -2 no live feed, -3 company feed down. rain_h: hours "
                 "between the rain forecast the overflow's probabilities used and generated_at. low: overflows "
                 "snapped by proximity alone, whose weight is multiplied by low_confidence_factor."),
        **({"credits": credits} if credits else {}),
    }
    (out / "overflow_days.json").write_text(json.dumps(body, separators=(",", ":")))
    return {"days": len(days), "n": len(ids), "rain": rstats,
            "no_data_share": round(float(np.isnan(p).mean()), 4) if p.size else 0.0}


def size_of(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def build(site: Path, net: RiverNetwork, ov: pd.DataFrame, probabilities, credits: dict | None = None,
          now: pd.Timestamp | None = None, state_dir: Path | None = None) -> dict:
    """Write site/data/anypoint/. Returns a summary for the build log and spots.json's build."""
    from dipcast.ingest.lakes import PATH as LAKES_PATH
    t0 = time.time()
    now = now or pd.Timestamp.now(tz="Europe/London")
    out = site / "data" / OUT
    out.mkdir(parents=True, exist_ok=True)
    net_path = config.PROCESSED / "river_network.pkl"
    net_hash = file_hash(net_path) if net_path.exists() else "unknown"
    lakes = transport._lakes()
    lakes_hash = file_hash(LAKES_PATH) if LAKES_PATH.exists() else "none"
    cache = (state_dir or config.STATE) / CACHE_NAME
    table, stats = upstream_table(net, ov, lakes, net_hash, lakes_hash, cache)
    t1 = time.time()
    meta = write_files(out, net, ov, table, net_hash, credits)
    t2 = time.time()
    days = write_overflow_days(out, ov, meta["ids"], meta["ids_version"], meta["past_days"], now, probabilities, credits)
    t3 = time.time()
    (out / "tiles.json").write_text(json.dumps({
        "generated_at": now.isoformat(), "network": net_hash, "ids": meta["ids_version"], "tile_deg": TILE_DEG,
        "tiles": meta["tiles"], "dilution_scale": DIL_SCALE, "p_scale": P_SCALE, "simplify_m": SIMPLIFY_M,
        "forms": FORMS, "snap_m": PIN_SNAP_M, "lake_snap_m": LAKE_SNAP_M, "lake_shore_m": LAKE_SHORE_M,
        "lake_bias": transport.LAKE_BIAS, **({"credits": credits} if credits else {})}, separators=(",", ":")))
    summary = {**stats, **meta["counts"], "tiles": len(meta["tiles"]), "overflows": days["n"], "days": days["days"],
               "rain": days["rain"], "rain_unavailable_share": days["no_data_share"], "bytes": size_of(out),
               "table_s": round(t1 - t0, 1), "files_s": round(t2 - t1, 1), "days_s": round(t3 - t2, 1),
               "seconds": round(time.time() - t0, 1)}
    log.info("any-point data: %s", summary)
    return summary
