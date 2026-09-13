"""Build the static site: a forecast for every spot in spots.csv, the overflow
layer, the verification data and the pages, written to ./site for GitHub Pages.

Also refreshes live status, rebuilds the overflow table and scores logged
forecasts (the same work the API's in-process scheduler does), so one scheduled
run of this script is the whole back end.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time

import pandas as pd

from dipcast import __version__, config
from dipcast.forecast_log import load_verification
from dipcast.jobs import refresh_all
from dipcast.model.forecast import _net, forecast_point, overflows_geojson, reload_caches

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("build_site")

ROOT = config.ROOT
SITE = ROOT / "site"
STATIC = ROOT / "src" / "dipcast" / "api" / "static"
TEMPLATE = ROOT / "src" / "dipcast" / "site" / "index.html"
KEEP_CONTRIBUTORS = 10
# The pages were written for the FastAPI routes; rewrite them for flat files.
REWRITES = [('href="/verification"', 'href="verification.html"'), ('href="/terms"', 'href="terms.html"'),
            ('href="/privacy"', 'href="privacy.html"'), ('href="/"', 'href="index.html"'),
            ('href="/static/page.css"', 'href="page.css"'), ("fetch('/api/verification')", "fetch('data/verification.json')")]


def build(refresh: bool = True) -> dict:
    t0 = time.time()
    if refresh:
        refresh_all(_net())
        reload_caches()
    spots = pd.read_csv(ROOT / "spots.csv").fillna("")
    results = []
    for r in spots.itertuples(index=False):
        try:
            f = forecast_point(float(r.lat), float(r.lon), gauge=False,
                               kind_hint=(r.kind if r.kind in ("lake", "river") else None))
            f["contributors"] = f.get("contributors", [])[:KEEP_CONTRIBUTORS]
        except Exception as e:  # noqa: BLE001 - one bad spot must not sink the site
            log.error("%s: %s", r.name, e)
            f = {"error": f"forecast failed: {e}"}
        results.append({"id": r.id, "name": r.name, "kind": r.kind, "source": r.source, "notes": r.notes,
                        "lat": float(r.lat), "lon": float(r.lon), **f})
    generated = pd.Timestamp.now(tz="Europe/London")
    (SITE / "data").mkdir(parents=True, exist_ok=True)
    (SITE / "data" / "spots.json").write_text(json.dumps({
        "generated_at": generated.isoformat(), "version": __version__, "n": len(results), "spots": results}, default=str))
    (SITE / "data" / "overflows.geojson").write_text(json.dumps(overflows_geojson(limit=20000), default=str))
    (SITE / "data" / "verification.json").write_text(json.dumps(load_verification(), default=str))
    for name in ["verification.html", "terms.html", "privacy.html"]:
        s = (STATIC / name).read_text()
        for a, b in REWRITES:
            s = s.replace(a, b)
        (SITE / name).write_text(s)
    shutil.copy(STATIC / "page.css", SITE / "page.css")
    (SITE / "index.html").write_text(TEMPLATE.read_text())
    (SITE / ".nojekyll").write_text("")
    ok = sum(1 for r in results if "days" in r)
    summary = {"spots": len(results), "forecast_ok": ok, "seconds": round(time.time() - t0, 1),
               "generated_at": generated.isoformat()}
    log.info("site built: %s", summary)
    return summary


if __name__ == "__main__":
    print(build(refresh="--no-refresh" not in sys.argv))
