"""Hourly rainfall from Open-Meteo, on a coarse grid shared by nearby overflows.

Two sources with one interface:
  * archive  - ERA5-Land reanalysis, ~0.1 deg, from 1940 to ~5 days ago.
  * forecast - current model run, hourly, up to 16 days ahead plus recent past.

Both are cached under data/cache/rain. Archive is cached per cell-year (it never
changes); forecast per cell with a short TTL.
"""

from __future__ import annotations

import logging
import time

import httpx
import numpy as np
import pandas as pd

from dipcast import config

log = logging.getLogger(__name__)

CACHE = config.CACHE / "rain"
FORECAST_TTL_S = 3600
BATCH = 10  # coordinates per Open-Meteo request


def grid_cell(lat: float | np.ndarray, lon: float | np.ndarray) -> tuple:
    """Snap to the centre of a RAIN_GRID_DEG cell. Returns (cell_lat, cell_lon)."""
    d = config.RAIN_GRID_DEG
    return (np.round(np.round(np.asarray(lat) / d) * d, 3), np.round(np.round(np.asarray(lon) / d) * d, 3))


def cell_key(cell_lat: float, cell_lon: float) -> str:
    return f"{cell_lat:.3f}_{cell_lon:.3f}"


def _request(url: str, params: dict) -> list[dict] | dict:
    for attempt in range(5):
        try:
            r = httpx.get(url, params=params, timeout=120)
            if r.status_code == 429:
                raise httpx.HTTPStatusError("rate limited", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            wait = 5 * 2**attempt
            log.warning("open-meteo %s; retry in %ss", e, wait)
            time.sleep(wait)
    raise RuntimeError("open-meteo request failed")


def _to_frame(res: dict, cell_lat: float, cell_lon: float) -> pd.DataFrame:
    h = res["hourly"]
    t = pd.to_datetime(h["time"], utc=True)
    p = np.asarray(h["precipitation"], dtype=float)
    return pd.DataFrame({"cell_lat": cell_lat, "cell_lon": cell_lon, "time": t, "precip_mm": p})


# ------------------------------------------------------------------ archive
def fetch_archive(cells: list[tuple[float, float]], year: int) -> pd.DataFrame:
    """Hourly precipitation for each cell for one calendar year (cached)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    frames, todo = [], []
    for cl, cn in cells:
        p = CACHE / f"archive_{cell_key(cl, cn)}_{year}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
        else:
            todo.append((cl, cn))
    end = min(pd.Timestamp(f"{year}-12-31"), pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=6))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        params = {
            "latitude": ",".join(f"{c[0]:.3f}" for c in chunk),
            "longitude": ",".join(f"{c[1]:.3f}" for c in chunk),
            "start_date": f"{year}-01-01",
            "end_date": end.strftime("%Y-%m-%d"),
            "hourly": "precipitation",
            "timezone": "UTC",
        }
        try:
            res = _request(config.OPEN_METEO_ARCHIVE, params)
        except RuntimeError as e:
            log.error("archive %d: skipping %d cells after repeated failures (%s)", year, len(chunk), e)
            continue
        results = res if isinstance(res, list) else [res]
        for (cl, cn), r in zip(chunk, results, strict=True):
            df = _to_frame(r, cl, cn)
            df.to_parquet(CACHE / f"archive_{cell_key(cl, cn)}_{year}.parquet", index=False)
            frames.append(df)
        log.info("archive %d: %d/%d cells", year, min(i + BATCH, len(todo)), len(todo))
        time.sleep(0.5)  # stay well inside the free-tier rate limit
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["cell_lat", "cell_lon", "time", "precip_mm"])


# ------------------------------------------------------------------ forecast
def fetch_forecast(cells: list[tuple[float, float]],
                   past_days: int = config.FORECAST_PAST_DAYS,
                   forecast_days: int = config.FORECAST_DAYS) -> pd.DataFrame:
    """Recent observed-ish and forecast hourly precipitation per cell (cached 1 h)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    frames, todo = [], []
    now = time.time()
    for cl, cn in cells:
        p = CACHE / f"forecast_{cell_key(cl, cn)}.parquet"
        if p.exists() and now - p.stat().st_mtime < FORECAST_TTL_S:
            frames.append(pd.read_parquet(p))
        else:
            todo.append((cl, cn))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        params = {
            "latitude": ",".join(f"{c[0]:.3f}" for c in chunk),
            "longitude": ",".join(f"{c[1]:.3f}" for c in chunk),
            "hourly": "precipitation",
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "UTC",
        }
        res = _request(config.OPEN_METEO_FORECAST, params)
        results = res if isinstance(res, list) else [res]
        for (cl, cn), r in zip(chunk, results, strict=True):
            df = _to_frame(r, cl, cn)
            df["issued_at"] = pd.Timestamp.now(tz="UTC")
            df.to_parquet(CACHE / f"forecast_{cell_key(cl, cn)}.parquet", index=False)
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["cell_lat", "cell_lon", "time", "precip_mm", "issued_at"])


def cells_for_sites(lat: pd.Series, lon: pd.Series) -> list[tuple[float, float]]:
    cl, cn = grid_cell(lat.to_numpy(), lon.to_numpy())
    return sorted({(float(a), float(b)) for a, b in zip(cl, cn, strict=True)})
