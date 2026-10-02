"""Build the static site: a forecast for every spot in spots.csv, the overflow
layer, the verification data and the pages, written to ./site for GitHub Pages.

Also refreshes live status, rebuilds the overflow table and scores logged
forecasts (the same work the API's in-process scheduler does), so one scheduled
run of this script is the whole back end.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from html import escape
from pathlib import Path

import pandas as pd

from dipcast import __version__, config
from dipcast.algae import by_site, refresh_algae
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
# The pages were written for the FastAPI routes; rewrite them for flat files. Their icons are
# /static/icons/ on the API server, which keeps copies of the site's own (src/dipcast/site/icons/)
# so that its pages get a favicon too; the static site has the originals at icons/ (copy_app_files).
REWRITES = [('href="/feedback"', 'href="feedback.html"'), ('href="/feedback?type=spot"', 'href="feedback.html?type=spot"'), ('href="/verification"', 'href="verification.html"'), ('href="/terms"', 'href="terms.html"'),
            ('href="/about"', 'href="about.html"'), ('href="/testing"', 'href="testing.html"'),
            ('href="/privacy"', 'href="privacy.html"'), ('href="/terms#data"', 'href="terms.html#data"'), ('href="/"', 'href="index.html"'),
            ('href="/static/page.css"', 'href="page.css"'), ('href="/static/fonts/LICENSE.txt"', 'href="fonts/LICENSE.txt"'),
            ('href="/static/icons/icon.svg"', 'href="icons/icon.svg"'), ('href="/static/icons/apple-touch-icon.png"', 'href="icons/apple-touch-icon.png"'),
            ('href="/static/fonts/SourceSans3-latin.woff2"', 'href="fonts/SourceSans3-latin.woff2"'),
            ('href="/static/fonts/SourceSerif4-latin.woff2"', 'href="fonts/SourceSerif4-latin.woff2"'),
            ("fetch('/api/verification')", "fetch('data/verification.json')")]
BRAND = "SwimSignal"
HOME_TITLE = f"{BRAND} · sewage-spill forecasts for swim spots"
DESCRIPTION = ("Sewage-pollution risk forecasts for river and lake swim spots in England, from live storm-overflow "
               "data, rainfall forecasts and the river network.")
SAVED_TITLE = f"Saved spots · {BRAND}"
SAVED_DESCRIPTION = "A list of river and lake swim spots, each with its five-day sewage-spill forecast."
# Spot ids that get a page of their own at spot/<id>/; index.html uses the same rule. Any other
# id keeps its ?spot= address: one odd row in spots.csv must not stop the build.
SPOT_ID = re.compile(r"[A-Za-z0-9_-]+")
PAGE_META = re.compile(r"<!-- page-meta.*?<!-- /page-meta -->", re.DOTALL)
LOADING = '<div id="result"><p class="muted">Loading forecasts…</p></div>'
# The brand mark, inline in every page's header (the static pages carry the same markup), so it needs no path.
MARK = ('<svg viewBox="0 0 512 512" aria-hidden="true"><rect width="512" height="512" fill="#1a6871"/>'
        '<path d="M430-20C330 110 470 230 300 290S110 380 190 540" fill="none" stroke="#5CC2B5" stroke-width="70" stroke-linecap="round"/>'
        '<circle cx="318" cy="138" r="38" fill="#F08A4B"/><circle cx="165" cy="358" r="46" fill="none" stroke="#fff" stroke-width="22"/></svg>')
SITE_URL_ENV = "DIPCAST_SITE_URL"
# Optional page-view counter (Cloudflare Web Analytics). Off unless the repository
# variable is set; the token is public (it sits in the page), so it is a variable,
# not a secret. Since 5 Feb 2026 PECR (Schedule A1) lets a counter run without consent only
# if visitors get clear information and a free, simple way to object: counter.js loads it
# only for a browser that has not objected, and the home page has the button. The first
# string is the privacy notice's own lead, so the terms page's date is not touched.
COUNTER_TOKEN_ENV = "DIPCAST_CF_BEACON_TOKEN"
COUNTER_JS = TEMPLATE.parent / "counter.js"
NO_COUNTER = ("and what it does not. Last updated 1 October 2026.", "There is no analytics script and no third-party tracking.")
WITH_COUNTER = ("and what it does not. Last updated 1 October 2026 (page-view counter).",
                ("Page views are counted with Cloudflare Web Analytics. Cloudflare states that it sets no cookies, "
                 "uses no local storage and does not fingerprint visitors. It sees your IP address when the counter "
                 "loads, as any web server would, and its "
                 '<a href="https://www.cloudflare.com/privacypolicy/">privacy policy</a> applies to that. '
                 "Your visits are not counted if you choose \"Don't count my visits\" under \"Saved spots, "
                 "offline use and your data\" at the bottom of the home page; the choice is kept in your browser. "
                 "Nor are they if your browser sends Global Privacy Control or Do Not Track. "
                 "There is no other analytics or tracking."))


def lead_skill(processed: Path = config.PROCESSED) -> dict | None:
    """How good a forecast for each day ahead has been, as a share of the same-day forecast's skill
    (Brier skill against climatology), for a picked day on a spot's page. Spills: the 2025 held-out
    test with archived rain forecasts, lead-calibrated and cross-fitted as the site runs it. Water
    quality: the leave-one-year-out replay on rivers, where the figure is shown. Held from rising
    with the days: a forecast further ahead is not better, and water quality's 0.64 at four days
    against 0.55 at three (29 Sep 2026) is noise in 907 river samples. None if the tables are
    missing."""
    try:
        leads = pd.read_csv(processed / "verification_leads_2025.csv").set_index("source")["brier_skill_vs_clim"]
        spill = [float(leads[f"forecast lead {k}, isotonic cross-fitted (odd/even months)"]) for k in range(5)]
        ev = json.loads((processed / "ecoli_model_eval.json").read_text())
        clim = next(r["brier_river"] for r in ev["loyo"] if r["model"] == "climatology by type")
        by = {r["lead"]: r["brier_river"] for r in ev["by_lead"] if r["exposure"] == "replayed exposure"}
        water = [1 - by[k] / clim for k in range(5)]
    except (OSError, KeyError, StopIteration, ValueError) as e:
        log.warning("lead skill tables unreadable, a picked day will not say how sure it is: %s", e)
        return None
    held = lambda xs: [round(min(xs[: k + 1]) / xs[0], 2) for k in range(len(xs))]
    return {"spill": held(spill), "water": held(water)}


# Alerts (push/): on when both repository variables are set. The Worker's address and its public
# key sit in spots.json for the page; the private key never leaves the Worker.
PUSH_URL_ENV, PUSH_KEY_ENV = "DIPCAST_PUSH_URL", "DIPCAST_VAPID_PUBLIC_KEY"
# With alerts on, the privacy notice's sentences that say nothing leaves the device, and that
# SwimSignal holds no personal data, would be untrue: each is swapped for one that is not. A test
# checks that every one is still in the notice, so a rewrite cannot leave one behind unswapped.
PUSH_SWAPS = [
    ("<li>Your saved spots and your location stay on your device.</li>",
     "<li>Your location stays on your device. So do your saved spots, unless you turn on alerts.</li>"),
    ("It stays on your device: it is not sent to SwimSignal or to anyone else.",
     "It stays on your device: it is not sent to SwimSignal or to anyone else, unless you turn on alerts (below)."),
    ("SwimSignal holds none, as described above;",
     "SwimSignal holds none except, if you turn on alerts, the record described under Alerts, which turning them off deletes;"),
    ('at the bottom of the home page. Clearing this site\'s data removes it too.',
     'at the bottom of the home page. Alerts need it, so turning it off turns them off too. Clearing this site\'s data removes it too.'),
]


def push_config() -> dict | None:
    url, key = (os.environ.get(PUSH_URL_ENV, "").strip(), os.environ.get(PUSH_KEY_ENV, "").strip())
    if not (url or key):
        return None
    # An https address and a 65-byte P-256 public key in base64url (87 characters, no padding).
    if not (re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?/", url) and re.fullmatch(r"[A-Za-z0-9_-]{87}", key)):
        log.warning("%s must be https://host/ and %s a base64url P-256 public key: alerts left off", PUSH_URL_ENV, PUSH_KEY_ENV)
        return None
    return {"url": url, "key": key}


def with_push(html: str, on: bool) -> str:
    """The privacy page: the alerts section when alerts are on, else the planned-feature note."""
    if not on:
        return html
    for a, b in PUSH_SWAPS:
        html = html.replace(a, b)
    return re.sub(r"<h2>If alerts are added</h2>\s*<p>.*?</p>", lambda _: PUSH_PRIVACY, html, count=1, flags=re.DOTALL)


PUSH_PRIVACY = (
    "<h2>Alerts</h2>\n<p>If you turn on alerts on the Saved page, your browser gives SwimSignal a push address: a "
    "long random web address, run by your browser's maker (Google, Apple, Mozilla or Microsoft), that delivers "
    "notifications to this browser. SwimSignal's alert service stores that address, with the identifiers of your "
    "saved spots, and nothing else: no name, email address or location. It uses them only to send a notification "
    "when one of those spots' forecast turns high. Each notification passes through your browser maker's push "
    "service, encrypted so that the push service cannot read it; that company is responsible for its own service. "
    "Your browser also keeps a note of what it last sent, so that an unchanged list is not sent again. The basis is "
    "your consent: you turn alerts on, and you can turn them off on the Saved page at any time, which withdraws it. "
    "An alert can be late or not come at all, so no alert does not mean the water is clean. The record is kept "
    "until you turn alerts off, remove all your saved spots or turn off the offline copy (alerts need it), or until "
    "your browser's push service says the address no longer works; then it is deleted. The alert service runs on "
    "Cloudflare Workers, which may handle the record outside the UK under its own safeguards, and which sees your "
    "IP address when you turn alerts on or off or change your saved spots, as any web server would; "
    '<a href="https://www.cloudflare.com/privacypolicy/">Cloudflare\'s privacy policy</a> applies to that.</p>')


def write_alerts(site: Path, root: str, push_on: bool) -> bool:
    """alerts.json beside spots.json: each spot's level and headline by the page's own rules
    (scripts/alerts.js runs levels.js in Node). Without Node the file is not written; with alerts
    on that stops them, so it is announced."""
    try:
        subprocess.run(["node", str(ROOT / "scripts" / "alerts.js"), str(site / "data" / "spots.json"),
                        str(site / "data" / "alerts.json"), root], check=True, capture_output=True, text=True, timeout=120)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        msg = f"alerts.json not written ({getattr(e, 'stderr', '') or e})"
        if push_on:
            announce(msg)
        else:
            log.warning("%s", msg)
        return False


def with_counter(html: str, token: str | None) -> str:
    """Add the counter's script to a page and, on the privacy page, say so. Without a
    plausible token (16-64 letters and digits, so nothing can break out of the
    attribute) the page is returned unchanged."""
    if not token:
        return html
    if not re.fullmatch(r"[A-Za-z0-9]{16,64}", token):
        log.warning("%s is not 16-64 letters and digits: page-view counter left off", COUNTER_TOKEN_ENV)
        return html
    beacon = '<script id="page-counter">' + COUNTER_JS.read_text().replace("__TOKEN__", token) + "</script>"
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


def attach_algae(results: list[dict], fetch: bool = True) -> int:
    """The EA sampler's latest visual algae check, and this season's tally, on each
    bathing-water spot (spot id 'bw-' + the EA id). Returns how many spots got one; a
    failure leaves the spots without it rather than failing the build."""
    try:
        checks = by_site(refresh_algae(fetch=fetch))
    except Exception as e:  # noqa: BLE001 - an observation beside the forecast must not sink the site
        log.warning("algae checks: %s", e)
        return 0
    n = 0
    for r in results:
        c = checks.get(str(r["id"]).removeprefix("bw-")) if str(r["id"]).startswith("bw-") else None
        if c:
            r["algae"] = c
            n += 1
    return n


# The credits every published data file carries. A credit has to travel with republished data:
# CC BY 4.0 s.3(a) and s.4 for the water companies' feeds, OGL v3 for the EA and OS. The full
# notices are on the terms page ("Data sources and credits"), which the "full" link points to.
LICENCES = {
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC BY-SA 4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "OGL v3.0": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
}


def data_credits(root: str) -> dict:
    return {
        "attribution": (
            "Storm overflow status from Anglian Water Services, Northumbrian Water, Severn Trent Water, South West Water, "
            "Southern Water (© 2026), Thames Water, United Utilities, Wessex Water (© 2024) and Yorkshire Water, via the "
            "National Storm Overflow Hub, CC BY 4.0. Environment Agency data © Environment Agency copyright and/or "
            "database right, OGL v3.0; river levels: this uses Environment Agency flood and river level data from the "
            "real-time data API (Beta). Contains OS data © Crown copyright and database right 2026. Weather data by "
            "Open-Meteo.com, CC BY 4.0, from Met Office forecasts © Crown copyright, CC BY-SA 4.0: rainfall figures "
            "stay under CC BY-SA 4.0."),
        "modified": ("Combined, filtered and modelled by SwimSignal. The forecasts, levels and scores are SwimSignal's own "
                     "estimates, not the data providers'. None of the providers endorses SwimSignal."),
        "licences": LICENCES,
        "full": f"{root}terms.html#data",
    }


CLASSIFICATIONS = config.RAW / "bathing_water_classifications.json"


def attach_classifications(results: list[dict], path: Path = CLASSIFICATIONS) -> int:
    """The EA's latest classification of each bathing-water spot (spot id 'bw-' + the EA id), from
    the file scripts/fetch_classifications.py writes, and the address of the EA's page for it.
    Returns how many spots got a classification. A missing or unreadable file leaves every spot
    without: the forecast stands without it, and the page then shows no rating."""
    try:
        sites = json.loads(path.read_text())["sites"]
    except Exception as e:  # noqa: BLE001 - a record beside the forecast must not sink the build
        log.warning("bathing-water classifications: %s", e)
        return 0
    n = 0
    for r in results:
        c = sites.get(str(r["id"]).removeprefix("bw-")) if str(r["id"]).startswith("bw-") else None
        if c:
            r["classification"] = {k: c[k] for k in ("class", "year", "history", "url") if k in c}
            n += "class" in c
    return n


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    r = math.pi / 180
    h = math.sin((lat2 - lat1) * r / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def attach_river_levels(results: list[dict], lookup=None, workers: int = 8) -> int:
    """The Environment Agency's nearest level gauge, on the spot's own river where it has one, as
    an observation beside the forecast: the latest level, the gauge's usual range and a word for
    where the level sits. The forecast itself is unchanged (forecast_point runs with gauge=False):
    the API version scales travel speed by the level; the site only shows it. Returns how many
    spots got one; a failure leaves a spot without, and nothing here can stop a build."""
    from concurrent.futures import ThreadPoolExecutor

    from dipcast.ingest.flows import _river_words, nearest_level_station
    lookup = lookup or nearest_level_station

    def one(r: dict) -> dict | None:
        if r.get("error") and str(r["error"]).startswith("forecast failed"):
            return None
        river = (r.get("location") or {}).get("watercourse")
        try:
            st = lookup(r["lat"], r["lon"], 15, river)
        except Exception as e:  # noqa: BLE001 - an observation beside the forecast must not sink the build
            log.warning("river level for %s: %s", r["name"], e)
            return None
        if st is None or st.level_m is None:
            return None
        return {"station": st.station, "river": st.river, "level_m": st.level_m, "typical_low_m": st.typical_low,
                "typical_high_m": st.typical_high, "index": None if st.index is None else round(st.index, 2),
                "label": st.label, "observed_at": st.observed_at, "rloi": st.rloi,
                "distance_km": round(_km(r["lat"], r["lon"], st.lat, st.lon), 1),
                "same_river": bool(_river_words(river) & _river_words(st.river)) if river else None}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        states = list(pool.map(one, results))
    n = 0
    for r, s in zip(results, states, strict=True):
        r["river_state"] = s
        n += s is not None
    return n


WEATHER_CREDIT = "Weather data by Open-Meteo.com"


def attach_weather(results: list[dict], request=None, batch: int = 50) -> int:
    """Each spot's daytime high, sunrise and sunset for the forecast's days, from Open-Meteo in a
    few calls for all spots (three daily variables over five days weigh one call per location).
    Context beside the forecast, not an input to it; a failed call leaves the spots without."""
    from dipcast.ingest.rainfall import _request
    request = request or _request
    spots = [r for r in results if not (r.get("error") and str(r["error"]).startswith("forecast failed"))]
    n = 0
    for i in range(0, len(spots), batch):
        chunk = spots[i:i + batch]
        try:
            res = request(config.OPEN_METEO_FORECAST, {
                "latitude": ",".join(f"{r['lat']:.3f}" for r in chunk), "longitude": ",".join(f"{r['lon']:.3f}" for r in chunk),
                "daily": "temperature_2m_max,sunrise,sunset", "forecast_days": config.FORECAST_DAYS, "timezone": "Europe/London"}, timeout=30)
        except Exception as e:  # noqa: BLE001 - context beside the forecast must not sink the build
            log.warning("weather: %s", e)
            return n
        for r, w in zip(chunk, res if isinstance(res, list) else [res], strict=False):
            d = (w or {}).get("daily") or {}
            try:
                days = [{"date": t, "tmax": None if x is None else round(float(x)), "sunrise": str(sr)[11:16], "sunset": str(ss)[11:16]}
                        for t, x, sr, ss in zip(d["time"], d["temperature_2m_max"], d["sunrise"], d["sunset"], strict=True)]
            except (KeyError, TypeError, ValueError) as e:
                log.warning("weather for %s: %s", r["name"], e)
                continue
            r["weather"] = {"days": days, "credit": WEATHER_CREDIT}
            n += 1
    return n


# What the offline copy (sw.js) stores, or decides what it stores: the files whose hash names its
# cache. A file here changes, and so does sw.js, so browsers install the new worker and fill a new
# cache from the server; nothing else (the forecast, a spot's page) changes the name. Directories
# count whole.
SHELL_SOURCES = [TEMPLATE, TEMPLATE.parent / "sw.js", TEMPLATE.parent / "levels.js", TEMPLATE.parent / "experience.js",
                 TEMPLATE.parent / "manifest.webmanifest", TEMPLATE.parent / "icons", TEMPLATE.parent / "vendor",
                 STATIC / "page.css", STATIC / "feedback.html", STATIC / "fonts"]
# The worker's build line, and the page's scripts, which get ?v=<stamp> so a page and its scripts
# always come from one build (sw.js explains). The page must keep these exact tags.
SW_BUILD = "const BUILD = 'dev';"
VERSIONED_SCRIPTS = ('<script src="experience.js"></script>', '<script src="levels.js"></script>')


def shell_stamp() -> str:
    """Eight hex digits of a hash over the shell's source files, their paths and their bytes."""
    h = hashlib.sha256()
    for src in SHELL_SOURCES:
        for f in sorted(src.rglob("*")) if src.is_dir() else [src]:
            if f.is_file() and f.name != ".DS_Store":
                h.update(str(f.relative_to(ROOT)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:8]


def with_build(template: str, stamp: str) -> str:
    """The app page with its scripts asked for at ?v=<stamp>, as the worker stores them."""
    for tag in VERSIONED_SCRIPTS:
        if tag not in template:
            raise ValueError(f"index.html has lost {tag}, which the offline copy versions")
        template = template.replace(tag, tag.replace('.js"', f'.js?v={stamp}"'), 1)
    return template


def copy_app_files(site: Path, stamp: str | None = None) -> None:
    """The web-app manifest and icons beside index.html: Add to Home Screen then gives an
    icon, a name and a full-screen window. The offline copy, sw.js, gets the build's stamp."""
    stamp = stamp or shell_stamp()
    shutil.copy(TEMPLATE.parent / "manifest.webmanifest", site / "manifest.webmanifest")
    sw = (TEMPLATE.parent / "sw.js").read_text()
    if sw.count(SW_BUILD) != 1:
        raise ValueError(f"sw.js has lost its line {SW_BUILD!r}, which the build stamps")
    (site / "sw.js").write_text(sw.replace(SW_BUILD, f"const BUILD = '{stamp}';"))   # the offline copy; see the file
    shutil.copy(TEMPLATE.parent / "experience.js", site / "experience.js")
    shutil.copy(TEMPLATE.parent / "levels.js", site / "levels.js")   # the level rules, which the page loads
    shutil.copytree(TEMPLATE.parent / "icons", site / "icons", dirs_exist_ok=True)
    # The map library, Leaflet, served from this site (vendor/leaflet/VERSION.txt) rather than a CDN.
    shutil.copytree(TEMPLATE.parent / "vendor", site / "vendor", dirs_exist_ok=True)


def site_url() -> str:
    """The published address, for canonical links, share previews and the sitemap. A custom
    domain sets DIPCAST_SITE_URL; otherwise it is this repository's GitHub Pages address, so a
    fork or a renamed repository gets its own."""
    url = os.environ.get(SITE_URL_ENV, "").strip()
    if url and not re.match(r"https?://[^/\s]+", url):   # "swimsignal.co.uk" would make every preview link relative
        log.warning("%s=%r is not an http(s) address; using the Pages address", SITE_URL_ENV, url)
        url = ""
    if not url:
        owner, _, repo = os.environ.get("GITHUB_REPOSITORY", "ethanbuckley/swimsignal").partition("/")
        # A repository named <owner>.github.io is that account's own site, served at the root.
        home = f"{owner.lower()}.github.io"
        url = f"https://{home}/" if repo.lower() == home else f"https://{home}/{repo}/"
    return url.rstrip("/") + "/"


def page_meta(title: str, description: str, url: str, root: str, base: str | None = None, noindex: bool = False) -> str:
    """The <head> block index.html marks with page-meta. Messaging apps and search engines need
    absolute addresses for the page and its preview image. Every page gets a relative <base> at
    the site root ("./" on the home page, "../../" in spot/<id>/). The page moves between the list
    and spots with pushState, and without a <base> every relative link would then resolve inside
    spot/<id>/; a <base> is resolved once, as the page loads. Relative, so the pages work at any
    address (a custom domain serves the site at / rather than /dipcast/)."""
    return "\n".join([
        *([f'<base href="{escape(base)}">'] if base else []),
        f"<title>{escape(title)}</title>",
        f'<meta name="description" content="{escape(description)}">',
        *(['<meta name="robots" content="noindex">'] if noindex else []),
        f'<link rel="canonical" href="{escape(url)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{escape(BRAND)}">',
        f'<meta property="og:title" content="{escape(title)}">',
        f'<meta property="og:description" content="{escape(description)}">',
        f'<meta property="og:url" content="{escape(url)}">',
        f'<meta property="og:image" content="{escape(root)}icons/og.png">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        f'<meta property="og:image:alt" content="{escape(BRAND)}: sewage-spill forecasts for river and lake swim spots">',
        '<meta name="twitter:card" content="summary_large_image">',
    ])


def spot_blurb(spot: dict) -> str:
    """One sentence for a spot's search result and link preview. It leaves out today's level:
    messaging apps keep a preview for days, and a level in it would go stale."""
    name, kind = spot["name"], "lake" if spot.get("kind") == "lake" else "river"
    n = (spot.get("upstream_summary") or {}).get("overflows")
    if spot.get("error") and not str(spot["error"]).startswith("forecast failed"):
        return f"{name}: no monitored storm overflow can reach this {kind} along the river network, so {BRAND} has no spill forecast for it."
    if n == 0:
        return f"{name}: no monitored storm overflows upstream. {BRAND} forecasts sewage-spill exposure for river and lake swim spots in England."
    upstream = f" from the {n} monitored storm overflow{'' if n == 1 else 's'} upstream," if n else ""
    return (f"Five-day sewage-spill forecast for {name},{upstream} using live overflow status, rainfall forecasts "
            f"and the river network. Updated several times a day.")


def spot_page(template: str, spot: dict, root: str) -> str:
    """A spot's own page: the map page with the spot's name, description and address in its
    <head>, and its name in the body for crawlers and for the moment before the script runs."""
    url = f"{root}spot/{spot['id']}/"
    blurb = spot_blurb(spot)
    page = PAGE_META.sub(lambda m: page_meta(f"{spot['name']}: sewage-spill forecast · {BRAND}", blurb, url, root,
                                             base="../../"), template, count=1)
    return page.replace(LOADING, f'<div id="result"><h2 class="spot-name">{escape(spot["name"])}</h2>'
                                 f'<p class="muted">{escape(blurb)} Loading the forecast…</p></div>', 1)


def saved_page(template: str, root: str) -> str:
    """The Saved page, saved/: the map page, where the script lists the spots this browser has
    saved, or offers a list someone shared (saved/#spots=...). The list lives in the browser, not
    in the page, so search engines are asked to leave it out and the sitemap does not list it."""
    page = PAGE_META.sub(lambda m: page_meta(SAVED_TITLE, SAVED_DESCRIPTION, f"{root}saved/", root, base="../",
                                             noindex=True), template, count=1)
    return page.replace(LOADING, '<div id="result"><h1 class="page-h">Saved spots</h1>'
                                 '<p class="muted">Loading your saved spots…</p></div>', 1)


def with_counts(html: str, results: list[dict]) -> str:
    """The About page's spot counts, from this build's spots, so they cannot go stale."""
    n_bw = sum(r.get("source") == "designated" for r in results)
    html = re.sub(r'(<span id="n-spots">)\d+(</span>)', rf"\g<1>{len(results)}\g<2>", html, count=1)
    return re.sub(r'(<span id="n-bw">)\d+(</span>)', rf"\g<1>{n_bw}\g<2>", html, count=1)


def not_found_page(root: str) -> str:
    """404.html: GitHub Pages serves it for any missing address, at any depth, so every link in it
    is absolute. Before this, a mistyped or outdated link got GitHub's own page, with no way back."""
    r = escape(root)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>Page not found · {BRAND}</title><meta name="robots" content="noindex">\n'
        '<meta name="theme-color" content="#3d5b5d">\n'
        f'<link rel="icon" href="{r}icons/icon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="{r}icons/apple-touch-icon.png">\n'
        f'<link rel="stylesheet" href="{r}page.css">\n'
        f'<link rel="preload" href="{r}fonts/SourceSans3-latin.woff2" as="font" type="font/woff2" crossorigin>'
        f'<link rel="preload" href="{r}fonts/SourceSerif4-latin.woff2" as="font" type="font/woff2" crossorigin>\n'
        f'</head><body>\n<header class="top"><a class="brand" href="{r}">{MARK}{BRAND}</a>'
        f'<nav aria-label="Site"><a href="{r}">Explore</a><a href="{r}verification.html">Accuracy</a><a href="{r}about.html">About</a><a href="{r}feedback.html">Feedback</a></nav></header>\n'
        '<main class="doc"><h1>No page at this address</h1>\n'
        '<p class="lead">A spot changes address when it is renamed or removed, and a link can be copied or typed wrongly.</p>\n'
        f'<ul><li><a href="{r}">All spots</a>, each with its five-day forecast</li><li><a href="{r}saved/">Your saved spots</a></li>'
        f'<li><a href="{r}feedback.html?type=spot">Ask for a spot to be added</a></li></ul></main>\n'
        f'<footer class="site-foot"><div class="rule"><p>{BRAND} is a free, non-commercial forecast of sewage-overflow risk at river and lake swim spots in England, run by Ethan Buckley. A forecast, not a water test.</p>'
        f'<p><a href="{r}terms.html">Terms of use</a> · <a href="{r}privacy.html">Privacy</a> · <a href="{r}feedback.html">Feedback</a></p></div></footer></body></html>\n')


