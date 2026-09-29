"""Draw the link-preview image (src/dipcast/site/icons/og.png, 1200 x 630) that messaging apps
and social sites show for a shared link. Rerun after a change of name or tagline:

    uv run --with pillow python scripts/make_share_image.py --name dipcast

Fonts default to Arial on macOS; pass --font and --bold elsewhere.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "src" / "dipcast" / "site" / "icons"
W, H = 1200, 630
TEAL, MINT, WHITE, SOFT = "#0F5A61", "#5CC2B5", "#FFFFFF", "#CFEDE8"


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
    ap.add_argument("--name", default="dipcast")
    ap.add_argument("--tagline", default="Will sewage from upstream storm overflows reach your swim spot?")
    ap.add_argument("--line", default="Five-day forecasts for river and lake spots in England")
    ap.add_argument("--font", default="/System/Library/Fonts/Supplemental/Arial.ttf")
    ap.add_argument("--bold", default="/System/Library/Fonts/Supplemental/Arial Bold.ttf")
    a = ap.parse_args()

    img = Image.new("RGB", (W, H), TEAL)
    d = ImageDraw.Draw(img)
    icon = Image.open(ICONS / "icon-512.png").convert("RGBA").resize((300, 300), Image.LANCZOS)
    mask = Image.new("L", icon.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *icon.size), radius=66, fill=255)
    img.paste(icon, (90, (H - 300) // 2), mask)
    d.rounded_rectangle((88, (H - 300) // 2 - 2, 392, (H + 300) // 2 + 2), radius=68, outline=MINT, width=3)

    x, width = 450, W - 450 - 80
    name = ImageFont.truetype(a.bold, 96)
    tag = ImageFont.truetype(a.bold, 40)
    small = ImageFont.truetype(a.font, 30)
    tag_lines = wrap(d, a.tagline, tag, width)
    small_lines = wrap(d, a.line, small, width)
    block = 96 + 30 + len(tag_lines) * 52 + 24 + len(small_lines) * 40
    y = (H - block) // 2
    d.text((x, y), a.name, font=name, fill=WHITE)
    y += 96 + 30
    for ln in tag_lines:
        d.text((x, y), ln, font=tag, fill=WHITE)
        y += 52
    y += 24
    for ln in small_lines:
        d.text((x, y), ln, font=small, fill=SOFT)
        y += 40
    out = ICONS / "og.png"
    img.save(out, optimize=True)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
