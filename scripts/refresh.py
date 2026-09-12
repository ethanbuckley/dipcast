"""Refresh live overflow status and rebuild the overflow table. Run on a schedule
(every 15-30 min) so that 'right now' risk and the accumulating live history stay
current. Safe to run while the API is up; call POST /api/reload afterwards."""

import logging

from dipcast.ingest.live import fetch_live, save_live
from dipcast.network.rivers import RiverNetwork
from dipcast.overflows import build_overflows

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

live = fetch_live()
save_live(live)
df = build_overflows(RiverNetwork.load())
print(f"live rows {len(live)}, discharging now {(live.status == 1).sum()}, overflows {len(df)}")
