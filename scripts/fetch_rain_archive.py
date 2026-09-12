"""Download hourly ERA5-Land rainfall for every grid cell containing a United
Utilities overflow, for the training years. Cached per cell-year; safe to rerun."""

import logging
import sys

import pandas as pd

from dipcast import config
from dipcast.ingest.rainfall import cells_for_sites, fetch_archive

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

years = [int(y) for y in sys.argv[1:]] or [2023, 2024, 2025]
ar = pd.read_parquet(config.PROCESSED / "annual_returns.parquet")
uu = ar[(ar.company == "United Utilities") & ar.lat.notna()]
cells = cells_for_sites(uu.lat, uu.lon)
print(f"{len(uu.site_id.unique())} UU sites -> {len(cells)} rain cells")
for y in years:
    df = fetch_archive(cells, y)
    print(y, len(df), "hourly rows", df.time.min(), "->", df.time.max())
print("RAIN ARCHIVE DONE")
