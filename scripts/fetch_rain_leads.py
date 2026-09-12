"""Download archived forecast rainfall by lead time for the UU cells (one year)."""
import logging
import sys

import pandas as pd

from dipcast import config
from dipcast.ingest.rainfall import cells_for_sites
from dipcast.ingest.rainfall_leads import fetch_leads

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
ar = pd.read_parquet(config.PROCESSED / "annual_returns.parquet")
uu = ar[(ar.company == "United Utilities") & ar.lat.notna()]
cells = cells_for_sites(uu.lat, uu.lon)
df = fetch_leads(cells, year)
print(year, len(df), "rows;", df.cell_lat.nunique() if len(df) else 0, "cells;", "leads", sorted(df.lead.unique()) if len(df) else [])
print("LEADS DONE")
