"""Refresh live overflow status, rebuild the overflow table and score logged
forecasts. Run on a schedule (every 15-30 min) when the API is not doing it
in-process (DIPCAST_REFRESH_MINUTES=0). Call POST /api/reload afterwards."""

import logging

from dipcast.jobs import refresh_all

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
print(refresh_all())
