"""Periodic jobs: refresh live status, rebuild the overflow table, score past forecasts."""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)


def refresh_all(net=None) -> dict:
    """Poll every live feed, rebuild the overflow table, re-score logged forecasts."""
    from dipcast.ingest.live import fetch_live, save_live
    from dipcast.network.rivers import RiverNetwork
    from dipcast.overflows import build_overflows

    t0 = time.time()
    live = fetch_live()
    save_live(live)
    net = net or RiverNetwork.load()
    ov = build_overflows(net)
    summary = {"live_rows": len(live), "discharging_now": int((live.status == 1).sum()),
               "overflows": len(ov), "seconds": round(time.time() - t0, 1)}
    try:
        from dipcast.forecast_log import verify_live
        v = verify_live()
        summary["verified_rows"] = int(v.get("n_scored", 0)) if v else 0
    except Exception as e:  # noqa: BLE001 - scoring must never block the refresh
        log.warning("live verification failed: %s", e)
    log.info("refresh done: %s", summary)
    return summary


def start_scheduler(interval_minutes: int, net_getter, on_done) -> threading.Event:
    """Run refresh_all every `interval_minutes` in a daemon thread. Returns a stop event."""
    stop = threading.Event()

    def loop() -> None:
        stop.wait(60)  # let the app finish warming up first
        while not stop.is_set():
            try:
                refresh_all(net_getter())
                on_done()
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                log.error("scheduled refresh failed: %s", e)
            stop.wait(max(60, interval_minutes * 60))

    threading.Thread(target=loop, name="dipcast-refresh", daemon=True).start()
    log.info("in-process refresh every %d min (DIPCAST_REFRESH_MINUTES)", interval_minutes)
    return stop
