"""The static site's pages: every spot gets a page of its own whose share preview carries its
name (never today's level, which a cached preview would show for days), every page carries
absolute preview links, a spot's page has a relative <base>, and the live scorer reports when the
observation records its coverage rule needs begin."""

import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _build_site():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_site
    return build_site


SPOTS = [
    {"id": "wharfe-ilkley", "name": 'Wharfe at "Cromwheel" & Ilkley', "kind": "river",
     "upstream_summary": {"overflows": 15}, "days": [{"label": "very high"}]},
    {"id": "tarn", "name": "A Tarn", "kind": "lake", "days": [],
     "error": "An isolated lake with no river connection in the network: storm overflows cannot reach it by water."},
    {"id": "Bad Id", "name": "Odd", "kind": "river", "upstream_summary": {"overflows": 0}, "days": []},   # a space
]


def test_every_spot_gets_its_own_page_and_preview(tmp_path):
    bs = _build_site()
    assert bs.write_pages(tmp_path, SPOTS, root="https://example.org/swim/", day="2026-09-29") == 2
    assert not (tmp_path / "spot" / "Bad Id").exists()   # keeps ?spot= instead
    page = (tmp_path / "spot" / "wharfe-ilkley" / "index.html").read_text()
    head = page.split("</head>")[0]
    assert '<base href="../../">' in head   # relative: the site works at any address
    assert '<link rel="canonical" href="https://example.org/swim/spot/wharfe-ilkley/">' in head
    assert '<meta property="og:image" content="https://example.org/swim/icons/og.png">' in head
    assert "<title>Wharfe at &quot;Cromwheel&quot; &amp; Ilkley: pollution risk forecast · SwimSignal</title>" in head
    assert "from the 15 monitored storm overflows upstream" in head
    assert "very high" not in head.lower().replace("veryhigh", "")
    assert head.count("<title>") == 1 and "page-meta" not in page
    assert '<h2 class="spot-name">Wharfe at &quot;Cromwheel&quot; &amp; Ilkley</h2>' in page   # before the script runs
    assert "no monitored storm overflow can reach this lake" in (tmp_path / "spot" / "tarn" / "index.html").read_text()
    home = (tmp_path / "index.html").read_text()
    assert '<link rel="canonical" href="https://example.org/swim/">' in home
    assert '<base href="./">' in home.split("</head>")[0]   # pushState to spot/<id>/ must not move its links
    assert "Loading forecasts…" in home
    for f in ["sw.js", "manifest.webmanifest", "icons/og.png", "icons/fells.webp", "verification.html", "privacy.html", "page.css", ".nojekyll", "404.html", "robots.txt",
              "fonts/SourceSans3-latin.woff2", "fonts/SourceSerif4-latin.woff2", "fonts/LICENSE.txt"]:   # the typefaces page.css declares
        assert (tmp_path / f).exists(), f
    # GitHub Pages serves 404.html at any depth, so its links must be absolute; search engines may not keep it.
    lost = (tmp_path / "404.html").read_text()
    assert 'href="https://example.org/swim/"' in lost and 'href="https://example.org/swim/page.css"' in lost and 'content="noindex"' in lost
    assert 'class="top"' in lost and 'href="https://example.org/swim/about.html">About</a>' in lost   # the shared header, absolute
    assert 'href="https://example.org/swim/icons/apple-touch-icon.png"' in lost
    assert (tmp_path / "robots.txt").read_text() == "User-agent: *\nAllow: /\nSitemap: https://example.org/swim/sitemap.xml\n"
    sm = (tmp_path / "sitemap.xml").read_text()
    assert sm.count("<url>") == 6 and "<loc>https://example.org/swim/spot/tarn/</loc>" in sm
    assert "<loc>https://example.org/swim/about.html</loc>" in sm and "<loc>https://example.org/swim/testing.html</loc>" in sm
    # The testers' briefing: linked from About and the feedback page, with flat links of its own.
    testing = (tmp_path / "testing.html").read_text()
    assert 'href="feedback.html"' in testing and 'href="/feedback"' not in testing and 'href="privacy.html"' in testing
    assert 'href="testing.html"' in (tmp_path / "about.html").read_text() and 'href="testing.html"' in (tmp_path / "feedback.html").read_text()
    assert "<lastmod>2026-09-29</lastmod>" in sm
    # The Saved page: its list is in the browser, so nothing in it for a search engine.
    saved = (tmp_path / "saved" / "index.html").read_text()
    head = saved.split("</head>")[0]
    assert '<base href="../">' in head and '<meta name="robots" content="noindex">' in head
    assert '<link rel="canonical" href="https://example.org/swim/saved/">' in head and "<title>Saved spots · SwimSignal</title>" in head
    assert "saved/" not in sm and "noindex" not in home


