"""From 'that overflow spilled' to 'it reaches this swim spot'.

Three physical effects, each a multiplicative weight in [0, 1]:

* travel time     t = d_river / v_river + d_lake / v_lake
* die-off         10 ** (-t / T90)   first-order decay of faecal indicator
                  bacteria; T90 is the time for a 90% reduction
* dilution        (L_up(overflow) + L0) / (L_up(spot) + L0), capped at 1, where
                  L_up is the total upstream network length, a proxy for
                  catchment area and hence flow. L0 keeps tiny tributaries
                  from vanishing.

Their product is treated as the probability that a spill at that overflow, if
it happens, meaningfully affects water quality at the spot. Risk is then
1 - prod(1 - p_i * w_i) over upstream overflows: the chance at least one spill
reaches the swimmer.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd
from shapely.geometry import Point

from dipcast import config
from dipcast.ingest.lakes import load_lakes
from dipcast.network.rivers import RiverNetwork, Snap, lonlat_to_bng

log = logging.getLogger(__name__)

L0_M = 5_000.0          # dilution smoothing length
LAKE_SNAP_M = 1_500.0   # fallback: a pin this close to a lake centreline is 'in the lake'
LAKE_SHORE_M = 150.0    # a click this close to a WFD lake polygon counts as in that lake
LAKE_A0_KM2 = 5.0       # lake area at which the lake dilution factor halves the weight
LOW_CONFIDENCE_FACTOR = 0.7   # outfalls snapped by proximity alone (750-1500 m, no name match)


@lru_cache(maxsize=1)
def _lakes():
    try:
        return load_lakes()
    except Exception as e:  # polygons are an enhancement; the centreline heuristic still works
        log.warning("lake polygons unavailable: %s", e)
        return None


@dataclass
class PinLocation:
    mode: str                          # 'river' | 'lake' | 'none'
    x: float
    y: float
    snap: Snap | None
    lake_links: set[str] = field(default_factory=set)
    outlet_node: str | None = None
    trace_node: str | None = None      # node from which upstream tracing starts
    trace_offset_m: float = 0.0        # distance from trace_node down to the pin (river mode)
    watercourse: str | None = None
    lake_area_km2: float | None = None
    lake_source: str | None = None     # 'polygon' (WFD) or 'centreline' (OS Open Rivers only)
    adopted_main_channel: bool = False # pin was on a side channel; traced the main river instead


PIN_SNAP_M = 1_500.0    # users click imprecisely; allow a wider search than for outfalls
LAKE_BIAS = 0.5         # lake centrelines sit further from the shore than inflow becks do
ADOPT_RADIUS_M = 500.0  # side channels: look this far for the main channel
ADOPT_MIN_M = 5_000.0   # ...when the snapped link has less than this much network upstream
ADOPT_RATIO = 5.0       # ...and the alternative has at least this many times more


def _adopt_main_channel(net: RiverNetwork, x: float, y: float, river: Snap) -> tuple[Snap, bool]:
    """A pin on a mill stream, leat or braided side channel is in main-channel
    water, but OS Open Rivers often leaves such channels disconnected upstream.
    If the snapped link has a tiny upstream network and a nearby river link has a
    much larger one, snap to that link instead."""
    own = net.link_upstream_m(river.link_id)
    if own >= ADOPT_MIN_M:
        return river, False
    cand = net.candidates_xy(x, y, ADOPT_RADIUS_M)
    cand = cand[cand["form"].isin(["inlandRiver", "tidalRiver"]) & (cand.index != river.link_id)]
    if cand.empty:
        return river, False
    ups = cand["start_node"].map(net.upstream_m).fillna(0.0)
    best = ups.idxmax()
    if ups[best] < max(ADOPT_MIN_M, ADOPT_RATIO * own):
        return river, False
    row = cand.loc[best]
    pt = Point(x, y)
    frac = float(row.geometry.project(pt, normalized=True))
    sp = row.geometry.interpolate(frac, normalized=True)
    log.info("adopted main channel %s (%.0f km upstream) over side channel (%.1f km)",
             row["watercourse_name"], ups[best] / 1000, own / 1000)
    return Snap(link_id=best, frac=frac, dist_m=float(row["dist_m"]), form=row["form"], x=sp.x, y=sp.y), True


def _lake_polygon_at(x: float, y: float):
    lakes = _lakes()
    if lakes is None or lakes.empty:
        return None
    pt = Point(x, y)
    idx = lakes.sindex.query(pt.buffer(LAKE_SHORE_M), predicate="intersects")
    if len(idx) == 0:
        return None
    cand = lakes.iloc[idx]
    return cand.loc[cand.geometry.distance(pt).idxmin()]


def _snap_to_links(net: RiverNetwork, x: float, y: float, link_ids: set[str]) -> Snap | None:
    if not link_ids:
        return None
    pt = Point(x, y)
    sub = net.links.loc[list(link_ids)]
    d = sub.geometry.distance(pt)
    lid = d.idxmin()
    geom = sub.loc[lid, "geometry"]
    frac = float(geom.project(pt, normalized=True))
    sp = geom.interpolate(frac, normalized=True)
    return Snap(link_id=lid, frac=frac, dist_m=float(d.min()), form=sub.loc[lid, "form"], x=sp.x, y=sp.y)


def locate_pin(net: RiverNetwork, lon: float, lat: float) -> PinLocation:
    x, y = lonlat_to_bng(lon, lat)

    # 1. Inside (or on the shore of) a WFD lake polygon: the lake's centreline
    #    links are the ones intersecting the polygon.
    poly = _lake_polygon_at(x, y)
    if poly is not None:
        idx = net.links.sindex.query(poly.geometry, predicate="intersects")
        cand = net.links.iloc[idx]
        lake_links = set(cand.index[cand["form"] == "lake"])
        if not lake_links:
            near = net.snap_xy(x, y, max_m=LAKE_SNAP_M, forms=("lake",))
            if near is not None:
                lake_links = net.lake_component(near.link_id)
        if lake_links:
            outlet = _lake_outlet(net, lake_links)
            name = poly.get("lake_name")
            return PinLocation("lake", x, y, _snap_to_links(net, x, y, lake_links), lake_links, outlet,
                               trace_node=outlet, watercourse=str(name) if isinstance(name, str) else None,
                               lake_area_km2=float(poly["area_km2"]), lake_source="polygon")

    # 2. Otherwise decide between river and (small, unmapped) lake by centreline distance.
    river = net.snap_xy(x, y, max_m=PIN_SNAP_M)
    lake = net.snap_xy(x, y, max_m=LAKE_SNAP_M, forms=("lake",))
    use_lake = lake is not None and (
        river is None or river.form == "lake" or lake.dist_m * LAKE_BIAS < river.dist_m
    )
    if use_lake:
        comp = net.lake_component(lake.link_id)
        outlet = _lake_outlet(net, comp)
        return PinLocation("lake", x, y, lake, comp, outlet, trace_node=outlet,
                           watercourse=_link_name(net, lake.link_id), lake_source="centreline")
    if river is None:
        return PinLocation("none", x, y, None)
    river, adopted = _adopt_main_channel(net, x, y, river)
    start, _ = net.link_nodes(river.link_id)
    return PinLocation("river", x, y, river, trace_node=start,
                       trace_offset_m=river.frac * net.links.loc[river.link_id, "length"],
                       watercourse=_link_name(net, river.link_id), adopted_main_channel=adopted)


def _link_name(net: RiverNetwork, link_id: str, max_steps: int = 12) -> str | None:
    """Name of the link, else the first named link downstream (the river it feeds)."""
    lid = link_id
    for _ in range(max_steps):
        if lid not in net.links.index:
            return None
        name = net.links.loc[lid, "watercourse_name"]
        if isinstance(name, str) and name:
            return name
        end = net.links.loc[lid, "end_node"]
        succ = list(net.graph.successors(end))
        if not succ:
            return None
        lid = net.graph.edges[end, succ[0]]["link"]
    return None


def _lake_outlet(net: RiverNetwork, comp: set[str]) -> str:
    """Node where water leaves the lake: an out-edge to a non-lake link, else a sink."""
    g = net.graph
    nodes = set()
    for lid in comp:
        s, e = net.link_nodes(lid)
        nodes.update((s, e))
    candidates = []
    for n in nodes:
        outs = [g.edges[n, m]["link"] for m in g.successors(n)]
        if not outs or any(l not in comp for l in outs):
            candidates.append(n)
    if not candidates:
        candidates = list(nodes)
    # Prefer the candidate with the most upstream network (the main outlet).
    return max(candidates, key=lambda n: net.upstream_length(n, max_length_m=200_000))


def upstream_lengths(net: RiverNetwork, root: str, cap_m: float) -> tuple[dict[str, float], dict[str, float]]:
    """(link -> distance from link's downstream end to root, node -> exact upstream
    network length within the cap) for the subgraph upstream of `root`.
    Exact means the total length of the *set* of links upstream, so braided
    channels that split and rejoin are not counted twice."""
    link_dist = net.upstream_edges(root, cap_m)
    nodes = {root}
    for lid in link_dist:
        if lid.startswith("repair:"):
            continue
        s_, e_ = net.link_nodes(lid)
        nodes.update((s_, e_))
    length_of = {lid: (net.repairs.get(lid, 0.0) if lid.startswith("repair:") else net.links.loc[lid, "length"])
                 for lid in link_dist}
    g = net.graph
    memo: dict[str, float] = {}

    def total(n: str) -> float:
        if n in memo:
            return memo[n]
        seen: set[str] = set()
        stack = [n]
        while stack:
            m = stack.pop()
            for p in g.predecessors(m):
                lid = g.edges[p, m]["link"]
                if lid in length_of and lid not in seen:
                    seen.add(lid)
                    stack.append(p)
        memo[n] = float(sum(length_of[l] for l in seen))
        return memo[n]

    lup = {n: total(n) for n in nodes}
    return link_dist, lup


def river_velocity(state_index: float | None) -> float:
    """Reach-averaged velocity (m/s) from the EA level index (0 = typical low, 1 = typical high)."""
    if state_index is None or not np.isfinite(state_index):
        return config.RIVER_VELOCITY_MS
    return float(0.3 + 0.7 * min(max(state_index, 0.0), 1.5))


def _path_to_lake(net: RiverNetwork, link_id: str, frac: float, comp: set[str],
                  on_path: set[str], max_steps: int = 5000) -> tuple[float, tuple[float, float]] | None:
    """Walk downstream from an overflow until the water enters the lake.
    Returns (river distance m, entry point xy) or None if the lake is never reached."""
    links = net.links
    if link_id in comp:
        pt = links.loc[link_id, "geometry"].interpolate(frac, normalized=True)
        return 0.0, (pt.x, pt.y)
    d = (1.0 - frac) * links.loc[link_id, "length"]
    node = links.loc[link_id, "end_node"]
    for _ in range(max_steps):
        nxt = None
        for m in net.graph.successors(node):
            lid = net.graph.edges[node, m]["link"]
            if lid in comp:
                pt = links.loc[lid, "geometry"].coords[0]
                return d, (pt[0], pt[1])
            if lid in on_path:
                nxt = (m, lid)
        if nxt is None:
            return None
        node, lid = nxt
        d += net.repairs.get(lid, 0.0) if lid.startswith("repair:") else links.loc[lid, "length"]
    return None


def upstream_overflows(net: RiverNetwork, pin: PinLocation, overflows: pd.DataFrame,
                       velocity_ms: float, max_km: float = config.MAX_UPSTREAM_KM,
                       t90_h: float = config.T90_HOURS) -> pd.DataFrame:
    """Overflows upstream of the pin with distance, travel time and transport weight."""
    cols = list(overflows.columns) + ["distance_m", "lake_distance_m", "travel_h", "decay", "dilution", "weight"]
    if pin.mode == "none" or pin.trace_node is None:
        return pd.DataFrame(columns=cols)
    cap = max_km * 1000.0
    link_dist, lup = upstream_lengths(net, pin.trace_node, cap)
    on_path = set(link_dist)
    ov = overflows[overflows["link_id"].isin(on_path)].copy()

    # Overflows on the pin's own link, upstream of the pin (river mode only).
    if pin.mode == "river" and pin.snap is not None:
        same = overflows[(overflows["link_id"] == pin.snap.link_id) & (overflows["frac"] < pin.snap.frac)].copy()
        same["distance_m"] = (pin.snap.frac - same["frac"]) * same["link_length"]
        ov = ov[ov["link_id"] != pin.snap.link_id]
    else:
        same = ov.iloc[0:0].copy()

    if pin.mode == "river":
        ov["distance_m"] = ((1.0 - ov["frac"]) * ov["link_length"]
                            + ov["link_id"].map(link_dist) + pin.trace_offset_m)
        ov["lake_distance_m"] = 0.0
        same["lake_distance_m"] = 0.0
        ov = pd.concat([ov, same], ignore_index=True)
        spot_lup = lup.get(pin.trace_node, 0.0) + pin.trace_offset_m
    else:
        rows = []
        for _, r in ov.iterrows():
            res = _path_to_lake(net, r["link_id"], r["frac"], pin.lake_links, on_path)
            if res is None:
                continue
            d_river, (ex, ey) = res
            d_lake = math.hypot(ex - pin.x, ey - pin.y)
            rows.append((r.name, d_river, d_lake))
        if rows:
            idx, dr, dl = zip(*rows, strict=True)
            ov = ov.loc[list(idx)].copy()
            ov["distance_m"] = list(dr)
            ov["lake_distance_m"] = list(dl)
        else:
            ov = ov.iloc[0:0].copy()
            ov["distance_m"] = []
            ov["lake_distance_m"] = []
        spot_lup = lup.get(pin.trace_node, 0.0)

    if ov.empty:
        return pd.DataFrame(columns=cols)
    ov["travel_h"] = (ov["distance_m"] / velocity_ms + ov["lake_distance_m"] / config.LAKE_VELOCITY_MS) / 3600.0
    ov["decay"] = np.power(10.0, -ov["travel_h"] / t90_h)
    ov_lup = ov["start_node"].map(lup).fillna(0.0) + (1.0 - ov["frac"]) * ov["link_length"] * 0  # at the outfall
    ov["dilution"] = np.minimum(1.0, (ov_lup + L0_M) / (spot_lup + L0_M))
    if pin.mode == "lake" and pin.lake_area_km2:
        # A big lake dilutes an inflow far more than a river reach of similar
        # upstream network would; halve the weight at LAKE_A0_KM2 and shrink from there.
        ov["dilution"] = ov["dilution"] / (1.0 + pin.lake_area_km2 / LAKE_A0_KM2)
    ov["weight"] = ov["decay"] * ov["dilution"]
    if "snap_confidence" in ov:
        ov.loc[ov["snap_confidence"] == "low", "weight"] *= LOW_CONFIDENCE_FACTOR
    ov = ov[ov["distance_m"] <= cap]
    return ov.sort_values("weight", ascending=False).reset_index(drop=True)


def combine_daily(p: np.ndarray, weights: np.ndarray, travel_h: np.ndarray) -> np.ndarray:
    """p: (n_overflows, n_days) spill probabilities by overflow and spill day.
    Returns risk per day at the spot, shifting each overflow's effect by its travel time."""
    n, d = p.shape
    if n == 0:
        return np.zeros(d)
    shift = travel_h / 24.0
    k = np.floor(shift).astype(int)
    a = shift - k
    eff = np.zeros_like(p)
    for i in range(n):
        for j in range(d):
            if j + k[i] < d:
                eff[i, j + k[i]] += (1 - a[i]) * p[i, j]
            if j + k[i] + 1 < d:
                eff[i, j + k[i] + 1] += a[i] * p[i, j]
    eff = np.clip(eff, 0, 1) * weights[:, None]
    return 1.0 - np.prod(1.0 - eff, axis=0)


def live_now_risk(ov: pd.DataFrame, now: pd.Timestamp, recent_h: float = config.RECENT_SPILL_HOURS,
                  t90_h: float = config.T90_HOURS) -> tuple[float, pd.Series]:
    """Risk right now from live status: discharging, or finished within recent_h."""
    if ov.empty:
        return 0.0, pd.Series(dtype=float)
    active = ov["status"] == 1
    end = pd.to_datetime(ov["latest_event_end"], utc=True)
    hrs_since = (now - end).dt.total_seconds() / 3600.0
    recent = (~active) & hrs_since.between(0, recent_h)
    extra = np.where(active, 1.0, np.where(recent, np.power(10.0, -hrs_since.fillna(1e9) / t90_h), 0.0))
    contrib = ov["weight"].to_numpy() * extra
    risk = 1.0 - np.prod(1.0 - np.clip(contrib, 0, 1))
    return float(risk), pd.Series(contrib, index=ov.index)


def risk_label(r: float) -> str:
    if r < 0.15:
        return "low"
    if r < 0.4:
        return "moderate"
    if r < 0.7:
        return "high"
    return "very high"
