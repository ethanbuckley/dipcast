"""Build the static site: a forecast for every spot in spots.csv, the overflow
layer, the verification data and the pages, written to ./site for GitHub Pages.

Also refreshes live status, rebuilds the overflow table and scores logged
forecasts (the same work the API's in-process scheduler does), so one scheduled
run of this script is the whole back end.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

from dipcast import __version__, config
from dipcast.forecast_log import describe_fetch, load_verification, samples_status
from dipcast.ingest.rainfall import cells_for_sites, fetch_forecast
from dipcast.jobs import refresh_all
from dipcast.model.forecast import (
    _net,
    _overflows,
    forecast_point,
    overflows_geojson,
    reload_caches,
)
from dipcast.model.transport import locate_pin, river_velocity, upstream_overflows

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("build_site")

ROOT = config.ROOT
SITE = ROOT / "site"
STATIC = ROOT / "src" / "dipcast" / "api" / "static"
TEMPLATE = ROOT / "src" / "dipcast" / "site" / "index.html"
KEEP_CONTRIBUTORS = 10
MIN_OK_SHARE = 0.8        # fewer spots with a forecast than this and the build fails (no publish)
MAX_NO_DATA_SHARE = 0.5   # more of today's forecasts without rainfall data than this: fail
# The pages were written for the FastAPI routes; rewrite them for flat files.
REWRITES = [('href="/verification"', 'href="verification.html"'), ('href="/terms"', 'href="terms.html"'),
            ('href="/privacy"', 'href="privacy.html"'), ('href="/"', 'href="index.html"'),
            ('href="/static/page.css"', 'href="page.css"'), ("fetch('/api/verification')", "fetch('data/verification.json')")]
# Optional page-view counter (Cloudflare Web Analytics). Off unless the repository
# variable is set; the token is public (it sits in the page), so it is a variable,
# not a secret.
COUNTER_TOKEN_ENV = "DIPCAST_CF_BEACON_TOKEN"
NO_COUNTER = ("Last updated 12 September 2026.", "There is no analytics script and no third-party tracking.")
WITH_COUNTER = ("Last updated 28 September 2026 (page-view counter).",
                ("Page views are counted with Cloudflare Web Analytics. Cloudflare states that it sets no cookies, "
                 "uses no local storage and does not fingerprint visitors. It sees your IP address when the counter "
                 "loads, as any web server would, and its "
                 '<a href="https://www.cloudflare.com/privacypolicy/">privacy policy</a> applies to that. '
                 "There is no other analytics or tracking."))


def with_counter(html: str, token: str | None) -> str:
    """Add the counter's script to a page and, on the privacy page, say so. Without a
    plausible token (16-64 letters and digits, so nothing can break out of the
    attribute) the page is returned unchanged."""
    if not token:
        return html
    if not re.fullmatch(r"[A-Za-z0-9]{16,64}", token):
        log.warning("%s is not 16-64 letters and digits: page-view counter left off", COUNTER_TOKEN_ENV)
        return html
    beacon = ('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
              "data-cf-beacon='" + json.dumps({"token": token}) + "'></script>")
    html = html.replace("</head>", beacon + "</head>", 1)
    for a, b in zip(NO_COUNTER, WITH_COUNTER):
        html = html.replace(a, b)
    return html


def prefetch_rain(spots: pd.DataFrame) -> None:
    """One pass over every spot's upstream overflows to collect the rainfall cells,
    then a handful of 50-cell requests. Per-spot fetching meant up to one request
    per spot, and each one risked a slow TLS handshake on shared CI runners."""
    net, ov = _net(), _overflows()
    lat, lon = list(spots["lat"]), list(spots["lon"])
    for r in spots.itertuples(index=False):
        try:
            pin = locate_pin(net, float(r.lon), float(r.lat), kind_hint=(r.kind if r.kind in ("lake", "river") else None))
            up = upstream_overflows(net, pin, ov, velocity_ms=river_velocity(None))
            lat += list(up["lat"]); lon += list(up["lon"])
        except Exception as e:  # noqa: BLE001
            log.warning("prefetch: %s: %s", r.name, e)
    cells = cells_for_sites(pd.Series(lat, dtype=float), pd.Series(lon, dtype=float))
    t0 = time.time()
    df = fetch_forecast(cells)
    log.info("rainfall prefetched: %d cells, %d rows, %.0fs", len(cells), len(df), time.time() - t0)


class BuildUnhealthy(RuntimeError):
    """Raised instead of publishing when most forecasts failed or lack data. The
    workflow's deploy job depends on the build job, so this keeps the previous
    site up rather than replacing it with a page of blanks."""


def build_health(results: list[dict], ecoli_samples: dict | None = None) -> dict:
    """Counts the workflow and the page use to judge a build; raises BuildUnhealthy
    when the site should not be published. `ecoli_samples` is the last EA sample fetch
    (forecast_log.samples_status); if no source answered it goes in `warnings`. That
    stalls the E. coli scores, not the forecasts, so it does not stop the publish."""
    n = len(results)
    # A spot the model cannot say anything about (an isolated lake, no river within
    # reach) returns an explanation with an empty day list; that is an answer, not a
    # failure. A failure is an exception in forecast_point, which leaves no `days`.
    ok = [r for r in results if "days" in r]
    with_days = [r for r in ok if r["days"]]
    today_no_data = sum(1 for r in with_days if r["days"][0].get("data_status") == "rain unavailable")
    health = {"spots": n, "forecast_ok": len(ok), "forecast_failed": n - len(ok),
              "no_forecast_possible": len(ok) - len(with_days), "today_rain_unavailable": today_no_data,
              "failed_spots": [r["name"] for r in results if "days" not in r][:20], "warnings": []}
    if n and len(ok) < MIN_OK_SHARE * n:
        raise BuildUnhealthy(f"only {len(ok)}/{n} spots got a forecast; not publishing")
    if with_days and today_no_data > MAX_NO_DATA_SHARE * len(with_days):
        raise BuildUnhealthy(f"{today_no_data}/{len(ok)} forecasts have no rainfall data for today; not publishing")
    if ecoli_samples:
        s = ecoli_samples
        health["ecoli_samples"] = {k: s.get(k) for k in ("checked_at", "n_sites", "n_failed", "last_ok_at", "sources")}
        if s.get("all_failed"):
            health["warnings"].append(f"E. coli scoring stalled: no EA source answered for any of the {s.get('n_sites')} "
                                      f"bathing waters at {s.get('checked_at')} ({describe_fetch(s)}); "
                                      f"last good fetch {s.get('last_ok_at') or 'never'}")
    return health


def copy_app_files(site: Path) -> None:
    """The web-app manifest and icons beside index.html: Add to Home Screen then gives an
    icon, a name and a full-screen window."""
    shutil.copy(TEMPLATE.parent / "manifest.webmanifest", site / "manifest.webmanifest")
    shutil.copytree(TEMPLATE.parent / "icons", site / "icons", dirs_exist_ok=True)


def announce(warning: str) -> None:
    """Log a build warning and, on GitHub Actions, raise it as an annotation on the run page."""
    log.warning("%s", warning)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=dipcast build health::" + warning.replace("%", "%25").replace("\n", "%0A"), flush=True)


def build(refresh: bool = True) -> dict:
    t0 = time.time()
    if refresh:
        refresh_all(_net())
        reload_caches()
    spots = pd.read_csv(ROOT / "spots.csv").fillna("")
    prefetch_rain(spots)
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
    health = build_health(results, samples_status())   # raises before anything is written if the build is bad
    for w in health["warnings"]:
        announce(w)
    (SITE / "data").mkdir(parents=True, exist_ok=True)
    (SITE / "data" / "spots.json").write_text(json.dumps({
        "generated_at": generated.isoformat(), "version": __version__, "n": len(results), "build": health,
        "spots": results}, default=str))
    (SITE / "data" / "overflows.geojson").write_text(json.dumps(overflows_geojson(limit=20000), default=str))
    (SITE / "data" / "verification.json").write_text(json.dumps(load_verification(), default=str))
    token = os.environ.get(COUNTER_TOKEN_ENV, "").strip()
    for name in ["verification.html", "terms.html", "privacy.html"]:
        s = (STATIC / name).read_text()
        for a, b in REWRITES:
            s = s.replace(a, b)
        (SITE / name).write_text(with_counter(s, token))
    shutil.copy(STATIC / "page.css", SITE / "page.css")
    copy_app_files(SITE)
    (SITE / "index.html").write_text(with_counter(TEMPLATE.read_text(), token))
    (SITE / ".nojekyll").write_text("")
    summary = {**health, "seconds": round(time.time() - t0, 1), "generated_at": generated.isoformat()}
    summary.pop("failed_spots", None)
    log.info("site built: %s", summary)
    return summary


if __name__ == "__main__":
    try:
        print(build(refresh="--no-refresh" not in sys.argv))
    except BuildUnhealthy as e:
        log.error("%s", e)
        sys.exit(2)
