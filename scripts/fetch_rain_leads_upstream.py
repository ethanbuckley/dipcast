"""Archived forecast rainfall by lead time for every rain cell a full E. coli replay
needs: the inland bathing waters *and* all their upstream overflows, 2023 on.

scripts/replay_ecoli_leads.py uses it to recompute dipcast's overflow exposure from
the rain forecast issued k days before each sample, so the lead-k evaluation in
train_ecoli.py replays the whole pipeline rather than only the spot rainfall.
Cached per cell-year; safe to rerun until every cell is present.
"""
import logging
import sys

import pandas as pd

from dipcast import config
from dipcast.ingest import rainfall_leads
from dipcast.ingest.rainfall import cells_for_sites
from dipcast.ingest.rainfall_leads import fetch_leads
from dipcast.model.transport import locate_pin, river_velocity, upstream_overflows
from dipcast.network.rivers import RiverNetwork
from dipcast.overflows import load_overflows

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
rainfall_leads.BATCH = 2  # a full year x 5 leads x 5 cells times out; 2 works
years = [int(y) for y in sys.argv[1:]] or [2023, 2024, 2025, 2026]


def replay_cells() -> list[tuple[float, float]]:
    net = RiverNetwork.load()
    ov = load_overflows(net)
    s = pd.read_parquet(config.RAW / "bwq_samples.parquet").drop_duplicates("bw_id")
    lat, lon = list(s.lat), list(s.lon)
    for r in s.itertuples(index=False):
        pin = locate_pin(net, float(r.lon), float(r.lat))
        up = upstream_overflows(net, pin, ov, velocity_ms=river_velocity(None))
        lat += list(up.lat)
        lon += list(up.lon)
    return cells_for_sites(pd.Series(lat, dtype=float), pd.Series(lon, dtype=float))


if __name__ == "__main__":
    cells = replay_cells()
    print(len(cells), "cells (bathing waters plus their upstream overflows)")
    for year in years:
        df = fetch_leads(cells, year)
        print(year, len(df), "rows;", df.cell_lat.nunique() if len(df) else 0, "cells")
    print("LEADS DONE")
