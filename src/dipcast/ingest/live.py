"""Pull the current status of every monitored storm overflow.

Each company feed is a snapshot: one row per overflow with its current status
and the start/end of its latest event. We keep the latest snapshot and append
every distinct (site, status, status_start) to a history file so that polling
over time accumulates an event log for companies that publish no history.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from dipcast import config
from dipcast.arcgis import fetch_all
from dipcast.ingest.common import to_utc, write_parquet

log = logging.getLogger(__name__)

COLS = [
    "site_id", "company", "status", "status_start", "latest_event_start",
    "latest_event_end", "lat", "lon", "receiving_watercourse", "last_updated", "fetched_at",
]
RENAME = {
    "Id": "site_id", "Company": "company", "Status": "status", "StatusStart": "status_start",
    "LatestEventStart": "latest_event_start", "LatestEventEnd": "latest_event_end",
    "Latitude": "lat", "Longitude": "lon", "ReceivingWaterCourse": "receiving_watercourse",
    "LastUpdated": "last_updated",
}


def fetch_live(companies: dict[str, str] | None = None) -> pd.DataFrame:
    feeds = companies or config.LIVE_FEEDS
    frames = []
    for company, url in feeds.items():
        try:
            rows = fetch_all(url, geometry=True)
        except Exception as e:  # one dead feed must not kill the poll
            log.error("live feed failed for %s: %s", company, e)
            continue
        df = pd.DataFrame(rows).rename(columns=RENAME)
        df["company"] = company
        # Some feeds (South West Water) carry location only as geometry.
        for col, geo in (("lat", "_y"), ("lon", "_x")):
            if col not in df:
                df[col] = np.nan
            if geo in df:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(df[geo])
        frames.append(df)
        log.info("%s: %d overflows", company, len(df))
    if not frames:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(frames, ignore_index=True)
    for c in ["status_start", "latest_event_start", "latest_event_end", "last_updated"]:
        df[c] = to_utc(df[c]) if c in df else pd.NaT
    df["status"] = pd.to_numeric(df["status"], errors="coerce").fillna(-1).astype(int)
    df["fetched_at"] = pd.Timestamp.now(tz="UTC")
    df = df.dropna(subset=["lat", "lon"])
    return df[COLS]


def save_live(df: pd.DataFrame) -> None:
    write_parquet(df, config.PROCESSED / "live_latest.parquet")
    hist_path = config.PROCESSED / "live_history.parquet"
    keep = df[["site_id", "company", "status", "status_start", "latest_event_start",
               "latest_event_end", "fetched_at"]]
    if hist_path.exists():
        old = pd.read_parquet(hist_path)
        keep = pd.concat([old, keep], ignore_index=True)
    keep = keep.drop_duplicates(subset=["site_id", "status", "status_start"], keep="last")
    write_parquet(keep, hist_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    save_live(fetch_live())