def robots(root: str) -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {root}sitemap.xml\n"


def sitemap(root: str, spot_ids: list[str], day: str) -> str:
    urls = [root, f"{root}about.html", f"{root}verification.html", f"{root}testing.html"] + [f"{root}spot/{i}/" for i in spot_ids]
    body = "".join(f"<url><loc>{escape(u)}</loc><lastmod>{day}</lastmod></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'


def write_pages(site: Path, results: list[dict], token: str | None = None, root: str | None = None,
                day: str | None = None, push: bool = False) -> int:
    """Every HTML page, the sitemap and the app files. Returns the number of spot pages. A spot
    whose id is not letters, digits and hyphens gets no page of its own and keeps ?spot=."""
    root = root or site_url()
    stamp = shell_stamp()
    template = with_build(TEMPLATE.read_text(), stamp)
    if not (PAGE_META.search(template) and LOADING in template):
        raise ValueError("index.html has lost its page-meta block or its loading placeholder")
    for name in ["about.html", "verification.html", "terms.html", "privacy.html", "feedback.html", "testing.html"]:
        s = (STATIC / name).read_text()
        for a, b in REWRITES:
            s = s.replace(a, b)
        if name == "about.html":
            s = with_counts(s, results)
        (site / name).write_text(with_counter(with_push(s, push) if name == "privacy.html" else s, token))
    shutil.copy(STATIC / "page.css", site / "page.css")
    shutil.copytree(STATIC / "fonts", site / "fonts", dirs_exist_ok=True)   # declared in page.css and index.html
    copy_app_files(site, stamp)
    home = PAGE_META.sub(lambda m: page_meta(HOME_TITLE, DESCRIPTION, root, root, base="./"), template, count=1)
    (site / "index.html").write_text(with_counter(home, token))
    (site / "saved").mkdir(exist_ok=True)
    (site / "saved" / "index.html").write_text(with_counter(saved_page(template, root), token))
    shutil.rmtree(site / "spot", ignore_errors=True)   # a spot dropped from spots.csv loses its page
    ids = []
    for r in results:
        if not SPOT_ID.fullmatch(str(r["id"])):
            log.warning("spot id %r is not letters, digits and hyphens: no page of its own, it keeps ?spot=", r["id"])
            continue
        (site / "spot" / r["id"]).mkdir(parents=True, exist_ok=True)
        (site / "spot" / r["id"] / "index.html").write_text(with_counter(spot_page(template, r, root), token))
        ids.append(r["id"])
    (site / "sitemap.xml").write_text(sitemap(root, ids, day or pd.Timestamp.now(tz="Europe/London").date().isoformat()))
    (site / "404.html").write_text(with_counter(not_found_page(root), token))
    (site / "robots.txt").write_text(robots(root))
    (site / ".nojekyll").write_text("")
    return len(ids)


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
    n_algae = attach_algae(results, fetch=refresh)
    n_classified = attach_classifications(results)
    n_levels = attach_river_levels(results) if refresh else 0   # observations beside the forecast, network only
    n_weather = attach_weather(results) if refresh else 0
    generated = pd.Timestamp.now(tz="Europe/London")
    health = build_health(results, samples_status())   # raises before anything is written if the build is bad
    health["algae_checks"], health["classifications"] = n_algae, n_classified
    health["river_levels"], health["weather"] = n_levels, n_weather
    for w in health["warnings"]:
        announce(w)
    (SITE / "data").mkdir(parents=True, exist_ok=True)
    credits = data_credits(site_url())
    push = push_config()
    (SITE / "data" / "spots.json").write_text(json.dumps({
        "generated_at": generated.isoformat(), "version": __version__, "n": len(results), "build": health,
        "lead_skill": lead_skill(), **({"push": push} if push else {}), "credits": credits, "spots": results}, default=str))
    write_alerts(SITE, site_url(), push is not None)   # carries the credits from spots.json
    # GeoJSON allows extra top-level members, so the credits sit beside the features.
    (SITE / "data" / "overflows.geojson").write_text(json.dumps({**overflows_geojson(limit=20000), "credits": credits}, default=str))
    (SITE / "data" / "verification.json").write_text(json.dumps({**load_verification(), "credits": credits}, default=str))
    token = os.environ.get(COUNTER_TOKEN_ENV, "").strip()
    health["spot_pages"] = write_pages(SITE, results, token, day=generated.date().isoformat(), push=push is not None)
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
