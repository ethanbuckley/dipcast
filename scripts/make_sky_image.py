"""Draw the picture behind the top of the app's pages (src/dipcast/site/icons/fells.webp): a pale
sky, three ridges of fells fading into the mist, a lake that mirrors them, and mist that ends in the
page's paper. Drawn here rather than photographed, so it needs no licence and no request to anyone
else's server. Rerun after changing it:

    uv run --with pillow python scripts/make_sky_image.py

The page (index.html, "The sky and the glass") shows it 600 CSS px tall at twice that in pixels, and
anchors it to the bottom of a view's opening words: everything above ANCHOR is sky, pale enough for
the words on it, the fells rise in the band below them, and by FADE_END the picture is the paper, so
nothing further down sits on it. Its top rows are the page's --sky, which runs on above it, and its
last the paper. The same seed draws the same picture.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "dipcast" / "site" / "icons" / "fells.webp"
W, H = 1000, 1200
ANCHOR = 580               # the sky ends here: the page sets a view's words above it
SHORE = 905                # the lake's edge
FADE_START, FADE_END = 960, 1180   # from the lake into the paper
PAPER, SKY, SKY_TOP, SKY_LOW = "#f6f4ee", "#dfe6e7", "#d4dfe4", "#ece9e1"   # --bg, --sky; the sky high up and at the fells
# Far to near: base, height, roughness, colour, and where the mist in its valley starts and how deep.
RIDGES = [(770, 92, .7, "#b9c9cc", 50, 120), (840, 100, 1.0, "#93acae", 60, 120), (905, 108, 1.3, "#6f8f8e", 70, 110)]


def hexc(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], float)


def lerp(a, b, t):
    return a + (b - a) * t


def noise(rng, scales, weights, stretch=1.0) -> np.ndarray:
    """Value noise in -0.5..0.5: random grids at several scales, upsampled smoothly and summed;
    stretch > 1 draws the shapes out sideways (cloud, ripples)."""
    out = np.zeros((H, W))
    for s, wt in zip(scales, weights):
        g = rng.random((max(2, int(H / s)), max(2, int(W / (s * stretch)))))
        im = Image.fromarray((g * 255).astype(np.uint8), "L").resize((W, H), Image.BICUBIC)
        out += wt * (np.asarray(im, float) / 255 - .5)
    return out / sum(weights)


def ridge(rng, base, amp, rough) -> np.ndarray:
    """A line of fells across the picture: broad rounded shoulders and a few crags, y for each x."""
    x = np.arange(W)
    y = np.full(W, float(base))
    for n, a in [(0.8, 1.0), (1.9, .5), (4.3, .22), (10, .07 * rough), (23, .03 * rough)]:
        y -= amp * a * (np.sin(2 * np.pi * n * x / W + rng.random() * 2 * np.pi) * .5 + .5)
    fine = Image.fromarray((rng.random((1, W // 5)) * 255).astype(np.uint8), "L").resize((W, 1), Image.BICUBIC)
    return y + (np.asarray(fine, float)[0] / 255 - .5) * 6 * rough


def draw(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    Y = np.arange(H)[:, None]
    sky_low = hexc(SKY_LOW)
    # The sky: cool at the top, warmer and paler towards the fells, with soft cloud drawn out sideways.
    t = (np.clip(Y / 706, 0, 1) ** .85)[..., None]
    img = lerp(hexc(SKY_TOP), sky_low, t) * np.ones((1, W, 1))
    cloud = np.clip((noise(rng, [700, 300, 120, 50], [1, .6, .3, .12], 2.6) + .04) * 3.2, 0, 1) ** 1.5
    cloud *= np.clip(1 - Y / 980, 0, 1)
    img += (cloud[..., None] * (hexc("#fbfaf6") - img)) * .55
    img += noise(rng, [500, 180], [1, .5], 2.2)[..., None] * 9
    # The top rows become the page's --sky, which fills the page above the picture: no edge shows.
    img = lerp(img, hexc(SKY), (np.clip(1 - Y / 200, 0, 1) ** 1.5)[..., None])
    # The fells, far to near, darker and greener as they come closer. Mist lies in level bands in the
    # valleys, so each ridge fades towards a fixed height, not away from its own skyline.
    for base, amp, rough, col, mist_from, mist_depth in RIDGES:
        top = ridge(rng, base, amp, rough)[None, :]
        c = hexc(col)
        layer = c + noise(rng, [220, 80, 26], [1, .5, .25], 3.0)[..., None] * 10
        depth = (np.clip((Y - (base - mist_from)) / mist_depth, 0, 1) ** 1.2)[..., None]
        layer = lerp(layer, lerp(c, sky_low, .7), depth)
        img = lerp(img, layer, np.clip((Y - top) / 2.0, 0, 1)[..., None])   # an antialiased skyline
    # The lake: the scene above mirrored, softened and lightened, broken by ripples, with a thin bright
    # line where mist sits on the water at the shore.
    mirror = Image.fromarray(np.clip(img[np.clip(2 * SHORE - Y[:, 0], 0, H - 1)], 0, 255).astype(np.uint8), "RGB")
    mirror = np.asarray(mirror.filter(ImageFilter.GaussianBlur(5)), float)
    water = lerp(mirror, hexc("#dbe4e4"), .5) + noise(rng, [300, 110, 36, 12], [1, .7, .45, .3], 16)[..., None] * 18
    img = lerp(img, water, np.clip((Y - SHORE) / 3, 0, 1)[..., None])
    img = lerp(img, hexc("#eef0ec"), (np.exp(-((Y - SHORE - 5) / 6.0) ** 2) * .5)[..., None])
    # Into the paper, so the picture has no lower edge.
    img = lerp(img, hexc(PAPER), (np.clip((Y - FADE_START) / (FADE_END - FADE_START), 0, 1) ** 1.1)[..., None])
    img += rng.normal(0, 1.6, img.shape)   # grain, so the soft tones do not band
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(.6))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", type=Path, default=OUT)
    a = p.parse_args()
    draw(a.seed).save(a.out, quality=82, method=6)
    print(a.out, f"{a.out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
