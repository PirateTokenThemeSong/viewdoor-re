#!/usr/bin/env python3
"""Build the project icon from the stock VIEW Door app icon (black on yellow).

The upstream launcher icon is a black glyph on a #FFD500 square. This script
recolours the background to Home Assistant blue (#03A9F4), crops the generous
launcher padding, rounds the corners and writes the PNGs used by the README and
by the Home Assistant brand folder.

Usage:
    python tools/make_icon.py [path/to/logo_foreground.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "work" / "apktool-out" / "res" / "mipmap-xxxhdpi" / "logo_foreground.png"

BG = (0x18, 0xBC, 0xF2)  # Home Assistant logo blue
FG = (0xFF, 0xFF, 0xFF)
YELLOW_LUMA = 0.299 * 255 + 0.587 * 213 + 0.114 * 0  # luminance of #FFD500

MARGIN = 1.5  # cropped side = longest glyph side * MARGIN
RADIUS = 0.22  # corner radius, as a fraction of the side
SS = 8  # supersampling for the corner mask

ICON_README = ROOT / "assets" / "icon.png"
BRAND = ROOT / "custom_components" / "vimar_door" / "brand"


def glyph_alpha(src: Path) -> Image.Image:
    """Ink coverage of the stock icon: 255 where black, 0 where yellow."""
    luma = Image.open(src).convert("RGB").convert("L")
    return luma.point(lambda v: round(255 * max(0.0, 1.0 - v / YELLOW_LUMA)))


def square_icon(alpha: Image.Image) -> Image.Image:
    """Black glyph over HA blue, cropped tight and padded back to a square."""
    box = alpha.point(lambda v: 255 if v > 128 else 0).getbbox()
    assert box is not None, "empty glyph"
    x0, y0, x1, y1 = box
    side = round(max(x1 - x0, y1 - y0) * MARGIN)
    cx, cy = round((x0 + x1) / 2), round((y0 + y1) / 2)
    half = side // 2

    out = Image.new("RGBA", (side, side), BG + (255,))
    # crop the glyph with the same offset, then ink it in
    shifted = alpha.crop((cx - half, cy - half, cx - half + side, cy - half + side))
    out.paste(FG + (255,), (0, 0), shifted)
    return out


def rounded(img: Image.Image, size: int) -> Image.Image:
    r = round(size * RADIUS)
    mask = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * SS - 1, size * SS - 1), radius=r * SS, fill=255
    )
    mask = mask.resize((size, size), Image.LANCZOS)
    out = img.resize((size, size), Image.LANCZOS)
    out.putalpha(mask)
    return out


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    master = square_icon(glyph_alpha(src))

    BRAND.mkdir(parents=True, exist_ok=True)
    targets = {ICON_README: 256, BRAND / "icon.png": 256, BRAND / "icon@2x.png": 512}
    for path, size in targets.items():
        rounded(master, size).save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({size}x{size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
