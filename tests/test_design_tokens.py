"""The design system's tokens live twice: in page.css for the prose pages and inline in index.html
for the app, which paints before any stylesheet arrives (docs/DESIGN.md, "Rules for changes"). These
checks catch the two drifting apart, and the rules a token stands for being bypassed in a rule."""

import base64
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "dipcast" / "site" / "index.html"
CSS = ROOT / "src" / "dipcast" / "api" / "static" / "page.css"
STATIC = CSS.parent
# The app's own layout and its SVG filter, which only index.html has.
APP_ONLY = {"--tile-filter", "--header-h", "--nav-h"}


def app_css() -> str:
    s = APP.read_text()
    return s[s.index("<style>") + 7: s.index("</style>")]


def tokens(css: str) -> dict[tuple[str, str], str]:
    """Every `--name: value` declared in a :root rule, keyed by (the @media it sits in, or "", name),
    with the value's spacing normalised. A small scanner: comments out, quoted strings skipped,
    each block's prelude kept on a stack."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    out, stack, buf, i = {}, [], "", 0
    while i < len(css):
        c = css[i]
        if c in "\"'":
            j = css.index(c, i + 1)
            buf += css[i: j + 1]
            i = j + 1
            continue
        if c == "{":
            stack.append(buf.strip())
            buf = ""
        elif c == "}":
            if stack and stack[-1] == ":root":
                media = next((p for p in reversed(stack[:-1]) if p.startswith("@media")), "")
                for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", buf):
                    out[(re.sub(r"\s+", " ", media), name)] = norm(value)
            stack.pop()
            buf = ""
        else:
            buf += c
        i += 1
    return out


def norm(value: str) -> str:
    v = re.sub(r"\s+", " ", value.strip())
    v = re.sub(r"\s*,\s*", ",", v)
    return re.sub(r"#[0-9a-fA-F]{3,6}\b", lambda m: m.group().lower(), v)


def rules(css: str) -> str:
    """The stylesheet without its comments and its :root token blocks."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return re.sub(r":root\s*\{[^}]*\}", "", css)


def test_the_app_and_the_prose_pages_share_one_set_of_tokens():
    app, prose = tokens(app_css()), tokens(CSS.read_text())
    assert app and prose
    names = lambda t: {k for k in t if k[1] not in APP_ONLY}
    assert names(app) == names(prose), ("only in index.html", sorted(names(app) - names(prose)),
                                        "only in page.css", sorted(names(prose) - names(app)))
    for k in names(app):
        assert app[k] == prose[k], (k, app[k], prose[k])
    # "30 on phones", at one step in both.
    assert app[("@media (max-width: 540px)", "--fs-title")] == "30px"


def test_rules_use_the_tokens():
    sheets = {"index.html": app_css(), "page.css": CSS.read_text()}
    v = (STATIC / "verification.html").read_text()
    sheets["verification.html"] = v[v.index("<style>") + 7: v.index("</style>")]
    for name, css in sheets.items():
        body = rules(css)
        # One radius (and the mark's and a link's focus ring, as tokens); circles and round badges.
        for value in re.findall(r"border-radius\s*:\s*([^;}]+)", body):
            parts = value.replace("!important", "").split()
            assert all(p in {"0", "50%", "999px"} or re.fullmatch(r"var\(--radius(-mark|-ring)?\)", p) for p in parts), (name, value)
        # Six sizes: no font size written as a number.
        assert not re.search(r"font-size\s*:\s*[\d.]+(px|rem|em)", body), name
        assert not re.search(r"\bfont\s*:\s*(?:[a-z]+\s+|\d{3}\s+)*[\d.]+(px|rem|em)\b", body), name   # the size, not the line height
        # No colour written in a rule: a mask's black is its alpha, not a colour.
        for decl in re.findall(r"[\w-]+\s*:[^;{}]*", body):
            if decl.split(":")[0].strip() in {"mask", "-webkit-mask"}:
                continue
            assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", decl), (name, decl)


def test_the_mark_is_the_icons_teal_and_the_browser_bar_the_headers_slate():
    teal = re.search(r'<rect width="512" height="512" fill="(#[0-9A-Fa-f]{6})"/>', (ROOT / "src/dipcast/site/icons/icon.svg").read_text()).group(1).lower()
    bar = tokens(CSS.read_text())[("", "--bar")]
    pages = [APP, ROOT / "scripts" / "build_site.py", *sorted(STATIC.glob("*.html"))]
    for p in pages:
        s = p.read_text()
        fills = re.findall(r'<rect width="512" height="512" fill="(#[0-9A-Fa-f]{6})"/>', s)
        assert fills and {f.lower() for f in fills} == {teal}, (p.name, fills)
        for colour in re.findall(r'<meta name="theme-color" content="([^"]+)"', s):
            assert colour.lower() == bar, (p.name, colour)


def test_the_map_library_is_served_from_the_site_and_matches_its_pinned_hashes():
    page = APP.read_text()
    assert "unpkg.com" not in page and "unpkg.com" not in (ROOT / "src/dipcast/site/sw.js").read_text()
    vendor = ROOT / "src" / "dipcast" / "site" / "vendor" / "leaflet"
    for f in ("leaflet.css", "leaflet.js"):
        m = re.search(rf'(?:href|src)="vendor/leaflet/{f}" integrity="(sha256-[^"]+)"', page)
        assert m, f
        digest = "sha256-" + base64.b64encode(hashlib.sha256((vendor / f).read_bytes()).digest()).decode()
        assert m.group(1) == digest, f
    for img in re.findall(r"url\((images/[^)]+)\)", (vendor / "leaflet.css").read_text()):
        assert (vendor / img).exists(), img
    assert "BSD 2-Clause" in (vendor / "LICENSE").read_text() and "1.9.4" in (vendor / "VERSION.txt").read_text()
