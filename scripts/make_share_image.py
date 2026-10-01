"""Draw the link-preview image (src/dipcast/site/icons/og.png, 1200 x 630) that messaging apps
and social sites show for a shared link. Rerun after a change of name or tagline:

    uv run --with pillow python scripts/make_share_image.py --name SwimSignal

The site's own typefaces (docs/DESIGN.md): the name in Source Serif 4, the rest in Source Sans 3.
The repository holds them as web fonts only (src/dipcast/api/static/fonts/, WOFF2, which Pillow
cannot read), so pass the TTFs with --display and --font, downloaded from Google Fonts or
github.com/adobe-fonts; without them the image falls back to the system's Georgia and Arial.
A variable font is set to the weight asked for; a static one is used as it is.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "src" / "dipcast" / "site" / "icons"
W, H = 1200, 630
TEAL, MINT, WHITE, SOFT = "#0F5A61", "#5CC2B5", "#FFFFFF", "#CFEDE8"
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

    img = Image.new("RGB", (W, H), TEAL)
    d = ImageDraw.Draw(img)
    icon = Image.open(ICONS / "icon-512.png").convert("RGBA").resize((300, 300), Image.LANCZOS)
    mask = Image.new("L", icon.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *icon.size), radius=60, fill=255)
    img.paste(icon, (90, (H - 300) // 2), mask)
    d.rounded_rectangle((88, (H - 300) // 2 - 2, 392, (H + 300) // 2 + 2), radius=62, outline=MINT, width=3)

    x, width = 450, W - 450 - 80
    name = font(a.display, 100, 600)
    tag = font(a.font, 42, 600)
    small = font(a.font, 30, 400)
    tag_lines = wrap(d, a.tagline, tag, width)
    small_lines = wrap(d, a.line, small, width)
    block = 100 + 26 + len(tag_lines) * 54 + 20 + len(small_lines) * 40
    y = (H - block) // 2
    d.text((x, y), a.name, font=name, fill=WHITE)
    y += 100 + 26
    for ln in tag_lines:
        d.text((x, y), ln, font=tag, fill=WHITE)
        y += 54
    y += 20
    for ln in small_lines:
        d.text((x, y), ln, font=small, fill=SOFT)
        y += 40
    out = ICONS / "og.png"
    img.save(out, optimize=True)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
