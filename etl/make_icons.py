"""Generate the Whereabouts app icons: a compass on the app's teal.

Draws at high resolution with Pillow and downsamples, writing favicon.ico,
icon-32/180/192/512 into docs/. Run after changing the design:

    cd etl && uv run python make_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

DOCS = Path(__file__).parent.parent / "docs"

TEAL = "#0f4c5c"
CREAM = "#f5f2ea"
AMBER = "#e0912f"
FAINT = "#3d6b7a"  # subtle ticks against the teal


def draw_compass(size: int = 2048) -> Image.Image:
    img = Image.new("RGB", (size, size), TEAL)
    d = ImageDraw.Draw(img)
    c = size / 2

    # outer ring
    ring_r = 0.365 * size
    ring_w = 0.042 * size
    d.ellipse([c - ring_r, c - ring_r, c + ring_r, c + ring_r],
              outline=CREAM, width=round(ring_w))

    # cardinal ticks just inside the ring
    tick_out, tick_in, tick_w = 0.325 * size, 0.27 * size, round(0.018 * size)
    for ang in (0, 90, 180, 270):
        a = math.radians(ang)
        d.line([c + tick_out * math.sin(a), c - tick_out * math.cos(a),
                c + tick_in * math.sin(a), c - tick_in * math.cos(a)],
               fill=FAINT, width=tick_w)

    # needle: a diamond pointing north-east, amber tip and cream tail
    theta = math.radians(45)
    tip_r, half_w = 0.30 * size, 0.085 * size
    ux, uy = math.sin(theta), -math.cos(theta)      # unit vector to tip
    px, py = -uy, ux                                 # perpendicular
    tip = (c + tip_r * ux, c + tip_r * uy)
    tail = (c - tip_r * ux, c - tip_r * uy)
    left = (c + half_w * px, c + half_w * py)
    right = (c - half_w * px, c - half_w * py)
    d.polygon([tip, left, right], fill=AMBER)
    d.polygon([tail, right, left], fill=CREAM)

    # hub
    hub = 0.045 * size
    d.ellipse([c - hub, c - hub, c + hub, c + hub], fill=TEAL, outline=CREAM,
              width=round(0.016 * size))
    return img


def main() -> None:
    art = draw_compass()
    for px in (32, 180, 192, 512):
        art.resize((px, px), Image.LANCZOS).save(DOCS / f"icon-{px}.png")
        print(f"  icon-{px}.png")
    art.resize((64, 64), Image.LANCZOS).save(
        DOCS / "favicon.ico", sizes=[(16, 16), (32, 32)])
    print("  favicon.ico")


if __name__ == "__main__":
    main()
