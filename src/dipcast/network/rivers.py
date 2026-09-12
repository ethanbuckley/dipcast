"""River network from OS Open Rivers: snapping, upstream tracing, path lengths.

The OS Open Rivers product is a directed link/node network in British National
Grid (EPSG:27700, metres). Links carry `form` (inlandRiver, tidalRiver, lake,
canal) and a flow direction. We build a `networkx.DiGraph` whose nodes are
hydro-node IDs and whose edges are links oriented in the direction of flow.

Coordinates in this module are always (easting, northing) in metres unless a
function name says otherwise.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

from dipcast import config

log = logging.getLogger(__name__)

_TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)
_TO_WGS = Transformer.from_crs(27700, 4326, always_xy=True)


def lonlat_to_bng(lon: float, lat: float) -> tuple[float, float]:
    return _TO_BNG.transform(lon, lat)


def bng_to_lonlat(x: float, y: float) -> tuple[float, float]:
    return _TO_WGS.transform(x, y)


@dataclass(frozen=True)
class Snap:
    """A point snapped onto a link."""

    link_id: str
    frac: float        # fraction along the link in flow direction, 0 = upstream end
    dist_m: float      # distance from the original point to the link
    form: str
    x: float           # snapped easting
    y: float           # snapped northing


class RiverNetwork:
    def __init__(self, links: gpd.GeoDataFrame, graph: nx.DiGraph):
        self.links = links            # indexed by link id, EPSG:27700
        self.graph = graph
        self._sindex = links.sindex   # STRtree, built lazily by geopandas

    # ------------------------------------------------------------------ build
    @classmethod
    def from_gpkg(cls, path: Path = config.RIVERS_GPKG) -> RiverNetwork:
        log.info("reading %s ...", path)
        links = gpd.read_file(path, layer="watercourse_link")
        links = links.set_index("id", drop=False)
        # Orient every link in the direction of flow.
        rev = links["flow_direction"].eq("in opposite direction")
        if rev.any():
            s, e = links.loc[rev, "start_node"].copy(), links.loc[rev, "end_node"].copy()
            links.loc[rev, "start_node"], links.loc[rev, "end_node"] = e.values, s.values
            links.loc[rev, "geometry"] = links.loc[rev, "geometry"].reverse()
        links["length"] = links.geometry.length
        g = nx.DiGraph()
        g.add_edges_from(
            zip(links["start_node"], links["end_node"], strict=True),
        )
        # Store link id and length on edges (one link per node pair in practice).
        nx.set_edge_attributes(
            g,
            {(s, e): {"link": i, "length": ln, "form": f}
             for s, e, i, ln, f in zip(links["start_node"], links["end_node"], links.index,
                                       links["length"], links["form"], strict=True)},
        )
        log.info("network: %d links, %d nodes", len(links), g.number_of_nodes())
        return cls(links, g)

    def save(self, path: Path = config.PROCESSED / "river_network.pkl") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"links": self.links, "graph": self.graph}, f, protocol=5)

    @classmethod
    def load(cls, path: Path = config.PROCESSED / "river_network.pkl") -> RiverNetwork:
        if not path.exists():
            net = cls.from_gpkg()
            net.save(path)
            return net
        with open(path, "rb") as f:
            d = pickle.load(f)
        return cls(d["links"], d["graph"])

    # ------------------------------------------------------------------ snap
    def snap_xy(self, x: float, y: float, max_m: float = config.SNAP_MAX_M,
                forms: tuple[str, ...] | None = None) -> Snap | None:
        pt = Point(x, y)
        cand = self.links if forms is None else self.links[self.links["form"].isin(forms)]
        if cand.empty:
            return None
        idx = cand.sindex.nearest(pt, return_all=False, max_distance=max_m)
        if idx.shape[1] == 0:
            return None
        row = cand.iloc[idx[1, 0]]
        geom = row.geometry
        frac = float(geom.project(pt, normalized=True))
        sp = geom.interpolate(frac, normalized=True)
        return Snap(link_id=row["id"], frac=frac, dist_m=float(geom.distance(pt)),
                    form=row["form"], x=sp.x, y=sp.y)

    def snap_lonlat(self, lon: float, lat: float, **kw) -> Snap | None:
        x, y = lonlat_to_bng(lon, lat)
        return self.snap_xy(x, y, **kw)

    def candidates_xy(self, x: float, y: float, max_m: float) -> pd.DataFrame:
        """All links within `max_m` of a point, with distance, nearest first."""
        pt = Point(x, y)
        idx = self.links.sindex.query(pt.buffer(max_m), predicate="intersects")
        if len(idx) == 0:
            return self.links.iloc[0:0].assign(dist_m=pd.Series(dtype=float))
        cand = self.links.iloc[idx].copy()
        cand["dist_m"] = cand.geometry.distance(pt)
        return cand[cand["dist_m"] <= max_m].sort_values("dist_m")

    def snap_many(self, lon: np.ndarray, lat: np.ndarray, max_m: float = config.SNAP_MAX_M) -> pd.DataFrame:
        """Vectorised snap for many points. Returns a frame aligned to the input."""
        x, y = _TO_BNG.transform(lon, lat)
        pts = gpd.GeoSeries(gpd.points_from_xy(x, y), crs=27700)
        idx = self.links.sindex.nearest(pts, return_all=False, max_distance=max_m)
        out = pd.DataFrame({"link_id": pd.Series([None] * len(pts), dtype=object),
                            "frac": np.nan, "dist_m": np.nan, "form": None})
        if idx.shape[1]:
            q, t = idx[0], idx[1]
            geoms = self.links.geometry.values[t]
            p = pts.values[q]
            out.loc[q, "link_id"] = self.links.index.values[t]
            out.loc[q, "frac"] = [g.project(pp, normalized=True) for g, pp in zip(geoms, p, strict=True)]
            out.loc[q, "dist_m"] = [g.distance(pp) for g, pp in zip(geoms, p, strict=True)]
            out.loc[q, "form"] = self.links["form"].values[t]
        return out

    # ------------------------------------------------------------------ trace
    def link_nodes(self, link_id: str) -> tuple[str, str]:
        r = self.links.loc[link_id]
        return r["start_node"], r["end_node"]

    def upstream_edges(self, node: str, max_length_m: float | None = None) -> dict[str, float]:
        """All links upstream of `node`, mapped to the along-network distance (m)
        from the link's downstream end to `node`. Capped by path distance."""
        cap = np.inf if max_length_m is None else max_length_m
        dist = {node: 0.0}
        out: dict[str, float] = {}
        stack = [node]
        g = self.graph
        while stack:
            n = stack.pop()
            dn = dist[n]
            for pred in g.predecessors(n):
                ed = g.edges[pred, n]
                d_pred = dn + ed["length"]
                lid = ed["link"]
                if lid not in out or dn < out[lid]:
                    out[lid] = dn
                if d_pred <= cap and d_pred < dist.get(pred, np.inf):
                    dist[pred] = d_pred
                    stack.append(pred)
        return out

    def upstream_length(self, node: str, max_length_m: float | None = None) -> float:
        """Total upstream network length (m): a proxy for catchment area / flow."""
        edges = self.upstream_edges(node, max_length_m)
        return float(self.links.loc[list(edges.keys()), "length"].sum()) if edges else 0.0

    def downstream_nodes(self, node: str, max_length_m: float) -> dict[str, float]:
        """Nodes reachable downstream within a path distance, with distances."""
        dist = {node: 0.0}
        stack = [node]
        g = self.graph
        while stack:
            n = stack.pop()
            for succ in g.successors(n):
                d = dist[n] + g.edges[n, succ]["length"]
                if d <= max_length_m and d < dist.get(succ, np.inf):
                    dist[succ] = d
                    stack.append(succ)
        return dist

    def lake_component(self, link_id: str) -> set[str]:
        """All lake-form links forming the same water body as `link_id`."""
        if self.links.loc[link_id, "form"] != "lake":
            return {link_id}
        seen: set[str] = set()
        frontier = [link_id]
        while frontier:
            lid = frontier.pop()
            if lid in seen:
                continue
            seen.add(lid)
            s, e = self.link_nodes(lid)
            for n in (s, e):
                for a, b in list(self.graph.in_edges(n)) + list(self.graph.out_edges(n)):
                    nb = self.graph.edges[a, b]["link"]
                    if nb not in seen and self.links.loc[nb, "form"] == "lake":
                        frontier.append(nb)
        return seen


def load_network() -> RiverNetwork:
    return RiverNetwork.load()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    net = RiverNetwork.load()
    # Smoke test: the Lune at Kirkby Lonsdale (Devil's Bridge).
    s = net.snap_lonlat(-2.5945, 54.1995)
    print("snap:", s)
    if s:
        start, end = net.link_nodes(s.link_id)
        up = net.upstream_edges(start, max_length_m=config.MAX_UPSTREAM_KM * 1000)
        print("upstream links within cap:", len(up), "upstream length km:",
              round(net.upstream_length(start) / 1000, 1))