def test_about_page_counts_this_builds_spots_and_links_work_on_the_static_site(tmp_path):
    bs = _build_site()
    spots = [{**SPOTS[0], "source": "designated"}, {**SPOTS[1], "source": "curated"}]
    bs.write_pages(tmp_path, spots, root="https://example.org/")
    about = (tmp_path / "about.html").read_text()
    assert '<span id="n-spots">2</span> spots, <span id="n-bw">1</span> of them designated' in about
    # The server's absolute links become the static site's relative files, on every page.
    for name in ["about.html", "verification.html", "terms.html", "privacy.html", "testing.html", "feedback.html"]:
        page = (tmp_path / name).read_text()
        assert 'href="about.html">About</a>' in page, name
        assert 'href="/' not in page, name
        # A page that declares an icon has its icons and preloaded typefaces at files the static site has.
        if 'rel="icon"' in (bs.STATIC / name).read_text():
            for ref in ("icons/icon.svg", "icons/apple-touch-icon.png", "fonts/SourceSans3-latin.woff2", "fonts/SourceSerif4-latin.woff2"):
                assert f'href="{ref}"' in page and (tmp_path / ref).exists(), (name, ref)
        # On the API server the same links are /static/..., which must resolve there too.
        for ref in re.findall(r'href="/static/([^"]+)"', (bs.STATIC / name).read_text()):
            assert (bs.STATIC / ref).exists(), (name, ref)
    for icon in ("icon.svg", "apple-touch-icon.png"):   # the API server's copies of the site's icons
        assert (bs.STATIC / "icons" / icon).read_bytes() == (bs.TEMPLATE.parent / "icons" / icon).read_bytes(), icon
    home = (tmp_path / "index.html").read_text()
    assert '<a href="about.html">About SwimSignal</a>' in home and '<a href="about.html" class="desk-only">About</a>' in home


def test_the_page_view_counter_reaches_spot_pages_and_a_dropped_spot_loses_its_page(tmp_path):
    bs = _build_site()
    bs.write_pages(tmp_path, SPOTS[:2], token="abcdefghij0123456789", root="https://example.org/")
    assert "cloudflareinsights" in (tmp_path / "spot" / "tarn" / "index.html").read_text()
    bs.write_pages(tmp_path, SPOTS[:1], root="https://example.org/")
    assert (tmp_path / "spot" / "wharfe-ilkley").exists() and not (tmp_path / "spot" / "tarn").exists()
    assert "cloudflareinsights" not in (tmp_path / "index.html").read_text()
    template = (ROOT / "src" / "dipcast" / "site" / "index.html").read_text()
    assert 'id="count-btn"' in template and "getElementById('page-counter')" in template   # the way to object


