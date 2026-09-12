"""HTTP API and static map. Run: uv run uvicorn dipcast.api.app:app --reload"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dipcast import __version__, config
from dipcast.forecast_log import load_verification
from dipcast.jobs import start_scheduler
from dipcast.model.forecast import _net, forecast_point, overflows_geojson, reload_caches

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
# uvicorn configures its own loggers only; make dipcast's INFO visible alongside them.
logging.getLogger("dipcast").setLevel(logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
NO_CACHE = {"Cache-Control": "no-cache"}


def _warm_up() -> None:
    """Load the river network, overflow table and model so the first visitor
    does not pay the ~15 s cold-start cost. Runs in a thread; health stays green."""
    try:
        from dipcast.model.forecast import _model, _overflows
        _net(); _overflows(); _model()
        log.info("warm-up complete")
    except Exception as e:  # noqa: BLE001 - warm-up is best effort
        log.warning("warm-up failed: %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import threading
    threading.Thread(target=_warm_up, name="dipcast-warmup", daemon=True).start()
    stop = None
    if config.REFRESH_MINUTES > 0:
        stop = start_scheduler(config.REFRESH_MINUTES, _net, reload_caches)
    yield
    if stop is not None:
        stop.set()


app = FastAPI(title="dipcast", version=__version__, lifespan=lifespan,
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


@app.get("/api/verification")
def api_verification():
    return JSONResponse(load_verification())


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "refresh_minutes": config.REFRESH_MINUTES}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


@app.get("/verification")
def verification_page():
    return FileResponse(STATIC / "verification.html", headers=NO_CACHE)


@app.get("/terms")
def terms_page():
    return FileResponse(STATIC / "terms.html", headers=NO_CACHE)


@app.get("/privacy")
def privacy_page():
    return FileResponse(STATIC / "privacy.html", headers=NO_CACHE)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
