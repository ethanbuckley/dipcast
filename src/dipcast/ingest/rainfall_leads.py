"""Archived rainfall forecasts by lead time, from Open-Meteo's previous-runs API.

`precipitation` is the most recent model run for each hour (lead 0, close to an
analysis); `precipitation_previous_dayN` is the forecast for that hour issued N
days earlier. Together they let us verify the spill model with the rain a user
would actually have seen, rather than reanalysis.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.ingest.rainfall import _request, cell_key

log = logging.getLogger(__name__)

PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
CACHE = config.CACHE / "rain"
LEADS = [0, 1, 2, 3, 4]
VARS = ["precipitation"] + [f"precipitation_previous_day{k}" for k in LEADS if k > 0]
BATCH = 5


def fetch_leads(cells: list[tuple[float, float]], year: int) -> pd.DataFrame:
    """Hourly precipitation per cell and lead for one year. Columns:
    cell_lat, cell_lon, time, lead, precip_mm."""
    CACHE.mkdir(parents=True, exist_ok=True)
    frames, todo = [], []
    for cl, cn in cells:
        p = CACHE / f"leads_{cell_key(cl, cn)}_{year}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
        else:
            todo.append((cl, cn))
    end = min(pd.Timestamp(f"{year}-12-31"), pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=2))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        params = {
            "latitude": ",".join(f"{c[0]:.3f}" for c in chunk),
            "longitude": ",".join(f"{c[1]:.3f}" for c in chunk),
            "start_date": f"{year}-01-01", "end_date": end.strftime("%Y-%m-%d"),
            "hourly": ",".join(VARS), "timezone": "UTC",
        }
        try:
            res = _request(PREVIOUS_RUNS, params)
        except RuntimeError as e:
            log.error("leads %d: skipping %d cells (%s)", year, len(chunk), e)
            continue
        results = res if isinstance(res, list) else [res]
        for (cl, cn), r in zip(chunk, results, strict=True):
            h = r["hourly"]
            t = pd.to_datetime(h["time"], utc=True)
            parts = []
            for lead, var in zip(LEADS, VARS, strict=True):
                parts.append(pd.DataFrame({"cell_lat": cl, "cell_lon": cn, "time": t, "lead": lead,
                                           "precip_mm": np.asarray(h[var], dtype=float)}))
            df = pd.concat(parts, ignore_index=True)
            df.to_parquet(CACHE / f"leads_{cell_key(cl, cn)}_{year}.parquet", index=False)
            frames.append(df)
        log.info("leads %d: %d/%d cells", year, min(i + BATCH, len(todo)), len(todo))
        time.sleep(1.0)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["cell_lat", "cell_lon", "time", "lead", "precip_mm"])
