"""Paths, service endpoints and physical constants.

Everything that a deployment might want to change lives here. Nothing in this
module performs I/O.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(os.environ.get("DIPCAST_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
CACHE = DATA / "cache"
RIVERS_GPKG = RAW / "Data" / "oprvrs_gb.gpkg"
# Mutable runtime files (live status, overflow table, forecast log, live
# verification) go to STATE so a deployment can mount a volume there while the
# built artefacts stay read-only in PROCESSED. Reads fall back to PROCESSED.
STATE = Path(os.environ.get("DIPCAST_STATE", PROCESSED))
DUCKDB_PATH = STATE / "dipcast.duckdb"
# In-process refresh of live status every N minutes; 0 disables (use scripts/refresh.py).
REFRESH_MINUTES = int(os.environ.get("DIPCAST_REFRESH_MINUTES", "0"))


def state_write(name: str) -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    return STATE / name


def state_read(name: str) -> Path:
    p = STATE / name
    return p if p.exists() else PROCESSED / name

# ---------------------------------------------------------------------------
# Live storm-overflow status feeds (one ArcGIS FeatureServer layer per company).
# All share the same schema: Id, Company, Status, StatusStart, LatestEventStart,
# LatestEventEnd, Latitude, Longitude, ReceivingWaterCourse, LastUpdated.
# Status: 1 = discharging, 0 = not discharging, -1 = monitor offline.
# Dwr Cymru (Wales) publishes no live feed to ArcGIS and is not covered live.
# ---------------------------------------------------------------------------
LIVE_FEEDS: dict[str, str] = {
    "United Utilities": "https://services5.arcgis.com/5eoLvR0f8HKb7HWP/arcgis/rest/services/United_Utilities_Storm_Overflow_Activity/FeatureServer/0",
    "Thames Water": "https://services2.arcgis.com/g6o32ZDQ33GpCIu3/arcgis/rest/services/Thames_Water_Storm_Overflow_Activity_(Production)_view/FeatureServer/0",
    "Yorkshire Water": "https://services-eu1.arcgis.com/1WqkK5cDKUbF0CkH/arcgis/rest/services/Yorkshire_Water_Storm_Overflow_Activity/FeatureServer/0",
    "Severn Trent Water": "https://services1.arcgis.com/NO7lTIlnxRMMG9Gw/arcgis/rest/services/Severn_Trent_Water_Storm_Overflow_Activity/FeatureServer/0",
    "Anglian Water": "https://services3.arcgis.com/VCOY1atHWVcDlvlJ/arcgis/rest/services/stream_service_outfall_locations_view/FeatureServer/0",
    "Wessex Water": "https://services.arcgis.com/3SZ6e0uCvPROr4mS/arcgis/rest/services/Wessex_Water_Storm_Overflow_Activity/FeatureServer/0",
    "South West Water": "https://services-eu1.arcgis.com/OMdMOtfhATJPcHe3/arcgis/rest/services/NEH_outlets_PROD/FeatureServer/0",
    "Northumbrian Water": "https://services-eu1.arcgis.com/MSNNjkZ51iVh8yBj/arcgis/rest/services/Northumbrian_Water_Storm_Overflow_Activity_2_view/FeatureServer/0",
    "Southern Water": "https://services-eu1.arcgis.com/6qJmARkS2dt2IjVA/arcgis/rest/services/SouthernWater_StormOverflowActivity_PROD_view/FeatureServer/0",
}

# Event-level spill history (start/end per event). Used for model training.
STREAM_BASE = "https://services-eu1.arcgis.com/XxS6FebPX29TRGDJ/arcgis/rest/services"
EDM_EVENT_FEEDS: dict[str, str] = {
    "United Utilities 2023": f"{STREAM_BASE}/United_Utilities_Event_Duration_Monitoring_2023/FeatureServer/0",
    "United Utilities 2024": f"{STREAM_BASE}/United_Utilities_Event_Duration_Monitoring_2024/FeatureServer/0",
    "United Utilities 2025": f"{STREAM_BASE}/United_Utilities_Event_Duration_Monitoring_2025/FeatureServer/0",
}

# Environment Agency annual returns, all years, all companies: spill counts and
# hours per overflow per calendar year, with WFD waterbody IDs. Used for priors.
EDM_ANNUAL_RETURNS = "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services/edm_annual_returns_all_years_public/FeatureServer/0"

# ---------------------------------------------------------------------------
# Rainfall: Open-Meteo (no key). Archive is ERA5-Land reanalysis at ~0.1 deg.
# Sites are snapped to a RAIN_GRID_DEG grid so one call serves many overflows.
# ---------------------------------------------------------------------------
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
RAIN_GRID_DEG = 0.1
FORECAST_DAYS = 5
FORECAST_PAST_DAYS = 31   # enough history for the 30-day and API features

# ---------------------------------------------------------------------------
# River flow: Environment Agency hydrology API (qualified, lagged) and
# flood-monitoring API (near real time).
# ---------------------------------------------------------------------------
EA_HYDROLOGY = "https://environment.data.gov.uk/hydrology"
EA_FLOOD_MONITORING = "https://environment.data.gov.uk/flood-monitoring"

# ---------------------------------------------------------------------------
# Physics defaults for transport. See model/transport.py for how they are used.
# ---------------------------------------------------------------------------
SNAP_MAX_M = 750.0            # max distance to snap an outfall to a river link
RIVER_VELOCITY_MS = 0.5       # default reach-averaged velocity when no gauge
LAKE_VELOCITY_MS = 0.05       # effective advection across a lake: ~1% of a 5 m/s wind
T90_HOURS = 30.0              # time for 90% die-off of faecal indicator bacteria
RECENT_SPILL_HOURS = 48.0     # how long a finished spill keeps contributing
MAX_UPSTREAM_KM = 60.0        # do not trace further than this upstream
