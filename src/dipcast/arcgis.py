"""Minimal ArcGIS FeatureServer query client with paging.

Only the small subset of the REST API this project needs: attribute queries
with paging, count-only queries and grouped statistics. Geometry is returned
as attributes where the layer exposes Latitude/Longitude columns, otherwise the
point geometry is unpacked into `_x`/`_y` (layer CRS, usually WGS84).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_PAGE = 2000
RETRIES = 4


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET with retries. ArcGIS returns 200 with an `error` body on failure."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = client.get(url, params=params, timeout=120)
            r.raise_for_status()
            body = r.json()
            if "error" in body:
                raise RuntimeError(f"ArcGIS error {body['error']}")
            return body
        except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as e:
            last = e
            wait = 2**attempt
            log.warning("ArcGIS request failed (%s); retry in %ss", e, wait)
            time.sleep(wait)
    raise RuntimeError(f"ArcGIS request failed after {RETRIES} attempts: {last}")


def count(layer_url: str, where: str = "1=1") -> int:
    with httpx.Client() as c:
        body = _get(c, f"{layer_url}/query", {"where": where, "returnCountOnly": "true", "f": "json"})
    return int(body["count"])


def iter_features(
    layer_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    page: int = DEFAULT_PAGE,
    geometry: bool = True,
    order_by: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield one flat dict per feature, paging with resultOffset.

    Layers cap page size (often 1000 or 2000); we honour `exceededTransferLimit`
    and keep going until a short page arrives.
    """
    params: dict[str, Any] = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if geometry else "false",
        "outSR": 4326,
        "resultRecordCount": page,
        "f": "json",
    }
    if order_by:
        params["orderByFields"] = order_by
    offset = 0
    with httpx.Client() as c:
        while True:
            params["resultOffset"] = offset
            body = _get(c, f"{layer_url}/query", params)
            feats = body.get("features", [])
            for f in feats:
                row = dict(f.get("attributes", {}))
                geom = f.get("geometry")
                if geom and "x" in geom:
                    row["_x"], row["_y"] = geom["x"], geom["y"]
                yield row
            offset += len(feats)
            if not feats or (len(feats) < page and not body.get("exceededTransferLimit")):
                break


def fetch_all(layer_url: str, **kw: Any) -> list[dict[str, Any]]:
    return list(iter_features(layer_url, **kw))


def statistics(
    layer_url: str,
    stats: list[dict[str, str]],
    where: str = "1=1",
    group_by: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"where": where, "outStatistics": json.dumps(stats), "f": "json"}
    if group_by:
        params["groupByFieldsForStatistics"] = group_by
    with httpx.Client() as c:
        body = _get(c, f"{layer_url}/query", params)
    return [f["attributes"] for f in body.get("features", [])]


def distinct(layer_url: str, field: str, where: str = "1=1") -> list[Any]:
    params = {
        "where": where,
        "outFields": field,
        "returnDistinctValues": "true",
        "returnGeometry": "false",
        "f": "json",
    }
    with httpx.Client() as c:
        body = _get(c, f"{layer_url}/query", params)
    return [f["attributes"][field] for f in body.get("features", [])]