def test_site_url_follows_the_repository_unless_set(monkeypatch):
    bs = _build_site()
    monkeypatch.delenv("DIPCAST_SITE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "EthanBuckley/swimcast")
    assert bs.site_url() == "https://ethanbuckley.github.io/swimcast/"
    monkeypatch.setenv("GITHUB_REPOSITORY", "dipspot/dipspot.github.io")   # an organisation's own site
    assert bs.site_url() == "https://dipspot.github.io/"
    monkeypatch.setenv("GITHUB_REPOSITORY", "EthanBuckley/swimcast")
    monkeypatch.setenv("DIPCAST_SITE_URL", "https://swim.example")
    assert bs.site_url() == "https://swim.example/"
    monkeypatch.setenv("DIPCAST_SITE_URL", "swim.example")   # no scheme: previews would get relative links
    assert bs.site_url() == "https://ethanbuckley.github.io/swimcast/"


def test_the_page_draws_the_spill_levels_at_the_cut_offs_the_build_labels_them_by():
    # The page's scales and bars place a day by SPILL_CUTS (levels.js); the labels in spots.json come
    # from transport.risk_label. A change to one without the other would put "High" on a moderate bar.
    from dipcast.model.transport import risk_label
    rules = (ROOT / "src" / "dipcast" / "site" / "levels.js").read_text()
    cuts = [float(x) for x in re.search(r"const SPILL_CUTS = \[([^\]]+)\]", rules).group(1).split(",")]
    levels = ["low", "moderate", "high", "very high"]
    for i, c in enumerate(cuts):
        assert risk_label(c - 1e-9) == levels[i] and risk_label(c) == levels[i + 1], c


def test_the_page_and_the_build_use_one_id_rule():
    # Not a check of spots.csv: an odd id only loses its own page (it keeps ?spot=), and a failing
    # test here would stop every build and leave the whole site stale.
    bs = _build_site()
    assert bs.SPOT_ID.pattern == "[A-Za-z0-9_-]+"
    page = (ROOT / "src" / "dipcast" / "site" / "index.html").read_text()
    assert "const PAGE_ID = /^[A-Za-z0-9_-]+$/;" in page and "/\\/spot\\/([A-Za-z0-9_-]+)\\/?$/" in page
    # The pages the build writes below the root, which the page strips to find the root without a <base>.
    assert "replace(/(spot\\/[^/]+|saved)\\/?$/, '')" in page


def test_the_ea_rating_reaches_bathing_water_spots_only(tmp_path):
    bs = _build_site()
    f = tmp_path / "c.json"
    f.write_text('{"sites": {"uke4100-08901": {"class": "poor", "year": 2025, "history": [[2025, "poor"]], "url": "https://e/x", "name": "W"},'
                 ' "uki2203-11942": {"url": "https://e/y", "name": "Ham"}}}')
    spots = [{"id": "bw-uke4100-08901"}, {"id": "bw-uki2203-11942"}, {"id": "thames-henley"}, {"id": "bw-ukx-unknown"}]
    assert bs.attach_classifications(spots, f) == 1   # the not-yet-rated water gets its EA page, not a rating
    assert spots[0]["classification"] == {"class": "poor", "year": 2025, "history": [[2025, "poor"]], "url": "https://e/x"}
    assert spots[1]["classification"] == {"url": "https://e/y"}
    assert "classification" not in spots[2] and "classification" not in spots[3]
    assert bs.attach_classifications(spots, tmp_path / "missing.json") == 0   # no file: no rating, and no failed build


def test_the_committed_ratings_cover_every_inland_bathing_water():
    import json
    sites = json.loads((ROOT / "data" / "raw" / "bathing_waters_inland.json").read_text())
    c = json.loads((ROOT / "data" / "raw" / "bathing_water_classifications.json").read_text())
    assert set(c["sites"]) == {s["id"] for s in sites}
    rated = [v for v in c["sites"].values() if "class" in v]
    assert rated and all(v["class"] in {"excellent", "good", "sufficient", "poor"} and v["year"] >= 2020 for v in rated)
    assert all(v["url"].startswith("https://environment.data.gov.uk/bwq/profiles/") for v in c["sites"].values())
    rules = (ROOT / "src" / "dipcast" / "site" / "levels.js").read_text()   # the page's names for the four
    assert "const CLASS_LEVEL = { excellent: 'low', good: 'low', sufficient: 'moderate', poor: 'high' };" in rules


def test_the_credits_travel_with_the_data_and_the_terms_link_every_licence(tmp_path):
    # CC BY 4.0 s.3(a) and s.4 (the water companies' feeds) and OGL v3 (EA, OS): a republished
    # data file must carry its credits, and the page they point to must link each licence.
    bs = _build_site()
    c = bs.data_credits("https://example.org/")
    assert {"attribution", "modified", "licences", "full"} <= set(c) and c["full"] == "https://example.org/terms.html#data"
    terms = (ROOT / "src" / "dipcast" / "api" / "static" / "terms.html").read_text()
    assert all(url in terms for url in c["licences"].values())
    for company in ("Anglian", "Northumbrian", "Severn Trent", "South West", "Southern", "Thames", "United Utilities", "Wessex", "Yorkshire"):
        assert company in c["attribution"] and company in terms, company
    assert "Met Office" in c["attribution"] and "CC BY-SA 4.0" in c["attribution"]
    src = (ROOT / "scripts" / "build_site.py").read_text()   # all three published files get them
    assert '"credits": credits, "spots": results' in src
    assert '{**overflows_geojson(limit=20000), "credits": credits}' in src and '{**load_verification(), "credits": credits}' in src
    page = (ROOT / "src" / "dipcast" / "site" / "index.html").read_text()
    assert "None of these bodies endorses SwimSignal" in page and 'href="terms.html#data"' in page
    # With the counter on, only the privacy notice changes; the terms keep their own date.
    bs.write_pages(tmp_path, SPOTS[:1], token="abcdefghij0123456789", root="https://example.org/")
    assert "(page-view counter)" in (tmp_path / "privacy.html").read_text()
    assert "(page-view counter)" not in (tmp_path / "terms.html").read_text()
    assert 'href="terms.html#data"' in (tmp_path / "verification.html").read_text()


def test_nothing_credits_the_ea_with_advice_the_law_gives_to_the_council():
    # Bathing Water Regulations 2013 reg 13(1)(b): at a poor water the local authority that
    # controls it issues the advice against bathing, not the EA.
    for p in [ROOT / "src" / "dipcast" / "site" / "index.html", ROOT / "src" / "dipcast" / "api" / "static" / "terms.html"]:
        assert "advises against bathing" not in p.read_text(), p.name


def test_observations_from_is_the_first_day_with_a_mask():
    from dipcast.forecast_log import observations_from
    days = pd.to_datetime(["2026-09-27", "2026-09-28", "2026-09-29"]).date
    cov = pd.DataFrame({"site_id": ["a", "a", "b"], "day": days, "slots": [0, 1 << 20, 1 << 3]})
    assert observations_from(cov) == "2026-09-28"
    assert observations_from(cov.assign(slots=0)) is None   # rows written before the masks existed
    assert observations_from(pd.DataFrame()) is None


def test_lead_skill_starts_at_one_and_never_rises():
    skill = _build_site().lead_skill()
    for k in ("spill", "water"):
        s = skill[k]
        assert len(s) == 5 and s[0] == 1.0 and all(0 < b <= a for a, b in pairwise(s))


def test_alerts_are_off_unless_both_settings_are_sound(monkeypatch):
    bs = _build_site()
    monkeypatch.delenv(bs.PUSH_URL_ENV, raising=False)
    monkeypatch.delenv(bs.PUSH_KEY_ENV, raising=False)
    assert bs.push_config() is None
    key = "B" + "A" * 86
    monkeypatch.setenv(bs.PUSH_URL_ENV, "https://dipspot-push.example.workers.dev/")
    monkeypatch.setenv(bs.PUSH_KEY_ENV, key)
    assert bs.push_config() == {"url": "https://dipspot-push.example.workers.dev/", "key": key}
    monkeypatch.setenv(bs.PUSH_URL_ENV, "http://dipspot-push.example.workers.dev/")   # not https
    assert bs.push_config() is None
    monkeypatch.setenv(bs.PUSH_URL_ENV, "https://dipspot-push.example.workers.dev/")
    monkeypatch.setenv(bs.PUSH_KEY_ENV, key + '"')   # would break out of the page's JSON
    assert bs.push_config() is None


def test_the_privacy_notice_describes_alerts_only_when_they_are_on(tmp_path):
    bs = _build_site()
    bs.write_pages(tmp_path, SPOTS[:1], root="https://example.org/")
    off = (tmp_path / "privacy.html").read_text()
    assert "<h2>If alerts are added</h2>" in off and "push address" not in off
    for before, _ in bs.PUSH_SWAPS:   # a rewrite of the notice must not leave a swap with nothing to swap
        assert before in off, before
    bs.write_pages(tmp_path, SPOTS[:1], root="https://example.org/", push=True)
    on = (tmp_path / "privacy.html").read_text()
    assert "<h2>Alerts</h2>" in on and "push address" in on and "If alerts are added" not in on
    for before, after in bs.PUSH_SWAPS:
        assert after in on and before not in on
    assert (tmp_path / "levels.js").exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_offline_copy_rules():
    # sw.js runs in a browser, so its tests are JavaScript; here so that the build's test step runs them.
    r = subprocess.run(["node", "--test", str(ROOT / "tests" / "site_cache.test.cjs"), str(ROOT / "tests" / "site_planner.test.cjs"),
                        str(ROOT / "tests" / "site_counter.test.cjs")], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_alerts_file_uses_the_page_rules(tmp_path):
    import json
    bs = _build_site()
    (tmp_path / "data").mkdir()
    river = {"id": "a", "name": "A river", "kind": "river", "upstream_summary": {"overflows": 3}, "location": {"mode": "river"},
             "now": {"label": "low", "discharging_upstream": 0},
             "days": [{"date": "2026-09-29", "risk": 0.5, "label": "high"}, {"date": "2026-09-30", "risk": 0.01, "label": "low"}]}
    (tmp_path / "data" / "spots.json").write_text(json.dumps({"generated_at": "2026-09-29T08:00:00+01:00", "spots": [river, SPOTS[1]]}))
    assert bs.write_alerts(tmp_path, "https://example.org/swim/", push_on=False)
    out = json.loads((tmp_path / "data" / "alerts.json").read_text())
    assert out["generated_at"] == "2026-09-29T08:00:00+01:00"
    assert out["spots"]["a"] == {"name": "A river", "rank": 2, "level": "high", "headline": "High risk today: sewage spills",
                                 "url": "https://example.org/swim/spot/a/"}
    assert out["spots"]["tarn"]["rank"] == -1 and out["spots"]["tarn"]["level"] == "not covered"


def test_feedback_and_experience_are_built_for_nested_github_pages(tmp_path):
    bs = _build_site()
    bs.write_pages(tmp_path, SPOTS[:1], root="https://example.org/dipcast/")
    feedback = (tmp_path / "feedback.html").read_text()
    assert 'href="page.css"' in feedback and 'href="index.html"' in feedback
    assert 'href="/' not in feedback
    assert "hello@swimsignal.co.uk" in feedback and "Send by email" in feedback
    home = (tmp_path / "index.html").read_text()
    stamp = bs.shell_stamp()
    assert re.fullmatch(r"[0-9a-f]{8}", stamp)
    # The page asks for its scripts at the build's stamp, as the offline copy stores them.
    assert f'<script src="experience.js?v={stamp}">' in home and f'<script src="levels.js?v={stamp}">' in home
    assert (tmp_path / "experience.js").exists()
    sw = (tmp_path / "sw.js").read_text()
    assert 'feedback.html' in sw and f"const BUILD = '{stamp}';" in sw and "const CACHE = `dipcast-${BUILD}`;" in sw
    assert "`levels.js?v=${BUILD}`" in sw and "unpkg" not in sw and "unpkg" not in home
    # The map library from this site, at the paths the page and the worker ask for.
    for f in ("vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css", "vendor/leaflet/LICENSE", "vendor/leaflet/images/layers.png"):
        assert (tmp_path / f).exists(), f
    assert 'href="vendor/leaflet/leaflet.css"' in home and 'src="vendor/leaflet/leaflet.js"' in home
    assert 'SwimSignal' in (tmp_path / "manifest.webmanifest").read_text()
    # Rebranding preserves the installed application's URL and saved-spot storage; the cache keeps its
    # dipcast- prefix, which the worker and the page's "Turn off the offline copy" clear by.
    assert '"start_url": "./"' in (tmp_path / "manifest.webmanifest").read_text()
    assert "k.startsWith('dipcast-')" in sw


def test_river_levels_are_an_observation_beside_the_forecast():
    """The nearest gauge on the spot's own river, as a dict the page can show; a lookup that fails
    or finds nothing leaves the spot without, and a failed forecast is not looked up at all."""
    from dipcast.ingest.flows import RiverState
    bs = _build_site()
    calls = []

    def lookup(lat, lon, dist, river):
        calls.append((lat, lon, dist, river))
        if river == "River Wharfe":
            return RiverState(station="Netherside Hall", river="River Wharfe", lat=lat + 0.05, lon=lon, level_m=0.433,
                              typical_low=0.3, typical_high=2.2, observed_at="2026-10-01T12:15:00Z", rloi="8276")
        if river == "Hebden Beck":
            raise RuntimeError("EA timed out")
        return None

    spots = [{"id": "a", "name": "Burnsall", "lat": 54.047, "lon": -1.953, "location": {"watercourse": "River Wharfe"}},
             {"id": "b", "name": "Hebden", "lat": 54.0, "lon": -2.0, "location": {"watercourse": "Hebden Beck"}},
             {"id": "c", "name": "Tarn", "lat": 54.4, "lon": -3.0, "location": {"watercourse": None}},
             {"id": "d", "name": "Broken", "lat": 54.5, "lon": -3.1, "error": "forecast failed: boom"}]
    now = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)   # an hour after the reading: current, not stale
    assert bs.attach_river_levels(spots, lookup=lookup, workers=2, now=now) == 1
    rs = spots[0]["river_state"]
    assert rs["station"] == "Netherside Hall" and rs["level_m"] == 0.433 and rs["label"] == "low" and rs["rloi"] == "8276"
    assert rs["same_river"] is True and 5 < rs["distance_km"] < 6 and rs["index"] == 0.07
    assert rs["stale"] is False and rs["age_hours"] == 0.8
    assert spots[1]["river_state"] is None and spots[2]["river_state"] is None and spots[3]["river_state"] is None
    assert len(calls) == 3 and all(c[2] == 15 for c in calls)   # the failed forecast is skipped
    # The EA's own attribution line travels with the data and sits on the terms page.
    line = "this uses Environment Agency flood and river level data from the real-time data API (Beta)"
    assert line in bs.data_credits("https://example.org/")["attribution"]
    assert line in (ROOT / "src" / "dipcast" / "api" / "static" / "terms.html").read_text()
    assert line in (ROOT / "src" / "dipcast" / "site" / "index.html").read_text()


def test_weather_context_is_attached_per_spot_and_a_failed_call_leaves_none():
    bs = _build_site()
    seen = []

    def request(url, params, timeout=30):
        seen.append(params)
        lats = params["latitude"].split(",")
        return [{"daily": {"time": ["2026-10-01", "2026-10-02"], "temperature_2m_max": [11.4, None],
                           "sunrise": ["2026-10-01T07:12", "2026-10-02T07:14"], "sunset": ["2026-10-01T18:40", "2026-10-02T18:38"]}}
                for _ in lats]

    spots = [{"id": "a", "name": "A", "lat": 54.0, "lon": -2.0}, {"id": "b", "name": "B", "lat": 54.1, "lon": -2.1},
             {"id": "d", "name": "Broken", "lat": 54.5, "lon": -3.1, "error": "forecast failed: boom"}]
    assert bs.attach_weather(spots, request=request) == 2
    assert seen[0]["daily"] == "temperature_2m_max,sunrise,sunset" and seen[0]["latitude"] == "54.000,54.100"
    assert spots[0]["weather"]["days"] == [{"date": "2026-10-01", "tmax": 11, "sunrise": "07:12", "sunset": "18:40"},
                                           {"date": "2026-10-02", "tmax": None, "sunrise": "07:14", "sunset": "18:38"}]
    assert spots[0]["weather"]["credit"] == "Weather data by Open-Meteo.com" and "weather" not in spots[2]

    def broken(url, params, timeout=30):
        raise RuntimeError("open-meteo request failed")
    fresh = [{"id": "a", "name": "A", "lat": 54.0, "lon": -2.0}]
    assert bs.attach_weather(fresh, request=broken) == 0 and "weather" not in fresh[0]


def test_the_api_server_sends_its_home_page_to_the_site_and_keeps_the_prose_pages(monkeypatch):
    # The API's own map page (static/index.html) was outside the design system and is gone: the
    # map is the static site's. Without the lifespan (no `with`), nothing is warmed up or scheduled.
    # SITE_URL is read when the module loads, so each setting is a reload.
    import importlib

    from fastapi.testclient import TestClient

    from dipcast.api import app as api
    try:
        for env, want in [(None, "https://swimsignal.co.uk/"), ("http://localhost:8769", "http://localhost:8769/"),
                          ("swimsignal.co.uk", "https://swimsignal.co.uk/")]:   # no scheme: not an address
            if env is None:
                monkeypatch.delenv("DIPCAST_SITE_URL", raising=False)
            else:
                monkeypatch.setenv("DIPCAST_SITE_URL", env)
            importlib.reload(api)
            r = TestClient(api.app).get("/", follow_redirects=False)
            assert r.status_code == 307 and r.headers["location"] == want, env
        c = TestClient(api.app)
        assert not (api.STATIC / "index.html").exists()
        for path in ("/about", "/verification", "/terms", "/privacy", "/feedback", "/testing", "/static/page.css", "/static/fonts/SourceSans3-latin.woff2"):
            assert c.get(path).status_code == 200, path
    finally:
        monkeypatch.undo()
        importlib.reload(api)   # as the environment has it, for any later test


def test_the_data_page_lists_every_file_in_data_with_its_size(tmp_path):
    # data.html describes the files the build publishes; the build fills in their sizes and lists
    # any file in data/ the page does not describe, so the list is always every file published.
    bs = _build_site()
    data = tmp_path / "data"
    (data / "links").mkdir(parents=True)
    (data / "spots.json").write_bytes(b"x" * 626_076)
    (data / "overflows.geojson").write_bytes(b"x" * 5_947_289)
    (data / "verification.json").write_bytes(b"x" * 400)
    (data / "new.csv").write_text("a,b\n")   # a file nobody has described yet
    (data / "links" / "a.json").write_bytes(b"x" * 1500)
    (data / "links" / "b.json").write_bytes(b"x" * 1500)
    bs.write_pages(tmp_path, SPOTS[:1], token="abcdefghij0123456789", root="https://example.org/")
    page = (tmp_path / "data.html").read_text()
    row = lambda name: re.search(rf'<tr data-file="{re.escape(name)}">(.*?)</tr>', page).group(1)
    assert row("spots.json").endswith('<td class="num">626 kB</td>') and 'href="data/spots.json"' in row("spots.json")
    assert row("overflows.geojson").endswith(">5.9 MB</td>") and row("verification.json").endswith(">1 kB</td>")
    assert row("alerts.json").endswith(">Not in this build</td>")   # described, but this build wrote none
    assert row("new.csv") == '<td><a href="data/new.csv">new.csv</a></td><td>Not described here yet.</td><td class="num">1 kB</td>'
    assert row("links/") == '<td>links/</td><td>Not described here yet.</td><td class="num">3 kB</td>'   # a folder, whole
    assert bs.with_data_files((bs.STATIC / "data.html").read_text(), data)[1] == ["links/", "new.csv"]
    # The four files every build writes each have a section of their own.
    for name in ("spots.json", "alerts.json", "overflows.geojson", "verification.json"):
        assert f'<h2 id="{name.replace(".", "-")}">{name}</h2>' in page, name
    # A prose page like the others: the shared head and foot, flat links, and the counter when it is on.
    assert "<title>Data files · SwimSignal</title>" in page and 'href="page.css"' in page and 'href="/' not in page
    assert 'href="terms.html#data"' in page and 'href="about.html#embed"' in page and "cloudflareinsights" in page
    assert 'href="data.html">Data files</a>' in (tmp_path / "about.html").read_text()
    # An empty data/ (write_pages over a fresh folder): every described row says so, and nothing breaks.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert bs.write_data_page(empty) == [] and (empty / "data.html").read_text().count("Not in this build") == 4


def test_the_embed_is_written_beside_the_app_with_its_scripts_versioned(tmp_path):
    bs = _build_site()
    bs.write_pages(tmp_path, SPOTS[:1], token="abcdefghij0123456789", root="https://example.org/")
    page = (tmp_path / "embed.html").read_text()
    v = re.search(r'<script src="levels\.js\?v=([0-9a-f]{8})"></script>', page).group(1)
    assert f'<script src="embed.js?v={v}"></script>' in page and v == bs.write_embed(tmp_path)
    for f in ("embed.js", "levels.js", "page.css", "fonts/SourceSans3-latin.woff2", "fonts/SourceSerif4-latin.woff2", "icons/icon.svg"):
        assert (tmp_path / f).exists(), f
    assert '<meta name="robots" content="noindex">' in page and 'name="viewport" content="width=device-width, initial-scale=1"' in page
    # No page-view counter in someone else's page: it could not see a visitor's choice not to be counted.
    assert "cloudflareinsights" not in page and "page-counter" not in page
    assert 'id="card"' in page and "<noscript>" in page


def test_the_embed_fits_a_column_320_px_wide():
    # What the card lays out at a fixed size must leave the day's bar room at 320 px: the frame's
    # border and padding, and the rows' day and level columns and their gaps. The rest is fluid
    # (checked in headless Chrome on 3 Oct 2026: no spot's card overflowed at 320, 375 or 480 px).
    css = "\n".join(re.findall(r"<style>(.*?)</style>", (ROOT / "src" / "dipcast" / "site" / "embed.html").read_text(), re.DOTALL))
    card = re.search(r"\.card \{([^}]*)\}", css).group(1)
    pad = [int(x) for x in re.search(r"padding:(\d+)px (\d+)px", card).groups()]
    border = int(re.search(r"border:(\d+)px", card).group(1))
    drow = re.search(r"\.drow \{([^}]*)\}", css).group(1)
    cols = [int(x) for x in re.search(r"grid-template-columns:(\d+)px (\d+)px minmax\(0, 1fr\)", drow).groups()]
    gap = int(re.search(r"gap:(\d+)px", drow).group(1))
    bar = 320 - 2 * border - 2 * pad[1] - sum(cols) - 2 * gap
    assert bar >= 100, bar
    assert not re.search(r"(?<![-\w])(min-)?width\s*:\s*(3[2-9]\d|[4-9]\d\d|\d{4,})px", css)   # nothing wider than the frame
    assert "max-width:480px" in card.replace(" ", "")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_embed_card_rules():
    # embed.js runs in a browser, so its tests are JavaScript; here so that the build's test step runs them.
    r = subprocess.run(["node", "--test", str(ROOT / "tests" / "site_embed.test.cjs")], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_about_documents_the_data_page_and_the_embed_and_the_terms_allow_it(tmp_path):
    bs = _build_site()
    bs.write_pages(tmp_path, SPOTS[:1], root="https://example.org/")
    about = (tmp_path / "about.html").read_text()
    snippet = re.search(r'<h2 id="embed">.*?<pre><code>(.*?)</code></pre>', about, re.DOTALL).group(1)
    assert snippet.startswith("&lt;iframe src=\"https://swimsignal.co.uk/embed.html?spot=wharfe-burnsall\"") and 'title="' in snippet
    assert 'href="embed.html?spot=wharfe-burnsall"' in about and (tmp_path / "embed.html").exists()
    terms = (tmp_path / "terms.html").read_text()
    allow = re.search(r"<li>You may show a spot's forecast on your own website.*?</li>", terms).group(0)
    assert "embed.html" in allow and "credits intact" in allow and 'href="about.html#embed"' in allow
    assert 'href="data.html"' in terms


def test_the_api_server_serves_the_data_page():
    from fastapi.testclient import TestClient

    from dipcast.api import app as api
    r = TestClient(api.app).get("/data")
    assert r.status_code == 200 and "<h1>Data files</h1>" in r.text
