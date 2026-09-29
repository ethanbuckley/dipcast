"""The static site's pages: every spot gets a page of its own whose share preview carries its
name (never today's level, which a cached preview would show for days), every page carries
absolute preview links, a spot's page has a relative <base>, and the live scorer reports when the
observation records its coverage rule needs begin."""

import sys
from pathlib import Path

import pandas as pd

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
    {"id": "Bad Id", "name": "Odd", "kind": "river", "upstream_summary": {"overflows": 0}, "days": []},
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
    assert "<title>Wharfe at &quot;Cromwheel&quot; &amp; Ilkley: sewage-spill forecast · dipcast</title>" in head
    assert "from the 15 monitored storm overflows upstream" in head
    assert "very high" not in head.lower().replace("veryhigh", "")
    assert head.count("<title>") == 1 and "page-meta" not in page
    assert '<h2 class="spot-name">Wharfe at &quot;Cromwheel&quot; &amp; Ilkley</h2>' in page   # before the script runs
    assert "no monitored storm overflow can reach this lake" in (tmp_path / "spot" / "tarn" / "index.html").read_text()
    home = (tmp_path / "index.html").read_text()
    assert '<link rel="canonical" href="https://example.org/swim/">' in home
    assert "<base" not in home.split("</head>")[0]   # the home page resolves links from its own address
    assert "Loading forecasts…" in home
    for f in ["sw.js", "manifest.webmanifest", "icons/og.png", "verification.html", "privacy.html", "page.css", ".nojekyll"]:
        assert (tmp_path / f).exists(), f
    sm = (tmp_path / "sitemap.xml").read_text()
    assert sm.count("<url>") == 4 and "<loc>https://example.org/swim/spot/tarn/</loc>" in sm
    assert "<lastmod>2026-09-29</lastmod>" in sm


def test_the_page_view_counter_reaches_spot_pages_and_a_dropped_spot_loses_its_page(tmp_path):
    bs = _build_site()
    bs.write_pages(tmp_path, SPOTS[:2], token="abcdefghij0123456789", root="https://example.org/")
    assert "cloudflareinsights" in (tmp_path / "spot" / "tarn" / "index.html").read_text()
    bs.write_pages(tmp_path, SPOTS[:1], root="https://example.org/")
    assert (tmp_path / "spot" / "wharfe-ilkley").exists() and not (tmp_path / "spot" / "tarn").exists()
    assert "cloudflareinsights" not in (tmp_path / "index.html").read_text()


def test_site_url_follows_the_repository_unless_set(monkeypatch):
    bs = _build_site()
    monkeypatch.delenv("DIPCAST_SITE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "EthanBuckley/swimcast")
    assert bs.site_url() == "https://ethanbuckley.github.io/swimcast/"
    monkeypatch.setenv("DIPCAST_SITE_URL", "https://swim.example")
    assert bs.site_url() == "https://swim.example/"


def test_every_listed_spot_can_have_its_own_page():
    bs = _build_site()
    ids = pd.read_csv(ROOT / "spots.csv")["id"]
    assert ids.map(lambda i: bool(bs.SPOT_ID.fullmatch(i))).all()
    page = (ROOT / "src" / "dipcast" / "site" / "index.html").read_text()
    assert "const PAGE_ID = /^[a-z0-9-]+$/;" in page   # the page's script uses the same rule


def test_observations_from_is_the_first_day_with_a_mask():
    from dipcast.forecast_log import observations_from
    days = pd.to_datetime(["2026-09-27", "2026-09-28", "2026-09-29"]).date
    cov = pd.DataFrame({"site_id": ["a", "a", "b"], "day": days, "slots": [0, 1 << 20, 1 << 3]})
    assert observations_from(cov) == "2026-09-28"
    assert observations_from(cov.assign(slots=0)) is None   # rows written before the masks existed
    assert observations_from(pd.DataFrame()) is None
