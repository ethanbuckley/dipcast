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


def _pages(
    layer_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    page: int = DEFAULT_PAGE,
    geometry: bool = True,
    order_by: str | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Yield one list of flat dicts per page, paging with resultOffset.

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
            rows = []
            for f in feats:
                row = dict(f.get("attributes", {}))
                geom = f.get("geometry")
                if geom and "x" in geom:
                    row["_x"], row["_y"] = geom["x"], geom["y"]
                rows.append(row)
            if rows:
                yield rows
            offset += len(feats)
            if not feats or (len(feats) < page and not body.get("exceededTransferLimit")):
                break


def iter_features(layer_url: str, **kw: Any) -> Iterator[dict[str, Any]]:
    """Yield one flat dict per feature (see _pages for the arguments)."""
    for rows in _pages(layer_url, **kw):
        yield from rows


def fetch_all(layer_url: str, key: str | None = None, attempts: int = 3, **kw: Any) -> list[dict[str, Any]]:
    """Every feature of the layer. With `key`, a field that names one feature, a read
    that spans more than one page and repeats a key is read again, up to `attempts`
    times, and the read with the most distinct keys is returned.

    Each page is a separate request, so a layer rewritten between two of them gives a
    torn read: the second page comes from the new copy, in another order, and repeats
    rows of the first while others are never returned. Severn Trent's and Yorkshire
    Water's live layers look rewritten whole on each refresh (on 3 Oct 2026 their
    OBJECTIDs ran in one unbroken block starting near 143.7 million and 88.9 million),
    and the 3 Oct 13:05 BST poll got 410 repeated Severn Trent rows and 29 Yorkshire ones.
    `orderByFields` does not help: these layers ignore it when outFields is "*".
    A single page is one request, so one consistent read, and is not checked."""
    if key is None:
        return list(iter_features(layer_url, **kw))
    best: list[dict[str, Any]] = []
    best_n = -1
    for attempt in range(1, attempts + 1):
        pages = list(_pages(layer_url, **kw))
        rows = [r for p in pages for r in p]
        n_keys = len({r.get(key) for r in rows})
        if len(pages) < 2 or n_keys == len(rows):
            return rows
        log.warning("%s: %d rows over %d pages but %d distinct %s; the layer changed between pages (read %d of %d)",
                    layer_url, len(rows), len(pages), n_keys, key, attempt, attempts)
        if n_keys > best_n:
            best, best_n = rows, n_keys
        if attempt < attempts:
            time.sleep(2 * attempt)
    return best


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
