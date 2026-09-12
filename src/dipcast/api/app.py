"""HTTP API and static map. Run: uv run uvicorn dipcast.api.app:app --reload"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dipcast import __version__, config
from dipcast.model.forecast import forecast_point, overflows_geojson, reload_caches

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="dipcast", version=__version__,
              description="Sewage-pollution risk forecasts for inland swim spots in England.")


@app.get("/api/forecast")
def api_forecast(lat: float = Query(..., ge=49, le=61), lon: float = Query(..., ge=-9, le=3),
                 days: int = Query(4, ge=1, le=7), max_km: float = Query(config.MAX_UPSTREAM_KM, ge=5, le=200)):
    try:
        return JSONResponse(forecast_point(lat, lon, days_ahead=days, max_km=max_km))
    except Exception as e:  # surface the reason, keep the server up
        log.exception("forecast failed")
        raise HTTPException(500, f"forecast failed: {e}") from e


@app.get("/api/overflows")
def api_overflows(bbox: str | None = Query(None, description="min_lon,min_lat,max_lon,max_lat")):
    box = None
    if bbox:
        try:
            box = tuple(float(v) for v in bbox.split(","))
            assert len(box) == 4
        except (ValueError, AssertionError) as e:
            raise HTTPException(400, "bbox must be min_lon,min_lat,max_lon,max_lat") from e
    return JSONResponse(overflows_geojson(box))


@app.post("/api/reload")
def api_reload():
    reload_caches()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
