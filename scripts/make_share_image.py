"""Draw the link-preview image (src/dipcast/site/icons/og.png, 1200 x 630) that messaging apps
and social sites show for a shared link. Rerun after a change of name or tagline:

    uv run --with pillow python scripts/make_share_image.py --name SwimSignal

The site's own typefaces (docs/DESIGN.md): the name in Source Serif 4, the rest in Source Sans 3.
The repository holds them as web fonts only (src/dipcast/api/static/fonts/, WOFF2, which Pillow
cannot read), so pass the TTFs with --display and --font, downloaded from Google Fonts or
github.com/adobe-fonts; without them the image falls back to the system's Georgia and Arial.
A variable font is set to the weight asked for; a static one is used as it is. For the name, use
Source Serif 4's text cut, TTF/SourceSerif4-Semibold.ttf at github.com/adobe-fonts/source-serif:
the card is mostly seen at a third of its size, where the Display cut's thin strokes fade.

The card is the site's own page since the fifth round (docs/DESIGN.md): the paper, the mark, the
name in teal with the river line under it, as the list's heading has, and the words in ink and
the muted grey.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "src" / "dipcast" / "site" / "icons"
W, H = 1200, 630
PAPER, TEAL, INK, MUTED = "#F6F4EE", "#0F5A61", "#1B2328", "#5A6166"   # --bg, --brand, --ink, --muted
# The mark's river turned to run across, as RIVER in index.html: two curves in a 120 x 20 box.
RIVER = [((1.5, 7.3), (32, 11.9), (42.6, -8), (66.1, 7)), ((66.1, 7), (89.5, 21.9), (96.3, 22.7), (118.5, 7.3))]
SYSTEM_SERIF = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
SYSTEM_SANS = "/System/Library/Fonts/Supplemental/Arial.ttf"


def font(path: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    """A font at a size and, for a variable font, a weight."""
    f = ImageFont.truetype(path, size)
    try:
        f.set_variation_by_axes([weight])
    except OSError:   # a static font: its one weight
        pass
    return f


def river(img: Image.Image, x: int, y: int, scale: float, colour: str) -> None:
    """The river line at (x, y), scale times its 120 x 20 and its 1.75 stroke: drawn four times over
    size and scaled down, since Pillow's lines have no smooth edges."""
    k = 4 * scale
    layer = Image.new("L", (round(120 * k), round(20 * k)), 0)
    pts = []
    for p0, p1, p2, p3 in RIVER:
        for i in range(61):
            t = i / 60
            u = 1 - t
            pts.append(tuple((u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * e) * k for a, b, c, e in zip(p0, p1, p2, p3)))
    w = 1.75 * k
    d = ImageDraw.Draw(layer)
    d.line(pts, fill=255, width=round(w), joint="curve")
    for px, py in (pts[0], pts[-1]):   # round caps
        d.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=255)
    mask = layer.resize((round(120 * scale), round(20 * scale)), Image.LANCZOS)
    img.paste(ImageColor.getrgb(colour), (x, y, x + mask.width, y + mask.height), mask)


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + [line]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="SwimSignal")
    ap.add_argument("--tagline", default="Check the water before you go")
    ap.add_argument("--line", default="Five-day sewage-spill forecasts for river and lake swim spots in England")
    ap.add_argument("--display", default=SYSTEM_SERIF, help="serif TTF for the name (Source Serif 4)")
    ap.add_argument("--font", default=SYSTEM_SANS, help="sans TTF for the rest (Source Sans 3)")
    a = ap.parse_args()

    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    icon = Image.open(ICONS / "icon-512.png").convert("RGBA").resize((300, 300), Image.LANCZOS)
    mask = Image.new("L", icon.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *icon.size), radius=60, fill=255)
    img.paste(icon, (90, (H - 300) // 2), mask)

    x, width = 450, W - 450 - 80
    name = font(a.display, 100, 600)
    tag = font(a.font, 42, 600)
    small = font(a.font, 30, 400)
    tag_lines = wrap(d, a.tagline, tag, width)
    small_lines = wrap(d, a.line, small, width)
    block = 100 + 22 + 40 + 26 + len(tag_lines) * 54 + 20 + len(small_lines) * 40
    y = (H - block) // 2
    d.text((x, y), a.name, font=name, fill=TEAL)
    y += 100 + 22
    river(img, x + 2, y, 2, TEAL)
    y += 40 + 26
    for ln in tag_lines:
        d.text((x, y), ln, font=tag, fill=INK)
        y += 54
    y += 20
    for ln in small_lines:
        d.text((x, y), ln, font=small, fill=MUTED)
        y += 40
    out = ICONS / "og.png"
    img.save(out, optimize=True)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
