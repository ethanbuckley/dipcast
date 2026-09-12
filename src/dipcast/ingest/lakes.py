"""WFD lake water body polygons (Environment Agency, Cycle 3). ~1,000 lakes in
England above 50 ha (5 ha in protected areas). Used to decide that a click is
'in a lake', to find the lake's centreline links, and for a lake-size term."""

from __future__ import annotations

import json
import logging

import geopandas as gpd
import httpx

from dipcast import config

log = logging.getLogger(__name__)

SERVICE = "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services/WFD_Lake_Water_Bodies_Cycle_3/FeatureServer/1"
RAW = config.RAW / "wfd_lakes_cycle3.geojson"
PATH = config.PROCESSED / "lakes.parquet"


def fetch_lakes(page: int = 500) -> gpd.GeoDataFrame:
    feats, offset = [], 0
    with httpx.Client(timeout=180) as c:
        while True:
            r = c.get(f"{SERVICE}/query", params={
                "where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson",
                "resultRecordCount": page, "resultOffset": offset})
            r.raise_for_status()
            body = r.json()
            got = body.get("features", [])
            feats.extend(got)
            offset += len(got)
            if len(got) < page:
                break
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    g = gpd.GeoDataFrame.from_features(feats, crs=4326).to_crs(27700)
    g.columns = [c.lower() for c in g.columns]
    ren = {}
    if "wb_name" in g:
        ren["wb_name"] = "lake_name"
    if "opcat_name" in g:
        ren["opcat_name"] = "catchment"
    g = g.rename(columns=ren)
    g["area_km2"] = g.geometry.area / 1e6
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    keep = [c for c in ["wb_id", "lake_name", "catchment", "area_km2", "geometry"] if c in g]
    g = g[keep].reset_index(drop=True)
    g.to_parquet(PATH)
    log.info("lakes: %d polygons, %.0f km2 total", len(g), g.area_km2.sum())
    return g


def load_lakes() -> gpd.GeoDataFrame:
    if PATH.exists():
        return gpd.read_parquet(PATH)
    return fetch_lakes()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    g = fetch_lakes()
    print(g.drop(columns="geometry").sort_values("area_km2", ascending=False).head(8).to_string())
