"""The static site's pages: every spot gets a page of its own whose share preview carries its
name (never today's level, which a cached preview would show for days), every page carries
absolute preview links, a spot's page has a relative <base>, and the live scorer reports when the
observation records its coverage rule needs begin."""

import shutil
import subprocess
import sys
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
    assert "<title>Wharfe at &quot;Cromwheel&quot; &amp; Ilkley: sewage-spill forecast · SwimSignal</title>" in head
    assert "from the 15 monitored storm overflows upstream" in head
    assert "very high" not in head.lower().replace("veryhigh", "")
    assert head.count("<title>") == 1 and "page-meta" not in page
    assert '<h2 class="spot-name">Wharfe at &quot;Cromwheel&quot; &amp; Ilkley</h2>' in page   # before the script runs
    assert "no monitored storm overflow can reach this lake" in (tmp_path / "spot" / "tarn" / "index.html").read_text()
    home = (tmp_path / "index.html").read_text()
    assert '<link rel="canonical" href="https://example.org/swim/">' in home
    assert '<base href="./">' in home.split("</head>")[0]   # pushState to spot/<id>/ must not move its links
    assert "Loading forecasts…" in home
    for f in ["sw.js", "manifest.webmanifest", "icons/og.png", "verification.html", "privacy.html", "page.css", ".nojekyll", "404.html", "robots.txt"]:
        assert (tmp_path / f).exists(), f
    # GitHub Pages serves 404.html at any depth, so its links must be absolute; search engines may not keep it.
    lost = (tmp_path / "404.html").read_text()
    assert 'href="https://example.org/swim/"' in lost and 'href="https://example.org/swim/page.css"' in lost and 'content="noindex"' in lost
    assert (tmp_path / "robots.txt").read_text() == "User-agent: *\nAllow: /\nSitemap: https://example.org/swim/sitemap.xml\n"
    sm = (tmp_path / "sitemap.xml").read_text()
    assert sm.count("<url>") == 5 and "<loc>https://example.org/swim/spot/tarn/</loc>" in sm
    assert "<loc>https://example.org/swim/about.html</loc>" in sm
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
    for name in ["about.html", "verification.html", "terms.html", "privacy.html"]:
        page = (tmp_path / name).read_text()
        assert 'href="about.html">About</a>' in page, name
        assert 'href="/' not in page, name
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
    assert out["spots"]["a"] == {"name": "A river", "rank": 2, "level": "high", "headline": "High today: sewage spills",
                                 "url": "https://example.org/swim/spot/a/"}
    assert out["spots"]["tarn"]["rank"] == -1 and out["spots"]["tarn"]["level"] == "not covered"


def test_feedback_and_experience_are_built_for_nested_github_pages(tmp_path):
    bs = _build_site()
    bs.write_pages(tmp_path, SPOTS[:1], root="https://example.org/dipcast/")
    feedback = (tmp_path / "feedback.html").read_text()
    assert 'href="page.css"' in feedback and 'href="index.html"' in feedback
    assert 'href="/' not in feedback
    assert "hello@swimsignal.co.uk" in feedback and "Prepare email" in feedback
    home = (tmp_path / "index.html").read_text()
    assert '<script src="experience.js">' in home
    assert (tmp_path / "experience.js").exists()
    assert 'feedback.html' in (tmp_path / "sw.js").read_text()
    assert 'SwimSignal' in (tmp_path / "manifest.webmanifest").read_text()
    # Rebranding preserves the installed application's URL and saved-spot storage.
    assert '"start_url": "./"' in (tmp_path / "manifest.webmanifest").read_text()
    assert "dipcast-v1" in (tmp_path / "sw.js").read_text()
