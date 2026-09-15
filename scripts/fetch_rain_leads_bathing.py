"""Archived forecast rainfall by lead time for the inland bathing-water cells, 2023 on.

Feeds the sampling-plan test: would a plan made from the rain forecast issued k days
earlier have caught the same E. coli exceedances as one made from the rain that fell?
"""
import logging
import sys

import pandas as pd

from dipcast import config
from dipcast.ingest import rainfall_leads
from dipcast.ingest.rainfall import cells_for_sites
from dipcast.ingest.rainfall_leads import fetch_leads

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
rainfall_leads.BATCH = 2  # smaller requests: a full year x 5 leads x 5 cells was timing out
years = [int(y) for y in sys.argv[1:]] or [2023, 2024, 2025, 2026]
s = pd.read_parquet(config.RAW / "bwq_samples.parquet").drop_duplicates("bw_id")
cells = cells_for_sites(s.lat, s.lon)
print(len(cells), "cells for", len(s), "bathing waters")
for year in years:
    df = fetch_leads(cells, year)
    print(year, len(df), "rows;", df.cell_lat.nunique() if len(df) else 0, "cells;",
          "span", df.time.min() if len(df) else None, "->", df.time.max() if len(df) else None)
print("LEADS DONE")
