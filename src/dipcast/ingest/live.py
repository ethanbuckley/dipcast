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


LOCAL_TZ = "Europe/London"
COVERAGE_FILE = "live_coverage.parquet"
POLL_LOG_FILE = "poll_log.parquet"


def coverage_rows(df: pd.DataFrame) -> pd.DataFrame:
    """One row per overflow for this poll: its local day and whether the status was
    known (0 or 1) or unknown (offline, missing). Summed over polls this becomes the
    per-overflow daily observation count that the live scorer needs before it may
    treat an unmatched day as 'no spill'."""
    if df.empty:
        return pd.DataFrame(columns=["site_id", "day", "n_known", "n_unknown"])
    day = pd.to_datetime(df["fetched_at"], utc=True).dt.tz_convert(LOCAL_TZ).dt.date
    known = df["status"].isin([0, 1])
    return pd.DataFrame({"site_id": df["site_id"].astype(str).to_numpy(), "day": day.to_numpy(),
                         "n_known": known.astype(int).to_numpy(), "n_unknown": (~known).astype(int).to_numpy()})


def update_coverage(df: pd.DataFrame) -> pd.DataFrame:
    """Add this poll to live_coverage.parquet (site_id, day, n_known, n_unknown) and
    return the merged table. Compact: one row per overflow per day."""
    new = coverage_rows(df)
    p = config.state_read(COVERAGE_FILE)
    if p.exists():
        old = pd.read_parquet(p)
        old["day"] = pd.to_datetime(old["day"]).dt.date
        new = pd.concat([old, new], ignore_index=True)
    cov = new.groupby(["site_id", "day"], as_index=False)[["n_known", "n_unknown"]].sum()
    cov["day"] = pd.to_datetime(cov["day"])
    write_parquet(cov, config.state_write(COVERAGE_FILE))
    return cov


def log_poll(df: pd.DataFrame, feeds: dict[str, str] | None = None) -> None:
    """Append one row per company per poll: rows returned and how many had a known
    status. A company absent from a poll (feed failure) appears with zero rows, so
    feed outages are visible afterwards."""
    fetched = df["fetched_at"].iloc[0] if len(df) else pd.Timestamp.now(tz="UTC")
    companies = list((feeds or config.LIVE_FEEDS).keys())
    g = df.groupby("company") if len(df) else None
    rows = []
    for c in companies:
        sub = g.get_group(c) if g is not None and c in g.groups else df.iloc[0:0]
        rows.append({"fetched_at": fetched, "company": c, "n_rows": len(sub),
                     "n_known": int(sub["status"].isin([0, 1]).sum()) if len(sub) else 0})
    new = pd.DataFrame(rows)
    p = config.state_read(POLL_LOG_FILE)
    if p.exists():
        new = pd.concat([pd.read_parquet(p), new], ignore_index=True)
    write_parquet(new, config.state_write(POLL_LOG_FILE))


def save_live(df: pd.DataFrame) -> None:
    write_parquet(df, config.state_write("live_latest.parquet"))
    hist_read = config.state_read("live_history.parquet")
    keep = df[["site_id", "company", "status", "status_start", "latest_event_start",
               "latest_event_end", "fetched_at"]]
    if hist_read.exists():
        old = pd.read_parquet(hist_read)
        keep = pd.concat([old, keep], ignore_index=True)
    # The history keeps one row per distinct (site, status, status_start): an event
    # log, not an observation log. Observation counts live in the coverage file.
    keep = keep.drop_duplicates(subset=["site_id", "status", "status_start"], keep="last")
    write_parquet(keep, config.state_write("live_history.parquet"))
    update_coverage(df)
    log_poll(df)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    save_live(fetch_live())
